"""Exclusão definitiva dos dados de uma pessoa (LGPD; revisão de lançamento, bloqueador 3).

Uso, no servidor:
    docker compose exec bot python -m perai.exclusao --chat 123456789
        mostra quem é e quanto há guardado em cada tabela; não apaga nada.
    docker compose exec bot python -m perai.exclusao --chat 123456789 --confirmar "Nome Exato"
        apaga de vez: desconecta e revoga o Google, apaga do banco (tudo que aponta para a
        pessoa cai junto, em cascata) e apaga os arquivos dela. Confere no fim que não sobrou nada.

O que fica de fora, de propósito:
- chamadas_modelo: só contagem de tokens, custo e tempo; perde o vínculo com a pessoa e a
  mensagem de erro (que poderia ter trecho de conteúdo) é apagada.
- cópias de segurança: saem sozinhas em até 14 dias (servidor) e 30 dias (Drive).
- eventos que o Peraí criou na agenda Google da pessoa: são dela, ficam na agenda dela.
Mensagens são imutáveis (D-040) para edição, não para exclusão a pedido do dono.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("perai.exclusao")

SQL_ACHAR = "SELECT id, nome FROM usuarios WHERE telegram_chat_id = %s"
SQL_TABELAS = ("SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
               "AND column_name = 'usuario_id' ORDER BY table_name")
SQL_ANONIMIZAR = "UPDATE chamadas_modelo SET erro = NULL WHERE usuario_id = %s"
SQL_APAGAR = "DELETE FROM usuarios WHERE id = %s"


def contagens(cur, usuario_id: str) -> dict[str, int]:
    cur.execute(SQL_TABELAS)
    tabelas = [t for (t,) in cur.fetchall()]
    saida = {}
    for t in tabelas:
        # Nome de tabela vem do próprio catálogo do banco, não de quem chama.
        cur.execute(f'SELECT count(*) FROM "{t}" WHERE usuario_id = %s', (usuario_id,))
        saida[t] = cur.fetchone()[0]
    return saida


def excluir(conn, usuario_id: str, pasta_arquivos: Path, agenda=None) -> dict[str, int]:
    """Apaga tudo da pessoa. Devolve o que sobrou por tabela (tem que ser tudo zero, exceto
    chamadas_modelo, que fica sem vínculo)."""
    if agenda is not None:
        try:
            agenda.desconectar(usuario_id)   # revoga a autorização no Google
        except Exception as erro:            # segue mesmo assim: a linha some do banco abaixo
            log.warning("revogação do Google falhou: %s", erro)
    with conn.cursor() as cur:
        cur.execute(SQL_ANONIMIZAR, (usuario_id,))
        cur.execute(SQL_APAGAR, (usuario_id,))
        if cur.rowcount != 1:
            conn.rollback()
            raise RuntimeError("pessoa não encontrada no banco; nada foi apagado")
        restos = contagens(cur, usuario_id)
    if any(n for n in restos.values()):
        conn.rollback()
        raise RuntimeError(f"sobrou conteúdo; nada foi apagado: {restos}")
    conn.commit()
    pasta = pasta_arquivos / usuario_id
    if pasta.is_dir() and pasta.resolve().parent == pasta_arquivos.resolve():
        shutil.rmtree(pasta)
    return restos


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chat", type=int, required=True, help="id do chat do Telegram da pessoa")
    p.add_argument("--confirmar", help="nome exato da pessoa, como aparece na prévia")
    a = p.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL não definido.", file=sys.stderr)
        return 1
    import psycopg
    conn = psycopg.connect(url)
    with conn.cursor() as cur:
        cur.execute(SQL_ACHAR, (a.chat,))
        achado = cur.fetchone()
        if not achado:
            print(f"Nenhuma pessoa com o chat {a.chat}. Nada foi apagado.")
            return 1
        usuario_id, nome = str(achado[0]), achado[1]
        conta = contagens(cur, usuario_id)
    conn.rollback()

    print(f"Pessoa: {nome} (chat {a.chat})")
    for t, n in conta.items():
        if n:
            print(f"  {t}: {n}")
    pasta = Path(os.environ.get("PERAI_ARQUIVOS", "arquivos"))
    arquivos = list((pasta / usuario_id).glob("*")) if (pasta / usuario_id).is_dir() else []
    print(f"  arquivos: {len(arquivos)}")

    if a.confirmar is None:
        print(f'\nPrévia apenas. Para apagar de vez: --confirmar "{nome}"')
        return 0
    if a.confirmar != nome:
        print("O nome não confere. Nada foi apagado.")
        return 1

    from perai.agenda import agenda_do_ambiente
    from perai.armazenamento import RepositorioPostgres
    agenda = agenda_do_ambiente(RepositorioPostgres(url))
    agenda = agenda if agenda and agenda.disponivel else None
    excluir(conn, usuario_id, pasta, agenda)
    print(f"\n✅ {nome}: dados apagados do banco e dos arquivos; Google desconectado.")
    print("Falta: tirar o chat de PERAI_TELEGRAM_PERMITIDOS no .env e reiniciar o bot.")
    print("As cópias de segurança com esses dados saem sozinhas em até 14 dias (servidor) e 30 dias (Drive).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

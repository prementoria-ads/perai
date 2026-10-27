"""Roda o teste comparativo dos modelos (D-038).

Uso:
    python -m teste_comparativo.rodar                       # os 4 modelos da D-038
    python -m teste_comparativo.rodar --modelos haiku gpt-oss
    python -m teste_comparativo.rodar --simulado            # sem rede e sem custo

Entrada (fora do Git, na pasta dados/):
    dados/mensagens.csv   aba Mensagens da planilha, exportada em CSV
    dados/perguntas.csv   aba Perguntas, exportada em CSV
    dados/arquivos/       áudios, prints, fotos e .zip da pasta do Drive
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from perai.config import (LEITOR_IMAGEM_PADRAO, MODELOS, MODELOS_PADRAO, TRANSCRITOR_PADRAO,
                          TRANSCRITORES)
from perai.llm import Cliente
from teste_comparativo import relatorio
from teste_comparativo.dados import ler_mensagens, ler_perguntas
from teste_comparativo.executar import executar_modelo
from teste_comparativo.pontuacao import pontuar
from teste_comparativo.preparar import preparar


def carregar_env(caminho: Path) -> None:
    """Lê o .env sem sobrescrever variáveis já definidas. Nunca imprime valores."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Teste comparativo dos modelos do Peraí (D-038)")
    p.add_argument("--dados", type=Path, default=Path("dados"))
    p.add_argument("--saida", type=Path, default=Path("relatorios"))
    p.add_argument("--modelos", nargs="+", default=MODELOS_PADRAO, choices=sorted(MODELOS))
    p.add_argument("--transcritor", default=TRANSCRITOR_PADRAO, choices=sorted(TRANSCRITORES))
    p.add_argument("--leitor-imagem", default=LEITOR_IMAGEM_PADRAO, choices=sorted(MODELOS))
    p.add_argument("--hoje", help="data de referência das consultas (AAAA-MM-DD)")
    p.add_argument("--somente", type=int, help="usa só as N primeiras mensagens (ensaio barato)")
    p.add_argument("--simulado", action="store_true", help="tudo com modelo falso, sem rede")
    args = p.parse_args(argv)

    carregar_env(Path(".env"))
    if args.simulado:
        args.modelos, args.transcritor, args.leitor_imagem = ["simulado"], "simulado", "simulado"

    mensagens, avisos = ler_mensagens(args.dados / "mensagens.csv")
    perguntas, avisos_p = ler_perguntas(args.dados / "perguntas.csv")
    avisos += avisos_p
    if args.somente:
        mensagens = mensagens[: args.somente]
        numeros = {m.numero for m in mensagens}
        perguntas = [q for q in perguntas if not q.mensagens_fonte or set(q.mensagens_fonte) <= numeros]
    if not mensagens:
        print("Nenhuma mensagem encontrada em", args.dados / "mensagens.csv", file=sys.stderr)
        return 1

    cliente = Cliente()
    print(f"Preparando {len(mensagens)} mensagens...")
    entradas = preparar(
        mensagens, args.dados / "arquivos", cliente,
        TRANSCRITORES[args.transcritor], MODELOS[args.leitor_imagem],
        args.dados / "preparado" / f"{args.transcritor}+{args.leitor_imagem}.json",
    )
    avisos += [f"Mensagem {e.numero}: {e.aviso}" for e in entradas if e.aviso]
    hoje = args.hoje or max((m.data_situacao for m in mensagens if m.data_situacao), default=None)

    resultados, placares = [], []
    for apelido in args.modelos:
        print(f"Rodando {apelido}...")
        r = executar_modelo(cliente, MODELOS[apelido], entradas, perguntas, hoje)
        resultados.append(r)
        placares.append(pontuar(r, mensagens, perguntas, entradas))

    pasta = args.saida / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    resumo = relatorio.escrever(pasta, placares, resultados, mensagens, perguntas, entradas, avisos)
    print(f"\nRelatório: {resumo}")
    print(resumo.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

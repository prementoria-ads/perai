"""Migrações do banco, aplicadas em ordem na subida do bot.

`db/schema.sql` é a base (aplicada só na criação do banco). Cada arquivo NNN_nome.sql
desta pasta é idempotente e fica registrado na tabela `migracoes`.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("perai.migracoes")
PASTA = Path(__file__).parent


def arquivos() -> list[Path]:
    return sorted(PASTA.glob("[0-9][0-9][0-9]_*.sql"))


def aplicar(conn) -> list[str]:
    """Aplica as migrações pendentes. `conn` é uma conexão psycopg."""
    aplicadas = []
    with conn.cursor() as c:
        c.execute("CREATE TABLE IF NOT EXISTS migracoes ("
                  "nome text PRIMARY KEY, aplicada_em timestamptz NOT NULL DEFAULT now())")
        c.execute("SELECT nome FROM migracoes")
        feitas = {linha[0] for linha in c.fetchall()}
    conn.commit()
    for arquivo in arquivos():
        if arquivo.name in feitas:
            continue
        try:
            with conn.cursor() as c:
                c.execute(arquivo.read_text(encoding="utf-8"))
                c.execute("INSERT INTO migracoes (nome) VALUES (%s)", (arquivo.name,))
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception("migração %s falhou", arquivo.name)
            raise
        log.info("migração aplicada: %s", arquivo.name)
        aplicadas.append(arquivo.name)
    return aplicadas

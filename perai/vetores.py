"""Busca por significado (onda 2 do roteiro pós-25/11).

Cada registro ganha um vetor de significado (embedding), feito pelo modelo atrás da interface própria
(D-002, D-038). Na pergunta, os registros mais parecidos em significado entram junto com os que têm
palavras em comum ("carro" acha "Cobalt branco", "apto" acha "apartamento"). O vetor é derivado do
registro: some junto quando o registro é apagado (exclusão em cascata).

Guardado como float32 normalizado (bytes), sem extensão no banco: com alguns milhares de registros por
pessoa, o produto escalar em Python leva frações de segundo.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from array import array

DIMENSOES = 768
LOTE = 100               # textos por chamada ao modelo
NOVOS_POR_PERGUNTA = 400  # registros sem vetor tratados por pergunta (o resto fica para a próxima)
VIZINHOS = 40            # registros mais parecidos que entram na seleção


def modelo_vetor() -> str:
    return os.environ.get("PERAI_MODELO_VETOR", "gemini-embedding-001").strip() or "gemini-embedding-001"


def normalizar(valores) -> list[float]:
    n = math.sqrt(sum(v * v for v in valores)) or 1.0
    return [v / n for v in valores]


def empacotar(valores) -> bytes:
    return array("f", normalizar(valores)).tobytes()


def desempacotar(dados: bytes) -> array:
    a = array("f")
    a.frombytes(dados)
    return a


def similares(pergunta: list[float], vetores: dict[str, bytes], k: int = VIZINHOS,
              minimo: float = 0.0) -> list[tuple[str, float]]:
    q = normalizar(pergunta)
    notas = []
    for rid, dados in vetores.items():
        v = desempacotar(dados)
        if len(v) != len(q):
            continue
        s = sum(a * b for a, b in zip(q, v))
        if s > minimo:
            notas.append((rid, s))
    notas.sort(key=lambda x: -x[1])
    return notas[:k]


def texto_do_registro(r) -> str:
    entidades = ", ".join(getattr(r, "entidades", []) or [])
    return f"{r.conteudo} (dito por {r.dito_por}{'; ' + entidades if entidades else ''})"


def vetor_simulado(texto: str) -> list[float]:
    """Sem rede, para testes: soma de palavras (as 5 primeiras letras, sem acento) em posições fixas."""
    import unicodedata
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    v = [0.0] * DIMENSOES
    for p in re.findall(r"[a-z0-9]{3,}", t):
        h = int(hashlib.sha1(p[:5].encode()).hexdigest(), 16)
        v[h % DIMENSOES] += 1.0
    return v

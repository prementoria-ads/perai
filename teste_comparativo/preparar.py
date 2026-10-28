"""Etapa 1: transforma cada mensagem de teste em texto.

Áudios são transcritos, prints e fotos são lidos por um único modelo com visão,
e conversas exportadas do WhatsApp (.zip) viram texto com os áudios transcritos.
Tudo uma vez só: os modelos comparados recebem exatamente a mesma entrada.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from perai.config import Modelo, Transcritor
from perai.leitura import ler_conversa_exportada, ler_imagem
from perai.llm import Cliente
from teste_comparativo.dados import MensagemTeste



@dataclass
class Entrada:
    numero: str
    tipo: str
    texto: str
    data_situacao: str | None
    custo_usd: float = 0.0
    latencia_ms: int = 0
    aviso: str | None = None


def _achar_arquivo(pasta: Path, nome: str | None) -> Path | None:
    if not nome:
        return None
    caminho = pasta / nome
    if caminho.exists():
        return caminho
    # Tolera diferença de maiúsculas e extensão (.jpg x .jpeg).
    base = Path(nome).stem.lower()
    for candidato in pasta.iterdir():
        if candidato.stem.lower() == base:
            return candidato
    return None


def preparar(
    mensagens: list[MensagemTeste],
    pasta_arquivos: Path,
    cliente: Cliente,
    transcritor: Transcritor,
    leitor_imagem: Modelo,
    cache: Path,
) -> list[Entrada]:
    anteriores: dict[str, dict] = {}
    if cache.exists():
        anteriores = {e["numero"]: e for e in json.loads(cache.read_text(encoding="utf-8"))}

    entradas: list[Entrada] = []
    for m in mensagens:
        guardada = anteriores.get(m.numero)
        if guardada and not guardada.get("aviso"):
            entradas.append(Entrada(**guardada))
            continue

        arquivo = _achar_arquivo(pasta_arquivos, m.arquivo) if m.tipo != "texto" else None
        if m.tipo == "texto" or (not m.arquivo and m.texto):
            # Texto, ou áudio ditado cuja transcrição já está na planilha.
            entradas.append(Entrada(m.numero, m.tipo, m.texto or "", m.data_situacao))
        elif arquivo is None:
            # Sem o arquivo, usa o que você escreveu como conteúdo, se houver.
            aviso = f"arquivo {m.arquivo!r} não encontrado"
            entradas.append(Entrada(m.numero, m.tipo, m.texto or "", m.data_situacao, aviso=aviso))
        elif m.tipo == "audio":
            t = cliente.transcrever(transcritor, arquivo)
            entradas.append(Entrada(m.numero, m.tipo, t.texto, m.data_situacao, t.custo_usd, t.latencia_ms))
        elif m.tipo in ("print", "foto"):
            texto, custo, latencia = ler_imagem(cliente, leitor_imagem, arquivo, m.texto)
            entradas.append(Entrada(m.numero, m.tipo, texto, m.data_situacao, custo, latencia))
        elif m.tipo == "conversa_exportada":
            texto, custo, latencia = ler_conversa_exportada(
                cliente, transcritor, arquivo, cache.parent / "conversas" / m.numero
            )
            entradas.append(Entrada(m.numero, m.tipo, texto, m.data_situacao, custo, latencia))

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps([asdict(e) for e in entradas], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return entradas

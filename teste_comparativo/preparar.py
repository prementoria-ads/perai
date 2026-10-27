"""Etapa 1: transforma cada mensagem de teste em texto.

Áudios são transcritos, prints e fotos são lidos por um único modelo com visão,
e conversas exportadas do WhatsApp (.zip) viram texto com os áudios transcritos.
Tudo uma vez só: os modelos comparados recebem exatamente a mesma entrada.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from perai.config import Modelo, Transcritor
from perai.llm import Cliente
from teste_comparativo.dados import MensagemTeste

SISTEMA_LEITURA = """Transcreva fielmente o conteúdo desta imagem, em português.
Se for print de conversa: uma linha por fala, no formato "[data hora] Autor: texto",
usando "Eu" para as falas do dono do celular (balões do lado direito). Inclua mensagens
encaminhadas, nomes de arquivos e transcrições de áudio que aparecem na tela; marque
trechos cortados com "[...]" e áudios sem transcrição com "[áudio sem transcrição]".
Se for foto: descreva o que se vê, sem identificar pessoas pela aparência.
Não interprete nem resuma: só transcreva."""

EXTENSOES_AUDIO = {".ogg", ".opus", ".m4a", ".mp3", ".wav", ".aac"}


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


def _ler_conversa_exportada(cliente: Cliente, transcritor: Transcritor, zip_: Path,
                            destino: Path) -> tuple[str, float, int]:
    destino.mkdir(parents=True, exist_ok=True)
    custo, latencia, partes = 0.0, 0, []
    with zipfile.ZipFile(zip_) as z:
        for nome in z.namelist():
            # Só extrai arquivos planos, sem caminhos: proteção contra zip malicioso.
            if Path(nome).name != nome or nome.startswith("."):
                continue
            z.extract(nome, destino)
    for arquivo in sorted(destino.iterdir()):
        if arquivo.suffix.lower() == ".txt":
            partes.append(arquivo.read_text(encoding="utf-8", errors="replace"))
    for arquivo in sorted(destino.iterdir()):
        if arquivo.suffix.lower() in EXTENSOES_AUDIO:
            t = cliente.transcrever(transcritor, arquivo)
            custo += t.custo_usd
            latencia += t.latencia_ms
            partes.append(f"[áudio {arquivo.name}]: {t.texto}")
    return "\n".join(partes), custo, latencia


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
            legenda = f"Legenda do usuário: {m.texto or arquivo.stem}"
            r = cliente.conversar(
                leitor_imagem, "leitura_imagem", SISTEMA_LEITURA, legenda,
                imagem=arquivo, resposta_json=False,
            )
            texto = f"{legenda}\n{r.texto}"
            entradas.append(Entrada(m.numero, m.tipo, texto, m.data_situacao, r.custo_usd, r.latencia_ms))
        elif m.tipo == "conversa_exportada":
            texto, custo, latencia = _ler_conversa_exportada(
                cliente, transcritor, arquivo, cache.parent / "conversas" / m.numero
            )
            entradas.append(Entrada(m.numero, m.tipo, texto, m.data_situacao, custo, latencia))

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps([asdict(e) for e in entradas], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return entradas

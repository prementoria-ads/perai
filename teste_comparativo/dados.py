"""Leitura do conjunto de teste exportado da planilha (abas Mensagens e Perguntas)."""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TIPOS = {
    "texto": "texto",
    "audio": "audio",
    "print de conversa": "print",
    "foto": "foto",
    "conversa exportada": "conversa_exportada",
}


def normalizar(texto: str | None) -> str:
    """Minúsculas, sem acentos e sem pontuação: para comparar textos."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def data_iso(valor: str | None) -> str | None:
    if not valor:
        return None
    m = re.match(r"\s*'?(\d{1,2})/(\d{1,2})/(\d{4})", valor)
    if not m:
        return None
    dia, mes, ano = m.groups()
    return f"{ano}-{int(mes):02d}-{int(dia):02d}"


@dataclass
class MensagemTeste:
    numero: str
    tipo: str
    arquivo: str | None
    texto: str | None
    data_situacao: str | None
    pessoas_esperadas: str
    fatos_esperados: str
    natureza_esperada: str
    pendencia_esperada: str
    observacoes: str


@dataclass
class PerguntaTeste:
    numero: str
    tipo: str
    pergunta: str
    resposta_correta: str
    mensagens_fonte: list[str]
    observacoes: str


def _coluna(linha: dict, *inicios: str) -> str:
    """Acha a coluna pelo começo do nome, tolerando pequenas mudanças no cabeçalho."""
    for chave, valor in linha.items():
        if chave is None:
            continue
        nome = normalizar(chave)
        if any(nome.startswith(normalizar(i)) for i in inicios):
            return (valor or "").strip()
    return ""


def _preenchido(valor: str) -> bool:
    return bool(valor) and not normalizar(valor).startswith("preencher")


def ler_mensagens(caminho: Path) -> tuple[list[MensagemTeste], list[str]]:
    mensagens, avisos = [], []
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        for linha in csv.DictReader(f):
            numero = _coluna(linha, "Nº")
            if not numero or not numero.isdigit():
                continue  # cabeçalho repetido, linha de exemplo ou vazia
            tipo_bruto = _coluna(linha, "Tipo")
            if not tipo_bruto:
                continue
            tipo = TIPOS.get(normalizar(tipo_bruto))
            if tipo is None:
                avisos.append(f"Mensagem {numero}: tipo desconhecido {tipo_bruto!r}, ignorada.")
                continue
            texto = _coluna(linha, "Texto")
            arquivo = _coluna(linha, "Arquivo")
            if tipo == "texto" and not _preenchido(texto):
                avisos.append(f"Mensagem {numero}: texto vazio, ignorada.")
                continue
            mensagens.append(
                MensagemTeste(
                    numero=numero,
                    tipo=tipo,
                    arquivo=arquivo or None,
                    texto=texto if _preenchido(texto) else None,
                    data_situacao=data_iso(_coluna(linha, "Data")),
                    pessoas_esperadas=_coluna(linha, "Pessoas"),
                    fatos_esperados=_coluna(linha, "Fatos"),
                    natureza_esperada=_coluna(linha, "Natureza"),
                    pendencia_esperada=_coluna(linha, "Pendencia"),
                    observacoes=_coluna(linha, "Observacoes", "O que este"),
                )
            )
    return mensagens, avisos


def ler_perguntas(caminho: Path) -> tuple[list[PerguntaTeste], list[str]]:
    perguntas, avisos = [], []
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        for linha in csv.DictReader(f):
            numero = _coluna(linha, "Nº")
            pergunta = _coluna(linha, "Pergunta")
            if not numero or not numero.isdigit() or not pergunta:
                continue
            resposta = _coluna(linha, "Resposta")
            if not resposta:
                avisos.append(f"Pergunta {numero}: sem resposta correta, ignorada.")
                continue
            fontes = re.findall(r"\d+", _coluna(linha, "Mensagens"))
            perguntas.append(
                PerguntaTeste(
                    numero=numero,
                    tipo=normalizar(_coluna(linha, "Tipo")) or "consulta",
                    pergunta=pergunta,
                    resposta_correta=resposta,
                    mensagens_fonte=fontes,
                    observacoes=_coluna(linha, "Observacoes", "O que este"),
                )
            )
    return perguntas, avisos

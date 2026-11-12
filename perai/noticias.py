"""Notícias por segmento para o radar de risco (D-105).

O Peraí lê o RSS público do Google Notícias (últimos 7 dias) com os termos de cada segmento dos clientes.
A IA só aponta quais manchetes podem fazer clientes sumirem ou crescerem; quem decide registrar é o
usuário, com um toque. A notícia confirmada vira mensagem dele e fonte do fator (D-040), sempre como
"segundo <veículo>" (D-044).
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from perai.extracao import extrair_json

TERMOS = {
    "apostas": "apostas bets regulamentação", "varejo": "varejo vendas consumo",
    "prefeitura": "prefeituras municípios orçamento", "legislativo": "câmaras municipais legislativo",
    "governo_estadual": "governo estadual orçamento investimentos", "governo_federal": "governo federal orçamento corte",
    "seguranca_publica": "segurança pública videomonitoramento", "saude": "saúde pública hospitais orçamento",
    "educacao": "educação escolas orçamento", "industria": "indústria produção investimentos",
    "energia": "energia solar geração distribuída", "telecom": "telecomunicações provedores internet",
    "financeiro": "bancos cooperativas de crédito", "agro": "agronegócio cooperativas safra",
    "logistica": "logística transporte cargas", "tecnologia": "tecnologia cibersegurança empresas",
    "utilities": "saneamento concessionárias", "outro": "",
}
POR_SEGMENTO = 8      # manchetes por segmento
MAX_SEGMENTOS = 6


def url(segmento: str) -> str | None:
    termos = TERMOS.get(segmento)
    if not termos:
        return None
    q = urllib.parse.quote(f"{termos} when:7d")
    return f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


def ler_rss(conteudo: bytes, limite: int = POR_SEGMENTO) -> list[dict]:
    """Itens do RSS: título, veículo, link e data (AAAA-MM-DD). XML inválido → lista vazia."""
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError:
        return []
    itens = []
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        fonte = (it.findtext("source") or "").strip()
        if not titulo or not link.startswith("http"):
            continue
        if fonte and titulo.endswith(f" - {fonte}"):
            titulo = titulo[: -len(fonte) - 3].strip()
        try:
            data = parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            data = None
        itens.append({"titulo": titulo[:300], "fonte": fonte[:80] or "veículo não informado", "link": link[:600],
                      "data": data})
        if len(itens) >= limite:
            break
    return itens


SISTEMA_NOTICIAS = """Você recebe manchetes recentes [N1], [N2]... agrupadas pelo segmento de clientes de uma
empresa de tecnologia que vende para governo e empresas. Escolha só as que indicam um FATOR que pode fazer
clientes desse segmento sumirem, cortarem gastos ou crescerem e comprarem mais (lei, decisão judicial,
proibição, corte ou aumento de orçamento, crise, tendência forte de consumo). Ignore notícia de uma empresa
só, promoção, opinião solta e repetição do mesmo fato. No máximo 5. Devolva só JSON:

{"fatores": [{"noticia": "N3", "titulo": "<o fator em até 12 palavras>", "direcao": "ameaca"|"oportunidade",
  "segmentos": ["<segmento da lista>"], "intensidade": "baixa"|"media"|"alta",
  "situacao": "observacao"|"em_vigor", "por_que": "<uma frase: o que muda para esses clientes>"}]}

Use só o que a manchete diz; nada de números ou fatos que não estejam nela."""


def montar_pedido(por_segmento: dict[str, list[dict]]) -> tuple[str, dict[str, dict]]:
    codigos, linhas, n = {}, [], 0
    for seg, itens in por_segmento.items():
        linhas.append(f"\nSegmento: {seg}")
        for it in itens:
            n += 1
            codigos[f"N{n}"] = it | {"segmento": seg}
            linhas.append(f"[N{n}] {it['titulo']} ({it['fonte']}, {it['data'] or 'sem data'})")
    return "\n".join(linhas).strip(), codigos


def interpretar(resposta: str, codigos: dict[str, dict], segmentos_validos: set[str]) -> list[dict]:
    try:
        dados = extrair_json(resposta)
    except (ValueError, TypeError):
        return []
    saida, vistos = [], set()
    for f in (dados.get("fatores") or []) if isinstance(dados, dict) else []:
        if not isinstance(f, dict):
            continue
        cod = str(f.get("noticia") or "").strip("[] ").upper()
        noticia = codigos.get(cod)
        titulo = str(f.get("titulo") or "").strip()[:200]
        if noticia is None or not titulo or noticia["link"] in vistos:
            continue
        vistos.add(noticia["link"])
        segs = sorted({s for s in (f.get("segmentos") or []) if s in segmentos_validos} or {noticia["segmento"]})
        saida.append({"titulo": titulo, "direcao": "oportunidade" if f.get("direcao") == "oportunidade" else "ameaca",
                      "segmentos": segs,
                      "intensidade": f.get("intensidade") if f.get("intensidade") in ("baixa", "media", "alta") else "media",
                      "situacao": "em_vigor" if f.get("situacao") == "em_vigor" else "observacao",
                      "por_que": str(f.get("por_que") or "").strip()[:300], "noticia": noticia})
    return saida[:5]


def chave_link(link: str) -> str:
    return re.sub(r"[?#].*$", "", link)[-200:]

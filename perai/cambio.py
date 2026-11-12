"""Contraponto de câmbio nas propostas (D-107).

A PTAX de venda vem do serviço público do Banco Central (Olinda/PTAX). O código compara com o dólar
declarado no pricing; a IA não entra. A PTAX é referência do dia, não a taxa que a empresa contrata.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from perai.despesas import brl

URL = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo("
       "dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)?@dataInicial='{ini}'&@dataFinalCotacao='{fim}'"
       "&$top=20&$orderby=dataHoraCotacao%20desc&$format=json&$select=cotacaoVenda,dataHoraCotacao")
FONTE = "Banco Central (PTAX de venda)"


def url(hoje: date) -> str:
    """Últimos 10 dias: sempre pega o último dia útil, mesmo em feriado ou fim de semana."""
    f = lambda d: d.strftime("%m-%d-%Y")
    return URL.format(ini=f(hoje - timedelta(days=10)), fim=f(hoje))


def ler(conteudo: bytes) -> dict | None:
    """{"valor": "5.4886", "data": "2026-11-05"} da resposta do BCB, ou None."""
    try:
        itens = json.loads(conteudo).get("value") or []
    except (ValueError, AttributeError):
        return None
    validos = [i for i in itens if i.get("cotacaoVenda") and i.get("dataHoraCotacao")]
    if not validos:
        return None
    ultimo = max(validos, key=lambda i: i["dataHoraCotacao"])
    return {"valor": str(Decimal(str(ultimo["cotacaoVenda"]))), "data": ultimo["dataHoraCotacao"][:10]}


def _pct(a: Decimal, b: Decimal) -> Decimal:
    return ((a / b - 1) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _r(v: Decimal) -> str:
    return "R$ " + f"{v:.2f}".replace(".", ",")


def _fp(v: Decimal) -> str:
    return f"{v:+}%".replace(".", ",")


def comparar(dolar_pricing: Decimal, ptax: dict, custo_centavos: int | None, lucro_centavos: int | None) -> dict:
    """Diferença entre o dólar do pricing e a PTAX; o teto de efeito supõe todo o custo em dólar."""
    p = Decimal(ptax["valor"])
    desfavoravel = p > dolar_pricing
    teto = None
    if custo_centavos:
        teto = int((Decimal(custo_centavos) * (p / dolar_pricing - 1)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return {"dolar": dolar_pricing, "ptax": p, "data": ptax["data"], "dif_pct": _pct(p, dolar_pricing),
            "desfavoravel": desfavoravel, "teto_centavos": teto, "lucro_centavos": lucro_centavos}


def texto(c: dict, numero: str | None = None) -> str:
    dia = f"{c['data'][8:10]}/{c['data'][5:7]}"
    alvo = f"O pricing{' ' + numero if numero else ''} usa US$ 1 = {_r(c['dolar'])}; a PTAX de {dia} é {_r(c['ptax'])}"
    if c["desfavoravel"]:
        linhas = [f"💱 Ô, peraí! {alvo} ({_fp(c['dif_pct'])}). O custo em dólar já está acima do previsto."]
        if c["teto_centavos"] is not None:
            linhas.append(f"   Se todo o custo for em dólar, o lucro previsto cai até {brl(c['teto_centavos'])}"
                          + (f" (de {brl(c['lucro_centavos'])} para {brl(c['lucro_centavos'] - c['teto_centavos'])})"
                             if c.get("lucro_centavos") is not None else "") + ".")
        linhas.append("   Vale atualizar o câmbio do pricing ou incluir cláusula de reajuste antes de fechar.")
        return "\n".join(linhas)
    folga = _pct(c["dolar"], c["ptax"])
    return (f"💱 Câmbio: {alvo}. Há folga: o pricing aguenta o dólar subir até {_r(c['dolar'])} "
            f"({_fp(folga)} sobre a PTAX) antes de comer a margem.")


def dolar_do_pricing(d: dict) -> Decimal | None:
    from perai.propostas import num
    v = num(d.get("moeda_dolar")) if isinstance(d, dict) else None
    return v if v is not None and Decimal("1") < v < Decimal("50") else None

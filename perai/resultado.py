"""Resultado por projeto e por conta (D-093), calculado pelo código em centavos.

Projeto = oportunidade ganha. Resultado do projeto = lucro bruto previsto (pricings ligados) − comissões
do projeto (cada beneficiário, sobre a base líquida = preço de venda − impostos do pricing, nunca o
faturamento bruto) − despesas ligadas ao projeto. Resultado da conta = soma dos projetos ganhos −
despesas da conta fora de projeto. O que faltar fica "não informado" e o resultado não é fechado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from perai.comissao import fp
from perai.despesas import brl


def _pct(v: int, pct: float) -> int:
    return int((Decimal(v) * Decimal(str(pct)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _b(v: int | None) -> str:
    return brl(v) if v is not None else "não informado"


@dataclass
class Projeto:
    op: dict
    venda: int | None = None
    impostos: int | None = None
    lucro: int | None = None
    base_liquida: int | None = None
    comissoes: list[tuple[dict, int | None]] = field(default_factory=list)
    despesas: int = 0
    despesas_sem_valor: int = 0
    resultado: int | None = None
    faltando: list[str] = field(default_factory=list)
    faturado: int = 0
    recebido: int = 0
    vencidas: list[dict] = field(default_factory=list)
    comissoes_recebido: list[tuple[dict, int | None]] = field(default_factory=list)

    @property
    def a_receber(self) -> int:
        return self.faturado - self.recebido


def saldo_fatura(f: dict) -> int:
    return f["valor_centavos"] - sum(r["valor_centavos"] for r in f.get("recebimentos") or [])


def projeto(op: dict, propostas: list[dict], despesas: list[dict], comissoes: list[dict],
            faturas: list[dict] | None = None, hoje: str | None = None) -> Projeto:
    p = Projeto(op, venda=op.get("valor_centavos"))
    for f in faturas or []:
        p.faturado += f["valor_centavos"]
        p.recebido += f["valor_centavos"] - saldo_fatura(f)
        if hoje and f.get("vencimento") and f["vencimento"] < hoje and saldo_fatura(f) > 0:
            p.vencidas.append(f)
    pricings = [x for x in propostas if x["tipo"] == "pricing"]
    if pricings:
        vals = lambda k: [x.get(k) for x in pricings]
        if None not in vals("impostos_centavos") and None not in vals("valor_centavos"):
            p.impostos = sum(vals("impostos_centavos"))
            p.base_liquida = sum(vals("valor_centavos")) - p.impostos
        if None not in vals("lucro_centavos"):
            p.lucro = sum(vals("lucro_centavos"))
    else:
        p.faltando.append("pricing (custo, impostos e lucro previsto)")
    for c in comissoes:
        base = p.base_liquida if c["base"] == "liquido" else p.lucro
        p.comissoes.append((c, _pct(base, c["percentual"]) if base is not None else None))
    for d in despesas:
        if d.get("valor_centavos") is None:
            p.despesas_sem_valor += 1
        else:
            p.despesas += d["valor_centavos"]
    # Sobre o recebido: base líquida na proporção do que entrou (estimativa; não confirma comissão).
    if p.recebido and p.venda:
        for c, v in p.comissoes:
            p.comissoes_recebido.append((c, int((Decimal(v) * p.recebido / p.venda).quantize(
                Decimal(1), rounding=ROUND_HALF_UP)) if v is not None else None))
    if p.lucro is not None and all(v is not None for _, v in p.comissoes):
        p.resultado = p.lucro - sum(v for _, v in p.comissoes) - p.despesas
    elif p.lucro is not None:
        p.faltando.append("base das comissões (impostos do pricing)")
    return p


def texto_projeto(p: Projeto) -> str:
    op = p.op
    linhas = [f"📦 {op['titulo']} · {op.get('conta') or 'sem conta'} · "
              + {"ganha": "projeto (ganha)", "perdida": "perdida"}.get(op["etapa"], f"em {op['etapa']}")]
    linhas.append(f"   Venda {_b(p.venda)} · base líquida {_b(p.base_liquida)} · lucro bruto previsto {_b(p.lucro)}")
    for c, v in p.comissoes:
        quem = "você" if c["eu"] else c["beneficiario"]
        linhas.append(f"   − Comissão {quem} ({fp(c['percentual'])}% sobre {'base líquida' if c['base'] == 'liquido' else 'lucro'}): {_b(v)}")
    if p.despesas or p.despesas_sem_valor:
        linhas.append(f"   − Despesas do projeto: {brl(p.despesas)}"
                      + (f" (+{p.despesas_sem_valor} sem valor)" if p.despesas_sem_valor else ""))
    linhas.append(f"   = Resultado previsto: {_b(p.resultado)}"
                  + (f" (falta: {', '.join(p.faltando)})" if p.faltando and p.resultado is None else ""))
    if p.faturado:
        pct = f" ({Decimal(p.recebido * 100) / p.venda:.0f}% da venda)" if p.venda else ""
        linhas.append(f"   🧾 Faturado {brl(p.faturado)} · recebido {brl(p.recebido)}{pct} · a receber {brl(p.a_receber)}")
        for f in p.vencidas:
            linhas.append(f"   ⚠️ Vencida: NF {f.get('numero') or 's/n'} em {f['vencimento'][8:10]}/{f['vencimento'][5:7]}"
                          f" · saldo {brl(saldo_fatura(f))}")
        for c, v in p.comissoes_recebido:
            quem = "você" if c["eu"] else c["beneficiario"]
            linhas.append(f"   💼 Comissão {quem} sobre o recebido (estimativa, não confirmada): {_b(v)}")
    return "\n".join(linhas)


@dataclass
class Conta:
    nome: str
    faturamento: int = 0
    faturado: int = 0
    recebido: int = 0
    resultado_projetos: int | None = 0
    despesas_fora: int = 0
    em_aberto: int = 0
    projetos: list[Projeto] = field(default_factory=list)

    @property
    def saldo(self) -> int | None:
        return None if self.resultado_projetos is None else self.resultado_projetos - self.despesas_fora


def conta(nome: str, projetos: list[Projeto], despesas_fora: int) -> Conta:
    c = Conta(nome, despesas_fora=despesas_fora, projetos=projetos)
    for p in projetos:
        c.faturado += p.faturado
        c.recebido += p.recebido
        if p.op["etapa"] == "ganha":
            c.faturamento += p.venda or 0
            if p.resultado is None:
                c.resultado_projetos = None
            elif c.resultado_projetos is not None:
                c.resultado_projetos += p.resultado
        elif p.op["etapa"] != "perdida":
            c.em_aberto += p.venda or 0
    return c


def texto_conta(c: Conta) -> str:
    return (f"🏢 {c.nome}: faturamento de projetos ganhos {brl(c.faturamento)} · resultado dos projetos "
            f"{_b(c.resultado_projetos)} · despesas da conta fora de projeto {brl(c.despesas_fora)} · "
            f"saldo {_b(c.saldo)}" + (f" · em negociação {brl(c.em_aberto)}" if c.em_aberto else "")
            + (f" · faturado {brl(c.faturado)}, recebido {brl(c.recebido)}" if c.faturado else ""))


VALOR_PERGUNTA = re.compile(r"(?:r\$\s*)?(\d+(?:[.,]\d+)?)\s*(mil|k|milh[aã]o|milh[oõ]es|mi)\b|r\$\s*(\d[\d.]*(?:,\d{2})?)", re.I)


def valor_da_pergunta(pergunta: str) -> int | None:
    """"35k", "35 mil", "R$ 35.000,00", "1,2 milhão" → centavos."""
    m = VALOR_PERGUNTA.search(pergunta or "")
    if not m:
        return None
    if m.group(3):
        t = m.group(3).replace(".", "").replace(",", ".")
        return int(Decimal(t) * 100)
    n = Decimal(m.group(1).replace(",", "."))
    mult = 1_000 if m.group(2).lower() in ("mil", "k") else 1_000_000
    return int(n * mult * 100)


def peso_investimento(valor: int, c: Conta) -> str:
    if c.saldo is None or c.saldo <= 0:
        return (f"Investimento de {brl(valor)}: a conta {c.nome} está com saldo {_b(c.saldo)}; "
                "não há saldo positivo calculado para cobri-lo.")
    return (f"Investimento de {brl(valor)} = {Decimal(valor * 100) / c.saldo:.1f}% do saldo da conta {c.nome} "
            f"({brl(c.saldo)}); saldo depois do investimento: {brl(c.saldo - valor)}.")

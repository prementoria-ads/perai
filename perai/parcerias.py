"""Parcerias e contratos (D-092): o documento vira contrato com composição, parcelas e regra de
indicação. A IA só lê; o código confere as somas e calcula os vencimentos a partir da assinatura."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from perai.despesas import brl
from perai.extracao import extrair_json
from perai.propostas import centavos, num

PARECE_PARCERIA = re.compile(r"hub de neg|termo de ades|ades[aã]o ao|parceria|indica[cç][aã]o comercial|"
                             r"remunera[cç][aã]o por indica|acordo de coopera", re.I)

SISTEMA_PARCERIA = """Você lê um contrato, termo de adesão ou proposta de parceria entre uma empresa e um
parceiro (ex.: adesão a um hub de negócios, acordo de indicação comercial). Devolva só um objeto JSON com
os números EXATAMENTE como estão no documento. Nada inventado; o que não estiver fica null.

{"parceiro": str, "empresa_l8": str|null, "titulo": str, "numero": str|null, "data": "AAAA-MM-DD"|null,
 "vigencia_ate": "AAAA-MM-DD"|null, "valor_global": str|null,
 "composicao": [{"descricao": str, "tipo": "pagar" | "compensar" | "receber", "valor": str,
                 "parcelas": número|null, "valor_parcela": str|null,
                 "dias_apos_assinatura": número|null, "dia_do_mes": número|null, "regra": str|null}],
 "indicacao": {"percentual": número|null, "base": str|null, "prazo_dias": número|null} | null}

Regras:
1. "empresa_l8": a empresa (razão social) que adere ou contrata, como escrita.
2. "composicao": cada parte do valor global. Dinheiro que a empresa paga ao parceiro: "pagar";
   contraprestação em serviço compensada por encontro de contas: "compensar"; o que a empresa recebe:
   "receber". Parcelamento: "parcelas" e "valor_parcela"; prazo contado da assinatura em
   "dias_apos_assinatura"; vencimento num dia fixo do mês em "dia_do_mes"; condição em "regra".
3. "vigencia_ate": a data final de validade da proposta ou do contrato, se escrita.
4. "indicacao": a remuneração por indicação (percentual, sobre o que incide e prazo de pagamento).
5. Não some nem corrija nada: a conferência é feita depois, pelo código."""


def interpretar(resposta: str) -> dict | None:
    try:
        d = extrair_json(resposta)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not str(d.get("parceiro") or "").strip():
        return None
    d["composicao"] = [c for c in (d.get("composicao") or []) if isinstance(c, dict) and centavos(c.get("valor"))]
    return d


def conferir(d: dict) -> list[str]:
    avisos = []
    total = centavos(d.get("valor_global"))
    partes = [centavos(c.get("valor")) for c in d["composicao"]]
    if total is not None and partes and sum(partes) != total:
        avisos.append(f"As partes somam {brl(sum(partes))}, mas o valor global é {brl(total)}.")
    for c in d["composicao"]:
        n, vp, v = c.get("parcelas"), centavos(c.get("valor_parcela")), centavos(c.get("valor"))
        if n and vp is not None and v is not None and int(n) * vp != v:
            avisos.append(f"{c['descricao'][:50]}: {int(n)} × {brl(vp)} = {brl(int(n) * vp)}, mas o texto diz {brl(v)}.")
    pagar = sum(centavos(c.get("valor")) or 0 for c in d["composicao"] if c.get("tipo") == "pagar")
    comp = sum(centavos(c.get("valor")) or 0 for c in d["composicao"] if c.get("tipo") == "compensar")
    if comp:
        d["saldo_em_dinheiro"] = pagar
    return avisos


def _dia_do_mes(apos: date, dia: int) -> date:
    """Primeiro dia `dia` de mês estritamente depois de `apos` (respeitando meses curtos)."""
    ano, mes = apos.year, apos.month
    while True:
        ultimo = calendar.monthrange(ano, mes)[1]
        candidato = date(ano, mes, min(dia, ultimo))
        if candidato > apos:
            return candidato
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def gerar_parcelas(d: dict, assinatura: str | None) -> list[dict]:
    """Uma linha por pagamento; vencimento só quando a assinatura é conhecida (senão fica a regra).
    Os pagamentos ("pagar") são numerados 1, 2, 3... ("paguei a parcela 3"); compensações e valores a
    receber vêm depois, a partir de 101."""
    assin = date.fromisoformat(assinatura) if assinatura else None
    saida, ultimo = [], assin
    contador = {"pagar": 0, "outro": 100}
    for c in d["composicao"]:
        qtd = int(c.get("parcelas") or 1)
        vp = centavos(c.get("valor_parcela")) if qtd > 1 else centavos(c.get("valor"))
        for k in range(1, qtd + 1):
            grupo = "pagar" if c.get("tipo", "pagar") == "pagar" else "outro"
            contador[grupo] += 1
            n = contador[grupo]
            venc = None
            if assin and c.get("dias_apos_assinatura") is not None:
                venc = assin + timedelta(days=int(c["dias_apos_assinatura"]))
            elif assin and c.get("dia_do_mes"):
                venc = _dia_do_mes(ultimo or assin, int(c["dia_do_mes"]))
            if venc:
                ultimo = max(ultimo or venc, venc)
            regra = c.get("regra") or (f"{c['dias_apos_assinatura']} dias após a assinatura"
                                       if c.get("dias_apos_assinatura") is not None else
                                       f"dia {c['dia_do_mes']} de cada mês" if c.get("dia_do_mes") else None)
            saida.append({"n": n, "descricao": c["descricao"][:120] + (f" ({k}/{qtd})" if qtd > 1 else ""),
                          "tipo": c.get("tipo") if c.get("tipo") in ("pagar", "compensar", "receber") else "pagar",
                          "valor_centavos": vp, "regra": regra,
                          "vencimento": venc.isoformat() if venc else None})
    return sorted(saida, key=lambda p: p["n"])


def _br(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "sem data"


def resumo(k: dict) -> str:
    linhas = [f"🤝 {k['titulo']} · {k.get('conta') or 'parceiro'}"
              + (f" · nº {k['numero']}" if k.get("numero") else "")
              + (f" · {k['empresa_l8']}" if k.get("empresa_l8") else "")]
    if k.get("valor_centavos") is not None:
        linhas.append(f"   Valor global: {brl(k['valor_centavos'])}"
                      + (f" · em dinheiro: {brl(k['dados']['saldo_em_dinheiro'])}" if k["dados"].get("saldo_em_dinheiro") else ""))
    if k.get("vigencia_ate") and not k.get("assinatura"):
        linhas.append(f"   ⏳ Proposta válida até {_br(k['vigencia_ate'])}")
    if k.get("assinatura"):
        linhas.append(f"   ✍️ Assinado em {_br(k['assinatura'])}")
    parcelas = k.get("parcelas") or []
    pagar = [p for p in parcelas if p["tipo"] == "pagar"]
    if pagar:
        pagas = [p for p in pagar if p["situacao"] == "paga"]
        aberto = sum(p["valor_centavos"] or 0 for p in pagar if p["situacao"] == "aberta")
        prox = next((p for p in pagar if p["situacao"] == "aberta"), None)
        linhas.append(f"   Pagamentos: {len(pagas)} de {len(pagar)} pagos · em aberto {brl(aberto)}"
                      + (f" · próximo: {brl(prox['valor_centavos'])} em {_br(prox['vencimento'])}"
                         if prox and prox.get("vencimento") else
                         f" · próximo: {brl(prox['valor_centavos'])} ({prox['regra'] or 'sem data'})" if prox else ""))
    for p in parcelas:
        if p["tipo"] == "compensar":
            linhas.append(f"   🔁 Compensação: {p['descricao']} · {brl(p['valor_centavos'])}"
                          + (f" · {p['regra']}" if p.get("regra") else ""))
    if k.get("percentual_indicacao") is not None:
        ind = k["dados"].get("indicacao") or {}
        linhas.append(f"   🎯 Indicação: {k['percentual_indicacao']:g}% sobre {ind.get('base') or 'o valor recebido'}"
                      + (f", pago em até {ind['prazo_dias']} dias" if ind.get("prazo_dias") else ""))
    if k.get("conferencia"):
        linhas.append("   🔍 " + " ".join(k["conferencia"]))
    return "\n".join(linhas)


def pct_indicacao(d: dict) -> float | None:
    ind = d.get("indicacao") or {}
    v = num(ind.get("percentual"))
    return float(v) if v is not None else None

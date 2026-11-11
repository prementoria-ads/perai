"""Comissão de indicação do fundador (D-092). Estimativa feita pelo código, nunca pelo modelo.

Cadeia de um negócio indicado a um parceiro (ex.: Itaipu Parquetec):
  base     = valor líquido que o parceiro efetivamente recebeu do cliente
  L8 bruto = base × percentual do contrato (5%)
  L8 líq.  = L8 bruto − impostos da L8 sobre essa receita (percentual configurável)
  minha    = L8 líquido × minha participação (faixa de 5% a 10% até ser definida)
  impostos = minha × alíquotas da minha empresa (lucro presumido: PIS, COFINS, IRPJ e CSLL sobre a
             presunção de serviços, e ISS do município)
  líquido  = minha − impostos

Tudo em centavos, arredondando meio para cima. Percentual não informado aparece como não informado e
a conta para nesse ponto: nada vira zero sem ninguém ter dito. Não é orientação contábil.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from perai.despesas import brl

# Lucro presumido, prestação de serviços em geral (presunção de 32% para IRPJ e CSLL). Valores usuais,
# para conferir com o contador; ISS depende do município e começa como não informado.
PRESUMIDO = {"pis": Decimal("0.65"), "cofins": Decimal("3.00"), "irpj": Decimal("4.80"),
             "csll": Decimal("2.88"), "iss": None}
ROTULO = {"pis": "PIS", "cofins": "COFINS", "irpj": "IRPJ (15% de 32%)", "csll": "CSLL (9% de 32%)",
          "iss": "ISS"}
LIMITE_ADICIONAL_IRPJ = 6_250_000   # receita mensal (centavos) cuja presunção (32%) passa de R$ 20 mil


@dataclass
class Config:
    percentual_parceiro: Decimal = Decimal("5")          # o que o parceiro paga à L8
    desconto_l8: Decimal | None = None                    # impostos da L8 sobre a receita de indicação
    minha_min: Decimal = Decimal("5")
    minha_max: Decimal = Decimal("10")
    aliquotas: dict = field(default_factory=lambda: dict(PRESUMIDO))

    @classmethod
    def de_dict(cls, d: dict | None) -> "Config":
        c = cls()
        d = d or {}
        for k in ("percentual_parceiro", "desconto_l8", "minha_min", "minha_max"):
            if k in d:
                setattr(c, k, Decimal(str(d[k])) if d[k] is not None else None)
        for k, v in (d.get("aliquotas") or {}).items():
            if k in c.aliquotas:
                c.aliquotas[k] = Decimal(str(v)) if v is not None else None
        return c

    def para_dict(self) -> dict:
        f = lambda v: str(v) if v is not None else None
        return {"percentual_parceiro": f(self.percentual_parceiro), "desconto_l8": f(self.desconto_l8),
                "minha_min": f(self.minha_min), "minha_max": f(self.minha_max),
                "aliquotas": {k: f(v) for k, v in self.aliquotas.items()}}


def fp(v) -> str:
    """Percentual sem zeros sobrando: 10.0 → "10", 14.250 → "14,25"."""
    t = format(Decimal(str(v)), "f")
    t = t.rstrip("0").rstrip(".") if "." in t else t
    return t.replace(".", ",")


def _pct(valor: int, pct: Decimal) -> int:
    return int((Decimal(valor) * pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass
class Calculo:
    base: int
    l8_bruto: int
    l8_liquido: int | None
    minha_min: int | None
    minha_max: int | None
    impostos_pct: Decimal | None
    liquido_min: int | None
    liquido_max: int | None
    faltando: list[str]


def calcular(base_centavos: int, cfg: Config, percentual_parceiro: Decimal | None = None) -> Calculo:
    faltando = []
    pct = percentual_parceiro if percentual_parceiro is not None else cfg.percentual_parceiro
    l8_bruto = _pct(base_centavos, pct)
    l8_liq = None
    if cfg.desconto_l8 is None:
        faltando.append("impostos da L8 sobre a receita de indicação (/impostos l8 <percentual>)")
    else:
        l8_liq = l8_bruto - _pct(l8_bruto, cfg.desconto_l8)
    mmin = _pct(l8_liq, cfg.minha_min) if l8_liq is not None else None
    mmax = _pct(l8_liq, cfg.minha_max) if l8_liq is not None else None
    sem = [ROTULO[k] for k, v in cfg.aliquotas.items() if v is None]
    if sem:
        faltando.append("alíquota de " + ", ".join(sem) + " da sua empresa (/impostos iss <percentual>)")
        imp = None
    else:
        imp = sum(cfg.aliquotas.values(), Decimal(0))
    lmin = mmin - _pct(mmin, imp) if mmin is not None and imp is not None else None
    lmax = mmax - _pct(mmax, imp) if mmax is not None and imp is not None else None
    return Calculo(base_centavos, l8_bruto, l8_liq, mmin, mmax, imp, lmin, lmax, faltando)


def _faixa(a: int | None, b: int | None) -> str:
    if a is None or b is None:
        return "não informado"
    return brl(a) if a == b else f"{brl(a)} a {brl(b)}"


def texto(c: Calculo, cfg: Config, pct_parceiro: Decimal | None = None) -> str:
    pct = pct_parceiro if pct_parceiro is not None else cfg.percentual_parceiro
    linhas = [f"   Base (líquido recebido pelo parceiro): {brl(c.base)}",
              f"   L8 recebe {fp(pct)}%: {brl(c.l8_bruto)}"]
    if c.l8_liquido is not None:
        linhas.append(f"   Menos impostos da L8 ({fp(cfg.desconto_l8)}%): {brl(c.l8_liquido)}")
    faixa = f"{fp(cfg.minha_min)}%" if cfg.minha_min == cfg.minha_max else f"{fp(cfg.minha_min)}% a {fp(cfg.minha_max)}%"
    linhas.append(f"   Sua parte ({faixa}): {_faixa(c.minha_min, c.minha_max)}")
    if c.impostos_pct is not None:
        linhas.append(f"   Seus impostos (lucro presumido, {fp(c.impostos_pct)}%): "
                      f"{_faixa(c.minha_min - c.liquido_min if c.liquido_min is not None else None, c.minha_max - c.liquido_max if c.liquido_max is not None else None)}")
        linhas.append(f"   💰 Você recebe líquido: {_faixa(c.liquido_min, c.liquido_max)}")
    if c.minha_max is not None and c.minha_max > LIMITE_ADICIONAL_IRPJ:
        linhas.append("   ⚠️ Acima de R$ 62.500 de receita no mês incide o adicional de 10% do IRPJ; confira com o contador.")
    if c.faltando:
        linhas.append("   Falta definir: " + "; ".join(c.faltando) + ".")
    return "\n".join(linhas)


def texto_config(cfg: Config) -> str:
    f = lambda v: f"{fp(v)}%" if v is not None else "não informado"
    linhas = ["🧾 Regras da sua comissão de indicação (estimativa; confira com o contador)",
              f"• Parceiro paga à L8: {f(cfg.percentual_parceiro)} do líquido recebido",
              f"• Impostos da L8 sobre essa receita: {f(cfg.desconto_l8)}",
              f"• Sua parte: {f(cfg.minha_min)} a {f(cfg.minha_max)}" if cfg.minha_min != cfg.minha_max
              else f"• Sua parte: {f(cfg.minha_min)}",
              "• Sua empresa (lucro presumido): " + ", ".join(f"{ROTULO[k]} {f(v)}" for k, v in cfg.aliquotas.items())]
    linhas.append("\nPara mudar: /impostos l8 14,25 · /impostos minha 5 10 · /impostos iss 3 · "
                  "/impostos parceiro 5 · /impostos pis 0,65 (o mesmo para cofins, irpj, csll)")
    return "\n".join(linhas)


def aplicar_comando(cfg: Config, args: list[str]) -> str | None:
    """Muda a configuração pelo /impostos; devolve erro em texto ou None."""
    if not args:
        return None
    chave = args[0].lower()
    try:
        vals = [Decimal(a.replace(",", ".").rstrip("%")) for a in args[1:]]
    except Exception:
        return "Não entendi o número. Ex.: /impostos iss 3"
    if not vals or any(v < 0 or v > 100 for v in vals):
        return "Use um percentual entre 0 e 100. Ex.: /impostos iss 3"
    if chave == "minha":
        cfg.minha_min, cfg.minha_max = (vals[0], vals[-1]) if vals[0] <= vals[-1] else (vals[-1], vals[0])
    elif chave == "l8":
        cfg.desconto_l8 = vals[0]
    elif chave == "parceiro":
        cfg.percentual_parceiro = vals[0]
    elif chave in cfg.aliquotas:
        cfg.aliquotas[chave] = vals[0]
    else:
        return "Não conheço esse item. Use: l8, minha, parceiro, iss, pis, cofins, irpj ou csll."
    return None


def calcular_direto(base_centavos: int, pct: Decimal, cfg: Config) -> Calculo:
    """Parceiro paga direto ao fundador (ou à empresa dele): só os impostos dele descontam."""
    minha = _pct(base_centavos, pct)
    sem = [ROTULO[k] for k, v in cfg.aliquotas.items() if v is None]
    imp = None if sem else sum(cfg.aliquotas.values(), Decimal(0))
    liq = minha - _pct(minha, imp) if imp is not None else None
    faltando = ["alíquota de " + ", ".join(sem) + " da sua empresa (/impostos iss <percentual>)"] if sem else []
    return Calculo(base_centavos, minha, minha, minha, minha, imp, liq, liq, faltando)


def texto_direto(c: Calculo, pct: Decimal) -> str:
    linhas = [f"   Base (líquido recebido pelo parceiro): {brl(c.base)}",
              f"   Você recebe {fp(pct)}% direto do parceiro: {brl(c.minha_min)}"]
    if c.impostos_pct is not None:
        linhas.append(f"   Seus impostos (lucro presumido, {fp(c.impostos_pct)}%): {brl(c.minha_min - c.liquido_min)}")
        linhas.append(f"   💰 Você recebe líquido: {brl(c.liquido_min)}")
    if c.minha_max > LIMITE_ADICIONAL_IRPJ:
        linhas.append("   ⚠️ Acima de R$ 62.500 de receita no mês incide o adicional de 10% do IRPJ; confira com o contador.")
    if c.faltando:
        linhas.append("   Falta definir: " + "; ".join(c.faltando) + ".")
    return "\n".join(linhas)

"""CRM de contas dentro do Peraí (D-089, etapa 1): custo acumulado por cliente ou parceiro.

As contas são as mesmas das atas (D-078). O custo vem das despesas ligadas a cliente (despesa_vinculos,
tipo "cliente"); quando uma despesa está ligada a mais de uma conta, ela é rateada em partes iguais,
conservando os centavos (regra trazida do CRM de Contas: um lançamento original com alocações que
somam exatamente o total; ausência de valor é "não informado", nunca zero). Toda conta é do código,
nunca do modelo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from perai.despesas import brl

_VAZIAS = {"grupo", "empresa", "cia", "ltda", "sa", "s", "a", "o", "os", "as", "de", "do", "da", "dos", "das",
           "pessoal", "me", "eireli", "epp"}


def normalizar(nome: str) -> str:
    t = unicodedata.normalize("NFKD", (nome or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    palavras = [p for p in re.findall(r"[a-z0-9]+", t) if p not in _VAZIAS]
    return " ".join(palavras)


def mesma_conta(a: str, b: str) -> bool:
    """"Integrado" e "Grupo Integrado" são a mesma conta; "Coamo" e "Copacol", não."""
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    curto, longo = sorted((na, nb), key=len)
    return len(curto) >= 4 and re.search(rf"\b{re.escape(curto)}\b", longo) is not None


def ratear(valor_centavos: int, partes: int) -> list[int]:
    """Partes iguais que somam exatamente o total; os centavos que sobram vão para as primeiras."""
    if partes <= 0:
        return []
    base, resto = divmod(valor_centavos, partes)
    return [base + (1 if i < resto else 0) for i in range(partes)]


@dataclass
class CustoConta:
    nome: str
    conta_id: str | None = None
    total_centavos: int = 0
    despesas: list[dict] = field(default_factory=list)   # {"despesa": d, "parte": centavos, "rateada_com": [...]}
    sem_valor: int = 0

    @property
    def primeira(self) -> str | None:
        datas = [x["despesa"]["data"] for x in self.despesas if x["despesa"].get("data")]
        return min(datas) if datas else None

    @property
    def ultima(self) -> str | None:
        datas = [x["despesa"]["data"] for x in self.despesas if x["despesa"].get("data")]
        return max(datas) if datas else None


def custos(despesas: list[dict], vinculos: dict[str, list[dict]], contas: list[dict]) -> list[CustoConta]:
    """Custo por conta. Nomes ditos nas despesas que não batem com nenhuma conta das atas viram uma
    conta própria (com o nome como foi dito)."""
    grupos: list[CustoConta] = [CustoConta(c["nome"], c["id"]) for c in contas]

    def grupo(nome: str) -> CustoConta:
        for g in grupos:
            if mesma_conta(g.nome, nome):
                return g
        g = CustoConta(nome.strip())
        grupos.append(g)
        return g

    for d in despesas:
        nomes = [v["cliente"] for v in vinculos.get(d["id"], []) if v["tipo"] == "cliente" and v.get("cliente")]
        alvo: list[CustoConta] = []
        for n in nomes:
            g = grupo(n)
            if g not in alvo:
                alvo.append(g)
        if not alvo:
            continue
        if d.get("valor_centavos") is None:
            for g in alvo:
                g.sem_valor += 1
                g.despesas.append({"despesa": d, "parte": None, "rateada_com": [x.nome for x in alvo if x is not g]})
            continue
        for g, parte in zip(alvo, ratear(d["valor_centavos"], len(alvo))):
            g.total_centavos += parte
            g.despesas.append({"despesa": d, "parte": parte, "rateada_com": [x.nome for x in alvo if x is not g]})
    return [g for g in grupos if g.despesas or g.conta_id]


def _dia(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso and len(iso) >= 10 else "sem data"


def linha_memoria(codigo: str, c: CustoConta) -> str:
    """Item citável na consulta: [K1] (custo calculado pelo Peraí; conta X) ..."""
    if not c.despesas:
        return f"[{codigo}] (custo calculado pelo Peraí; conta {c.nome}) nenhuma despesa ligada a esta conta"
    rateadas = sum(1 for x in c.despesas if x["rateada_com"])
    detalhe = "; ".join(
        f"{_dia(x['despesa'].get('data'))} {x['despesa'].get('estabelecimento') or x['despesa']['descricao']}: "
        + (brl(x["parte"]) if x["parte"] is not None else "valor não informado")
        + (f" (rateada com {', '.join(x['rateada_com'])}; total {brl(x['despesa']['valor_centavos'])})"
           if x["rateada_com"] and x["parte"] is not None else "")
        for x in sorted(c.despesas, key=lambda x: x["despesa"].get("data") or "")[-15:])
    return (f"[{codigo}] (custo calculado pelo Peraí; conta {c.nome}) {brl(c.total_centavos)} em "
            f"{len(c.despesas)} despesa(s), de {_dia(c.primeira)} a {_dia(c.ultima)}"
            + (f", {rateadas} rateada(s) com outras contas" if rateadas else "")
            + (f", {c.sem_valor} sem valor informado" if c.sem_valor else "") + f". Despesas: {detalhe}")


def resumo_conta(c: CustoConta, ultima_ata: dict | None, proxima: dict | None, n_atas: int) -> str:
    linhas = [f"🏢 {c.nome}"]
    if c.despesas:
        linhas.append(f"   💰 Custo: {brl(c.total_centavos)} em {len(c.despesas)} despesa(s)"
                      + (f" (+{c.sem_valor} sem valor)" if c.sem_valor else "")
                      + f" · última em {_dia(c.ultima)}")
    else:
        linhas.append("   💰 Nenhuma despesa ligada")
    if n_atas:
        linhas.append(f"   📄 {n_atas} reunião(ões) registrada(s)"
                      + (f" · última em {_dia(ultima_ata.get('data'))}" if ultima_ata and ultima_ata.get("data") else ""))
    if proxima and proxima.get("descricao"):
        quando = f" em {_dia(proxima['data'])}" if proxima.get("data") else ""
        linhas.append(f"   ➡️ Próxima atividade: {proxima['descricao']}{quando}"
                      + (" (sugestão)" if proxima.get("sugestao") else ""))
    return "\n".join(linhas)

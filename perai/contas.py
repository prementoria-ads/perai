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


def alocar(total: int | None, vinculos: list[dict]) -> tuple[list[int | None], str | None]:
    """Parte de cada vínculo (na ordem dada) e um aviso. Valor fixo vale como dito; percentual é do
    total; quem ficou sem parte divide em partes iguais o que sobrar. Partes que passam do total
    não valem: volta a divisão igual, com aviso. Sobra sem ninguém para receber fica fora das contas."""
    n = len(vinculos)
    if total is None or n == 0:
        return [None] * n, None
    fixos = [v.get("parte_centavos") for v in vinculos]
    pcts = [v.get("parte_percentual") for v in vinculos]
    if all(f is None for f in fixos) and all(p is None for p in pcts):
        return ratear(total, n), None
    partes: list[int | None] = list(fixos)
    com_pct = [i for i in range(n) if fixos[i] is None and pcts[i] is not None]
    if com_pct:
        brutos = [total * pcts[i] / 100 for i in com_pct]
        inteiros = [int(b) for b in brutos]
        sobra = round(sum(brutos)) - sum(inteiros)
        ordem = sorted(range(len(com_pct)), key=lambda k: brutos[k] - inteiros[k], reverse=True)
        for k in ordem[:max(sobra, 0)]:
            inteiros[k] += 1
        for k, i in enumerate(com_pct):
            partes[i] = inteiros[k]
    usado = sum(x for x in partes if x is not None)
    livres = [i for i in range(n) if partes[i] is None]
    if usado > total:
        return ratear(total, n), "a divisão dita passa do total; dividi em partes iguais"
    for i, parte in zip(livres, ratear(total - usado, len(livres))):
        partes[i] = parte
    sobra = total - sum(x for x in partes if x is not None)
    return partes, (f"{brl(sobra)} não foram atribuídos a nenhuma conta" if sobra else None)


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
        ligados = [v for v in vinculos.get(d["id"], []) if v["tipo"] == "cliente" and v.get("cliente")]
        alvo: list[CustoConta] = []
        por_grupo: list[dict] = []
        for v in ligados:
            g = grupo(v["cliente"])
            if g not in alvo:
                alvo.append(g)
                por_grupo.append(v)
        if not alvo:
            continue
        if d.get("valor_centavos") is None:
            for g in alvo:
                g.sem_valor += 1
                g.despesas.append({"despesa": d, "parte": None, "rateada_com": [x.nome for x in alvo if x is not g]})
            continue
        partes, _ = alocar(d["valor_centavos"], por_grupo)
        for g, parte in zip(alvo, partes):
            g.total_centavos += parte or 0
            g.despesas.append({"despesa": d, "parte": parte, "rateada_com": [x.nome for x in alvo if x is not g]})
    return [g for g in grupos if g.despesas or g.conta_id]


def no_periodo(despesas: list[dict], inicio: str | None, fim: str | None) -> list[dict]:
    return [d for d in despesas if (not inicio or (d.get("data") or "") >= inicio)
            and (not fim or (d.get("data") or "9999") <= fim)]


def custos_do_usuario(repo, usuario_id: str, inicio: str | None = None, fim: str | None = None) -> list[CustoConta]:
    from datetime import date
    lista = no_periodo(repo.despesas(usuario_id, date(2000, 1, 1)), inicio, fim)
    vinculos = repo.vinculos_despesas(usuario_id, [d["id"] for d in lista]) if lista else {}
    return custos(lista, vinculos, repo.contas_relacionamento(usuario_id))


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
    meses: dict[str, int] = {}
    for x in c.despesas:
        if x["parte"] is not None and x["despesa"].get("data"):
            mes = f"{x['despesa']['data'][5:7]}/{x['despesa']['data'][:4]}"
            meses[mes] = meses.get(mes, 0) + x["parte"]
    por_mes = "; ".join(f"{m}: {brl(v)}" for m, v in sorted(meses.items(), key=lambda t: t[0][3:] + t[0][:2]))
    return (f"[{codigo}] (custo calculado pelo Peraí; conta {c.nome}) {brl(c.total_centavos)} em "
            f"{len(c.despesas)} despesa(s), de {_dia(c.primeira)} a {_dia(c.ultima)}"
            + (f"; por mês: {por_mes}" if por_mes else "")
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


# -- Oportunidades (D-090) --------------------------------------------------------------

ROTULO_ETAPA = {"prospeccao": "Prospecção", "proposta": "Proposta", "negociacao": "Negociação",
                "ganha": "Ganha (projeto)", "perdida": "Perdida"}


def _valor(o: dict) -> str:
    return brl(o["valor_centavos"]) if o.get("valor_centavos") is not None else "valor não informado"


def total_conhecido(lista: list[dict]) -> tuple[int, int]:
    """(soma dos valores ditos, quantas estão sem valor): sem valor nunca vira zero na soma."""
    return (sum(o["valor_centavos"] for o in lista if o.get("valor_centavos") is not None),
            sum(1 for o in lista if o.get("valor_centavos") is None))


def _soma(lista: list[dict]) -> str:
    total, sem = total_conhecido(lista)
    return brl(total) + (f" + {sem} sem valor" if sem else "")


def funil(lista: list[dict]) -> str:
    blocos = []
    for etapa, rotulo in ROTULO_ETAPA.items():
        da_etapa = [o for o in lista if o["etapa"] == etapa]
        if not da_etapa:
            continue
        linhas = [f"{rotulo} · {len(da_etapa)} · {_soma(da_etapa)}"]
        for o in da_etapa[:15]:
            prev = f" · previsão {_dia(o['previsao'])}" if o.get("previsao") else ""
            linhas.append(f"• {o.get('conta') or 'sem conta'}: {o['titulo']} · {_valor(o)}{prev}")
        blocos.append("\n".join(linhas))
    abertas = [o for o in lista if o["etapa"] not in ("ganha", "perdida")]
    return ("\n\n".join(blocos) + f"\n\nEm aberto: {len(abertas)} · {_soma(abertas)}. "
            "Valores como você disse; comissão não é calculada aqui.")


def linha_oportunidade(codigo: str, o: dict) -> str:
    prev = f"; previsão {_dia(o['previsao'])}" if o.get("previsao") else ""
    return (f"[{codigo}] (oportunidade; conta {o.get('conta') or 'sem conta'}; etapa {ROTULO_ETAPA[o['etapa']]}; "
            f"atualizada em {_dia(o['atualizado_em'][:10])}) {o['titulo']}: {_valor(o)}{prev}")


def resumo_oportunidades(lista: list[dict]) -> str | None:
    if not lista:
        return None
    abertas = [o for o in lista if o["etapa"] not in ("ganha", "perdida")]
    ganhas = [o for o in lista if o["etapa"] == "ganha"]
    partes = []
    if abertas:
        partes.append(f"{len(abertas)} em aberto ({_soma(abertas)})")
    if ganhas:
        partes.append(f"{len(ganhas)} ganha(s) ({_soma(ganhas)})")
    perdidas = len(lista) - len(abertas) - len(ganhas)
    if perdidas:
        partes.append(f"{perdidas} perdida(s)")
    return "   📈 Oportunidades: " + " · ".join(partes)

"""Despesas (D-052): valores em centavos, leitura de NF-e em XML e totais calculados pelo código.

Regras trazidas do CRM de Contas:
- dinheiro sempre em centavos inteiros, nunca número quebrado (float);
- valor que não foi dito é "não informado" (None), nunca zero;
- um lançamento original por despesa; correção vira histórico, sem apagar.
O CPF do consumidor que aparece na nota nunca é guardado.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

CATEGORIAS = ("alimentacao", "combustivel", "transporte", "hospedagem", "saude", "mercado", "casa",
              "educacao", "lazer", "servicos", "impostos", "outros")
ROTULO_CATEGORIA = {
    "alimentacao": "alimentação", "combustivel": "combustível", "transporte": "transporte",
    "hospedagem": "hospedagem", "saude": "saúde", "mercado": "mercado", "casa": "casa",
    "educacao": "educação", "lazer": "lazer", "servicos": "serviços", "impostos": "impostos",
    "outros": "outros",
}
PAGAMENTOS_NFE = {"01": "dinheiro", "02": "cheque", "03": "cartão de crédito", "04": "cartão de débito",
                  "05": "crédito loja", "10": "vale-alimentação", "11": "vale-refeição", "15": "boleto",
                  "16": "depósito", "17": "PIX", "18": "transferência", "99": "outros"}
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro")


def valor_em_centavos(valor) -> int | None:
    """'R$ 1.234,56' / '182,40' / '182.40' / 180 -> centavos. Ilegível ou ausente -> None."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor * 100 if valor >= 0 else None
    texto = str(valor).strip().lower().replace("r$", "").replace(" ", "")
    if not texto:
        return None
    if "," in texto:  # formato brasileiro: ponto separa milhar, vírgula separa centavos
        texto = texto.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):  # "1.234" = mil duzentos e trinta e quatro
        texto = texto.replace(".", "")
    try:
        d = Decimal(texto)
    except InvalidOperation:
        return None
    if d < 0:
        return None
    return int((d * 100).quantize(Decimal("1")))


def brl(centavos: int | None) -> str:
    if centavos is None:
        return "valor não informado"
    reais, cent = divmod(centavos, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


def refeicao(hora: str | None, categoria: str | None) -> str | None:
    """Pela hora, só para alimentação (D-112): até 10h30 café da manhã, até 15h almoço, até 18h lanche,
    depois jantar (madrugada também conta como jantar)."""
    if categoria != "alimentacao" or not hora or len(hora) < 5:
        return None
    hm = hora[:5]
    if "04:00" <= hm < "10:30":
        return "café da manhã"
    if "10:30" <= hm < "15:00":
        return "almoço"
    if "15:00" <= hm < "18:00":
        return "lanche"
    return "jantar"


def categoria_valida(valor) -> str:
    return valor if valor in CATEGORIAS else "outros"


# --- NF-e em XML (lida pelo código, sem IA) -------------------------------------------

@dataclass
class NotaFiscal:
    emitente: str
    cnpj: str | None
    data: str | None              # AAAA-MM-DD
    total_centavos: int | None
    pagamento: str | None
    itens: list[tuple[str, int | None]] = field(default_factory=list)
    chave: str | None = None
    # Por item: descrição, quantidade, unidade e valor unitário da nota (histórico de preços, D-084).
    detalhes: list[dict] = field(default_factory=list)
    hora: str | None = None       # HH:MM da emissão (D-112)
    cidade: str | None = None     # cidade do emitente (D-112)


def _sem_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _achar(no, *caminho):
    for parte in caminho:
        if no is None:
            return None
        no = next((f for f in no if _sem_ns(f.tag) == parte), None)
    return no


def _texto(no, *caminho) -> str | None:
    alvo = _achar(no, *caminho)
    return alvo.text.strip() if alvo is not None and alvo.text else None


def ler_nfe_xml(caminho: Path) -> NotaFiscal | None:
    """Lê o XML da NF-e/NFC-e (procNFe ou NFe). Não é nota fiscal -> None."""
    try:
        raiz = ET.parse(caminho).getroot()
    except ET.ParseError:
        return None
    inf = next((n for n in raiz.iter() if _sem_ns(n.tag) == "infNFe"), None)
    if inf is None:
        return None
    emit = _achar(inf, "emit")
    data = _texto(inf, "ide", "dhEmi") or _texto(inf, "ide", "dEmi")
    itens, detalhes = [], []
    for det in (n for n in inf if _sem_ns(n.tag) == "det"):
        itens.append((_texto(det, "prod", "xProd") or "item", valor_em_centavos(_texto(det, "prod", "vProd"))))
        detalhes.append({"descricao": _texto(det, "prod", "xProd") or "item",
                         "quantidade": _texto(det, "prod", "qCom"), "unidade": _texto(det, "prod", "uCom"),
                         "valor_unitario": _texto(det, "prod", "vUnCom")})
    codigo_pag = _texto(inf, "pag", "detPag", "tPag")
    chave = (inf.get("Id") or "").removeprefix("NFe") or None
    # O destinatário (dest, com CPF) é ignorado de propósito.
    return NotaFiscal(
        emitente=_texto(emit, "xFant") or _texto(emit, "xNome") or "Emitente não identificado",
        cnpj=_texto(emit, "CNPJ"),
        data=data[:10] if data else None,
        total_centavos=valor_em_centavos(_texto(inf, "total", "ICMSTot", "vNF")),
        pagamento=PAGAMENTOS_NFE.get(codigo_pag or "", None),
        itens=itens,
        chave=chave,
        detalhes=detalhes,
        hora=data[11:16] if data and len(data) >= 16 and data[10] == "T" else None,
        cidade=_texto(emit, "enderEmit", "xMun"),
    )


def resumo_nota(n: NotaFiscal, fonte: str = "XML") -> str:
    itens = "; ".join(f"{d} ({brl(v)})" for d, v in n.itens)
    return (f"Nota fiscal eletrônica ({fonte}). Emitente: {n.emitente}"
            + (f" (CNPJ {n.cnpj})" if n.cnpj else "")
            + f". Data: {n.data or 'não informada'}. Total: {brl(n.total_centavos)}"
            + (f". Pagamento: {n.pagamento}" if n.pagamento else "")
            + (f". Itens: {itens}" if itens else "") + ".")


def categoria_por_itens(n: NotaFiscal) -> str:
    """Primeiro pelo nome do emitente (supermercado com café nos itens continua mercado); depois pelos
    itens (restaurante costuma ter prato, bebida e taxa de serviço)."""
    regras = (("combustivel", ("gasolina", "etanol", "diesel", "combust", "posto", "gnv")),
              ("mercado", ("supermerc", "mercado", "atacad", "hipermerc")),
              ("saude", ("farm", "drogaria", "remédio", "medic")),
              ("hospedagem", ("hotel", "pousada")),
              ("alimentacao", ("restaurante", "lanchonete", "lanche", "refei", "pizza", "padaria", "café",
                               "cafe ", "churrasc", "grill", "bistr", "cantina", "sushi", "burger", "bar ")))
    emitente = f"{n.emitente.lower()} "
    for categoria, palavras in regras:
        if any(p in emitente for p in palavras):
            return categoria
    itens = " ".join(d for d, _ in n.itens).lower() + " "
    if re.search(r"\btx\b|taxa de servi|couvert|gar[cç]om|\b10%", itens):
        return "alimentacao"
    for categoria, palavras in regras:
        if any(p in itens for p in palavras):
            return categoria
    if re.search(r"strogon|risoto|prato|executivo|buffet|marmit|por[cç][ãa]o|lasanha|parmegian|grelhad|"
                 r"fil[eé] |espeto|sobremesa|suco|refri|coca|cerveja|chopp", itens):
        return "alimentacao"
    return "outros"


# --- totais (calculados aqui, nunca pelo modelo) --------------------------------------

def totais_por_mes(despesas: list[dict]) -> list[str]:
    """Linhas de totais por mês e por categoria. `despesas`: dicts com data, valor_centavos, categoria."""
    grupos: dict[str, dict[str, list[int | None]]] = {}
    for d in despesas:
        if not d.get("data"):
            continue
        mes = d["data"][:7]
        grupos.setdefault(mes, {}).setdefault(d["categoria"], []).append(d.get("valor_centavos"))
    linhas = []
    for mes in sorted(grupos, reverse=True):
        ano, m = mes.split("-")
        partes, total, sem_valor = [], 0, 0
        for cat, valores in sorted(grupos[mes].items()):
            conhecidos = [v for v in valores if v is not None]
            sem_valor += len(valores) - len(conhecidos)
            total += sum(conhecidos)
            partes.append(f"{ROTULO_CATEGORIA[cat]} {brl(sum(conhecidos))} ({len(valores)})")
        extra = f"; {sem_valor} sem valor informado" if sem_valor else ""
        linhas.append(f"{MESES[int(m) - 1]}/{ano}: total {brl(total)}{extra} — " + ", ".join(partes))
    return linhas


def data_br(iso: str | None) -> str:
    if not iso:
        return "sem data"
    d = date.fromisoformat(iso)
    return f"{d:%d/%m}"

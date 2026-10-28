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
    itens = []
    for det in (n for n in inf if _sem_ns(n.tag) == "det"):
        itens.append((_texto(det, "prod", "xProd") or "item", valor_em_centavos(_texto(det, "prod", "vProd"))))
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
    )


def resumo_nota(n: NotaFiscal) -> str:
    itens = "; ".join(f"{d} ({brl(v)})" for d, v in n.itens[:20])
    return (f"Nota fiscal eletrônica (XML). Emitente: {n.emitente}"
            + (f" (CNPJ {n.cnpj})" if n.cnpj else "")
            + f". Data: {n.data or 'não informada'}. Total: {brl(n.total_centavos)}"
            + (f". Pagamento: {n.pagamento}" if n.pagamento else "")
            + (f". Itens: {itens}" if itens else "") + ".")


def categoria_por_itens(n: NotaFiscal) -> str:
    texto = " ".join([n.emitente] + [d for d, _ in n.itens]).lower()
    regras = (("combustivel", ("gasolina", "etanol", "diesel", "combust", "posto", "gnv")),
              ("alimentacao", ("restaurante", "lanche", "refei", "pizza", "padaria", "café")),
              ("mercado", ("supermerc", "mercado", "atacad")),
              ("saude", ("farm", "drogaria", "remédio", "medic")),
              ("hospedagem", ("hotel", "pousada")))
    for categoria, palavras in regras:
        if any(p in texto for p in palavras):
            return categoria
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

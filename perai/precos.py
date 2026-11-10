"""Histórico de preços (D-084): ofertas de panfletos e preços pagos nas notas fiscais, para comparar.

Cada preço guarda produto, marca, tamanho, preço, loja, cidade e data, com a mensagem de origem como
fonte (D-040). Para comparar tamanhos diferentes, o código calcula o preço de referência por kg, por
litro ou por unidade. A comparação na resposta usa só esses números: "vale a pena" vira "é o menor
preço registrado" ou "em 12/10 você pagou menos", nunca opinião.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from perai.extracao import extrair_json

SISTEMA_PRECOS = """Você lê um panfleto, encarte ou lista de ofertas de supermercado ou loja e devolve só
um objeto JSON com cada preço que aparece no texto. Use SOMENTE o que está no texto; nada inventado.

{"loja": str|null, "cidade": str|null, "validade": "AAAA-MM-DD"|null,
 "itens": [{"produto": str, "marca": str|null, "quantidade": número|null, "unidade": "g"|"kg"|"ml"|"l"|"un"|null,
            "preco": número, "por": "un"|"kg"|"l", "condicao": str|null, "loja": str|null}]}

Regras:
1. "produto": nome curto e comum, sem marca ("coxa e sobrecoxa de frango congelada", "leite condensado").
2. "quantidade" e "unidade": o tamanho da embalagem (395 g, 2 l, 12 un); "por": a que o preço se refere
   (por unidade/embalagem, por kg ou por litro).
3. "condicao": quando o preço depende de algo ("clube +amigo", "na 2ª unidade", "pagando no Pix").
   Desconto sem preço final (só "20% na 2ª unidade") não vira item.
4. Se o texto tem várias lojas, "loja" de cada item diz qual. "validade": o dia final da oferta, se dito.
5. "preco": só o número, com ponto decimal (7.99), sem "R$" nem unidade."""

PEDIDO_ARQUIVO = ("O arquivo anexo é um panfleto ou encarte de ofertas. Leia TODAS as páginas e liste TODOS os "
                  "preços que aparecem, um item por produto, seguindo as regras.")

PADRAO_PRECO = re.compile(r"R\$\s?\d")
PALAVRAS_OFERTA = re.compile(r"ofert|encarte|panflet|promo|tabloide|tabl[oó]ide|folheto", re.I)
INTENCAO = re.compile(r"pre[cç]o|oferta|barat|caro|vale a pena|promo|quanto (custa|paguei|t[aá])|mercado|encarte",
                      re.I)
FALA_DE_LISTA = re.compile(r"\blista\b|minhas compras|\bcompras\b", re.I)
E_LISTA = re.compile(r"\b(comprar|lista|mercado|compras)\b", re.I)
UNIDADES_KG = {"g": Decimal("0.001"), "kg": Decimal(1)}
UNIDADES_L = {"ml": Decimal("0.001"), "l": Decimal(1)}


def parece_oferta(texto: str) -> bool:
    n = len(PADRAO_PRECO.findall(texto or ""))
    return n >= 6 or (n >= 3 and bool(PALAVRAS_OFERTA.search(texto or "")))


def parece_panfleto(texto: str) -> bool:
    """Para foto, print ou PDF: o leitor costuma resumir o encarte, então basta o resumo citar um preço
    e dizer que é oferta/encarte (ou citar 3 preços)."""
    n = len(PADRAO_PRECO.findall(texto or ""))
    return n >= 3 or (n >= 1 and bool(PALAVRAS_OFERTA.search(texto or "")))


def _num(v) -> Decimal | None:
    """Aceita 7.99, "7,99", "R$ 7,99", "R$ 1.299,90/kg"."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        bruto = str(v)
    else:
        m = re.search(r"\d[\d.,]*", str(v))
        if not m:
            return None
        bruto = m.group(0).rstrip(".,")
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        elif bruto.count(".") > 1:
            bruto = bruto.replace(".", "")
    try:
        d = Decimal(bruto)
    except InvalidOperation:
        return None
    return d if d > 0 else None


def referencia(preco: Decimal, por: str, quantidade: Decimal | None, unidade: str | None) -> tuple[int, str]:
    """(centavos por unidade de referência, "kg" | "l" | "un")."""
    if por in ("kg", "l"):
        return int((preco * 100).quantize(Decimal(1))), por
    if quantidade and unidade in UNIDADES_KG:
        return int((preco * 100 / (quantidade * UNIDADES_KG[unidade])).quantize(Decimal(1))), "kg"
    if quantidade and unidade in UNIDADES_L:
        return int((preco * 100 / (quantidade * UNIDADES_L[unidade])).quantize(Decimal(1))), "l"
    if quantidade and unidade == "un" and quantidade > 1:
        return int((preco * 100 / quantidade).quantize(Decimal(1))), "un"
    return int((preco * 100).quantize(Decimal(1))), "un"


def interpretar(resposta: str) -> list[dict]:
    try:
        d = extrair_json(resposta)
    except (ValueError, TypeError):
        return []
    if not isinstance(d, dict):
        return []
    validade = d.get("validade") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d.get("validade") or "")) else None
    saida = []
    for i in d.get("itens") or []:
        if not isinstance(i, dict) or not str(i.get("produto") or "").strip():
            continue
        preco = _num(i.get("preco"))
        if not preco or preco > 100_000:
            continue
        por = i.get("por") if i.get("por") in ("un", "kg", "l") else "un"
        unidade = str(i.get("unidade") or "").lower() or None
        unidade = unidade if unidade in ("g", "kg", "ml", "l", "un") else None
        quantidade = _num(i.get("quantidade"))
        ref, ref_un = referencia(preco, por, quantidade, unidade)
        saida.append({"produto": str(i["produto"]).strip()[:120], "marca": (str(i.get("marca")).strip()[:60]
                                                                            if i.get("marca") else None),
                      "quantidade": float(quantidade) if quantidade else None, "unidade": unidade,
                      "preco_centavos": int((preco * 100).quantize(Decimal(1))), "por": por,
                      "ref_centavos": ref, "ref_unidade": ref_un,
                      "condicao": (str(i.get("condicao")).strip()[:120] if i.get("condicao") else None),
                      "loja": str(i.get("loja") or d.get("loja") or "").strip()[:80] or None,
                      "cidade": str(d.get("cidade") or "").strip()[:60] or None, "data": validade})
    return saida[:200]


def de_nota(nota) -> list[dict]:
    """Preços pagos, item a item, do XML da nota fiscal (quantidade e unidade da própria nota)."""
    saida = []
    for d in getattr(nota, "detalhes", []) or []:
        unitario = _num(d.get("valor_unitario"))
        if not unitario:
            continue
        unidade = (d.get("unidade") or "").lower()
        por = "kg" if unidade in ("kg", "kgs", "quilo") else "l" if unidade in ("l", "lt", "litro") else "un"
        ref, ref_un = referencia(unitario, por, None, None)
        saida.append({"produto": d["descricao"][:120], "marca": None, "quantidade": None, "unidade": None,
                      "preco_centavos": int((unitario * 100).quantize(Decimal(1))), "por": por,
                      "ref_centavos": ref, "ref_unidade": ref_un, "condicao": None,
                      "loja": nota.emitente[:80], "cidade": None, "data": nota.data})
    return saida


def chave(texto: str) -> set[str]:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {p[:5] for p in re.findall(r"[a-z]{3,}", t)} - {"para", "com", "sem", "tipo", "cada", "unida"}


def identidade(p: dict) -> tuple:
    """O mesmo preço mandado de novo (o mesmo encarte duas vezes) tem a mesma identidade."""
    return (p.get("origem", "oferta"), frozenset(chave(f"{p['produto']} {p.get('marca') or ''}")),
            (p.get("loja") or "").lower(), p["preco_centavos"], p.get("data"), p.get("por"))


def sem_repetidos(itens: list[dict]) -> list[dict]:
    vistos, saida = set(), []
    for p in itens:
        k = identidade(p)
        if k not in vistos:
            vistos.add(k)
            saida.append(p)
    return saida


def brl(centavos: int) -> str:
    return f"R$ {centavos / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def linha(codigo: str, p: dict) -> str:
    """Linha de memória citável: [Q1] (preço; oferta; loja, cidade; data) produto: R$ x/kg."""
    origem = "oferta de panfleto" if p["origem"] == "oferta" else "preço pago (nota fiscal)"
    onde = ", ".join(x for x in (p.get("loja"), p.get("cidade")) if x) or "loja não informada"
    quando = p.get("data") or "sem data"
    tamanho = f" {p['quantidade']:g} {p['unidade']}" if p.get("quantidade") and p.get("unidade") else ""
    marca = f" {p['marca']}" if p.get("marca") else ""
    ref = (f"; equivale a {brl(p['ref_centavos'])}/{p['ref_unidade']}" if p["por"] == "un" and (
        p["ref_unidade"] != "un" or p["ref_centavos"] != p["preco_centavos"]) else "")
    cond = f" ({p['condicao']})" if p.get("condicao") else ""
    return (f"[{codigo}] (preço; {origem}; {onde}; {quando}) {p['produto']}{marca}{tamanho}: "
            f"{brl(p['preco_centavos'])}/{p['por']}{ref}{cond}")


def _dia(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}" if iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else ""


def texto_compartilhar(itens: list[dict]) -> str:
    """Texto para o WhatsApp (D-086), montado só com os números guardados: nada de IA, nada pessoal."""
    linhas = ["🏷️ Preços anotados"]
    for p in sorted(sem_repetidos(itens), key=lambda p: (p["produto"].lower(), p["ref_unidade"], p["ref_centavos"])):
        tamanho = f" {p['quantidade']:g} {p['unidade']}" if p.get("quantidade") and p.get("unidade") else ""
        marca = f" {p['marca']}" if p.get("marca") else ""
        ref = (f" ({brl(p['ref_centavos'])}/{p['ref_unidade']})" if p["por"] == "un" and (
            p["ref_unidade"] != "un" or p["ref_centavos"] != p["preco_centavos"]) else "")
        cond = f", {p['condicao']}" if p.get("condicao") else ""
        onde = ", ".join(x for x in (p.get("loja"), p.get("cidade")) if x) or "loja não informada"
        dia = _dia(p.get("data"))
        quando = ((f"oferta até {dia}" if dia else "oferta") if p["origem"] == "oferta"
                  else (f"pago em {dia}" if dia else "preço pago"))
        linhas.append(f"• {p['produto'][:1].upper()}{p['produto'][1:]}{marca}{tamanho}: "
                      f"{brl(p['preco_centavos'])}/{p['por']}{ref}{cond} — {onde} ({quando})")
    return "\n".join(linhas)

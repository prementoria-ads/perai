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
FALA_DE_LISTA = re.compile(r"\blistas?\b|\bcompras\b|qual (mercado|supermercado|loja)|"
                           r"(mercado|supermercado|loja) (é |e )?(melhor|mais barat)|onde (eu )?(vou|ir|compro|comprar)",
                           re.I)
E_LISTA = re.compile(r"\b(comprar|lista|mercado|compras)\b", re.I)
# Pedido para VER a lista (organizada, completa, com caixinhas), e não para comparar preços.
MOSTRAR_LISTA = re.compile(r"organiz|departament|se[cç](?:ão|ao|ões|oes)|corredor|check|caixinha|marcar|completa|"
                           r"inteira|toda a lista|mostr|\bver\b|\bveja\b|manda a lista|me (passa|d[aá]) a lista|"
                           r"o que (tem|falta|est[aá]) na lista|qual (é |e )?a (minha )?lista", re.I)
MUDAR_LISTA = re.compile(r"inclu|adicion|acrescent|\btir[ae]\b|remov|retir|apag|coloc|\bbot[ae]\b|comprei", re.I)
COMPARAR_LISTA = re.compile(r"barat|pre[cç]o|oferta|qual (mercado|supermercado|loja)|\bonde\b|melhor (ir|comprar)|"
                            r"\bcaro\b|economi|promo", re.I)
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


# -- Lista de compras x ofertas (D-088): a IA só diz qual preço é de qual item; a conta é do código --

SISTEMA_LISTA = """Você recebe os itens de uma lista de compras e preços registrados [Q1], [Q2]... e devolve só
um objeto JSON dizendo, para cada item, quais [Q] são o MESMO produto. Marca e tamanho podem ser
diferentes ("ovos" aceita "ovos brancos Nida c/20" e "ovos Tominaga c/30"); produto diferente não
("leite zero lactose" não aceita leite condensado; "peito de frango" não aceita coxa; "outras frutas"
não aceita nenhum preço). A forma também precisa ser a mesma: "detergente" é o líquido de louça e não
aceita detergente em pó nem sabão em pó; "chocolate em barra" não aceita achocolatado em pó. Na dúvida, deixe
de fora.

{"itens": [{"item": "<como está na lista>", "precos": ["Q1", "Q7"]}]}"""


def _loja_chave(loja: str | None) -> str:
    t = unicodedata.normalize("NFKD", (loja or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    palavras = [w for w in re.findall(r"[a-z0-9]+", t) if w not in {"supermercado", "supermercados", "mercado",
                                                                      "atacado", "atacadista", "hiper", "de", "do", "da"}]
    return palavras[0] if palavras else t.strip() or "?"


def _nome_loja(nomes: list[str]) -> str:
    curtos = sorted({re.sub(r"(?i)\s*supermercados?\s*", " ", n).strip() or n for n in nomes if n}, key=len)
    return curtos[0] if curtos else "loja não informada"


def _desc(p: dict) -> str:
    tamanho = f" {p['quantidade']:g} {p['unidade']}" if p.get("quantidade") and p.get("unidade") else ""
    marca = f" {p['marca']}" if p.get("marca") else ""
    ref = (f" ({brl(p['ref_centavos'])}/{p['ref_unidade']})" if p["por"] == "un" and (
        p["ref_unidade"] != "un" or p["ref_centavos"] != p["preco_centavos"]) else "")
    return f"{p['produto']}{marca}{tamanho} {brl(p['preco_centavos'])}/{p['por']}{ref}"


def comparar_lista(itens: list[str], mapa: dict[str, list[str]], codigos: dict[str, dict], hoje: str
                   ) -> tuple[str, list[str]]:
    """(texto, códigos [Q] usados). Por item: só ofertas vigentes, comparadas pela referência (kg, l ou
    unidade); fica a loja mais barata, e o empate vai para a loja que já ganhou mais itens."""
    nomes: dict[str, list[str]] = {}
    resultado, sem_preco = [], []
    for item in itens:
        qs = [(c, codigos[c]) for c in mapa.get(item, []) if c in codigos]
        vig = [(c, p) for c, p in qs if p["origem"] == "oferta" and (p.get("data") or "") >= hoje]
        if not vig:
            sem_preco.append(item)
            continue
        unidades = [p["ref_unidade"] for _, p in vig]
        un = max(set(unidades), key=unidades.count)
        por_loja: dict[str, tuple[str, dict]] = {}
        for c, p in vig:
            if p["ref_unidade"] != un:
                continue
            k = _loja_chave(p.get("loja"))
            nomes.setdefault(k, []).append(p.get("loja") or "")
            if k not in por_loja or p["ref_centavos"] < por_loja[k][1]["ref_centavos"]:
                por_loja[k] = (c, p)
        menor = min(p["ref_centavos"] for _, p in por_loja.values())
        empatadas = [k for k, (_, p) in por_loja.items() if p["ref_centavos"] == menor]
        resultado.append((item, por_loja, empatadas))
    vitorias: dict[str, int] = {}
    for _, _, emp in resultado:
        if len(emp) == 1:
            vitorias[emp[0]] = vitorias.get(emp[0], 0) + 1
    grupos: dict[str, list[str]] = {}
    usados: list[str] = []
    for item, por_loja, emp in resultado:
        loja = max(emp, key=lambda k: (vitorias.get(k, 0), -emp.index(k)))
        c, p = por_loja[loja]
        usados.append(c)
        extra = []
        outras = [k for k in emp if k != loja]
        if outras:
            extra.append("mesmo preço no " + " e no ".join(_nome_loja(nomes[k]) for k in outras))
        for k, (c2, p2) in por_loja.items():
            if k not in emp:
                usados.append(c2)
                extra.append(f"no {_nome_loja(nomes[k])}: {brl(p2['preco_centavos'])}/{p2['por']}"
                             + (f" ({brl(p2['ref_centavos'])}/{p2['ref_unidade']})" if p2["ref_unidade"] != p2["por"]
                                or p2["ref_centavos"] != p2["preco_centavos"] else ""))
        validade = f", até {p['data'][8:10]}/{p['data'][5:7]}" if p.get("data") else ""
        linha_ = f"• {item[:1].upper()}{item[1:]}: {_desc(p)}{validade}" + (f" — {'; '.join(extra)}" if extra else "")
        grupos.setdefault(loja, []).append(linha_)
    if not grupos:
        return "", []
    ordem = sorted(grupos, key=lambda k: -len(grupos[k]))
    topo = ordem[0]
    linhas = [f"🛒 Melhor ir ao {_nome_loja(nomes[topo])}: {len(grupos[topo])} item(ns) da sua lista pelo menor "
              f"preço registrado lá."]
    for k in ordem:
        linhas += ["", f"Compre no {_nome_loja(nomes[k])}:"] + grupos[k]
    if sem_preco:
        linhas += ["", "Sem oferta vigente registrada: " + ", ".join(sem_preco) + "."]
    return "\n".join(linhas), usados


# -- lista de compras: reconhecer, juntar repetidos e organizar por departamento ---------------------

_ABRE_LISTA = re.compile(r"^\s*(comprar|lista(?: de compras)?:?)\s", re.I)
_TIRAR_DO_NUCLEO = re.compile(
    r"\(.*?\)|\b\d+(?:[.,]\d+)?\s*(?:a\s*\d+\s*)?(?:g|kg|ml|l|un|unidades?|litros?|gramas?)?\b|"
    r"\b(?:pacotes?|pct|caixinhas?|caixas?|latas?|garrafas?|sachês?|saches?|potes?|bandejas?|d[uú]zias?|"
    r"tipo|em barra|barra|de|do|da|dos|das|um|uma)\b", re.I)


def pede_ver_lista(texto: str) -> bool:
    """"Organiza a lista por departamento", "Lista completa": ver a lista, sem comparar nem mudar."""
    t = (texto or "").strip()
    return (len(t) < 200 and bool(FALA_DE_LISTA.search(t) or t.lower().startswith("lista"))
            and bool(MOSTRAR_LISTA.search(t)) and not COMPARAR_LISTA.search(t) and not MUDAR_LISTA.search(t))


def nucleo(item: str) -> str:
    """O produto sem quantidade nem embalagem: "1 caixinha de creme de leite (200g)" → "creme leite"."""
    t = unicodedata.normalize("NFKD", (item or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _TIRAR_DO_NUCLEO.sub(" ", t)
    return " ".join(re.findall(r"[a-z]+", t))


def itens_unicos(itens: list[str]) -> list[str]:
    """Junta o mesmo produto escrito duas vezes; fica a forma mais detalhada, na posição da primeira."""
    saida, onde = [], {}
    for i in itens:
        n = nucleo(i) or i.lower()
        if n in onde:
            k = onde[n]
            if len(i) > len(saida[k]):
                saida[k] = i
        else:
            onde[n] = len(saida)
            saida.append(i)
    return saida


def e_lista_de_compras(descricao: str, responsavel: str | None = None, itens: int = 0) -> bool:
    """A pendência que é a lista do mercado: começa com "Comprar" ou "Lista" e tem mais de um item.
    "Aguardando o Nilson... Mercado Pago" ou "Comprar adubo" sozinho não são a lista."""
    return responsavel != "aguardando" and bool(_ABRE_LISTA.match(descricao or "")) and itens >= 2


DEPARTAMENTOS = [
    ("🥬 Hortifrúti", r"banana|manga|mam[aã]o|papaia|ma[cç][aã]|laranja|lim[aã]o|uva|abacaxi|melancia|mel[aã]o|"
                     r"morango|abacate|kiwi|pera|fruta|tomate|alface|cebola|alho|batata|cenoura|abobrinha|"
                     r"ab[oó]bora|pepino|piment[aã]o|repolho|br[oó]colis|couve|cheiro|salsinha|coentro|verdura|"
                     r"legume|mandioca|chuchu|beterraba|ovos?\b"),
    ("🥩 Açougue e peixaria", r"carne|frango|peito|coxa|sobrecoxa|bife|patinho|alcatra|picanha|costela|lingui[cç]a|"
                             r"porco|lombo|peixe|til[aá]pia|sardinha fresca|camar[aã]o|mo[ií]da|fil[eé]"),
    ("🧀 Frios e laticínios", r"mussarela|mu[cç]arela|queijo|presunto|peito de peru|salame|mortadela|requeij[aã]o|"
                             r"iogurte|manteiga|margarina|leite(?! condensado)(?! de coco)|nata|creme de leite"),
    ("🥖 Padaria", r"p[aã]o|bisnaguinha|bolo|torrada"),
    ("🥫 Mercearia", r"arroz|feij[aã]o|macarr[aã]o|farinha|fub[aá]|floc[aã]o|milho|a[cç][uú]car|sal\b|[oó]leo|azeite|"
                    r"vinagre|caf[eé]|ch[aá]\b|achocolatado|chocolate|biscoito|bolacha|maizena|amido|fermento|royal|"
                    r"conserva|extrato|molho|maionese|ketchup|mostarda|tempero|leite condensado|creme de leite|"
                    r"amendoim|castanha|granola|aveia|atum|sardinha|gelatina|pudim|cereal|pipoca"),
    ("🥤 Bebidas", r"refrigerante|suco|[aá]gua|cerveja|vinho|energ[eé]tico|ch[aá] gelado"),
    ("🧽 Limpeza", r"detergente|sab[aã]o|amaciante|desinfetante|[aá]gua sanit[aá]ria|alvejante|esponja|saco de lixo|"
                  r"lixo|limpador|multiuso|lustra|vassoura|rodo|pano|[aá]lcool(?! .*vinagre)"),
    ("🧴 Higiene", r"papel higi[eê]nico|sabonete|shampoo|xampu|condicionador|creme dental|pasta de dente|escova|"
                  r"desodorante|absorvente|fralda|len[cç]o|cotonete|fio dental|barbear"),
]


def departamento(item: str) -> str:
    t = (item or "").lower()
    # Vinagre "de álcool" é mercearia; creme de leite é laticínio de mercearia: a ordem da lista decide.
    if re.search(r"vinagre|creme de leite|leite condensado|chocolate|achocolatado|biscoito|bolacha|leite de coco", t):
        return "🥫 Mercearia"
    for nome, padrao in DEPARTAMENTOS:
        if re.search(padrao, t):
            return nome
    return "📦 Outros"


def texto_lista(itens: list[str], titulo: str = "🛒 Lista de compras") -> str:
    """A lista por departamento, com caixinha para marcar. Montada pelo código, sem IA."""
    grupos: dict[str, list[str]] = {}
    for i in itens_unicos(itens):
        grupos.setdefault(departamento(i), []).append(i)
    ordem = [n for n, _ in DEPARTAMENTOS] + ["📦 Outros"]
    linhas = [f"{titulo} ({sum(len(v) for v in grupos.values())} itens)"]
    for d in ordem:
        if d in grupos:
            linhas.append(f"\n{d}")
            linhas += [f"☐ {i[:1].upper()}{i[1:]}" for i in grupos[d]]
    linhas.append("\nComprou? Diga “tira ovos e banana da lista”. Para saber onde sai mais barato: "
                  "“em qual mercado é melhor eu ir?”")
    return "\n".join(linhas)

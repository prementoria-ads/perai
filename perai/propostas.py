"""Propostas e pricing por oportunidade (D-091, CRM de contas etapa 4).

Proposta comercial, proposta técnica e planilha de pricing (custo, impostos, lucro, margem) ficam
ligadas à conta e à oportunidade, com o arquivo de origem (D-040). A IA só lê o documento e devolve os
números como estão; quem confere é o código:
- quantidade × valor unitário de cada item e a soma dos itens contra o total;
- campos por preencher ("[preencher ...]") que sobraram no texto;
- pricing contra a proposta: o mesmo item com valor diferente, e o cliente do cabeçalho;
- validade (data + dias).
Valor não lido é "não informado", nunca zero. Comissão não é calculada aqui (decisão à parte).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from perai.despesas import brl
from perai.extracao import extrair_json

PARECE_PROPOSTA = re.compile(r"proposta comercial|business proposal|validade da proposta|proposta t[eé]cnica|"
                             r"pricing|lucro esperado|markup|custo cont[aá]bil", re.I)
PENDENTE = re.compile(r"\[\s*preencher[^\]]*\]|\[\s*(a definir|xx+|tbd)\s*\]", re.I)

SISTEMA_PROPOSTA = """Você lê um documento comercial de uma empresa que vende para clientes: proposta
comercial, proposta técnica ou planilha de pricing (formação de preço). Devolva só um objeto JSON com os
números EXATAMENTE como estão no documento (texto, com vírgula e ponto como impressos). Nada inventado;
o que não estiver no documento fica null.

{"tipo": "comercial" | "tecnica" | "pricing",
 "numero": str|null, "revisao": str|null, "cliente": str|null, "objeto": str|null,
 "data": "AAAA-MM-DD"|null, "validade_dias": número|null, "condicao_pagamento": str|null,
 "moeda_dolar": str|null,
 "itens": [{"codigo": str|null, "descricao": str, "quantidade": str|null, "unitario": str|null,
            "total": str|null, "recorrencia": "unico" | "mensal" | null, "meses": número|null,
            "custo": str|null, "impostos": str|null, "lucro": str|null, "margem_percentual": str|null}],
 "total_declarado": str|null, "mensal_declarado": str|null, "implantacao_declarada": str|null,
 "totais_pricing": {"preco_venda": str|null, "custo": str|null, "impostos": str|null,
                    "lucro": str|null, "margem_percentual": str|null},
 "quantitativos": [{"item": str, "quantidade": str}]}

Regras:
1. Proposta comercial: um item por linha da tabela de valores ("Valor total" é total_declarado). Serviço
   mensal: "unitario" = valor mensal, "total" = total do contrato, "recorrencia": "mensal", "meses".
   Instalação ou implantação cobrada à parte e não somada no total: "implantacao_declarada".
2. Planilha de pricing: um item por produto ou serviço com "total" = preço de venda (valor do produto ou
   nota fiscal), "custo" = custo contábil com custo financeiro, "impostos" = total de impostos, "lucro" =
   lucro contábil bruto e "margem_percentual"; "totais_pricing" com a linha de totais. "cliente" e "data"
   como estão no cabeçalho, mesmo que pareçam errados.
3. Proposta técnica: "quantitativos" com cada quantidade dimensionada (ativos, usuários, domínios, vCores,
   meses de suporte, turmas) como escrita; "itens" pode ficar vazio.
4. Não some, não arredonde e não corrija nada: a conferência é feita depois, pelo código."""


def num(v) -> Decimal | None:
    """"1.211.570,40", "R$ 7.945,46", "  -  " (vazio), 2, "6,0000"."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    t = str(v).replace("R$", "").replace("US$", "").replace("%", "").strip()
    m = re.search(r"-?\d[\d.,]*", t)
    if not m:
        return None
    t = m.group(0).rstrip(".,")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def centavos(v) -> int | None:
    d = num(v)
    return int((d * 100).quantize(Decimal(1))) if d is not None else None


def texto_do_arquivo(arquivo: Path) -> str:
    """Texto bruto, pelo código: pdftotext para PDF; CSV enxuto (sem colunas e linhas vazias)."""
    sufixo = arquivo.suffix.lower()
    if sufixo == ".pdf" and shutil.which("pdftotext"):
        try:
            r = subprocess.run(["pdftotext", "-layout", str(arquivo), "-"], capture_output=True, timeout=60)
            return r.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return ""
    if sufixo == ".csv":
        return enxugar_csv(arquivo.read_text(encoding="utf-8", errors="replace"))
    return ""


def enxugar_csv(texto: str) -> str:
    import csv
    import io
    linhas = []
    for linha in csv.reader(io.StringIO(texto)):
        celulas = [c.strip() for c in linha]
        if not any(c and c not in ("-",) for c in celulas):
            continue
        while celulas and not celulas[-1]:
            celulas.pop()
        linhas.append(" | ".join(celulas))
    # Linhas de item vazias (só número do item e zeros) não ajudam: tira as repetidas.
    saida, vistas = [], set()
    for l in linhas:
        chave = re.sub(r"\d+", "#", l)
        if chave in vistas and not re.search(r"[A-Za-z]{3,}", l.split("|", 3)[-1] if "|" in l else l):
            continue
        vistas.add(chave)
        saida.append(l)
    return "\n".join(saida)[:120_000]


def interpretar(resposta: str) -> dict | None:
    try:
        d = extrair_json(resposta)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("tipo") not in ("comercial", "tecnica", "pricing"):
        return None
    d["itens"] = [i for i in (d.get("itens") or []) if isinstance(i, dict) and str(i.get("descricao") or "").strip()]
    return d


def _brl(c: int | None) -> str:
    return brl(c) if c is not None else "não informado"


def conferir(d: dict, texto_bruto: str = "") -> list[str]:
    """Avisos da conferência feita pelo código (vazio = tudo bate)."""
    avisos: list[str] = []
    soma, faltou = 0, False
    for n, i in enumerate(d.get("itens") or [], start=1):
        q, u, t = num(i.get("quantidade")), num(i.get("unitario")), centavos(i.get("total"))
        if t is None:
            faltou = True
            continue
        soma += t
        if q is not None and u is not None and d["tipo"] != "pricing":
            esperado = int((q * u * 100).quantize(Decimal(1)))
            if esperado != t:
                tol = max(int(q), 1)   # arredondamento do unitário: até 1 centavo por unidade
                tipo = "arredondamento" if abs(esperado - t) <= tol else "conta não fecha"
                avisos.append(f"Item {n} ({i['descricao'][:40]}): {q:g} × {_brl(centavos(u))} = {_brl(esperado)}, "
                              f"mas o total do item é {_brl(t)} ({tipo}).")
    total = centavos(d.get("total_declarado")) if d["tipo"] != "pricing" else \
        centavos((d.get("totais_pricing") or {}).get("preco_venda"))
    if total is not None and d.get("itens") and not faltou and soma != total:
        avisos.append(f"A soma dos itens dá {_brl(soma)}, mas o total escrito é {_brl(total)} "
                      f"(diferença de {_brl(abs(soma - total))}).")
    if d["tipo"] == "pricing":
        tp = d.get("totais_pricing") or {}
        for campo, nome in (("custo", "custo"), ("lucro", "lucro")):
            itens_c = [centavos(i.get(campo)) for i in d.get("itens") or []]
            tot = centavos(tp.get(campo))
            if tot is not None and itens_c and None not in itens_c and abs(sum(itens_c) - tot) > len(itens_c):
                avisos.append(f"A soma do {nome} dos itens dá {_brl(sum(itens_c))}, mas o total é {_brl(tot)}.")
    pendentes = PENDENTE.findall(texto_bruto or "")
    if pendentes:
        avisos.append(f"Ficaram {len(pendentes)} campo(s) por preencher no texto (ex.: “[preencher …]”).")
    return avisos


def resumo_valores(d: dict) -> dict:
    """Valores em centavos para guardar e somar (comercial: total + implantação; pricing: totais)."""
    if d["tipo"] == "pricing":
        tp = d.get("totais_pricing") or {}
        venda = centavos(tp.get("preco_venda"))
        if venda is None and d.get("itens"):
            vals = [centavos(i.get("total")) for i in d["itens"]]
            venda = sum(vals) if None not in vals else None
        margem = num(tp.get("margem_percentual"))
        return {"valor_centavos": venda, "custo_centavos": centavos(tp.get("custo")),
                "impostos_centavos": centavos(tp.get("impostos")), "lucro_centavos": centavos(tp.get("lucro")),
                "margem": float(margem) if margem is not None else None}
    if d["tipo"] == "comercial":
        total = centavos(d.get("total_declarado"))
        if total is None and d.get("itens"):
            vals = [centavos(i.get("total")) for i in d["itens"]]
            total = sum(vals) if None not in vals else None
        impl = centavos(d.get("implantacao_declarada"))
        if total is not None and impl is not None:
            total += impl
        return {"valor_centavos": total}
    return {}


def validade(d: dict) -> str | None:
    if d.get("data") and d.get("validade_dias"):
        try:
            return (date.fromisoformat(d["data"]) + timedelta(days=int(d["validade_dias"]))).isoformat()
        except (ValueError, TypeError):
            return None
    return None


def cruzar_pricing(pricing: dict, comerciais: list[dict]) -> tuple[dict | None, list[str]]:
    """A proposta comercial que bate com a planilha (pelos valores dos itens) e as diferenças."""
    venda_itens = [centavos(i.get("total")) for i in pricing.get("itens") or []]
    venda_itens = [v for v in venda_itens if v]
    melhor, nota = None, 0
    for c in comerciais:
        totais = [centavos(i.get("total")) for i in (c["dados"].get("itens") or [])]
        totais += [centavos(c["dados"].get("implantacao_declarada"))]
        totais = [t for t in totais if t]
        bate = sum(1 for v in venda_itens if any(abs(v - t) <= max(100, t // 1000) for t in totais))
        if bate > nota:
            melhor, nota = c, bate
    if not melhor or nota == 0:
        return None, []
    avisos = []
    totais = [(i.get("descricao") or "", centavos(i.get("total"))) for i in melhor["dados"].get("itens") or []]
    totais.append(("implantação", centavos(melhor["dados"].get("implantacao_declarada"))))
    for i in pricing.get("itens") or []:
        v = centavos(i.get("total"))
        if not v:
            continue
        for desc, t in totais:
            if t and v != t and abs(v - t) <= max(100, t // 1000):
                tipo = "arredondamento" if abs(v - t) <= 10 else "confira qual vale"
                avisos.append(f"{i['descricao'][:40]}: {_brl(v)} na planilha e {_brl(t)} na proposta "
                              f"{melhor.get('numero') or ''} ({tipo}).")
    return melhor, avisos


def linha(codigo: str, p: dict, conta: str | None) -> str:
    """Item citável na consulta: [R1] (proposta comercial nº 8896; conta X; 10/11/2026) ..."""
    tipo = {"comercial": "proposta comercial", "tecnica": "proposta técnica", "pricing": "pricing"}[p["tipo"]]
    partes = [f"[{codigo}] ({tipo}{' nº ' + p['numero'] if p.get('numero') else ''}; conta {conta or 'sem conta'}"
              f"{'; ' + p['data'] if p.get('data') else ''}{'; válida até ' + p['validade'] if p.get('validade') else ''})"]
    if p.get("valor_centavos") is not None:
        partes.append(f"valor {_brl(p['valor_centavos'])}")
    if p["tipo"] == "pricing":
        partes.append(f"custo {_brl(p.get('custo_centavos'))}, impostos {_brl(p.get('impostos_centavos'))}, "
                      f"lucro bruto previsto {_brl(p.get('lucro_centavos'))}"
                      + (f" ({p['margem']:.2f}%)" if p.get("margem") is not None else ""))
    if p.get("conferencia"):
        partes.append("conferência: " + " ".join(p["conferencia"][:6]))
    return " · ".join(partes)

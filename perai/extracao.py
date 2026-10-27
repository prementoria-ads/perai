"""Extração: de uma mensagem para entidades, registros e pendências.

As regras do prompt vêm das decisões aprovadas: nada sem fonte (D-040),
natureza explícita, quem disse e opinião separada de fato (D-044), quatro
contextos (D-045), dados sensíveis marcados e lembrete só com aceite (D-007).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

NATUREZAS = {"confirmado", "intencao", "hipotese", "opiniao"}
CONTEXTOS = {"familia", "profissional", "projetos_proprios", "igreja"}
TIPOS_ENTIDADE = {"pessoa", "organizacao", "projeto", "evento", "local", "assunto"}

SISTEMA_EXTRACAO = """Você é o módulo de memória do Peraí, um assistente pessoal brasileiro.
Recebe uma mensagem do usuário (texto, transcrição de áudio ou texto lido de um print ou foto)
e devolve SOMENTE um objeto JSON com o que ela contém.

Regras, em ordem de importância:
1. Nunca invente. Registre só o que está na mensagem. Não complete nomes, sobrenomes,
   empresas, valores ou datas que não foram ditos. Não expanda siglas sem fonte.
2. Natureza de cada registro: "confirmado" (fato), "intencao" (algo que alguém pretende ou
   combinou fazer), "hipotese" (ideia, possibilidade, "talvez", "acredito que") ou
   "opiniao" (julgamento de alguém sobre algo ou alguém). Opinião nunca vira fato.
3. Quem disse: "eu" quando é o próprio usuário; senão, o nome de quem disse, como aparece.
   Em prints de conversa, cada fala tem um autor.
4. Pendência: algo a fazer. "responsavel" é "eu" quando o usuário fará, ou "aguardando"
   quando ele espera outra pessoa (preencha "quem"). Pedido de outra pessoa que o usuário
   não assumiu não é pendência dele: registre como fato.
5. Datas: converta datas relativas ("sexta", "amanhã") usando a data da situação. Guarde
   também como foi dito. Prazo vago ("antes de março") fica vago: "prazo" null.
6. Contexto de cada entidade: "familia", "profissional" (trabalho na L8 Group),
   "projetos_proprios" (negócios pessoais do usuário fora da L8) ou "igreja". Se não der
   para saber, use null.
7. "sensivel": true para saúde e finanças pessoais.
8. Mensagem ambígua ("falar com ele sobre aquilo"): registre com confiança baixa e anote
   a dúvida em "duvidas". Nunca adivinhe quem é "ele".
9. Correção ("não foi o Wagner, foi o Marcos"): registre o fato corrigido e preencha
   "corrige" com o que está sendo corrigido.
10. Fotos: identifique pessoas só pela legenda do usuário, nunca pela aparência.

Formato:
{"entidades": [{"nome": str, "tipo": "pessoa|organizacao|projeto|evento|local|assunto",
                "contexto": str|null, "descricao": str|null}],
 "registros": [{"conteudo": str, "natureza": str, "dito_por": str, "data_fato": "AAAA-MM-DD"|null,
                "entidades": [nomes], "confianca": 0.0-1.0, "sensivel": bool, "corrige": str|null}],
 "pendencias": [{"descricao": str, "responsavel": "eu|aguardando", "quem": str|null,
                 "prazo": "AAAA-MM-DD"|null, "prazo_como_dito": str|null}],
 "duvidas": [str]}"""


@dataclass
class Entidade:
    nome: str
    tipo: str
    contexto: str | None = None
    descricao: str | None = None


@dataclass
class Registro:
    id: str
    mensagem: str          # Nº da mensagem de origem (fonte obrigatória)
    conteudo: str
    natureza: str
    dito_por: str
    data_fato: str | None
    entidades: list[str]
    confianca: float
    sensivel: bool
    corrige: str | None = None


@dataclass
class Pendencia:
    id: str
    mensagem: str
    descricao: str
    responsavel: str
    quem: str | None
    prazo: str | None
    prazo_como_dito: str | None


@dataclass
class Extracao:
    mensagem: str
    entidades: list[Entidade] = field(default_factory=list)
    registros: list[Registro] = field(default_factory=list)
    pendencias: list[Pendencia] = field(default_factory=list)
    duvidas: list[str] = field(default_factory=list)
    json_valido: bool = True
    problemas: list[str] = field(default_factory=list)


def montar_pedido(numero: str, texto: str, data_situacao: str | None, tipo: str,
                  entidades_conhecidas: list[str]) -> str:
    partes = [f"Mensagem nº {numero} ({tipo})."]
    partes.append(f"Data da situação: {data_situacao or 'não informada'}.")
    if entidades_conhecidas:
        partes.append(
            "Entidades já registradas (reuse o mesmo nome se for a mesma): "
            + "; ".join(sorted(set(entidades_conhecidas))[:200])
        )
    partes.append("Conteúdo:\n" + texto.strip())
    return "\n".join(partes)


def extrair_json(texto: str) -> dict:
    """Aceita JSON puro, cercado por ``` ou com texto antes e depois."""
    limpo = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(limpo)
    except json.JSONDecodeError:
        inicio, fim = limpo.find("{"), limpo.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise
        return json.loads(limpo[inicio : fim + 1])


def _texto(valor) -> str | None:
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor or None


def interpretar(numero: str, resposta: str) -> Extracao:
    resultado = Extracao(mensagem=numero)
    try:
        dados = extrair_json(resposta)
    except (json.JSONDecodeError, ValueError):
        resultado.json_valido = False
        resultado.problemas.append("resposta não é JSON")
        return resultado
    if not isinstance(dados, dict):
        resultado.json_valido = False
        resultado.problemas.append("JSON não é um objeto")
        return resultado

    for e in dados.get("entidades") or []:
        if not isinstance(e, dict) or not _texto(e.get("nome")):
            resultado.problemas.append("entidade sem nome")
            continue
        tipo = e.get("tipo") if e.get("tipo") in TIPOS_ENTIDADE else "assunto"
        contexto = e.get("contexto") if e.get("contexto") in CONTEXTOS else None
        resultado.entidades.append(Entidade(_texto(e["nome"]), tipo, contexto, _texto(e.get("descricao"))))

    for i, r in enumerate(dados.get("registros") or [], start=1):
        if not isinstance(r, dict) or not _texto(r.get("conteudo")):
            resultado.problemas.append("registro sem conteúdo")
            continue
        natureza = r.get("natureza")
        if natureza not in NATUREZAS:
            resultado.problemas.append(f"natureza inválida: {natureza!r}")
            natureza = "hipotese"  # na dúvida, nunca vira fato
        try:
            confianca = min(max(float(r.get("confianca", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            confianca = 0.5
        resultado.registros.append(
            Registro(
                id=f"R{numero}-{i}",
                mensagem=numero,
                conteudo=_texto(r["conteudo"]),
                natureza=natureza,
                dito_por=_texto(r.get("dito_por")) or "eu",
                data_fato=_texto(r.get("data_fato")),
                entidades=[str(n) for n in (r.get("entidades") or []) if _texto(n)],
                confianca=confianca,
                sensivel=bool(r.get("sensivel", False)),
                corrige=_texto(r.get("corrige")),
            )
        )

    for i, p in enumerate(dados.get("pendencias") or [], start=1):
        if not isinstance(p, dict) or not _texto(p.get("descricao")):
            resultado.problemas.append("pendência sem descrição")
            continue
        responsavel = p.get("responsavel") if p.get("responsavel") in ("eu", "aguardando") else "eu"
        resultado.pendencias.append(
            Pendencia(
                id=f"P{numero}-{i}",
                mensagem=numero,
                descricao=_texto(p["descricao"]),
                responsavel=responsavel,
                quem=_texto(p.get("quem")),
                prazo=_texto(p.get("prazo")),
                prazo_como_dito=_texto(p.get("prazo_como_dito")),
            )
        )

    resultado.duvidas = [str(d) for d in (dados.get("duvidas") or []) if _texto(d)]
    return resultado

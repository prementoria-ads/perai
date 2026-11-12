"""CRM de contas, etapas 8 a 13 (D-095 a D-100).

A IA só lê a mensagem e preenche o objeto "crm" da extração (atividades, sinais, maturidade, mapa,
MEDDPICC, parceiros de projeto, fatores de risco e segmentos). Tudo o que é conta — semáforo de saúde,
placar MEDDPICC, grade de ofertas, nível de maturidade, exposição e concentração — é feito aqui, pelo
código, a partir dos registros com fonte (D-040). O que não foi dito fica "não informado" ou
"não avaliado", nunca zero. Opinião de terceiro carrega quem disse (D-044).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from perai.contas import mesma_conta, normalizar
from perai.despesas import brl

# -- vocabulário ---------------------------------------------------------------------------------

TIPOS = {"call": "Call", "visita": "Visita", "reuniao": "Reunião", "evento": "Evento", "email": "E-mail",
         "apresentacao": "Apresentação", "workshop": "Workshop"}
FASES = {"prospeccao": "prospecção", "relacionamento": "relacionamento", "negociacao": "negociação",
         "pos_venda": "pós-venda", "expansao": "expansão"}
PAPEIS = ("decisor", "defensor", "influenciador", "tecnico", "financeiro", "indicador", "outro")
CADENCIA_PADRAO = {"prospeccao": 7, "negociacao": 7, "relacionamento": 30, "pos_venda": 30, "expansao": 21,
                   "parceiro": 30}
TIPOS_SINAL = {"entrega": "entrega", "atraso_pagamento": "atraso de pagamento", "troca_decisor": "troca de decisor",
               "corte_orcamento": "corte de orçamento", "fusao_venda": "fusão ou venda", "mandato": "troca de mandato",
               "elogio": "elogio", "outro": "outro"}
SINAIS_RUINS = {"entrega", "atraso_pagamento", "troca_decisor", "corte_orcamento", "fusao_venda", "mandato", "outro"}
PAPEIS_PARCEIRO = {"indicou": "indicou", "entregou": "entrega", "revendeu": "revendeu", "coentregou": "entrega junto"}
SEGMENTOS = ("apostas", "varejo", "prefeitura", "legislativo", "governo_estadual", "governo_federal", "seguranca_publica",
             "saude", "educacao", "industria", "energia", "telecom", "financeiro", "agro", "logistica", "tecnologia",
             "utilities", "outro")
DOMINIOS = {"rede": "Rede e conectividade", "datacenter": "Data center e energia da TI",
            "seguranca_fisica": "Segurança física e vídeo", "ciberseguranca": "Cibersegurança", "nuvem": "Nuvem",
            "governanca": "Governança de TI", "energia": "Energia (geração própria)"}
NIVEIS = {1: "inexistente ou improvisado", 2: "básico", 3: "gerenciado", 4: "otimizado"}
SITUACOES_MAPA = {"tem_conosco": "tem conosco", "em_negociacao": "em negociação", "concorrente": "tem com concorrente",
                  "sem_solucao": "sem solução", "nao_se_aplica": "não se aplica", "nao_avaliado": "não avaliado"}

MEDDPICC = {  # elemento: (rótulo, pergunta, ação quando falta)
    "metrics": ("Metrics", "Que ganho mensurável o cliente espera?", "pedir a meta ou o número que o projeto deve mover"),
    "economic_buyer": ("Economic buyer", "Quem assina e libera o dinheiro?", "pedir a reunião com quem assina"),
    "decision_criteria": ("Decision criteria", "Como vão comparar as propostas?",
                          "perguntar os critérios do edital ou da compra"),
    "decision_process": ("Decision process", "Quais etapas até a decisão?", "mapear as etapas e as datas da decisão"),
    "paper_process": ("Paper process", "Contrato, jurídico, licitação, empenho",
                      "perguntar a modalidade de compra (pregão, ata, dispensa, cooperação)"),
    "identify_pain": ("Identify pain", "Que dor justifica o projeto agora?", "levar perguntas de implicação (SPIN)"),
    "champion": ("Champion", "Quem defende a gente lá dentro?", "identificar e fortalecer um defensor"),
    "competition": ("Competition", "Quem mais está na disputa?", "perguntar ao defensor quem mais concorre"),
}

SPIN_PERGUNTAS = {
    "problema": ["O que mais dá trabalho hoje nessa área?", "Onde isso já falhou ou atrasou?"],
    "implicacao": ["Quando isso falha, quanto custa (tempo, multa, imagem)?",
                   "Quem sente primeiro quando isso dá errado?"],
    "necessidade": ["Se isso estivesse resolvido, o que mudaria para vocês?",
                    "Qual resultado faria esse projeto valer a pena?"],
}

# Catálogo da apresentação L8 Group 2026 + pacotes para governo. Fica em configuração (chave
# "catalogo_ofertas") quando o fundador mudar; este é o ponto de partida.
CATALOGO_PADRAO = [
    {"chave": "rede", "unidade": "Tecnologia da Informação", "nome": "Rede, Wi-Fi e conectividade", "dominio": "rede",
     "palavras": ["switch", "roteador", "wi-fi", "wifi", "gpon", "dwdm", "cgnat", "dns", "rede", "huawei", "ruckus",
                  "extreme", "tp-link", "conectividade"]},
    {"chave": "balanceamento", "unidade": "Tecnologia da Informação", "nome": "Balanceadores e entrega de aplicações",
     "dominio": "rede", "palavras": ["balanceador", "a10", "akamai", "load balanc"]},
    {"chave": "storage", "unidade": "Tecnologia da Informação", "nome": "Storage", "dominio": "datacenter",
     "palavras": ["storage", "armazenamento de dados"]},
    {"chave": "servicos_gerenciados", "unidade": "Tecnologia da Informação", "nome": "Serviços gerenciados",
     "dominio": "governanca", "palavras": ["serviço gerenciado", "servicos gerenciados", "managed"]},
    {"chave": "videomonitoramento", "unidade": "Segurança Eletrônica", "nome": "Videomonitoramento e analíticos",
     "dominio": "seguranca_fisica", "palavras": ["câmera", "camera", "vms", "videomonitoramento", "cftv", "analítico",
                                                 "reconhecimento facial", "lpr", "leitura de placa", "milestone",
                                                 "hikvision", "axis"]},
    {"chave": "controle_acesso", "unidade": "Segurança Eletrônica", "nome": "Controle de acesso",
     "dominio": "seguranca_fisica", "palavras": ["controle de acesso", "catraca", "biometria", "cancela", "lenel"]},
    {"chave": "incendio", "unidade": "Segurança Eletrônica", "nome": "Detecção e combate a incêndio",
     "dominio": "seguranca_fisica", "palavras": ["incêndio", "incendio", "hssd", "simplex"]},
    {"chave": "sonorizacao", "unidade": "Segurança Eletrônica", "nome": "Sonorização", "dominio": "seguranca_fisica",
     "palavras": ["sonorização", "sonorizacao", "som ambiente"]},
    {"chave": "mobilidade", "unidade": "Segurança Eletrônica", "nome": "Bodycams e câmeras embarcadas",
     "dominio": "seguranca_fisica", "palavras": ["bodycam", "câmera corporal", "camera corporal", "embarcada", "mdvr"]},
    {"chave": "seguranca_publica", "unidade": "Segurança Eletrônica",
     "nome": "Segurança pública (evidências, viaturas, postes, radares)", "dominio": "seguranca_fisica",
     "palavras": ["evidência", "evidencias", "cadeia de custódia", "viatura", "poste inteligente", "radar",
                  "muralha digital"]},
    {"chave": "datacenter", "unidade": "Data Center", "nome": "Data center (turn key, pré-fabricado, energia, clima)",
     "dominio": "datacenter", "palavras": ["data center", "datacenter", "turn key", "cabeamento", "nobreak", "ups",
                                           "gerador", "climatização", "climatizacao", "rack", "pdu", "ats",
                                           "pré-fabricado", "micro data center"]},
    {"chave": "soc", "unidade": "Cibersegurança", "nome": "SOC 24x7 (MSSP)", "dominio": "ciberseguranca",
     "palavras": ["soc", "mssp", "monitoramento de segurança"]},
    {"chave": "firewall", "unidade": "Cibersegurança", "nome": "Segurança de rede (Check Point)",
     "dominio": "ciberseguranca", "palavras": ["firewall", "check point", "checkpoint", "ngfw"]},
    {"chave": "nuvem_waf", "unidade": "Cibersegurança", "nome": "Nuvem, WAF e DDoS (Cloudflare, A10)",
     "dominio": "nuvem", "palavras": ["cloudflare", "waf", "ddos", "zero trust", "sase"]},
    {"chave": "endpoint", "unidade": "Cibersegurança", "nome": "Endpoint (Trend)", "dominio": "ciberseguranca",
     "palavras": ["endpoint", "antivírus", "antivirus", "trend", "edr", "xdr"]},
    {"chave": "identidades", "unidade": "Cibersegurança", "nome": "Identidades e acessos (CyberArk)",
     "dominio": "ciberseguranca", "palavras": ["cyberark", "identidade", "pam", "acesso privilegiado"]},
    {"chave": "vulnerabilidades", "unidade": "Cibersegurança", "nome": "Gestão de vulnerabilidades (Qualys)",
     "dominio": "ciberseguranca", "palavras": ["qualys", "vulnerabilidade", "vmdr"]},
    {"chave": "siem", "unidade": "Cibersegurança", "nome": "SIEM e visibilidade (Elastic)", "dominio": "ciberseguranca",
     "palavras": ["siem", "elastic", "correlação de eventos", "logs"]},
    {"chave": "pentest", "unidade": "Cibersegurança", "nome": "Pentest e Red Team", "dominio": "ciberseguranca",
     "palavras": ["pentest", "red team", "teste de intrusão", "security assessment"]},
    {"chave": "solar", "unidade": "Energia", "nome": "Usina solar (EPC, O&M, engenharia do proprietário)",
     "dominio": "energia", "palavras": ["solar", "usina", "fotovolt", "epc", "o&m", "l8 energy"]},
    {"chave": "cidade_protegida", "unidade": "Pacotes para governo", "nome": "Cidade Protegida", "dominio": None,
     "palavras": ["cidade protegida"]},
    {"chave": "prefeitura_digital", "unidade": "Pacotes para governo", "nome": "Prefeitura Digital Segura",
     "dominio": None, "palavras": ["prefeitura digital"]},
    {"chave": "legislativo_digital", "unidade": "Pacotes para governo", "nome": "Legislativo Digital", "dominio": None,
     "palavras": ["legislativo digital", "modernização do legislativo", "modernizacao do legislativo"]},
    {"chave": "saude_conectada", "unidade": "Pacotes para governo", "nome": "Saúde Conectada", "dominio": None,
     "palavras": ["saúde conectada", "saude conectada"]},
    {"chave": "gestao_publica", "unidade": "Pacotes para governo", "nome": "Gestão Pública Eficiente", "dominio": None,
     "palavras": ["gestão pública eficiente", "gestao publica eficiente"]},
]

# -- extração -----------------------------------------------------------------------------------

REGRA_EXTRACAO = """34. CRM de contas (D-095 a D-100) — objeto "crm", só com o que a mensagem diz:
   - Interação com um cliente ou parceiro (call, visita, reunião, evento, e-mail, apresentação,
     workshop), feita ou marcada → "atividades": "conta", "tipo", "fase" (prospeccao se ainda não é
     cliente; negociacao com proposta na mesa; relacionamento com cliente sem negócio em curso;
     pos_venda durante ou depois da entrega; expansao ao levar outra oferta a um cliente), "data",
     "situacao" ("feita" ou "agendada"), "resumo" curto, "proximo_passo" se combinado, "participantes"
     (nome e papel dito: decisor, defensor, influenciador, tecnico, financeiro, indicador), "pelo_parceiro"
     (nome do parceiro, se foi ele quem esteve com o cliente), "oportunidade" (código O1, O2... se for
     dela) e "spin": o que foi levantado na conversa como situacao, problema, implicacao ou necessidade
     do cliente (frases curtas; só o que foi dito). Marcar compromisso continua em "compromissos".
   - Fato que afeta a conta ("a Altatech atrasou a instalação", "trocaram o secretário", "cortaram o
     orçamento", "estão atrasando o pagamento", "a empresa foi vendida") → "sinais" com "conta", "tipo"
     (entrega, atraso_pagamento, troca_decisor, corte_orcamento, fusao_venda, mandato, elogio, outro),
     "descricao", "dito_por" e "resolvido": true quando a mensagem diz que um problema já se resolveu.
   - Nível de TI do cliente dito ou mostrado ("a prefeitura não tem firewall", "o SOC deles é maduro")
     → "maturidade" com "conta", "dominio" (rede, datacenter, seguranca_fisica, ciberseguranca, nuvem,
     governanca, energia), "nivel" 1 (inexistente) a 4 (otimizado), "nota" e "dito_por".
   - O que o cliente já tem ou não tem ("eles usam Fortinet", "não têm câmeras", "já compraram o
     SOC conosco") → "mapa" com "conta", "oferta" (como dita), "situacao" (tem_conosco, em_negociacao,
     concorrente, sem_solucao, nao_se_aplica) e "concorrente" quando houver.
   - Qualificação de uma oportunidade de "Oportunidades abertas" → "meddpicc" com "oportunidade" (O1...),
     "elemento" (metrics, economic_buyer, decision_criteria, decision_process, paper_process,
     identify_pain, champion, competition), "nota" (1 = citado por nós ou por terceiro; 2 = o próprio
     cliente confirmou), "evidencia" e "dito_por". Nunca dê nota pelo que ninguém disse.
   - Parceiro num projeto ("a MW Consulting vai entregar o projeto da Câmara") → "parceiros_projeto" com
     "projeto", "parceiro" e "papel" (indicou, entregou, revendeu, coentregou).
   - Fator externo que pode fazer clientes sumirem ou crescerem (lei, decisão judicial, crise, tendência
     de consumo) → "fatores" com "titulo", "direcao" (ameaca ou oportunidade), "segmentos" (da lista
     abaixo), "situacao" (observacao, em_vigor, revertido), "intensidade" (baixa, media, alta) e
     "fonte_url" se veio um link. Notícia encaminhada também conta, com a fonte.
   - Ramo de um cliente dito ("a Betnova é de apostas") → "segmentos" com "conta" e "segmento":
     apostas, varejo, prefeitura, legislativo, governo_estadual, governo_federal, seguranca_publica,
     saude, educacao, industria, energia, telecom, financeiro, agro, logistica, tecnologia, utilities, outro.
   - Plano de uma conta dito pelo usuário ("o plano da Câmara para 2027 é renovar o SOC e vender o
     Legislativo Digital, meta de 1 milhão, revisar em março"; "a Coamo é conta-chave") → "planos" com
     "conta", "ano", "objetivos" (frases curtas), "iniciativas" (ofertas ou ações), "meta" (valor como
     dito), "revisao" (data) e "chave": true quando ele disser que a conta é estratégica ou chave.
     Só do próprio usuário, nunca de texto de terceiros.
   Nada de nota, nível, saúde ou risco calculado por você: o código calcula."""

FORMATO_EXTRACAO = """ "crm": {"atividades": [{"conta": str, "tipo": str, "fase": str|null, "data": "AAAA-MM-DD"|null,
                   "situacao": "feita"|"agendada", "resumo": str, "proximo_passo": str|null,
                   "participantes": [{"nome": str, "papel": str|null}], "pelo_parceiro": str|null,
                   "oportunidade": "O1"|null, "spin": {"situacao": [str], "problema": [str],
                   "implicacao": [str], "necessidade": [str]}|null}],
         "sinais": [{"conta": str, "tipo": str, "descricao": str, "dito_por": str, "resolvido": bool}],
         "maturidade": [{"conta": str, "dominio": str, "nivel": 1-4, "nota": str, "dito_por": str}],
         "mapa": [{"conta": str, "oferta": str, "situacao": str, "concorrente": str|null}],
         "meddpicc": [{"oportunidade": "O1", "elemento": str, "nota": 1|2, "evidencia": str, "dito_por": str}],
         "parceiros_projeto": [{"projeto": str, "parceiro": str, "papel": str}],
         "fatores": [{"titulo": str, "direcao": "ameaca"|"oportunidade", "segmentos": [str], "situacao": str,
                      "intensidade": str, "fonte_url": str|null}],
         "segmentos": [{"conta": str, "segmento": str}],
         "planos": [{"conta": str, "ano": número|null, "objetivos": [str], "iniciativas": [str],
                     "meta": str|null, "revisao": "AAAA-MM-DD"|null, "chave": bool|null}]},"""


def _t(v, n: int = 400) -> str:
    return str(v).strip()[:n] if isinstance(v, (str, int, float)) and str(v).strip() else ""


def _data(v) -> str | None:
    s = _t(v, 10)
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def _chave(v) -> str:
    s = unicodedata.normalize("NFKD", _t(v, 60).lower())
    return re.sub(r"[^a-z0-9]+", "_", "".join(c for c in s if not unicodedata.combining(c))).strip("_")


def _de(v, opcoes, padrao=None):
    k = _chave(v)
    return k if k in opcoes else padrao


def _codigo_o(v) -> str | None:
    s = _t(v, 10).strip("[] ").upper()
    return s if re.fullmatch(r"O\d+", s) else None


def segmento(v) -> str | None:
    k = _chave(v)
    apelidos = {"bets": "apostas", "bet": "apostas", "jogos": "apostas", "camara": "legislativo",
                "camara_municipal": "legislativo", "assembleia": "legislativo", "municipio": "prefeitura",
                "governo_municipal": "prefeitura", "estado": "governo_estadual", "hospital": "saude",
                "comercio": "varejo", "supermercado": "varejo", "banco": "financeiro", "cooperativa": "agro",
                "universidade": "educacao", "escola": "educacao", "operadora": "telecom"}
    k = apelidos.get(k, k)
    return k if k in SEGMENTOS else None


def interpretar(dados) -> dict:
    """Limpa o objeto "crm" vindo do modelo; o que não fecha com o formato fica de fora."""
    saida = {k: [] for k in ("atividades", "sinais", "maturidade", "mapa", "meddpicc", "parceiros_projeto",
                             "fatores", "segmentos", "planos")}
    if not isinstance(dados, dict):
        return saida
    for a in dados.get("atividades") or []:
        if not isinstance(a, dict) or not _t(a.get("conta")):
            continue
        tipo = _de(a.get("tipo"), TIPOS)
        if tipo is None:
            continue
        part = []
        for p in a.get("participantes") or []:
            if isinstance(p, dict) and _t(p.get("nome")):
                part.append({"nome": _t(p["nome"], 80), "papel": _de(p.get("papel"), PAPEIS)})
        spin = None
        if isinstance(a.get("spin"), dict):
            spin = {k: [_t(x, 200) for x in (a["spin"].get(k) or []) if _t(x)][:6]
                    for k in ("situacao", "problema", "implicacao", "necessidade")}
            spin = spin if any(spin.values()) else None
        saida["atividades"].append({
            "conta": _t(a["conta"], 120), "tipo": tipo, "fase": _de(a.get("fase"), FASES),
            "data": _data(a.get("data")), "situacao": "agendada" if _chave(a.get("situacao")) == "agendada" else "feita",
            "resumo": _t(a.get("resumo"), 600) or None, "proximo_passo": _t(a.get("proximo_passo"), 300) or None,
            "participantes": part[:12], "pelo_parceiro": _t(a.get("pelo_parceiro"), 120) or None,
            "oportunidade": _codigo_o(a.get("oportunidade")), "spin": spin})
    for s in dados.get("sinais") or []:
        if isinstance(s, dict) and _t(s.get("conta")) and _t(s.get("descricao")):
            saida["sinais"].append({"conta": _t(s["conta"], 120), "tipo": _de(s.get("tipo"), TIPOS_SINAL, "outro"),
                                    "descricao": _t(s["descricao"], 400), "dito_por": _t(s.get("dito_por"), 80) or "eu",
                                    "resolvido": s.get("resolvido") is True})
    for m in dados.get("maturidade") or []:
        if not isinstance(m, dict) or not _t(m.get("conta")):
            continue
        dom = _de(m.get("dominio"), DOMINIOS)
        try:
            nivel = int(m.get("nivel"))
        except (TypeError, ValueError):
            continue
        if dom and 1 <= nivel <= 4:
            saida["maturidade"].append({"conta": _t(m["conta"], 120), "dominio": dom, "nivel": nivel,
                                        "nota": _t(m.get("nota"), 300) or None,
                                        "dito_por": _t(m.get("dito_por"), 80) or "eu"})
    for m in dados.get("mapa") or []:
        sit = _de(m.get("situacao"), SITUACOES_MAPA) if isinstance(m, dict) else None
        if sit and sit != "nao_avaliado" and _t(m.get("conta")) and _t(m.get("oferta")):
            saida["mapa"].append({"conta": _t(m["conta"], 120), "oferta": _t(m["oferta"], 120), "situacao": sit,
                                  "concorrente": _t(m.get("concorrente"), 80) or None,
                                  "dito_por": _t(m.get("dito_por"), 80) or "eu"})
    for m in dados.get("meddpicc") or []:
        if not isinstance(m, dict):
            continue
        el, cod = _de(m.get("elemento"), MEDDPICC), _codigo_o(m.get("oportunidade"))
        try:
            nota = int(m.get("nota"))
        except (TypeError, ValueError):
            continue
        if el and cod and nota in (1, 2) and _t(m.get("evidencia")):
            saida["meddpicc"].append({"oportunidade": cod, "elemento": el, "nota": nota,
                                      "evidencia": _t(m["evidencia"], 300), "dito_por": _t(m.get("dito_por"), 80) or "eu"})
    for p in dados.get("parceiros_projeto") or []:
        papel = _de(p.get("papel"), PAPEIS_PARCEIRO) if isinstance(p, dict) else None
        if papel and _t(p.get("projeto")) and _t(p.get("parceiro")):
            saida["parceiros_projeto"].append({"projeto": _t(p["projeto"], 160), "parceiro": _t(p["parceiro"], 120),
                                               "papel": papel})
    for f in dados.get("fatores") or []:
        if not isinstance(f, dict) or not _t(f.get("titulo")):
            continue
        segs = [s for s in (segmento(x) for x in (f.get("segmentos") or [])) if s]
        url = _t(f.get("fonte_url"), 500)
        saida["fatores"].append({"titulo": _t(f["titulo"], 200),
                                 "direcao": "oportunidade" if _chave(f.get("direcao")) == "oportunidade" else "ameaca",
                                 "segmentos": sorted(set(segs)),
                                 "situacao": _de(f.get("situacao"), ("observacao", "em_vigor", "revertido"), "observacao"),
                                 "intensidade": _de(f.get("intensidade"), ("baixa", "media", "alta"), "media"),
                                 "fonte_url": url if url.startswith("http") else None})
    for s in dados.get("segmentos") or []:
        seg = segmento(s.get("segmento")) if isinstance(s, dict) else None
        if seg and _t(s.get("conta")):
            saida["segmentos"].append({"conta": _t(s["conta"], 120), "segmento": seg})
    for p in dados.get("planos") or []:
        if not isinstance(p, dict) or not _t(p.get("conta")):
            continue
        try:
            ano = int(p.get("ano")) if p.get("ano") is not None else None
        except (TypeError, ValueError):
            ano = None
        plano = {"conta": _t(p["conta"], 120), "ano": ano if ano and 2000 <= ano <= 2100 else None,
                 "objetivos": [_t(x, 200) for x in (p.get("objetivos") or []) if _t(x)][:10],
                 "iniciativas": [_t(x, 200) for x in (p.get("iniciativas") or []) if _t(x)][:15],
                 "meta_centavos": valor_dito(p.get("meta")), "revisao": _data(p.get("revisao")),
                 "chave": p.get("chave") if isinstance(p.get("chave"), bool) else None}
        if plano["objetivos"] or plano["iniciativas"] or plano["meta_centavos"] or plano["revisao"] or plano["chave"] is not None:
            saida["planos"].append(plano)
    return saida


def valor_dito(v) -> int | None:
    """"1 milhão", "R$ 500 mil", "800000" → centavos; o que não for valor fica None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v * 100)) if v > 0 else None
    from perai.propostas import centavos
    from perai.resultado import valor_da_pergunta
    t = str(v)
    c = valor_da_pergunta(t) or valor_da_pergunta("R$ " + t.strip()) if re.search(r"\d", t) else None
    if c is None:
        c = centavos(t)
    return c if c and c > 0 else None


def vazio(crm: dict | None) -> bool:
    return not crm or not any(crm.values())


# -- utilidades ---------------------------------------------------------------------------------

def _dia(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}" if iso else "sem data"


def dia_local(ts) -> str:
    """Data no horário de Brasília de um momento guardado (UTC no repositório em memória, com fuso no
    PostgreSQL). Às 21h de São Paulo já é o dia seguinte em UTC."""
    if not ts:
        return ""
    t = str(ts)
    if len(t) <= 10:
        return t
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return t[:10]
    if d.tzinfo is None:
        return d.date().isoformat()
    return d.astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def _dias(de: str, ate: str) -> int:
    return (date.fromisoformat(ate[:10]) - date.fromisoformat(de[:10])).days


def ultima_por(linhas: list[dict], *chaves: str) -> dict[tuple, dict]:
    """A linha mais recente de cada combinação de chaves (as listas vêm em ordem de criação)."""
    saida: dict[tuple, dict] = {}
    for x in linhas:
        saida[tuple(x.get(k) for k in chaves)] = x
    return saida


def cadencia(cfg: dict | None) -> dict:
    c = dict(CADENCIA_PADRAO)
    for k, v in (cfg or {}).items():
        if k in c and isinstance(v, int) and 1 <= v <= 365:
            c[k] = v
    return c


def aplicar_cadencia(cfg: dict | None, args: list[str]) -> tuple[dict, str | None]:
    """/cadencia prospeccao 10 → muda; devolve (config nova, erro)."""
    c = cadencia(cfg)
    if len(args) != 2:
        return c, "Use: /cadencia <fase> <dias>. Ex.: /cadencia relacionamento 45"
    fase = {"pos-venda": "pos_venda", "posvenda": "pos_venda"}.get(args[0].lower(), _chave(args[0]))
    if fase not in c:
        return c, "Fases: prospeccao, negociacao, relacionamento, pos_venda, expansao, parceiro."
    try:
        dias = int(args[1])
    except ValueError:
        return c, "Os dias precisam ser um número. Ex.: /cadencia relacionamento 45"
    if not 1 <= dias <= 365:
        return c, "Use de 1 a 365 dias."
    c[fase] = dias
    return c, None


def texto_cadencia(c: dict) -> str:
    return ("⏱️ Cadência de contato (dias sem falar com a conta até acender o amarelo; o dobro, o vermelho):\n"
            + "\n".join(f"• {FASES.get(k, k)}: {v} dias" for k, v in c.items())
            + "\n\nPara mudar: /cadencia relacionamento 45")


# -- atividades ---------------------------------------------------------------------------------

def linha_atividade(a: dict, conta: str | None = None) -> str:
    quem = ", ".join(p["nome"] + (f" ({p['papel']})" if p.get("papel") else "") for p in a.get("participantes") or [])
    return (f"{'🗓️' if a['situacao'] == 'agendada' else '•'} {_dia(a.get('data'))} · {TIPOS.get(a['tipo'], a['tipo'])}"
            + (f" · {conta}" if conta else "") + (f" · {FASES[a['fase']]}" if a.get("fase") in FASES else "")
            + (" · marcada" if a["situacao"] == "agendada" else "")
            + (f" · pelo parceiro {a['pelo_parceiro']}" if a.get("pelo_parceiro") else "")
            + (f"\n   {a['resumo']}" if a.get("resumo") else "")
            + (f"\n   Com: {quem}" if quem else "")
            + (f"\n   ➡️ Próximo passo: {a['proximo_passo']}" if a.get("proximo_passo") else ""))


def lacunas_spin(spin: dict | None) -> list[str]:
    """Perguntas sugeridas para o que a conversa ainda não levantou (Problema, Implicação, Necessidade)."""
    if not spin:
        return []
    saida = []
    for k in ("problema", "implicacao", "necessidade"):
        if not spin.get(k):
            saida += SPIN_PERGUNTAS[k][:1]
    return saida


# -- saúde da conta (D-095) -----------------------------------------------------------------------

ORDEM = {"verde": 0, "amarelo": 1, "vermelho": 2}
COR = {"verde": "🟢", "amarelo": "🟡", "vermelho": "🔴"}


@dataclass
class Sinal:
    nivel: str
    texto: str
    acao: str


@dataclass
class Saude:
    conta_id: str
    nome: str
    tipo: str
    fase: str | None
    dias_sem_contato: int | None
    sinais: list[Sinal] = field(default_factory=list)

    @property
    def cor(self) -> str:
        return max((s.nivel for s in self.sinais), key=ORDEM.get, default="verde")


def _fase_derivada(atividades: list[dict], ops: list[dict]) -> str:
    feitas = [a for a in atividades if a.get("fase")]
    if feitas:
        return feitas[-1]["fase"]
    if any(o["etapa"] in ("proposta", "negociacao") for o in ops):
        return "negociacao"
    if any(o["etapa"] == "ganha" for o in ops):
        return "pos_venda"
    if any(o["etapa"] == "prospeccao" for o in ops):
        return "prospeccao"
    return "relacionamento"


def saude(conta: dict, hoje: str, cad: dict, atividades: list[dict], ops: list[dict], propostas: list[dict],
          faturas: list[dict], pendencias: list, sinais: list[dict], fatores: list[dict], segmento_conta: str | None,
          resultado_conta=None, contratos: list[dict] | None = None, plano: dict | None = None,
          chave: bool = False) -> Saude:
    """Semáforo de uma conta. `atividades` e `sinais` já filtrados pela conta, em ordem de data."""
    parceiro = conta.get("tipo") == "parceiro"
    fase = _fase_derivada(atividades, ops)
    feitas = sorted((a for a in atividades if a["situacao"] == "feita" and a.get("data") and a["data"] <= hoje),
                    key=lambda a: a["data"])
    futuras = [a for a in atividades if a["situacao"] == "agendada" and (a.get("data") or "") >= hoje]
    dias = _dias(feitas[-1]["data"], hoje) if feitas else None
    s = Saude(conta["id"], conta["nome"], conta.get("tipo") or "cliente", fase, dias)
    for a in atividades:
        if a["situacao"] == "agendada" and a.get("data") and a["data"] < hoje:
            s.sinais.append(Sinal("amarelo", f"{TIPOS.get(a['tipo'], a['tipo'])} marcada para {_dia(a['data'])} "
                                  "sem registro de como foi", "contar como foi (o Peraí registra)"))
    limite = cad["parceiro"] if parceiro else cad.get(fase, 30)
    if dias is None:
        if not atividades:
            s.sinais.append(Sinal("amarelo", "nenhuma atividade registrada", "registrar o último contato ou marcar um"))
    elif dias > 2 * limite and not futuras:
        s.sinais.append(Sinal("vermelho", f"{dias} dias sem contato (cadência de {limite})", "marcar call ou visita"))
    elif dias > limite and not futuras:
        s.sinais.append(Sinal("amarelo", f"{dias} dias sem contato (cadência de {limite})", "marcar call ou visita"))
    # Contatos
    pessoas = {normalizar(p["nome"]): p.get("papel") for a in atividades for p in a.get("participantes") or []}
    abertas = [o for o in ops if o["etapa"] not in ("ganha", "perdida")]
    if not parceiro and abertas and not any(p in ("decisor", "defensor") for p in pessoas.values()):
        s.sinais.append(Sinal("amarelo", "nenhum decisor ou defensor citado nas conversas",
                              "pedir apresentação a quem decide"))
    elif not parceiro and len(pessoas) == 1 and feitas:
        s.sinais.append(Sinal("amarelo", "só um contato registrado na conta", "abrir um segundo contato"))
    # Próximo passo
    if feitas and not feitas[-1].get("proximo_passo") and not futuras:
        s.sinais.append(Sinal("amarelo", "última conversa sem próximo passo combinado", "combinar o próximo passo"))
    nome_n = normalizar(conta["nome"])
    for p in pendencias:
        if p.prazo and p.prazo < hoje and nome_n and mesma_conta(conta["nome"], p.descricao):
            s.sinais.append(Sinal("vermelho", f"pendência vencida em {_dia(p.prazo)}: {p.descricao[:80]}",
                                  "cumprir ou renegociar o prazo"))
            break
    # Propostas abertas
    ids_abertas = {o["id"] for o in abertas}
    for pr in propostas:
        v = pr.get("validade")
        if not v or pr.get("oportunidade_id") not in ids_abertas:
            continue
        if v < hoje:
            s.sinais.append(Sinal("vermelho", f"proposta {pr.get('numero') or ''} venceu em {_dia(v)} sem resposta",
                                  "ligar para fechar ou renovar a validade"))
        elif _dias(hoje, v) <= 15:
            s.sinais.append(Sinal("amarelo", f"proposta {pr.get('numero') or ''} vence em {_dia(v)}",
                                  "ligar para fechar antes do vencimento"))
    # Faturas
    for f in faturas:
        saldo = f["valor_centavos"] - sum(r["valor_centavos"] for r in f.get("recebimentos") or [])
        vc = f.get("vencimento")
        if saldo <= 0 or not vc:
            continue
        if vc < hoje:
            s.sinais.append(Sinal("vermelho", f"NF {f.get('numero') or 's/n'} vencida em {_dia(vc)}, saldo {brl(saldo)}",
                                  "cobrar com o contato financeiro"))
        elif _dias(hoje, vc) <= 5:
            s.sinais.append(Sinal("amarelo", f"NF {f.get('numero') or 's/n'} vence em {_dia(vc)}, saldo {brl(saldo)}",
                                  "confirmar o pagamento com o financeiro"))
    # Funil
    if not parceiro and not abertas:
        if ops:
            recente = max(dia_local(o.get("atualizado_em")) for o in ops)
            if recente and _dias(recente, hoje) > 90:
                nivel = "vermelho" if dias is None or dias > 90 else "amarelo"
                s.sinais.append(Sinal(nivel, "nenhuma oportunidade em aberto há mais de 90 dias",
                                      "levar uma oferta do /mapa"))
        else:
            primeira = min((a["data"] for a in atividades if a.get("data")), default=None)
            if primeira and _dias(primeira, hoje) > 90:
                s.sinais.append(Sinal("amarelo", "nenhuma oportunidade aberta desde o primeiro contato, há mais "
                                      "de 90 dias", "levar uma oferta do /mapa"))
    # Resultado
    if resultado_conta is not None and resultado_conta.faturamento:
        if resultado_conta.saldo is None:
            s.sinais.append(Sinal("amarelo", "saldo da conta não fechado (falta pricing ou base de comissão)",
                                  "completar o pricing dos projetos ganhos"))
        elif resultado_conta.saldo < 0:
            s.sinais.append(Sinal("vermelho", f"saldo da conta negativo ({brl(resultado_conta.saldo)})",
                                  "rever despesas ou preço"))
    # Sinais da conta
    for sg in sinais:
        if sg["situacao"] != "aberto" or sg["tipo"] not in SINAIS_RUINS:
            continue
        idade = _dias(dia_local(sg["criado_em"]), hoje)
        quem = "" if (sg.get("dito_por") or "eu").lower() == "eu" else f" (segundo {sg['dito_por']})"
        if sg["tipo"] == "entrega":
            s.sinais.append(Sinal("vermelho" if idade > 15 else "amarelo",
                                  f"problema de entrega há {idade} dia{'' if idade == 1 else 's'}: {sg['descricao'][:90]}{quem}",
                                  "falar com o parceiro e com o cliente"))
        else:
            s.sinais.append(Sinal("amarelo", f"{TIPOS_SINAL[sg['tipo']]}: {sg['descricao'][:90]}{quem}",
                                  "entender o impacto e ajustar o plano da conta"))
    # Contratos e parcerias da conta (D-103): proposta sem assinatura perto de vencer, parcela a pagar
    for k in contratos or []:
        v = k.get("vigencia_ate")
        if v and not k.get("assinatura"):
            if v < hoje:
                s.sinais.append(Sinal("amarelo", f"proposta “{k['titulo'][:60]}” venceu em {_dia(v)} sem assinatura",
                                      "renovar a validade ou registrar que não segue"))
            elif _dias(hoje, v) <= 30:
                s.sinais.append(Sinal("amarelo", f"proposta “{k['titulo'][:60]}” vale até {_dia(v)} e não foi assinada",
                                      "decidir e assinar antes do vencimento"))
        for pc in k.get("parcelas") or []:
            vc = pc.get("vencimento")
            if pc.get("tipo") != "pagar" or pc.get("situacao") != "aberta" or not vc:
                continue
            if vc < hoje:
                s.sinais.append(Sinal("vermelho", f"parcela {pc['n']} de {brl(pc['valor_centavos'] or 0)} venceu em "
                                      f"{_dia(vc)}", "pagar e avisar “paguei a parcela " + str(pc["n"]) + "”"))
            elif _dias(hoje, vc) <= 5:
                s.sinais.append(Sinal("amarelo", f"parcela {pc['n']} de {brl(pc['valor_centavos'] or 0)} vence em "
                                      f"{_dia(vc)}", "programar o pagamento"))
    # Plano de conta (D-103)
    if chave and not parceiro:
        if not plano or not (plano.get("objetivos") or plano.get("iniciativas") or plano.get("meta_centavos")):
            s.sinais.append(Sinal("amarelo", "conta-chave sem plano de conta", "montar o plano (/plano "
                                  + conta["nome"].split()[0] + ")"))
        elif plano.get("revisao") and plano["revisao"] < hoje:
            s.sinais.append(Sinal("amarelo", f"plano de conta para revisar desde {_dia(plano['revisao'])}",
                                  "revisar objetivos e iniciativas"))
    # Risco do negócio
    if segmento_conta:
        for f in fatores:
            if f["direcao"] != "ameaca" or f["situacao"] == "revertido" or segmento_conta not in f["segmentos"]:
                continue
            s.sinais.append(Sinal("vermelho" if f["situacao"] == "em_vigor" else "amarelo",
                                  f"risco no segmento {segmento_conta}: {f['titulo'][:90]}",
                                  "rever exposição e plano da conta (/riscos)"))
    return s


def texto_saude(s: Saude, curto: bool = False) -> str:
    cab = (f"{COR[s.cor]} {s.nome}" + (" (parceiro)" if s.tipo == "parceiro" else "")
           + (f" · {FASES.get(s.fase, s.fase)}" if s.fase else "")
           + (f" · último contato há {s.dias_sem_contato} dia{'' if s.dias_sem_contato == 1 else 's'}"
              if s.dias_sem_contato is not None else ""))
    if curto or not s.sinais:
        return cab + ("" if s.sinais else " · tudo em dia")
    ordenados = sorted(s.sinais, key=lambda x: -ORDEM[x.nivel])
    return cab + "\n" + "\n".join(f"   {COR[x.nivel]} {x.texto} → {x.acao}" for x in ordenados)


def alertas(saudes: list[Saude], n: int = 3) -> list[str]:
    """Os n alertas mais urgentes da carteira: vermelho antes de amarelo; dentro, mais dias sem contato."""
    todos = [(ORDEM[x.nivel], s.dias_sem_contato or 0, s.nome, x) for s in saudes for x in s.sinais]
    todos.sort(key=lambda t: (-t[0], -t[1]))
    return [f"{COR[x.nivel]} {nome}: {x.texto} → {x.acao}" for _, _, nome, x in todos[:n]]


# -- MEDDPICC (D-097) -----------------------------------------------------------------------------

@dataclass
class Placar:
    op: dict
    itens: dict          # elemento -> linha (nota, evidência, quem disse) ou None
    total: int

    @property
    def lacunas(self) -> list[str]:
        return [k for k, v in self.itens.items() if v is None or v["nota"] < 2]


def placar(op: dict, evidencias: list[dict]) -> Placar:
    itens = {k: None for k in MEDDPICC}
    for e in evidencias:   # em ordem de criação: a maior nota vence; empate, a mais recente
        atual = itens.get(e["elemento"])
        if e["elemento"] in itens and (atual is None or e["nota"] >= atual["nota"]):
            itens[e["elemento"]] = e
    return Placar(op, itens, sum(v["nota"] for v in itens.values() if v))


def texto_placar(p: Placar) -> str:
    linhas = [f"🎯 {p.op['titulo']} · {p.op.get('conta') or 'sem conta'} · MEDDPICC {p.total}/16"]
    for k, (rot, _, acao) in MEDDPICC.items():
        e = p.itens[k]
        if e is None:
            linhas.append(f"   ⚪ {rot}: nada registrado → {acao}")
        else:
            quem = "" if (e.get("dito_por") or "eu").lower() == "eu" else f" (segundo {e['dito_por']})"
            marca = "🟢" if e["nota"] == 2 else "🟡"
            linhas.append(f"   {marca} {rot} ({e['nota']}): {e['evidencia'][:100]}{quem}"
                          + ("" if e["nota"] == 2 else f" → confirmar com o cliente"))
    return "\n".join(linhas)


# -- mapa de oportunidades (D-098) ----------------------------------------------------------------

def catalogo(cfg) -> list[dict]:
    return cfg if isinstance(cfg, list) and cfg else CATALOGO_PADRAO


def oferta_do_texto(texto: str, cat: list[dict]) -> dict | None:
    t = (texto or "").lower()
    melhor, tam = None, 0
    for o in cat:
        for p in o.get("palavras") or []:
            if re.search(rf"(?<![a-z]){re.escape(p.lower())}", t) and len(p) > tam:
                melhor, tam = o, len(p)
        if _chave(o["nome"]) == _chave(texto):
            return o
    return melhor


def grade(cat: list[dict], linhas_mapa: list[dict], ops: list[dict]) -> dict[str, dict]:
    """chave da oferta -> {"situacao", "fonte"}; oportunidade ganha/aberta vale mais que a fala."""
    g = {o["chave"]: {"situacao": "nao_avaliado", "fonte": None, "concorrente": None} for o in cat}
    for m in linhas_mapa:
        o = oferta_do_texto(m["oferta"], cat)
        if o:
            g[o["chave"]] = {"situacao": m["situacao"], "fonte": m, "concorrente": m.get("concorrente")}
    for op in ops:
        o = oferta_do_texto(op["titulo"], cat)
        if not o or op["etapa"] == "perdida":
            continue
        if op["etapa"] == "ganha":
            g[o["chave"]] = {"situacao": "tem_conosco", "fonte": op, "concorrente": None}
        elif g[o["chave"]]["situacao"] != "tem_conosco":
            g[o["chave"]] = {"situacao": "em_negociacao", "fonte": op, "concorrente": None}
    return g


def sugestoes(cat: list[dict], g: dict, maturidade: dict[str, int], s: Saude | None, saldo: int | None,
              n: int = 3) -> list[str]:
    """Próximas ofertas: sem solução > com concorrente (troca) > não avaliado num domínio de nível baixo."""
    pontos = []
    for o in cat:
        sit = g[o["chave"]]["situacao"]
        nivel = maturidade.get(o.get("dominio") or "")
        if sit == "sem_solucao":
            pts, motivo = 3, "não tem solução"
        elif sit == "concorrente":
            pts, motivo = 2, f"tem com concorrente ({g[o['chave']]['concorrente'] or 'não informado'}): preparar a troca"
        elif sit == "nao_avaliado" and nivel is not None and nivel <= 2:
            pts, motivo = 2, f"maturidade {nivel} em {DOMINIOS[o['dominio']].lower()}"
        else:
            continue
        if nivel is not None and nivel <= 2 and sit != "nao_avaliado":
            pts += 1
            motivo += f"; maturidade {nivel}"
        pontos.append((pts, o["nome"], motivo))
    pontos.sort(key=lambda x: -x[0])
    saida = [f"{nome} — {motivo}" for _, nome, motivo in pontos[:n]]
    if saida and s is not None and s.cor == "vermelho":
        saida.append("⚠️ A conta está no vermelho: resolva os alertas antes de levar oferta nova.")
    elif saida and saldo is not None and saldo > 0:
        saida.append(f"Saldo positivo na conta ({brl(saldo)}): bom momento para expandir.")
    return saida


def texto_mapa(nome: str, cat: list[dict], g: dict, sug: list[str]) -> str:
    icone = {"tem_conosco": "✅", "em_negociacao": "🔄", "concorrente": "⚔️", "sem_solucao": "🎯",
             "nao_se_aplica": "➖", "nao_avaliado": "·"}
    linhas = [f"🗺️ Mapa de oportunidades · {nome}"]
    unidade = None
    for o in cat:
        if o["unidade"] != unidade:
            unidade = o["unidade"]
            linhas.append(f"\n{unidade}")
        x = g[o["chave"]]
        linhas.append(f"   {icone[x['situacao']]} {o['nome']}: {SITUACOES_MAPA[x['situacao']]}"
                      + (f" ({x['concorrente']})" if x.get("concorrente") else ""))
    avaliadas = sum(1 for x in g.values() if x["situacao"] != "nao_avaliado")
    linhas.append(f"\n{avaliadas} de {len(cat)} linhas avaliadas.")
    if sug:
        linhas.append("💡 Próximas ofertas:\n" + "\n".join(f"• {x}" for x in sug))
    return "\n".join(linhas)


# -- maturidade (D-099) ---------------------------------------------------------------------------

def maturidade_conta(linhas: list[dict]) -> dict[str, dict]:
    """domínio -> última avaliação."""
    return {k[0]: v for k, v in ultima_por(linhas, "dominio").items()}


def texto_maturidade(nome: str, m: dict[str, dict]) -> str:
    linhas = [f"📶 Maturidade de TI · {nome} (1 inexistente · 2 básico · 3 gerenciado · 4 otimizado)"]
    for k, rot in DOMINIOS.items():
        x = m.get(k)
        if x is None:
            linhas.append(f"   · {rot}: não avaliado")
        else:
            quem = "" if (x.get("dito_por") or "eu").lower() == "eu" else f", segundo {x['dito_por']}"
            linhas.append(f"   {'▮' * x['nivel']}{'▯' * (4 - x['nivel'])} {rot}: {x['nivel']} ({NIVEIS[x['nivel']]})"
                          + (f" — {x['nota'][:90]}" if x.get("nota") else "") + quem)
    return "\n".join(linhas)


@dataclass
class Processo:
    contas_ativas: int
    com_atividade_mes: int
    meddpicc_medio: float | None
    ganhas: int
    perdidas: int
    ciclo_medio: int | None
    nivel: int
    chaves: int = 0
    com_plano: int = 0


def processo(contas_ids: list[str], atividades: list[dict], ops: list[dict], placares: list[Placar],
             hoje: str, chaves: set | None = None, com_plano: set | None = None) -> Processo:
    """Maturidade do nosso processo comercial, pelos próprios dados."""
    ativas = [c for c in contas_ids if any(a.get("conta_id") == c and a.get("data") and _dias(a["data"], hoje) <= 90
                                           for a in atividades)
              or any(o.get("conta_id") == c and o["etapa"] not in ("ganha", "perdida") for o in ops)]
    mes = hoje[:7]
    com = sum(1 for c in ativas if any(a.get("conta_id") == c and (a.get("data") or "")[:7] == mes and
                                       a["situacao"] == "feita" for a in atividades))
    abertos = [p.total for p in placares if p.op["etapa"] not in ("ganha", "perdida")]
    media = round(sum(abertos) / len(abertos), 1) if abertos else None
    fechadas = [o for o in ops if o["etapa"] in ("ganha", "perdida")]
    ciclos = [_dias(dia_local(o["criado_em"]), dia_local(o["atualizado_em"])) for o in fechadas
              if o.get("criado_em") and o.get("atualizado_em")]
    nivel = 1
    if ativas and com / len(ativas) >= 0.5:
        nivel = 2
        if media is not None and media >= 10:
            nivel = 3
            if chaves and chaves <= (com_plano or set()):
                nivel = 4
    return Processo(len(ativas), com, media, sum(1 for o in fechadas if o["etapa"] == "ganha"),
                    sum(1 for o in fechadas if o["etapa"] == "perdida"),
                    round(sum(ciclos) / len(ciclos)) if ciclos else None, nivel,
                    len(chaves or ()), len((chaves or set()) & (com_plano or set())))


def texto_processo(p: Processo) -> str:
    rot = {1: "reativo", 2: "registrado", 3: "qualificado", 4: "planejado"}
    conv = (f"{100 * p.ganhas / (p.ganhas + p.perdidas):.0f}% ({p.ganhas} ganhas, {p.perdidas} perdidas)"
            if p.ganhas + p.perdidas else "sem oportunidades fechadas")
    return "\n".join([
        f"🏗️ Nosso processo comercial: nível {p.nivel} ({rot[p.nivel]}) — calculado pelo Peraí",
        f"   Contas ativas (atividade em 90 dias ou negócio aberto): {p.contas_ativas}",
        f"   Com atividade neste mês: {p.com_atividade_mes}"
        + (f" ({100 * p.com_atividade_mes / p.contas_ativas:.0f}%)" if p.contas_ativas else ""),
        f"   MEDDPICC médio das oportunidades abertas: "
        + (f"{p.meddpicc_medio}/16" if p.meddpicc_medio is not None else "não informado"),
        f"   Conversão: {conv}",
        f"   Ciclo médio: " + (f"{p.ciclo_medio} dias" if p.ciclo_medio is not None else "não informado"),
        (f"   Plano de conta: {p.com_plano} de {p.chaves} conta(s)-chave com plano" if p.chaves
         else "   Plano de conta: nenhuma conta-chave ainda (projeto de R$ 500 mil ou mais, ou marcada por você)"),
        "   Degraus: 1 reativo · 2 registrado (≥50% das contas com atividade no mês) · 3 qualificado "
        "(MEDDPICC médio ≥ 10) · 4 planejado (todas as contas-chave com plano)."])


# -- radar de risco (D-100) ------------------------------------------------------------------------

@dataclass
class Exposicao:
    fator: dict
    contas: list[str]
    faturamento: int
    em_aberto: int


def exposicao(fatores: list[dict], segmentos: dict[str, str], contas_r: dict[str, object]) -> list[Exposicao]:
    """`segmentos`: conta_id -> segmento; `contas_r`: conta_id -> resultado.Conta (faturamento, em_aberto)."""
    saida = []
    for f in fatores:
        if f["situacao"] == "revertido":
            continue
        ids = [c for c, seg in segmentos.items() if seg in f["segmentos"]]
        fat = sum(getattr(contas_r.get(c), "faturamento", 0) or 0 for c in ids)
        ab = sum(getattr(contas_r.get(c), "em_aberto", 0) or 0 for c in ids)
        saida.append(Exposicao(f, ids, fat, ab))
    return saida


def concentracao(contas_r: dict[str, object], nomes: dict[str, str], segmentos: dict[str, str]) -> list[str]:
    total = sum(getattr(c, "faturamento", 0) or 0 for c in contas_r.values())
    if not total:
        return []
    linhas = []
    por_conta = sorted(((getattr(c, "faturamento", 0) or 0, i) for i, c in contas_r.items()), reverse=True)
    v, i = por_conta[0]
    pct = 100 * v / total
    linhas.append(f"{'⚠️' if pct > 30 else '•'} Maior conta: {nomes.get(i, '?')} com {pct:.0f}% do faturamento"
                  + (" (acima de 30%: se ela sumir, pesa demais)" if pct > 30 else ""))
    por_seg: dict[str, int] = {}
    for i, c in contas_r.items():
        por_seg[segmentos.get(i) or "sem segmento"] = por_seg.get(segmentos.get(i) or "sem segmento", 0) + (
            getattr(c, "faturamento", 0) or 0)
    seg, vs = max(por_seg.items(), key=lambda x: x[1])
    linhas.append(f"• Maior segmento: {seg} com {100 * vs / total:.0f}% do faturamento")
    return linhas


def texto_riscos(exps: list[Exposicao], conc: list[str], nomes: dict[str, str], total: int,
                 sem_segmento: list[str]) -> str:
    rot_sit = {"observacao": "em observação", "em_vigor": "em vigor", "revertido": "revertido"}
    linhas = ["🧭 Radar de risco do negócio (exposição calculada pelo Peraí)"]
    if not exps:
        linhas.append("\nNenhum fator externo registrado. Mande uma notícia ou diga, por exemplo: “a proibição das "
                      "bets é risco alto para clientes de apostas”.")
    for e in sorted(exps, key=lambda e: (e.fator["direcao"] != "ameaca", -e.faturamento)):
        f = e.fator
        ic = "⚠️" if f["direcao"] == "ameaca" else "🌱"
        pct = f" ({100 * e.faturamento / total:.0f}% do faturamento)" if total and e.faturamento else ""
        linhas.append(f"\n{ic} {f['titulo']} · {'ameaça' if f['direcao'] == 'ameaca' else 'oportunidade'} "
                      f"{f['intensidade']} · {rot_sit[f['situacao']]}")
        linhas.append(f"   Segmentos: {', '.join(f['segmentos']) or 'não informado'}"
                      + (f" · fonte: {f['fonte_url']}" if f.get("fonte_url") else ""))
        if e.contas:
            linhas.append(f"   Contas atingidas: {', '.join(nomes.get(c, '?') for c in e.contas)}")
            linhas.append(f"   Exposição: faturamento {brl(e.faturamento)}{pct} · em negociação {brl(e.em_aberto)}")
        else:
            linhas.append("   Nenhuma conta sua neste segmento (ou o segmento das contas não foi informado).")
    if conc:
        linhas.append("\nConcentração:\n" + "\n".join(conc))
    if sem_segmento:
        linhas.append(f"\nSem segmento: {', '.join(sem_segmento[:12])}" + (" e outras" if len(sem_segmento) > 12 else "")
                      + ". Use /segmento <conta> <segmento> (ex.: /segmento Betnova apostas).")
    return "\n".join(linhas)


# -- plano de conta (D-103) ------------------------------------------------------------------------

LIMITE_CHAVE = 50_000_000   # R$ 500 mil: projeto a partir disso faz da conta uma conta-chave


def plano_atual(linhas: list[dict]) -> dict | None:
    """Junta as falas em ordem: o que vier depois substitui; campo vazio não apaga o anterior."""
    if not linhas:
        return None
    p: dict = {"objetivos": [], "iniciativas": [], "meta_centavos": None, "revisao": None, "ano": None,
               "chave": None, "fontes": []}
    for x in linhas:
        for k in ("objetivos", "iniciativas"):
            if x.get(k):
                p[k] = list(x[k])
        for k in ("meta_centavos", "revisao", "ano"):
            if x.get(k) is not None:
                p[k] = x[k] if k != "revisao" else str(x[k])[:10]
        if x.get("chave") is not None:
            p["chave"] = x["chave"]
        p["fontes"].append(x.get("mensagem_id"))
    return p


def contas_chave(ops: list[dict], planos: dict[str, dict]) -> set[str]:
    """Conta com projeto (não perdido) de R$ 500 mil ou mais, ou marcada como chave; desmarcada sai."""
    ids = {o["conta_id"] for o in ops if o.get("conta_id") and o["etapa"] != "perdida"
           and (o.get("valor_centavos") or 0) >= LIMITE_CHAVE}
    ids |= {c for c, p in planos.items() if p.get("chave") is True}
    return {c for c in ids if (planos.get(c) or {}).get("chave") is not False}


def tem_plano(p: dict | None) -> bool:
    return bool(p and (p.get("objetivos") or p.get("iniciativas") or p.get("meta_centavos")))


def mapa_pessoas(atividades: list[dict]) -> list[dict]:
    """Quem apareceu nas atividades da conta: papel mais recente dito, último contato e quantas vezes."""
    pessoas: dict[str, dict] = {}
    for a in sorted(atividades, key=lambda a: a.get("data") or ""):
        if a["situacao"] != "feita":
            continue
        for p in a.get("participantes") or []:
            k = normalizar(p["nome"])
            x = pessoas.setdefault(k, {"nome": p["nome"], "papel": None, "ultimo": None, "vezes": 0})
            x["papel"] = p.get("papel") or x["papel"]
            x["ultimo"] = a.get("data") or x["ultimo"]
            x["vezes"] += 1
    ordem = {"decisor": 0, "defensor": 1, "financeiro": 2, "influenciador": 3, "tecnico": 4, "indicador": 5}
    return sorted(pessoas.values(), key=lambda x: (ordem.get(x["papel"], 9), x["nome"].lower()))


def texto_plano(nome: str, plano: dict | None, chave: bool, pessoas: list[dict], ops: list[dict],
                hoje: str, sug: list[str], saude_conta: Saude | None) -> str:
    ano = (plano or {}).get("ano") or int(hoje[:4])
    linhas = [f"📋 Plano de conta · {nome} · {ano}" + (" · conta-chave" if chave else "")]
    if saude_conta is not None:
        linhas.append(f"   Saúde: {COR[saude_conta.cor]}" + (f" ({len(saude_conta.sinais)} alerta(s), veja /saude)"
                                                             if saude_conta.sinais else " em dia"))
    if not tem_plano(plano):
        linhas.append("\nAinda sem objetivos registrados. Conte do seu jeito, por exemplo: “o plano da "
                      f"{nome} para {ano} é renovar o SOC e vender o Legislativo Digital, meta de 1 milhão, "
                      "revisar em março”.")
    else:
        if plano["objetivos"]:
            linhas.append("\n🎯 Objetivos:\n" + "\n".join(f"• {x}" for x in plano["objetivos"]))
        ganhos = sum(o.get("valor_centavos") or 0 for o in ops if o["etapa"] == "ganha"
                     and dia_local(o.get("atualizado_em"))[:4] == str(ano))
        if plano.get("meta_centavos"):
            pct = 100 * ganhos / plano["meta_centavos"]
            linhas.append(f"\n💰 Meta {brl(plano['meta_centavos'])} · ganho em {ano}: {brl(ganhos)} ({pct:.0f}%)"
                          f" · falta {brl(max(plano['meta_centavos'] - ganhos, 0))}")
        if plano["iniciativas"]:
            linhas.append("\n🚀 Iniciativas:\n" + "\n".join(f"• {x}" for x in plano["iniciativas"]))
        if plano.get("revisao"):
            atrasada = plano["revisao"] < hoje
            linhas.append(f"\n🔁 Revisão: {_dia(plano['revisao'])}/{plano['revisao'][:4]}" + (" (atrasada)" if atrasada else ""))
    abertas = [o for o in ops if o["etapa"] not in ("ganha", "perdida")]
    if abertas:
        from perai.contas import ROTULO_ETAPA, _valor
        linhas.append("\n📈 Em aberto:\n" + "\n".join(f"• {o['titulo']} · {ROTULO_ETAPA[o['etapa']].lower()} · {_valor(o)}"
                                                    for o in abertas[:8]))
    linhas.append("\n👥 Mapa de pessoas (das atividades registradas):")
    if pessoas:
        for p in pessoas[:12]:
            linhas.append(f"• {p['nome']}" + (f" ({p['papel']})" if p.get("papel") else " (papel não informado)")
                          + (f" · último contato {_dia(p['ultimo'])}" if p.get("ultimo") else "")
                          + f" · {p['vezes']} conversa(s)")
        papeis = {p.get("papel") for p in pessoas}
        faltam = [r for r in ("decisor", "defensor", "financeiro") if r not in papeis]
        if faltam:
            linhas.append("   Falta conhecer: " + ", ".join(faltam) + ".")
    else:
        linhas.append("   ninguém registrado ainda; conte quem esteve nas conversas e o papel de cada um.")
    if sug:
        linhas.append("\n💡 Próximas ofertas (do /mapa):\n" + "\n".join(f"• {x}" for x in sug))
    return "\n".join(linhas)

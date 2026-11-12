"""Biblioteca de fatores de mercado (D-106): o estudo "Radar de fatores de mercado" (11/11/2026) dentro
do Peraí. Cada fator tem fonte, data, revisão, os segmentos de clientes que ele atinge (ameaça ou
oportunidade, separados) e a jogada: as ofertas da L8 que respondem a ele. Nada entra no radar do usuário
sem um toque dele; o registro vira mensagem dele com a fonte (D-040), "segundo" o veículo (D-044).

Os números e datas estão como nas fontes, na data do estudo. Fator com revisão vencida aparece para revisar.
"""

from __future__ import annotations

from perai.contas import normalizar

ESTUDO = "Radar de fatores de mercado (11/11/2026)"

BIBLIOTECA = [
    {"chave": "fim_6x1", "titulo": "Fim da escala 6x1 (EC 141/2026)", "situacao": "em_vigor", "data": "2026-10-16",
     "revisar_em": "2026-12-15", "fonte": "Movimento Econômico",
     "url": "https://movimentoeconomico.com.br/trabalho/2026/10/16/congresso-promulga-emenda-do-fim-da-6x1-e-escala-5x2-comeca-em-60-dias/",
     "resumo": "Promulgada em 16/10/2026: 42 horas e dois dias de descanso em 60 dias, 40 horas cerca de 12 meses "
               "depois, sem redução de salário. O Ipea estima custo do trabalho +7,8% na média e até +6,6% no custo "
               "total de vigilância; a CNC fala em +21% sobre a folha.",
     "ameaca": {"segmentos": ["varejo", "logistica", "facilities", "alimentos_bebidas", "farmacia", "prefeitura",
                              "legislativo"], "intensidade": "media"},
     "oportunidade": None,
     "jogada": {"segmentos": ["varejo", "logistica", "facilities", "farmacia", "alimentos_bebidas", "industria",
                              "prefeitura", "legislativo"],
                "ofertas": ["videomonitoramento", "controle_acesso", "servicos_gerenciados", "soc"],
                "texto": "hora de trabalho mais cara: automação (vídeo com analíticos, portaria e controle de acesso) "
                         "e serviços gerenciados no lugar de turnos que o cliente teria de recompor"}},
    {"chave": "dolar_alto", "titulo": "Dólar alto (R$ 5,49 em 05/11/2026)", "situacao": "observacao",
     "data": "2026-11-05", "revisar_em": "2026-12-05", "fonte": "Investalk BB",
     "url": "https://investalk.bb.com.br/radar/fechamento-cambio-dolar-vai-a-r-548-diante-de-piora-de-aversao-ao-risco-com-tombo-de-techs",
     "resumo": "Maior fechamento desde 30/12/2025, com dúvida fiscal e aversão a risco no exterior; Selic mantida em "
               "13,75%. A XP projeta R$ 5,30 no fim do ano (R$ 5,20 a R$ 5,70 conforme o fiscal).",
     "ameaca": {"segmentos": ["prefeitura", "legislativo", "governo_estadual", "governo_federal", "varejo", "energia",
                              "tecnologia", "saude", "farmacia"], "intensidade": "media"},
     "oportunidade": {"segmentos": ["agro", "industria"], "intensidade": "baixa"},
     "jogada": {"segmentos": [], "ofertas": [],
                "texto": "revisar propostas abertas com câmbio fixo e validade longa; incluir cláusula de reajuste"}},
    {"chave": "fim_bets", "titulo": "Proibição das bets (MP 1.394/2026)", "situacao": "em_vigor", "data": "2026-11-09",
     "revisar_em": "2026-12-09", "fonte": "Teletime", "url": "https://teletime.com.br/09/11/2026/fux-bets-acoes-proibicao/",
     "resumo": "Apostas de quota fixa proibidas; em 09/11 o ministro Fux suspendeu as ações contra a proibição até o "
               "plenário do STF julgar; a MP ainda precisa passar no Congresso.",
     "ameaca": {"segmentos": ["apostas", "midia_esportes"], "intensidade": "alta"},
     "oportunidade": {"segmentos": ["varejo"], "intensidade": "baixa"},
     "jogada": {"segmentos": ["financeiro", "pagamentos", "governo_federal", "governo_estadual"],
                "ofertas": ["siem", "soc", "vulnerabilidades"],
                "texto": "combate ao mercado ilegal: monitoramento, SIEM e SOC para bancos, pagamentos e fiscalização"}},
    {"chave": "glp1", "titulo": "Canetas para emagrecer (GLP-1, Mounjaro e genéricos)", "situacao": "em_vigor",
     "data": "2026-06-02", "revisar_em": "2027-01-31", "fonte": "Bloomberg Línea",
     "url": "https://www.bloomberglinea.com.br/negocios/uso-de-canetas-emagrecedoras-no-brasil-supera-media-global-e-muda-cesta-de-consumo/",
     "resumo": "5,5% dos brasileiros já usam (média global 3,7%); mercado perto de R$ 20 bilhões em 2026 (UBS); "
               "patente da semaglutida caiu em 20/03/2026. Caem doces e bebidas açucaradas; sobem proteína, "
               "frescos e academia. A Scanntech ainda não viu queda relevante no volume de comida.",
     "ameaca": {"segmentos": ["alimentos_bebidas"], "intensidade": "baixa"},
     "oportunidade": {"segmentos": ["farmacia", "saude"], "intensidade": "media"},
     "jogada": {"segmentos": ["farmacia", "varejo"], "ofertas": ["videomonitoramento", "controle_acesso"],
                "texto": "produto caro na prateleira e mix mudando: vídeo com analíticos e prevenção de perdas"}},
    {"chave": "nvidia", "titulo": "Investigação do CEO da Nvidia", "situacao": "observacao", "data": "2026-11-09",
     "revisar_em": "2026-12-09", "fonte": "Data Center Knowledge",
     "url": "https://datacenterknowledge.com/data-center-chips/nvidia-probes-huang-ties-as-ai-industry-weighs-the-fallout",
     "resumo": "Confirmado: o conselho abriu investigação independente em 08/11 e Jensen Huang admitiu infidelidade. "
               "Alegado, sem prova: ligação de uma ex-executiva da Megaspeed com a inteligência chinesa. Analistas "
               "esperam que o investimento em IA continue.",
     "ameaca": {"segmentos": ["tecnologia"], "intensidade": "baixa"}, "oportunidade": None,
     "jogada": {"segmentos": [], "ofertas": [],
                "texto": "acompanhar: ritmo de investimento em data center para IA e compras públicas de "
                         "fornecedores chineses"}},
    {"chave": "el_nino", "titulo": "El Niño muito forte 2026-27", "situacao": "em_vigor", "data": "2026-11-06",
     "revisar_em": "2027-01-31", "fonte": "Brasil 61 (INMET, NOAA, Cemaden)",
     "url": "https://brasil61.com/n/el-nino-atinge-intensidade-muito-forte-e-registra-anomalia-recorde-de-2-47-c-em-outubro-bras2617433.amp",
     "resumo": "Anomalia de +2,47°C em outubro (recorde do INMET); 96% de chance de seguir muito forte até janeiro "
               "(NOAA). Chuva acima da média no Sul, seca no Norte e no semiárido, calor em quase todo o país.",
     "ameaca": {"segmentos": ["agro", "logistica", "prefeitura", "energia", "alimentos_bebidas"], "intensidade": "media"},
     "oportunidade": None,
     "jogada": {"segmentos": ["prefeitura", "seguranca_publica", "governo_estadual", "energia", "saude", "industria"],
                "ofertas": ["cidade_protegida", "videomonitoramento", "sonorizacao", "datacenter", "solar"],
                "texto": "monitoramento de áreas de risco e alerta para defesa civil; energia de reserva e data "
                         "center para quem não pode parar"}},
]
POR_CHAVE = {f["chave"]: f for f in BIBLIOTECA}


def da_biblioteca(titulo: str) -> dict | None:
    """O fator da biblioteca de um fator do radar (o título registrado começa com o da biblioteca)."""
    t = normalizar(titulo)
    return next((f for f in BIBLIOTECA if t.startswith(normalizar(f["titulo"]))), None)


def registros(f: dict) -> list[dict]:
    """O que entra no radar: um fator por direção (o misto vira ameaça e oportunidade separadas)."""
    saida = []
    for direcao in ("ameaca", "oportunidade"):
        lado = f.get(direcao)
        if lado:
            saida.append({"titulo": f["titulo"] + (" (oportunidade)" if direcao == "oportunidade" else ""),
                          "direcao": direcao, "segmentos": list(lado["segmentos"]), "situacao": f["situacao"],
                          "intensidade": lado["intensidade"], "fonte_url": f["url"]})
    return saida


def texto_mensagem(f: dict) -> str:
    return (f"Fator da biblioteca do Peraí ({ESTUDO}) registrado no radar: {f['titulo']}. Segundo {f['fonte']} "
            f"({f['data']}): {f['resumo']} {f['url']}")


def jogadas(fatores_radar: list[dict], segmento_conta: str | None) -> list[dict]:
    """Fatores registrados (não revertidos) cuja jogada vale para o segmento da conta."""
    if not segmento_conta:
        return []
    saida, vistos = [], set()
    for x in fatores_radar:
        if x["situacao"] == "revertido":
            continue
        f = da_biblioteca(x["titulo"])
        if f and f["chave"] not in vistos and segmento_conta in f["jogada"]["segmentos"]:
            vistos.add(f["chave"])
            saida.append(f)
    return saida


def linha_jogada(f: dict, catalogo: list[dict]) -> str:
    nomes = {o["chave"]: o["nome"] for o in catalogo}
    ofertas = [nomes[k] for k in f["jogada"]["ofertas"] if k in nomes]
    return f"{f['titulo']}: {f['jogada']['texto']}" + (f" → {', '.join(ofertas[:3])}" if ofertas else "")

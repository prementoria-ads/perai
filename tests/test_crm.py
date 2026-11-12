"""CRM de contas, etapas 8 a 13 (D-095 a D-100): atividades, saúde, parceiros, MEDDPICC/SPIN, mapa,
maturidade e radar de risco. Dados fictícios."""

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from perai import crm
from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai, _agora


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.respostas, self.pedidos = [], []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append(usuario)
        if tarefa == "roteamento":
            return Resposta('{"tipo": "%s"}' % ("pergunta" if "?" in usuario else "registro"), 1, 1, 1, 0.0)
        return Resposta(json.dumps(self.respostas.pop(0) if self.respostas else
                                   {"resposta": "ok", "registros_citados": ["A1"], "sem_informacao": False}),
                        1, 1, 1, 0.0)


def dia(n: int) -> str:
    return (_agora().date() + timedelta(days=n)).isoformat()


class TesteInterpretar(unittest.TestCase):
    def test_limpa_o_que_nao_fecha(self):
        d = crm.interpretar({
            "atividades": [{"conta": "Câmara Delta", "tipo": "Visita", "fase": "prospecção", "situacao": "feita",
                            "participantes": [{"nome": "Ana Teste", "papel": "Decisor"}]},
                           {"conta": "X", "tipo": "almoço"}],
            "meddpicc": [{"oportunidade": "[O1]", "elemento": "champion", "nota": 3, "evidencia": "x"},
                         {"oportunidade": "O1", "elemento": "Economic Buyer", "nota": 2, "evidencia": "assina"}],
            "segmentos": [{"conta": "Betnova", "segmento": "bets"}, {"conta": "Y", "segmento": "astrologia"}],
            "fatores": [{"titulo": "Proibição de apostas", "segmentos": ["bets", "nada"], "situacao": "em vigor",
                         "fonte_url": "javascript:x"}]})
        self.assertEqual(len(d["atividades"]), 1)
        self.assertEqual(d["atividades"][0]["fase"], "prospeccao")
        self.assertEqual(d["atividades"][0]["participantes"][0]["papel"], "decisor")
        self.assertEqual([m["elemento"] for m in d["meddpicc"]], ["economic_buyer"])
        self.assertEqual(d["segmentos"], [{"conta": "Betnova", "segmento": "apostas"}])
        self.assertEqual(d["fatores"][0]["segmentos"], ["apostas"])
        self.assertEqual(d["fatores"][0]["situacao"], "em_vigor")
        self.assertIsNone(d["fatores"][0]["fonte_url"])
        self.assertTrue(crm.vazio(crm.interpretar(None)))

    def test_regra_entra_no_prompt(self):
        from perai.extracao import SISTEMA_EXTRACAO
        self.assertIn("34. CRM de contas", SISTEMA_EXTRACAO)
        self.assertIn('"crm": {"atividades"', SISTEMA_EXTRACAO)
        self.assertNotIn("@@", SISTEMA_EXTRACAO)


class TesteCRM(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.cli = Cli()
        self.p = Perai(self.repo, self.cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                       Path(tempfile.mkdtemp()))
        self.mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        self.conta = self.repo.criar_conta_relacionamento(self.uid, "Câmara Delta", "cliente")

    def falar(self, texto, resposta):
        self.cli.respostas = [resposta]
        return str(self.p.receber(self.uid, Entrada("telegram", "texto", texto)))

    def test_atividade_saude_e_spin(self):
        r = self.falar("visitei a Câmara Delta", {"crm": {"atividades": [{
            "conta": "Câmara Delta", "tipo": "visita", "fase": "relacionamento", "data": dia(-40), "situacao": "feita",
            "resumo": "Apresentei o Legislativo Digital", "participantes": [{"nome": "Vereadora Alfa", "papel": "decisor"}],
            "spin": {"situacao": ["usam papel nas sessões"]}}]}})
        self.assertIn("📞 Atividade: Visita · Câmara Delta", r)
        self.assertIn("💬 Para a próxima conversa (SPIN): O que mais dá trabalho", r)
        s = self.p.saude_contas(self.uid)
        self.assertIn("🟡 Câmara Delta · relacionamento · último contato há 40 dias", s)
        self.assertIn("40 dias sem contato (cadência de 30) → marcar call ou visita", s)
        self.assertIn("última conversa sem próximo passo combinado", s)
        self.assertIn("Vereadora Alfa (decisor)", self.p.listar_atividades(self.uid, "Delta"))
        # Cadência maior: o amarelo de contato some
        self.assertIn("relacionamento: 60 dias", self.p.mudar_cadencia(self.uid, ["relacionamento", "60"]))
        self.assertNotIn("dias sem contato (cadência", self.p.saude_contas(self.uid))
        self.assertIn("Fases:", self.p.mudar_cadencia(self.uid, ["almoco", "3"]))

    def test_marcada_que_passou_e_depois_aconteceu(self):
        self.falar("reunião com a Câmara Delta", {"crm": {"atividades": [{
            "conta": "Câmara Delta", "tipo": "reuniao", "data": dia(-2), "situacao": "agendada", "resumo": "Proposta"}]}})
        self.assertIn("Reunião marcada para", self.p.saude_contas(self.uid))
        self.assertIn("sem registro de como foi", self.p.saude_contas(self.uid))
        self.falar("foi boa a reunião com a Câmara Delta", {"crm": {"atividades": [{
            "conta": "Câmara Delta", "tipo": "reuniao", "data": dia(-1), "situacao": "feita",
            "proximo_passo": "mandar proposta", "participantes": [{"nome": "Beto Teste", "papel": "defensor"},
                                                                  {"nome": "Carla Teste", "papel": "tecnico"}]}]}})
        s = self.p.saude_contas(self.uid)
        self.assertNotIn("sem registro de como foi", s)
        self.assertIn("🟢 Câmara Delta", s)

    def test_sinal_de_entrega_do_parceiro_e_resolucao(self):
        self.repo.salvar_oportunidade(self.uid, self.mid, self.conta, "Videomonitoramento", "ganha", 50_000_00, None)
        r = self.falar("a MW Teste vai entregar o projeto da Câmara Delta",
                       {"crm": {"parceiros_projeto": [{"projeto": "Videomonitoramento", "parceiro": "MW Teste",
                                                       "papel": "entregou"}]}})
        self.assertIn("🤝 MW Teste entrega o projeto Videomonitoramento (Câmara Delta).", r)
        r = self.falar("a MW Teste atrasou a instalação na Câmara Delta", {"crm": {"sinais": [{
            "conta": "Câmara Delta", "tipo": "entrega", "descricao": "MW Teste atrasou a instalação",
            "dito_por": "eu"}]}})
        self.assertIn("⚠️ Câmara Delta: entrega — MW Teste atrasou a instalação", r)
        self.assertIn("problema de entrega há 0 dias", self.p.saude_contas(self.uid))
        par = self.p.listar_parceiros(self.uid)
        self.assertIn("MW Teste", par)
        self.assertIn("📦 entrega: Videomonitoramento · Câmara Delta", par)
        self.assertIn("⚠️ Entrega: MW Teste atrasou a instalação", par)
        r = self.falar("resolveram a instalação", {"crm": {"sinais": [{
            "conta": "Câmara Delta", "tipo": "entrega", "descricao": "instalação concluída", "resolvido": True}]}})
        self.assertIn("✅ Câmara Delta: entrega resolvido.", r)
        self.assertNotIn("problema de entrega", self.p.saude_contas(self.uid))

    def test_meddpicc_por_oportunidade(self):
        self.repo.salvar_oportunidade(self.uid, self.mid, self.conta, "Legislativo digital", "negociacao", 80_000_00, None)
        self.falar("o presidente da Câmara Delta é quem assina e confirmou", {"crm": {"meddpicc": [
            {"oportunidade": "O1", "elemento": "economic_buyer", "nota": 2, "evidencia": "presidente assina",
             "dito_por": "Presidente Teste"},
            {"oportunidade": "O1", "elemento": "champion", "nota": 1, "evidencia": "Beto parece apoiar",
             "dito_por": "eu"}]}})
        m = self.p.listar_meddpicc(self.uid, "Delta")
        self.assertIn("🎯 Legislativo digital · Câmara Delta · MEDDPICC 3/16", m)
        self.assertIn("🟢 Economic buyer (2): presidente assina (segundo Presidente Teste)", m)
        self.assertIn("🟡 Champion (1): Beto parece apoiar → confirmar com o cliente", m)
        self.assertIn("⚪ Paper process: nada registrado → perguntar a modalidade de compra", m)
        # Oportunidade aberta sem decisor citado nas conversas acende o amarelo
        self.assertIn("nenhum decisor ou defensor citado", self.p.saude_contas(self.uid))

    def test_mapa_e_maturidade(self):
        self.falar("a Câmara Delta usa Fortinet e não tem SOC; segurança deles é fraca", {"crm": {
            "mapa": [{"conta": "Câmara Delta", "oferta": "firewall Fortinet", "situacao": "concorrente",
                      "concorrente": "Fortinet"},
                     {"conta": "Câmara Delta", "oferta": "SOC", "situacao": "sem_solucao"}],
            "maturidade": [{"conta": "Câmara Delta", "dominio": "ciberseguranca", "nivel": 1, "nota": "sem SOC",
                            "dito_por": "eu"}]}})
        self.repo.salvar_oportunidade(self.uid, self.mid, self.conta, "Câmeras e VMS", "ganha", 30_000_00, None)
        m = self.p.mostrar_mapa(self.uid, "Câmara Delta")
        self.assertIn("⚔️ Segurança de rede (Check Point): tem com concorrente (Fortinet)", m)
        self.assertIn("🎯 SOC 24x7 (MSSP): sem solução", m)
        self.assertIn("✅ Videomonitoramento e analíticos: tem conosco", m)
        self.assertIn("SOC 24x7 (MSSP) — não tem solução; maturidade 1", m)
        self.assertIn("Cibersegurança: 1 (inexistente ou improvisado) — sem SOC",
                      self.p.mostrar_maturidade(self.uid, "Delta"))
        self.assertIn("Nosso processo comercial: nível", self.p.mostrar_maturidade(self.uid))
        self.assertIn("Câmara Delta:", self.p.mostrar_mapa(self.uid))

    def test_radar_de_risco(self):
        apostas = self.repo.criar_conta_relacionamento(self.uid, "Betnova Teste", "cliente")
        op = self.repo.salvar_oportunidade(self.uid, self.mid, apostas, "SOC Betnova", "ganha", 200_000_00, None)
        self.repo.salvar_proposta(self.uid, self.mid, {"conta_id": apostas, "oportunidade_id": op, "tipo": "pricing",
                                                       "valor_centavos": 200_000_00, "impostos_centavos": 20_000_00,
                                                       "lucro_centavos": 50_000_00, "custo_centavos": 130_000_00,
                                                       "dados": {}, "conferencia": []})
        self.repo.salvar_oportunidade(self.uid, self.mid, self.conta, "Câmeras", "proposta", 10_000_00, None)
        r = self.falar("a proibição das bets é risco alto; a Betnova é de apostas", {"crm": {
            "fatores": [{"titulo": "Proibição das bets (MP fictícia)", "direcao": "ameaca", "segmentos": ["apostas"],
                         "situacao": "em_vigor", "intensidade": "alta", "fonte_url": "https://exemplo.com/noticia"}],
            "segmentos": [{"conta": "Betnova Teste", "segmento": "bets"}]}})
        self.assertIn("🧭 Fator no radar: Proibição das bets (MP fictícia) · ameaça alta · apostas", r)
        self.assertIn("🏷️ Betnova Teste: segmento apostas.", r)
        rk = self.p.listar_riscos(self.uid)
        self.assertIn("Contas atingidas: Betnova Teste", rk)
        self.assertIn("Exposição: faturamento R$ 200.000,00 (100% do faturamento)", rk)
        self.assertIn("⚠️ Maior conta: Betnova Teste com 100% do faturamento (acima de 30%", rk)
        self.assertIn("Sem segmento: Câmara Delta", rk)
        self.assertIn("risco no segmento apostas", self.p.saude_contas(self.uid, "Betnova"))
        # O mesmo fator de novo, revertido: atualiza em vez de duplicar, e sai da saúde
        r = self.falar("a proibição das bets caiu", {"crm": {"fatores": [{
            "titulo": "Proibição das bets (MP fictícia)", "direcao": "ameaca", "segmentos": ["apostas"],
            "situacao": "revertido", "intensidade": "alta"}]}})
        self.assertIn("🧭 Fator atualizado", r)
        self.assertEqual(len(self.repo.crm(self.uid, "fatores_risco")), 1)
        self.assertNotIn("risco no segmento", self.p.saude_contas(self.uid, "Betnova"))
        self.assertIn("segmento legislativo", self.p.definir_segmento(self.uid, ["Câmara", "Delta", "legislativo"]))
        self.assertIn("Não conheço esse segmento", self.p.definir_segmento(self.uid, ["Delta", "astrologia"]))

    def test_alertas_briefing_contas_e_consulta(self):
        self.falar("liguei para a Câmara Delta", {"crm": {"atividades": [{
            "conta": "Câmara Delta", "tipo": "call", "data": dia(-70), "situacao": "feita", "fase": "relacionamento",
            "resumo": "Conversa sobre câmeras"}]}})
        alertas = self.p.alertas_crm(self.uid)
        self.assertTrue(alertas[0].startswith("🔴 Câmara Delta: 70 dias sem contato"))
        self.assertIn("🩺 Saúde: 🔴 · 2 alerta(s), veja /saude", self.p.listar_contas(self.uid))
        self.p.receber(self.uid, Entrada("telegram", "texto", "quando falei com a Câmara Delta pela última vez?"))
        enviado = self.cli.pedidos[-1]
        self.assertIn("[A1] (atividade) • ", enviado)
        self.assertIn("Call · Câmara Delta", enviado)
        self.assertIn("[S1] (saúde calculada pelo Peraí) 🔴 Câmara Delta", enviado)

    def test_briefing_da_manha_traz_os_alertas(self):
        self.falar("liguei para a Câmara Delta", {"crm": {"atividades": [{
            "conta": "Câmara Delta", "tipo": "call", "data": dia(-70), "situacao": "feita", "fase": "relacionamento"}]}})
        self.cli.respostas = [{"resposta": "☀️ Bom dia! Agenda livre.", "registros_citados": [], "sem_informacao": False}]
        b = str(self.p.briefing_automatico(self.uid, 7))
        self.assertIn("🩺 Contas que pedem atenção:\n🔴 Câmara Delta: 70 dias sem contato", b)
        self.assertIn("Tudo em /saude", b)

    def test_plano_de_conta(self):
        # D-103: projeto de R$ 500 mil faz a conta ser chave; sem plano, acende o amarelo.
        op = self.repo.salvar_oportunidade(self.uid, self.mid, self.conta, "Videomonitoramento", "ganha",
                                           600_000_00, None)
        self.falar("visita", {"crm": {"atividades": [{
            "conta": "Câmara Delta", "tipo": "visita", "data": dia(-3), "situacao": "feita", "fase": "pos_venda",
            "proximo_passo": "agendar workshop", "participantes": [{"nome": "Presidente Teste", "papel": "decisor"},
                                                                   {"nome": "Beto Teste", "papel": "tecnico"}]}]}})
        self.assertIn("conta-chave sem plano de conta → montar o plano (/plano Câmara)", self.p.saude_contas(self.uid))
        self.assertIn("Câmara Delta (conta-chave): sem plano", self.p.mostrar_plano(self.uid))
        ano = int(_agora().date().isoformat()[:4])
        r = self.falar("o plano da Câmara Delta", {"crm": {"planos": [{
            "conta": "Câmara Delta", "ano": ano, "objetivos": ["Renovar o SOC", "Vender o Legislativo Digital"],
            "iniciativas": ["Workshop de cibersegurança"], "meta": "1 milhão", "revisao": dia(-1)}]}})
        self.assertIn("📋 Plano de Câmara Delta: 2 objetivo(s), 1 iniciativa(s), meta R$ 1.000.000,00", r)
        pl = self.p.mostrar_plano(self.uid, "Delta")
        self.assertIn(f"📋 Plano de conta · Câmara Delta · {ano} · conta-chave", pl)
        self.assertIn("• Renovar o SOC", pl)
        self.assertIn("💰 Meta R$ 1.000.000,00 · ganho em", pl)
        self.assertIn("• Presidente Teste (decisor) · último contato", pl)
        self.assertIn("Falta conhecer: defensor, financeiro.", pl)
        self.assertIn("(atrasada)", pl)
        s = self.p.saude_contas(self.uid)
        self.assertNotIn("sem plano de conta", s)
        self.assertIn("plano de conta para revisar desde", s)
        # Uma fala depois só com a nova revisão não apaga os objetivos
        self.falar("revisar o plano da Câmara em 3 meses", {"crm": {"planos": [{
            "conta": "Câmara Delta", "revisao": dia(90)}]}})
        pl = self.p.mostrar_plano(self.uid, "Delta")
        self.assertIn("• Renovar o SOC", pl)
        self.assertNotIn("(atrasada)", pl)
        self.assertIn("Plano de conta: 1 de 1 conta(s)-chave com plano", self.p.mostrar_maturidade(self.uid))

    def test_prazos_de_contrato_na_saude(self):
        parceiro = self.repo.criar_conta_relacionamento(self.uid, "Hub Teste", "parceiro")
        self.repo.salvar_contrato(self.uid, self.mid, {"conta_id": parceiro, "titulo": "Adesão ao Hub",
                                                       "vigencia_ate": dia(20), "valor_centavos": 120_000_00,
                                                       "dados": {}}, [])
        mid2 = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="y"))
        k = self.repo.salvar_contrato(self.uid, mid2, {"conta_id": parceiro, "titulo": "Parceria assinada",
                                                       "vigencia_ate": None, "valor_centavos": 30_000_00, "dados": {}},
                                      [{"n": 1, "descricao": "Entrada", "tipo": "pagar", "valor_centavos": 7_500_00,
                                        "regra": None, "vencimento": dia(-2)},
                                       {"n": 2, "descricao": "Parcela", "tipo": "pagar", "valor_centavos": 7_500_00,
                                        "regra": None, "vencimento": dia(3)}])
        self.repo.mudar_contrato(self.uid, k, dia(-30), self.repo.contratos(self.uid)[0]["parcelas"])
        s = self.p.saude_contas(self.uid, "Hub Teste")
        self.assertIn("proposta “Adesão ao Hub” vale até", s)
        self.assertIn("🔴 parcela 1 de R$ 7.500,00 venceu em", s)
        self.assertIn("🟡 parcela 2 de R$ 7.500,00 vence em", s)
        self.assertIn("pagar e avisar “paguei a parcela 1”", s)

    def test_revisao_semanal_e_guia(self):
        from datetime import datetime
        from unittest import mock
        segunda = datetime(2026, 11, 16, 7, 0)
        with mock.patch("perai.servico._agora", return_value=segunda):
            op = self.repo.salvar_oportunidade(self.uid, self.mid, self.conta, "Legislativo digital", "negociacao",
                                               80_000_00, None)
            self.falar("visita", {"crm": {"atividades": [
                {"conta": "Câmara Delta", "tipo": "visita", "data": "2026-11-12", "situacao": "feita",
                 "fase": "negociacao", "proximo_passo": "proposta",
                 "participantes": [{"nome": "Ana Teste", "papel": "decisor"}]},
                {"conta": "Câmara Delta", "tipo": "reuniao", "data": "2026-11-19", "situacao": "agendada",
                 "resumo": "Apresentar proposta"}]}})
            self.falar("plano", {"crm": {"planos": [{"conta": "Câmara Delta", "ano": 2026, "meta": "1 milhão",
                                                      "objetivos": ["Fechar o Legislativo"], "revisao": "2026-11-20"}]}})
            sem = self.p.revisao_semana(self.uid)
            self.assertIn("📊 Revisão da semana das contas (09/11 a 16/11)", sem)
            self.assertIn("• 1 atividade(s) com 1 conta(s): visita 1", sem)
            self.assertIn("• 19/11 reunião com Câmara Delta", sem)
            self.assertIn("• 20/11 revisão do plano de Câmara Delta", sem)
            self.assertIn("📈 Funil aberto: 1 oportunidade(s), R$ 80.000,00; 1 com MEDDPICC abaixo de 8/16", sem)
            self.assertIn("🎯 Metas: Câmara Delta 0% de R$ 1.000.000,00", sem)
            self.cli.respostas = [{"resposta": "☀️ Bom dia!", "registros_citados": [], "sem_informacao": False}]
            b = str(self.p.briefing_automatico(self.uid, 7))
            self.assertIn("📊 Revisão da semana das contas", b)
            self.assertNotIn("🩺 Contas que pedem atenção", b)       # na segunda, a revisão substitui
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 11, 17, 7, 0)):
            self.cli.respostas = [{"resposta": "☀️ Bom dia!", "registros_citados": [], "sem_informacao": False}]
            self.assertNotIn("📊 Revisão da semana", str(self.p.briefing_automatico(self.uid, 7)))
        self.assertIn("🧭 Guia do CRM no Peraí", self.p.guia_crm())
        self.assertIn("/plano [conta]", self.p.guia_crm())

    def test_data_de_criacao_no_fuso_de_brasilia(self):
        self.assertEqual(crm.dia_local("2026-11-12T00:30:00+00:00"), "2026-11-11")
        self.assertEqual(crm.dia_local("2026-11-11T21:30:00-03:00"), "2026-11-11")
        self.assertEqual(crm.dia_local("2026-11-11"), "2026-11-11")

    def test_noticias_por_segmento(self):
        from perai import noticias
        rss = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>
<item><title>Governo fict\xc3\xadcio proibe apostas online - Jornal Teste</title>
<link>https://news.google.com/rss/articles/AAA?oc=5</link><pubDate>Mon, 09 Nov 2026 12:00:00 GMT</pubDate>
<source url="https://jornal.teste">Jornal Teste</source></item>
<item><title>Casa de apostas lan\xc3\xa7a promo\xc3\xa7\xc3\xa3o - Blog Teste</title>
<link>https://news.google.com/rss/articles/BBB?oc=5</link><pubDate>Tue, 10 Nov 2026 12:00:00 GMT</pubDate>
<source url="https://blog.teste">Blog Teste</source></item></channel></rss>"""
        itens = noticias.ler_rss(rss)
        self.assertEqual(itens[0]["titulo"], "Governo fictício proibe apostas online")
        self.assertEqual((itens[0]["fonte"], itens[0]["data"]), ("Jornal Teste", "2026-11-09"))
        self.assertEqual(noticias.ler_rss(b"<html>nada"), [])
        self.assertIn("news.google.com/rss/search?q=apostas", noticias.url("apostas"))

        self.assertIn("/segmento", str(self.p.sugerir_noticias(self.uid)))      # sem segmento, pede
        conta = self.repo.criar_conta_relacionamento(self.uid, "Betnova Teste", "cliente")
        self.p.definir_segmento(self.uid, ["Betnova", "apostas"])
        baixados = []
        self.p._baixar = lambda endereco: baixados.append(endereco) or rss
        self.cli.respostas = [{"fatores": [
            {"noticia": "N1", "titulo": "Proibição fictícia das apostas online", "direcao": "ameaca",
             "segmentos": ["apostas", "astrologia"], "intensidade": "alta", "situacao": "em_vigor",
             "por_que": "Clientes de apostas podem encerrar contratos."},
            {"noticia": "N9", "titulo": "Inventada", "direcao": "ameaca"}]}]
        r = self.p.sugerir_noticias(self.uid)
        self.assertEqual(len(baixados), 1)
        self.assertIn("1. ⚠️ Proibição fictícia das apostas online · ameaça alta · apostas", r)
        self.assertIn("Segundo Jornal Teste, 09/11: “Governo fictício proibe apostas online”", r)
        self.assertNotIn("Inventada", r)                       # código de notícia que não existe
        self.assertEqual(r.botoes, (("🧭 Registrar 1 no radar", "nr:1"),))
        self.assertEqual(self.repo.crm(self.uid, "fatores_risco"), [])   # nada entra sem o toque
        ok = self.p.registrar_noticia(self.uid, "1")
        self.assertIn("🧭 No radar: Proibição fictícia das apostas online (ameaça alta, apostas)", ok)
        f = self.repo.crm(self.uid, "fatores_risco")[0]
        self.assertEqual(f["fonte_url"], "https://news.google.com/rss/articles/AAA?oc=5")
        msg = self.repo.b.mensagens[f["mensagem_id"]]["texto_original"]
        self.assertIn("Segundo Jornal Teste (2026-11-09)", msg)          # fonte e quem disse (D-040, D-044)
        self.assertIn("já está no radar", self.p.registrar_noticia(self.uid, "1"))
        self.assertIn("expirou", self.p.registrar_noticia(self.uid, "7"))
        self.assertIn("risco no segmento apostas", self.p.saude_contas(self.uid, "Betnova"))
        # A mesma manchete não volta na próxima busca
        self.assertIn("Nenhuma notícia nova", str(self.p.sugerir_noticias(self.uid)))

    def test_repositorio_recusa_tabela_ou_coluna_fora_da_lista(self):
        with self.assertRaises(ValueError):
            self.repo.salvar_crm(self.uid, "usuarios", {"nome": "x"})
        with self.assertRaises(ValueError):
            self.repo.salvar_crm(self.uid, "atividades", {"tipo": "call", "usuario_id": "outro"})
        i = self.repo.salvar_crm(self.uid, "sinais_conta", {"tipo": "outro", "descricao": "x", "situacao": "aberto"})
        with self.assertRaises(ValueError):
            self.repo.mudar_crm(self.uid, "sinais_conta", i, {"descricao": "y"})
        self.assertFalse(self.repo.mudar_crm("outro-usuario", "sinais_conta", i, {"situacao": "resolvido"}))


if __name__ == "__main__":
    unittest.main()

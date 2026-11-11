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

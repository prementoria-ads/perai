"""Oportunidades por conta (D-090): abrir, mudar de etapa, funil e respostas com fonte."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import contas, extracao
from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.extracoes, self.pedidos = [], []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append(usuario)
        if tarefa == "extracao":
            return Resposta(json.dumps(self.extracoes.pop(0) if self.extracoes else {}), 1, 1, 1, 0.0)
        if tarefa == "roteamento":
            return Resposta('{"tipo": "%s"}' % ("pergunta" if "?" in usuario else "registro"), 1, 1, 1, 0.0)
        return Resposta(json.dumps({"resposta": "Uma proposta de R$ 80.000,00 com o Grupo Integrado.",
                                    "registros_citados": ["O1"], "sem_informacao": False}), 1, 1, 1, 0.0)


class TesteOportunidades(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.cli = Cli()
        self.p = Perai(self.repo, self.cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                       Path(tempfile.mkdtemp()))
        self.repo.criar_conta_relacionamento(self.uid, "Grupo Integrado", "cliente")

    def test_interpretar(self):
        ext = extracao.interpretar("1", json.dumps({
            "oportunidades": [{"conta": "Integrado", "titulo": "switches HP", "etapa": "Proposta", "valor": "80.000"},
                              {"conta": "Coamo", "titulo": "firewall", "etapa": "xyz", "valor": None}],
            "ajustes_oportunidades": [{"oportunidade": "[O1]", "etapa": "negociação"}, {"oportunidade": "G1"}]}))
        self.assertEqual(ext.oportunidades[0].etapa, "proposta")
        self.assertEqual(ext.oportunidades[0].valor_centavos, 8000000)
        self.assertEqual(ext.oportunidades[1].etapa, "prospeccao")
        self.assertIsNone(ext.oportunidades[1].valor_centavos)
        self.assertEqual([(a.codigo, a.etapa) for a in ext.ajustes_oportunidades], [("O1", "negociacao")])

    def test_abrir_mudar_e_funil(self):
        self.cli.extracoes = [
            {"oportunidades": [{"conta": "Integrado", "titulo": "switches HP", "etapa": "proposta", "valor": "80000"}]},
            {"oportunidades": [{"conta": "Coamo", "titulo": "firewall", "etapa": "prospeccao"}]},
            {"ajustes_oportunidades": [{"oportunidade": "O2", "etapa": "ganha", "valor": "95000"}]},
        ]
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "mandei proposta de 80 mil de switches pro Integrado"))
        self.assertIn("📈 Oportunidade: Grupo Integrado · switches HP · proposta · R$ 80.000,00", str(r))
        self.p.receber(self.uid, Entrada("telegram", "texto", "a Coamo quer cotação de firewall"))
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "o Integrado aprovou, fechou em 95 mil"))
        self.assertIn("[O2] Grupo Integrado | switches HP | proposta | R$ 80.000,00", self.cli.pedidos[-1])
        self.assertIn("proposta → ganha", str(r))
        self.assertEqual(self.repo.b.oportunidade_historico[0]["anterior"], "proposta")    # histórico (D-040)
        funil = self.p.listar_oportunidades(self.uid)
        self.assertIn("Ganha (projeto) · 1 · R$ 95.000,00", funil)
        self.assertIn("Em aberto: 1 · R$ 0,00 + 1 sem valor", funil)
        self.assertIn("📈 Oportunidades: 1 ganha(s) (R$ 95.000,00)", self.p.listar_contas(self.uid))

    def test_encaminhado_nao_abre(self):
        self.cli.extracoes = [{"oportunidades": [{"conta": "Coamo", "titulo": "firewall", "etapa": "proposta"}]}]
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "Mensagem encaminhada de Fulano: proposta firewall Coamo",
                                             encaminhada="Fulano"))
        self.assertIn("não veio de você", str(r))
        self.assertEqual(self.repo.oportunidades(self.uid), [])

    def test_pergunta_recebe_oportunidades_da_conta(self):
        self.cli.extracoes = [
            {"oportunidades": [{"conta": "Integrado", "titulo": "switches HP", "etapa": "proposta", "valor": "80000"}]}]
        self.p.receber(self.uid, Entrada("telegram", "texto", "mandei proposta de switches pro Integrado"))
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "que propostas tenho com o Integrado?"))
        enviado = self.cli.pedidos[-1]
        self.assertIn("[O1] (oportunidade; conta Grupo Integrado; etapa Proposta;", enviado)
        self.assertIn("[OT] (total calculado pelo Peraí) oportunidades em aberto listadas acima: 1, somando R$ 80.000,00",
                      enviado)
        self.assertIn("R$ 80.000,00", str(r))

    def test_painel(self):
        from perai.painel import Painel
        from perai.armazenamento import NovaMensagem
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        cid = self.repo.contas_relacionamento(self.uid)[0]["id"]
        self.repo.salvar_oportunidade(self.uid, mid, cid, "switches HP", "negociacao", None, "2026-12-15")
        html = Painel(self.repo, None, "https://exemplo").pagina_painel(self.uid, "csrf")
        self.assertIn("📈 Oportunidades", html)
        self.assertIn("Negociação · valor não informado", html)


if __name__ == "__main__":
    unittest.main()

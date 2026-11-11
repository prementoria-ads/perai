"""CRM de contas no Peraí (D-089): custo por conta, rateio em partes iguais e /contas."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import contas
from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Despesa
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai


class TesteRegras(unittest.TestCase):
    def test_ratear_conserva_centavos(self):
        self.assertEqual(contas.ratear(10000, 3), [3334, 3333, 3333])
        self.assertEqual(sum(contas.ratear(30001, 7)), 30001)
        self.assertEqual(contas.ratear(500, 1), [500])

    def test_mesma_conta(self):
        self.assertTrue(contas.mesma_conta("Grupo Integrado", "Integrado"))
        self.assertTrue(contas.mesma_conta("Coamo", "COAMO"))
        self.assertFalse(contas.mesma_conta("Coamo", "Copacol"))
        self.assertFalse(contas.mesma_conta("L8", "L8 Group Governo"))   # curto demais para casar sozinho


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.pedidos = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append(usuario)
        if tarefa == "roteamento":
            return Resposta('{"tipo": "pergunta"}', 1, 1, 1, 0.0)
        return Resposta(json.dumps({"resposta": "R$ 250,00 com o Grupo Integrado.",
                                    "registros_citados": ["K1"], "sem_informacao": False}), 1, 1, 1, 0.0)


class TesteFluxo(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.cli = Cli()
        self.p = Perai(self.repo, self.cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                       Path(tempfile.mkdtemp()))
        self.repo.criar_conta_relacionamento(self.uid, "Grupo Integrado", "cliente")

    def despesa(self, valor, data, *clientes, descricao="Hotel"):
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto",
                                                               texto_original=descricao))
        did = self.repo.salvar_despesa(self.uid, mid, None, Despesa(descricao, valor, data, "hospedagem"), "texto")
        for c in clientes:
            self.repo.vincular_despesa(self.uid, did, "cliente", cliente=c)
        return did

    def test_custo_com_rateio(self):
        self.despesa(30000, "2026-11-03", "Integrado", "Coamo")        # hotel rateado
        self.despesa(10000, "2026-11-04", "Grupo Integrado", descricao="Almoço")
        self.despesa(None, "2026-11-05", "Integrado", descricao="Pedágio")
        self.despesa(5000, "2026-11-05", descricao="Café sem cliente")
        custos = {c.nome: c for c in self.p._custos_contas(self.uid)}
        self.assertEqual(custos["Grupo Integrado"].total_centavos, 25000)
        self.assertEqual(custos["Grupo Integrado"].sem_valor, 1)
        self.assertEqual(custos["Coamo"].total_centavos, 15000)
        self.assertEqual(len(custos), 2)

    def test_listar_contas(self):
        self.despesa(30000, "2026-11-03", "Integrado", "Coamo")
        texto = self.p.listar_contas(self.uid)
        self.assertIn("🏢 Grupo Integrado", texto)
        self.assertIn("💰 Custo: R$ 150,00 em 1 despesa(s)", texto)
        self.assertIn("🏢 Coamo", texto)
        self.assertIn("Total ligado a clientes e parceiros: R$ 300,00", texto)

    def test_pergunta_de_custo_recebe_a_soma_do_codigo(self):
        self.despesa(30000, "2026-11-03", "Integrado", "Coamo")
        self.despesa(10000, "2026-11-04", "Integrado", descricao="Almoço")
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "quanto já gastei com o Integrado?"))
        enviado = self.cli.pedidos[-1]
        self.assertIn("[K1] (custo calculado pelo Peraí; conta Grupo Integrado) R$ 250,00 em 2 despesa(s)", enviado)
        self.assertIn("rateada com Coamo; total R$ 300,00", enviado)
        self.assertNotIn("conta Coamo", enviado)                 # só a conta perguntada
        self.assertIn("R$ 250,00", str(r))

    def test_sem_contas(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("X", 11)
        p = Perai(repo, Cli(), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        self.assertIn("Ainda não há contas", p.listar_contas(uid))


if __name__ == "__main__":
    unittest.main()

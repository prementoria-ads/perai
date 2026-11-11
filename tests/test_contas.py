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

    def test_alocar_partes_ditas(self):
        v = lambda c=None, p=None: {"parte_centavos": c, "parte_percentual": p}
        self.assertEqual(contas.alocar(30000, [v(20000), v(10000)]), ([20000, 10000], None))
        self.assertEqual(contas.alocar(30000, [v(20000), v()]), ([20000, 10000], None))          # "o resto"
        self.assertEqual(contas.alocar(10000, [v(p=70), v()]), ([7000, 3000], None))
        self.assertEqual(contas.alocar(1000, [v(p=33.33), v(p=33.33), v(p=33.34)])[0], [333, 333, 334])
        partes, aviso = contas.alocar(10000, [v(8000), v(5000)])
        self.assertEqual(partes, [5000, 5000])
        self.assertIn("passa do total", aviso)
        partes, aviso = contas.alocar(10000, [v(6000), v(3000)])
        self.assertEqual(partes, [6000, 3000])
        self.assertIn("R$ 10,00 não foram atribuídos", aviso)

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

    def test_rateio_dito_e_periodo(self):
        did = self.despesa(30000, "2026-10-28", "Integrado", "Coamo")
        linhas = self.p._aplicar_partes(self.uid, did, [("Integrado", 20000, None), ("Coamo", None, None)])
        self.assertEqual(linhas, ["🤝 Rateio: Integrado R$ 200,00; Coamo R$ 100,00."])
        self.despesa(10000, "2026-11-04", "Integrado", descricao="Almoço")
        custos = {c.nome: c for c in self.p._custos_contas(self.uid)}
        self.assertEqual(custos["Grupo Integrado"].total_centavos, 30000)
        self.assertEqual(custos["Coamo"].total_centavos, 10000)
        from datetime import date
        texto = self.p.listar_contas(self.uid, date(2026, 11, 1), date(2026, 11, 30))
        self.assertIn("de 01/11/2026 a 30/11/2026", texto)
        self.assertIn("💰 Custo: R$ 100,00 em 1 despesa(s)", texto)
        self.assertNotIn("Coamo", texto.split("Total")[0])
        linha = contas.linha_memoria("K1", custos["Grupo Integrado"])
        self.assertIn("por mês: 10/2026: R$ 200,00; 11/2026: R$ 100,00", linha)

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

    def test_painel_mostra_contas(self):
        from perai.painel import Painel
        self.despesa(30000, "2026-11-03", "Integrado", "Coamo")
        html = Painel(self.repo, None, "https://exemplo").pagina_painel(self.uid, "csrf", aba="contas")
        self.assertIn("🏢 Contas", html)
        self.assertIn("<b>Grupo Integrado</b> · R$ 150,00", html)
        self.assertIn("1 rateada(s)", html)

    def test_sem_contas(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("X", 11)
        p = Perai(repo, Cli(), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        self.assertIn("Ainda não há contas", p.listar_contas(uid))


if __name__ == "__main__":
    unittest.main()

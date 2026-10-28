"""Conversa recente e ajuste de pendências: "muda para as 11h" mexe no lembrete certo."""

import json
import tempfile
import unittest
from pathlib import Path

from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente
from perai.servico import Entrada, Perai


class ClienteRoteiro(Cliente):
    """Devolve respostas de extração em ordem e guarda os pedidos recebidos."""

    def __init__(self, respostas):
        super().__init__()
        self.respostas = list(respostas)
        self.pedidos = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        from perai.llm import Resposta
        self.pedidos.append((tarefa, usuario))
        return Resposta(json.dumps(self.respostas.pop(0)), 1, 1, 1, 0.0)


class TesteAjustes(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 1)

    def perai(self, respostas):
        self.cliente = ClienteRoteiro(respostas)
        return Perai(self.repo, self.cliente, MODELOS["simulado"], TRANSCRITORES["simulado"],
                     MODELOS["simulado"], Path(tempfile.mkdtemp()))

    def test_muda_horario_do_lembrete(self):
        p = self.perai([
            {"registros": [{"conteudo": "Levar o lixo amanhã às 9h", "natureza": "intencao"}],
             "pendencias": [{"descricao": "Levar o lixo", "responsavel": "eu",
                             "prazo": "2026-10-28", "lembrete_em": "2026-10-28T09:00"}]},
            {"registros": [{"conteudo": "Lembrete do lixo para as 11h", "natureza": "intencao"}],
             "ajustes": [{"pendencia": "P1", "lembrete_em": "2026-10-28T11:00", "prazo": "2026-10-28"}]},
        ])
        p.receber(self.uid, Entrada("telegram", "texto", "Me lembra amanhã às 9h de levar o lixo"))
        r = p.receber(self.uid, Entrada("telegram", "texto", "muda para as 11h por favor"))

        # O modelo recebeu a conversa e a pendência com código.
        pedido = self.cliente.pedidos[-1][1]
        self.assertIn("[P1] Levar o lixo", pedido)
        self.assertIn("Você: Me lembra amanhã às 9h de levar o lixo", pedido)

        pendencias = list(self.repo.b.pendencias.values())
        self.assertEqual(len(pendencias), 1)  # não criou outra
        self.assertEqual(pendencias[0]["lembrete_em"], "2026-10-28T11:00")
        self.assertIn("mudou para 28/10 às 11:00", r)
        self.assertNotIn("Prazo", r)  # prazo não mudou
        trilha = [c for c in self.repo.b.correcoes if c["tabela"] == "pendencias"]
        self.assertEqual((trilha[0]["valor_anterior"], trilha[0]["valor_novo"]),
                         ("2026-10-28T09:00", "2026-10-28T11:00"))

    def test_ja_fiz(self):
        p = self.perai([
            {"pendencias": [{"descricao": "Avisar o Júlio", "responsavel": "eu"}]},
            {"ajustes": [{"pendencia": "[P1]", "situacao": "concluida"}]},
        ])
        p.receber(self.uid, Entrada("telegram", "texto", "Preciso avisar o Júlio"))
        r = p.receber(self.uid, Entrada("telegram", "texto", "já avisei"))
        self.assertIn("Marquei como feito: Avisar o Júlio", r)
        _, abertas = self.repo.memoria(self.uid)
        self.assertEqual(abertas, [])

    def test_so_duvida(self):
        p = self.perai([{"duvidas": ["Quais atividades devem ser canceladas?"]}])
        r = p.receber(self.uid, Entrada("telegram", "texto", "cancela as outras"))
        self.assertTrue(r.startswith("Antes de mexer em algo, uma dúvida"))

    def test_codigo_inexistente_e_ignorado(self):
        p = self.perai([{"ajustes": [{"pendencia": "P9", "situacao": "descartada"}]}])
        p.receber(self.uid, Entrada("telegram", "texto", "pode cancelar"))
        self.assertEqual(self.repo.b.correcoes, [])


if __name__ == "__main__":
    unittest.main()

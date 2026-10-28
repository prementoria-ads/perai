"""Modelo de reserva: quando o principal falha, a reserva responde (D-002)."""

import json
import tempfile
import unittest
from pathlib import Path

from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES, modelo_por_nome
from perai.llm import Cliente, ErroModelo, Resposta
from perai.servico import Entrada, Perai


class ClienteComFalha(Cliente):
    """O principal (google) sempre responde 503; a reserva usa o simulador."""

    def __init__(self):
        super().__init__()
        self.chamados = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.chamados.append(modelo.apelido)
        if modelo.fornecedor == "google":
            raise ErroModelo("generateContent respondeu 503: high demand")
        if tarefa == "roteamento":
            return Resposta(json.dumps({"tipo": "registro"}), 1, 1, 1, 0.0)
        return Resposta(json.dumps({"entidades": [{"nome": "Júlio", "tipo": "pessoa"}],
                                    "registros": [{"id": "r1", "texto": "Combinou avisar o Júlio",
                                                   "natureza": "intencao", "contexto": "profissional",
                                                   "entidades": ["Júlio"]}],
                                    "pendencias": []}), 1, 1, 1, 0.0)


class TesteReserva(unittest.TestCase):
    def test_nome_livre(self):
        m = modelo_por_nome("google:gemini-2.5-flash")
        self.assertEqual((m.fornecedor, m.id_api), ("google", "gemini-2.5-flash"))
        self.assertIs(modelo_por_nome("haiku"), MODELOS["haiku"])
        with self.assertRaises(ValueError):
            modelo_por_nome("inexistente")

    def _perai(self, reserva):
        repo = RepositorioMemoria()
        cliente = ClienteComFalha()
        p = Perai(repo, cliente, MODELOS["gemini-flash"], TRANSCRITORES["simulado"],
                  MODELOS["gemini-flash"], Path(tempfile.mkdtemp()), reserva=reserva)
        uid = repo.criar_usuario("Nelson", 1)
        return p, uid, cliente, repo

    def test_reserva_assume(self):
        p, uid, cliente, repo = self._perai(MODELOS["simulado"])
        r = p.receber(uid, Entrada("telegram", "texto", "Combinei de avisar o Júlio e o Rafael."))
        self.assertIn("Anotado", r)
        self.assertIn("simulado", cliente.chamados)
        self.assertTrue(any(c.get("erro") for c in repo.b.chamadas))

    def test_sem_reserva_avisa(self):
        p, uid, _, repo = self._perai(None)
        r = p.receber(uid, Entrada("telegram", "texto", "Combinei de avisar o Júlio."))
        self.assertIn("Guardei a sua mensagem original", r)
        self.assertEqual(len(repo.b.mensagens), 1)


if __name__ == "__main__":
    unittest.main()

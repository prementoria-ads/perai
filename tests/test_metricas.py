"""Avaliação das respostas e painel do teste de 4 semanas."""

import json
import tempfile
import unittest
from pathlib import Path

from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai, _agora
from perai.telegram import MensagemTelegram


class ClienteRoteiro(Cliente):
    def __init__(self, respostas):
        super().__init__()
        self.respostas = list(respostas)

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        return Resposta(json.dumps(self.respostas.pop(0)), 1, 1, 1234, 0.0)


class TesteMetricas(unittest.TestCase):
    def test_avaliar_e_painel(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        p = Perai(repo, ClienteRoteiro([
            {"registros": [{"conteudo": "Reunião com o Wagner na Romagnole", "natureza": "confirmado"}]},
            {"resposta": "Você vai falar da proposta.", "registros_citados": ["X"], "sem_informacao": False},
        ]), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"], Path(tempfile.mkdtemp()))
        p.receber(uid, Entrada("telegram", "texto", "Reunião com o Wagner na Romagnole"))
        rid_registro = next(iter(repo.b.registros))
        p.cliente.respostas[0]["registros_citados"] = [rid_registro]
        r = p.receber(uid, Entrada("telegram", "texto", "me prepara pra reunião com o Wagner"))
        self.assertEqual([b[0] for b in r.botoes], ["👍 Ajudou", "😐 Neutro", "👎 Atrapalhou"])
        bot = Bot(None, p, {10}, Path(tempfile.mkdtemp()))
        self.assertIn("ajudou", bot.tratar(MensagemTelegram(1, 10, "N", "botao", r.botoes[0][1])))
        painel = bot.tratar(MensagemTelegram(2, 10, "N", "comando", "/metricas"))
        self.assertIn("1 dias com captura", painel)
        self.assertIn("Briefings que ajudaram: 1", painel)
        self.assertIn("Correções: 0 de 1 registros", painel)
        self.assertIn("1.2 s", painel)

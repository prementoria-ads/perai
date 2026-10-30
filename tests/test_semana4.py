"""Teste das 30 perguntas da semana 4 (D-057): /teste roda as perguntas isoladas, guarda numa rodada
separada (fora do /metricas) e a web mostra respostas e fontes prontas para a planilha."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Extracao, Registro
from perai.llm import Cliente, Resposta
from perai.painel import pagina_teste
from perai.servico import Perai, resultado_teste
from perai.telegram import MensagemTelegram


class Cli(Cliente):
    def __init__(self, rid):
        super().__init__()
        self.rid = rid
        self.pedidos = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append(usuario)
        if "Gazin" in usuario.split("Pergunta:")[-1]:
            dados = {"resposta": "Wagner pediu a proposta\ttabelada", "registros_citados": [self.rid],
                     "sem_informacao": False}
        else:
            dados = {"resposta": "", "registros_citados": [], "sem_informacao": True}
        return Resposta(json.dumps(dados), 1, 1, 1, 0.0)


class TelegramFalso:
    def __init__(self):
        self.enviadas = []

    def enviar(self, chat, texto, botoes=()):
        self.enviadas.append(texto)


class TesteSemana4(unittest.TestCase):
    def test_rodada(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        mapa = repo.salvar_extracao(uid, mid, Extracao("1", registros=[
            Registro("R1-1", "1", "Wagner, da Gazin, pediu a proposta", "confirmado", "Wagner", None, [], 0.9, False)]))
        cli = Cli(mapa["R1-1"])
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        tg = TelegramFalso()
        bot = Bot(tg, p, {10}, Path(tempfile.mkdtemp()))
        self.assertIn("uma pergunta por linha", bot.tratar(MensagemTelegram(1, 10, "N", "comando", "/teste")))
        r = bot.tratar(MensagemTelegram(2, 10, "N", "comando",
                                        "/teste\n1. O que a Gazin pediu?\n2) Quem é o Rodrigo?\n- \n3. Qual o CNPJ da L8?"))
        self.assertIn("Rodando 3 perguntas", tg.enviadas[0])
        self.assertIn("Pronto: 3 respostas (2 sem informação)", r)
        self.assertIn("1. O que a Gazin pediu?", r)
        self.assertTrue(all("Conversa recente" not in x for x in cli.pedidos))  # cada pergunta sozinha
        res = resultado_teste(repo, uid)
        self.assertEqual([x["pergunta"] for x in res], ["O que a Gazin pediu?", "Quem é o Rodrigo?", "Qual o CNPJ da L8?"])
        self.assertEqual(res[0]["fontes"][0][13:], "Wagner: Wagner, da Gazin, pediu a proposta")
        pagina = pagina_teste(res)
        self.assertIn("Wagner pediu a proposta tabelada\t[", pagina)
        self.assertIn("sem fonte", pagina)
        # Fora do /metricas: nada de respostas contadas.
        m = repo.metricas(uid, datetime.now() - timedelta(days=1))
        self.assertEqual(m["respostas"], 0)
        # Uma rodada nova substitui a anterior na página.
        p.rodar_teste(uid, ["Quem é o Rodrigo?"])
        self.assertEqual(len(resultado_teste(repo, uid)), 1)


if __name__ == "__main__":
    unittest.main()

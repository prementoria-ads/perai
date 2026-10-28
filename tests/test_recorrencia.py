"""Repetição ("todo dia 1") e botões Feito / Adiar nos lembretes."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from perai import recorrencia
from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai, _agora
from perai.telegram import MensagemTelegram


class TesteRegra(unittest.TestCase):
    def test_proxima(self):
        p = recorrencia.proxima
        self.assertEqual(p("mensal:1", date(2026, 10, 28)), date(2026, 11, 1))
        self.assertEqual(p("mensal:1", date(2026, 11, 1)), date(2026, 12, 1))
        self.assertEqual(p("mensal:31", date(2027, 1, 31)), date(2027, 2, 28))   # mês curto
        self.assertEqual(p("mensal:1", date(2026, 12, 15)), date(2027, 1, 1))
        self.assertEqual(p("semanal:0", date(2026, 10, 28)), date(2026, 11, 2))  # quarta -> segunda
        self.assertEqual(p("semanal:2", date(2026, 10, 28)), date(2026, 11, 4))  # quarta -> quarta seguinte
        self.assertEqual(p("diaria", date(2026, 10, 28)), date(2026, 10, 29))
        self.assertEqual(p("anual:03-15", date(2026, 10, 28)), date(2027, 3, 15))

    def test_valida_e_extenso(self):
        self.assertEqual(recorrencia.valida(" Mensal:1 "), "mensal:1")
        self.assertIsNone(recorrencia.valida("mensal:32"))
        self.assertIsNone(recorrencia.valida("toda hora"))
        self.assertEqual(recorrencia.por_extenso("mensal:1"), "todo dia 1")
        self.assertEqual(recorrencia.por_extenso("semanal:0"), "toda segunda")
        self.assertEqual(recorrencia.por_extenso("semanal:5"), "todo sábado")

    def test_proxima_ocorrencia_mantem_hora(self):
        prazo, lembrete = recorrencia.proxima_ocorrencia("mensal:1", "2026-11-01", "2026-11-01T09:00",
                                                         date(2026, 11, 1))
        self.assertEqual((prazo, lembrete), ("2026-12-01", "2026-12-01T09:00"))
        # Lembrete na sexta 18h de uma reunião toda segunda: continua na sexta anterior.
        prazo, lembrete = recorrencia.proxima_ocorrencia("semanal:0", "2026-11-02", "2026-10-30T18:00",
                                                         date(2026, 10, 30))
        self.assertEqual((prazo, lembrete), ("2026-11-09", "2026-11-06T18:00"))
        # Atrasada (marcada só no dia 5): a próxima continua sendo dia 1 do mês seguinte.
        prazo, _ = recorrencia.proxima_ocorrencia("mensal:1", "2026-11-01", None, date(2026, 11, 5))
        self.assertEqual(prazo, "2026-12-01")


class ClienteRoteiro(Cliente):
    def __init__(self, respostas):
        super().__init__()
        self.respostas = list(respostas)

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        return Resposta(json.dumps(self.respostas.pop(0)), 1, 1, 1, 0.0)


class TelegramFalso:
    def __init__(self):
        self.enviadas = []

    def enviar(self, chat, texto, botoes=()):
        self.enviadas.append((texto, list(botoes)))

    def responder_botao(self, i, texto=""):
        pass

    def tirar_botoes(self, chat, msg):
        pass


class TesteFluxo(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        agora = _agora()
        self.lembrete = (agora - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
        self.prazo = agora.date().isoformat()
        self.perai = Perai(self.repo, ClienteRoteiro([
            {"registros": [{"conteudo": "Mandar a nota da Takemura ao financeiro da L8 todo dia 1",
                            "natureza": "intencao"}],
             "pendencias": [{"descricao": "Mandar a nota da Takemura ao financeiro da L8", "responsavel": "eu",
                             "prazo": self.prazo, "lembrete_em": self.lembrete, "recorrencia": "mensal:1"}]},
        ]), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"], Path(tempfile.mkdtemp()))
        self.tg = TelegramFalso()
        self.bot = Bot(self.tg, self.perai, {10}, Path(tempfile.mkdtemp()))

    def abertas(self):
        return self.repo.memoria(self.uid)[1]

    def test_lembrete_com_botoes_e_proxima_vez(self):
        r = self.perai.receber(self.uid, Entrada("telegram", "texto", "me lembra todo dia 1 às 9h de mandar a nota"))
        self.assertIn("🔁 todo dia 1", r)
        self.bot.enviar_lembretes()
        texto, botoes = self.tg.enviadas[-1]
        self.assertIn("⏰ Lembrete: Mandar a nota", texto)
        self.assertEqual([b[0] for b in botoes], ["✔️ Feito", "⏰ Adiar 1h"])
        # Ao disparar, a próxima vez já fica agendada (mesmo sem ninguém tocar no botão).
        self.assertEqual(len(self.abertas()), 2)
        seguinte = [p for p in self.abertas() if p.prazo != self.prazo][0]
        self.assertTrue(seguinte.lembrete_em.endswith(self.lembrete[11:]))

        feito = botoes[0][1]
        resposta = self.bot.tratar(MensagemTelegram(1, 10, "N", "botao", feito, botao_id="q", mensagem_id=3))
        self.assertIn("✔️ Feito: Mandar a nota", resposta)
        self.assertIn("🔁 Próxima vez", resposta)
        self.assertEqual(len(self.abertas()), 1)  # não criou uma terceira
        self.assertIn("já estava fechada",
                      self.bot.tratar(MensagemTelegram(2, 10, "N", "botao", feito, botao_id="q", mensagem_id=3)))

    def test_adiar(self):
        self.perai.receber(self.uid, Entrada("telegram", "texto", "me lembra todo dia 1 às 9h de mandar a nota"))
        self.bot.enviar_lembretes()
        adiar = self.tg.enviadas[-1][1][1][1]
        resposta = self.bot.tratar(MensagemTelegram(1, 10, "N", "botao", adiar, botao_id="q", mensagem_id=3))
        self.assertIn("⏰ Adiado", resposta)
        self.bot.enviar_lembretes()
        self.assertEqual(len(self.tg.enviadas), 1)  # ainda não venceu de novo

    def test_botao_invalido(self):
        r = self.bot.tratar(MensagemTelegram(1, 10, "N", "botao", "feito:nao-e-uuid", botao_id="q"))
        self.assertIn("Não encontrei", r)

    def test_vencida_sem_lembrete_avanca(self):
        repo, uid = self.repo, self.uid
        from perai.extracao import Extracao, Pendencia, Registro
        ontem = (date.today() - timedelta(days=3)).isoformat()
        ext = Extracao("1", registros=[Registro("R1-1", "1", "Pagar condomínio", "intencao", "eu", None, [], 1, False)],
                       pendencias=[Pendencia("P1-1", "1", "Pagar condomínio", "eu", None, ontem, None, None, "mensal:5")])
        repo.salvar_extracao(uid, repo.salvar_mensagem(uid, __import__("perai.armazenamento").armazenamento.NovaMensagem("teste", "texto", "x")), ext)
        self.assertEqual(self.perai.avancar_recorrencias(), 1)
        self.assertEqual(self.perai.avancar_recorrencias(), 0)  # só uma vez

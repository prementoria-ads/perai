"""Aparência e atalhos do Telegram (D-067) e briefing automático (D-068)."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Extracao, Pendencia
from perai.armazenamento import NovaMensagem
from perai.llm import Cliente
from perai.servico import Perai
from perai.telegram import MensagemTelegram, Telegram, formatar_html

CHAT = 111


class TelegramFalso:
    def __init__(self, recusar_html=False):
        self.chamadas, self.recusar_html = [], recusar_html

    def __call__(self, metodo, url, corpo):
        nome = url.rsplit("/", 1)[-1]
        dados = json.loads(corpo) if corpo else {}
        self.chamadas.append((nome, dados))
        if nome == "sendMessage" and self.recusar_html and "parse_mode" in dados:
            return json.dumps({"ok": False, "description": "Bad Request: can't parse entities"}).encode()
        return json.dumps({"ok": True, "result": True}).encode()

    def enviadas(self):
        return [d for n, d in self.chamadas if n == "sendMessage"]


def simulador(tarefa, sistema, usuario):
    if tarefa == "roteamento":
        return json.dumps({"tipo": "pergunta"})
    if tarefa in ("consulta", "briefing"):
        import re
        ids = re.findall(r"^\[([^\]]+)\]", usuario, flags=re.M)
        if not ids:
            return json.dumps({"resposta": "", "registros_citados": [], "sem_informacao": True})
        return json.dumps({"resposta": "☀️ Bom dia · sexta 30/10\n\n✅ Tarefas\n📌 Mandar a proposta",
                           "registros_citados": ids[:1], "sem_informacao": False})
    return json.dumps({})


class TesteFormatacao(unittest.TestCase):
    def test_titulo_blocos_e_escape(self):
        texto = "📅 Hoje, sexta 30/10\n\n🗓️ Compromissos\n14:00 · Dr <Leonardo> & cia\n\n✅ Anotado."
        html = formatar_html(texto)
        self.assertTrue(html.startswith("<b>📅 Hoje, sexta 30/10</b>"))
        self.assertIn("<b>🗓️ Compromissos</b>\n14:00 · Dr &lt;Leonardo&gt; &amp; cia", html)
        self.assertNotIn("<b>✅ Anotado.</b>", html)  # frase, não nome de bloco

    def test_bloco_longo_recolhido(self):
        itens = "\n".join(f"Fato {i}" for i in range(8))
        html = formatar_html(f"📅 Hoje\n\n📝 O que aconteceu\n{itens}\n\n✅ Ficou para fazer\n📌 Uma")
        self.assertIn("<b>📝 O que aconteceu</b>\n<blockquote expandable>Fato 0", html)
        self.assertIn("<b>✅ Ficou para fazer</b>\n📌 Uma", html)

    def test_uma_linha_so_escapa(self):
        self.assertEqual(formatar_html("a < b"), "a &lt; b")

    def test_html_recusado_vai_em_texto_puro(self):
        falso = TelegramFalso(recusar_html=True)
        Telegram("T", falso).enviar(1, "📅 Hoje\n\n🗓️ Compromissos\n14:00 · x")
        ultima = falso.enviadas()[-1]
        self.assertNotIn("parse_mode", ultima)
        self.assertEqual(ultima["text"], "📅 Hoje\n\n🗓️ Compromissos\n14:00 · x")


class TesteBotUx(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.falso = TelegramFalso()
        self.perai = Perai(self.repo, Cliente(simulador=simulador), MODELOS["simulado"], TRANSCRITORES["simulado"],
                           MODELOS["simulado"], Path(tempfile.mkdtemp()))
        self.bot = Bot(Telegram("T", self.falso), self.perai, {CHAT}, Path(tempfile.mkdtemp()))
        self.uid = self.repo.criar_usuario("Nelson", CHAT)

    def msg(self, texto, tipo="texto"):
        return MensagemTelegram(1, CHAT, "Nelson", tipo, texto, mensagem_id=55)

    def test_anotado_vira_reacao(self):
        self.bot.responder(self.msg("ok"), "✅ Anotado.")
        nomes = [n for n, _ in self.falso.chamadas]
        self.assertEqual(nomes, ["setMessageReaction"])
        self.assertEqual(self.falso.chamadas[0][1]["reaction"], [{"type": "emoji", "emoji": "✍"}])

    def test_start_leva_teclado_fixo(self):
        r = self.bot.tratar(self.msg("/start", "comando"))
        self.bot.responder(self.msg("/start", "comando"), r)
        teclado = self.falso.enviadas()[-1]["reply_markup"]["keyboard"]
        self.assertEqual(teclado[0][0]["text"], "📅 Hoje")

    def test_atalho_vira_pergunta(self):
        with mock.patch.object(self.perai, "receber", return_value="x") as receber:
            self.bot.tratar(self.msg("📅 Hoje"))
        self.assertEqual(receber.call_args[0][1].texto, "o que tenho hoje?")

    def test_briefing_ligar_enviar_uma_vez_e_desligar(self):
        self.assertIn("7h, 13h, 19h", self.bot.tratar(self.msg("/briefing", "comando")))
        self.assertEqual(self.repo.horas_briefing(self.uid), [7, 13, 19])
        self.assertIn("8h, 18h", self.bot.tratar(self.msg("/briefing 8 18h", "comando")))
        self.bot.tratar(self.msg("/briefing 7 13 19", "comando"))
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto",
                                                               texto_original="mandar a proposta"))
        self.repo.salvar_extracao(self.uid, mid, Extracao("1", pendencias=[
            Pendencia("P1-1", "1", "Mandar a proposta", "eu", None, None, None)]))
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 7, 2)):
            primeira = self.perai.briefings_para_enviar()
            segunda = self.perai.briefings_para_enviar()
        self.assertEqual(len(primeira), 1)
        self.assertEqual(segunda, [])
        self.assertTrue(primeira[0][1].startswith("☀️ Bom dia"))
        self.assertTrue(primeira[0][1].botoes)  # avaliação conta no teste (D-057)
        self.assertEqual(self.repo.b.respostas[next(iter(self.repo.b.respostas))]["tipo"], "briefing")
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 9, 0)):
            self.assertEqual(self.perai.briefings_para_enviar(), [])  # fora do horário
        self.bot.tratar(self.msg("/briefing desligar", "comando"))
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 31, 7, 0)):
            self.assertEqual(self.perai.briefings_para_enviar(), [])

    def test_briefing_sem_nada(self):
        self.repo.definir_briefing(self.uid, [19])
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 19, 5)):
            enviados = self.perai.briefings_para_enviar()
        self.assertEqual(enviados[0][1], "🌙 Boa noite! Não anotei nada hoje e não há compromissos amanhã.")


if __name__ == "__main__":
    unittest.main()

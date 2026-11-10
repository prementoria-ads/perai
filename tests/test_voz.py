"""Resposta falada (/voz) e áudio do próprio usuário sem "Pessoa 1" (09/11)."""

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from perai import voz
from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta, Transcricao
from perai.servico import Entrada, Perai
from perai.telegram import MensagemTelegram


def pcm(segundos=0.3):
    return b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(int(24000 * segundos)))


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.contexto = None
        self.extracao = ""

    def transcrever(self, transcritor, audio, contexto=""):
        self.contexto = contexto
        return Transcricao("Pessoa 1: Vou encontrar Eide e Abe, da Zaelly, amanhã.", 5, 10, 0.0)

    def falar(self, texto, modelo, voz_):
        self.falado = texto
        return pcm(), 10, 50

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        if tarefa == "extracao":
            self.extracao = usuario
            return Resposta(json.dumps({"registros": [{"conteudo": "Você vai encontrar Eide e Abe, da Zaelly",
                                                       "natureza": "intencao", "dito_por": "eu"}]}), 1, 1, 1, 0.0)
        return Resposta(json.dumps({"tipo": "registro"}), 1, 1, 1, 0.0)


class TG:
    def __init__(self):
        self.vozes, self.textos = [], []

    def enviar(self, chat, texto, botoes=(), teclado=None):
        self.textos.append(str(texto))

    def enviar_voz(self, chat, ogg):
        self.vozes.append(ogg)

    def reagir(self, *a):
        return False


class TesteVoz(unittest.TestCase):
    def test_falavel_tira_codigos_links_e_fontes(self):
        t = voz.falavel("✅ Anotado.\n📝 Você vai ao CEI [a7deb0e4]\nveja https://x.com\n📎 1 fonte(s)\n```\ncopiar\n```")
        self.assertEqual(t, "Anotado.\nVocê vai ao CEI\nveja")

    def test_pcm_vira_ogg(self):
        self.assertEqual(voz.pcm_para_ogg(pcm())[:4], b"OggS")

    def test_audio_do_dono_e_voz_de_volta(self):
        repo = RepositorioMemoria()
        cli = Cli()
        p = Perai(repo, cli, MODELOS["gemini-flash"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        repo.aceite_automatico = True
        tg = TG()
        bot = Bot(tg, p, {10}, Path(tempfile.mkdtemp()))
        msg = lambda t: MensagemTelegram(1, 10, "Nelson", "comando", t)
        self.assertIn("🔊 Pronto", bot.tratar(msg("/voz ligar")))
        uid = repo.usuario_por_telegram(10)
        audio = Path(tempfile.mkdtemp()) / "a.ogg"
        audio.write_bytes(b"x")
        m = MensagemTelegram(2, 10, "Nelson", "audio", None)
        resposta = p.receber(uid, Entrada("telegram", "audio", None, audio))
        self.assertIn("gravado pelo próprio Nelson", cli.contexto)
        self.assertNotIn("Pessoa 1", cli.extracao)
        bot.responder_com_voz(m, resposta)
        self.assertEqual(len(tg.vozes), 1)
        self.assertEqual(tg.vozes[0][:4], b"OggS")
        self.assertTrue(any(c["finalidade"] for c in repo.b.chamadas if c["modelo"] == voz.modelo_voz()))
        bot.tratar(msg("/voz desligar"))
        bot.responder_com_voz(m, resposta)
        self.assertEqual(len(tg.vozes), 1)


if __name__ == "__main__":
    unittest.main()

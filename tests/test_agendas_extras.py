"""Outras agendas, só leitura (D-064): .ics exportado e livre/ocupado."""

import json
import tempfile
import unittest
import urllib.parse
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from perai.agenda import Agenda
from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.google import Cifra, Google
from perai.ics import ler_ics, rotulo
from perai.servico import Entrada, Perai
from perai.telegram import MensagemTelegram
from tests.test_despesas import ClienteRoteiro

AMANHA = date.today() + timedelta(days=1)
A = AMANHA.strftime("%Y%m%d")


def ics(nome="nelson.takemura@l8group.net"):
    return f"""BEGIN:VCALENDAR
X-WR-CALNAME:{nome}
BEGIN:VEVENT
UID:l8-1
DTSTART;TZID=America/Sao_Paulo:{A}T140000
DTEND;TZID=America/Sao_Paulo:{A}T150000
SUMMARY:Call Gazin
END:VEVENT
BEGIN:VEVENT
UID:l8-2
DTSTART;TZID=America/Sao_Paulo:20260105T080000
DTEND;TZID=America/Sao_Paulo:20260105T083000
RRULE:FREQ=DAILY
SUMMARY:Daily L8
END:VEVENT
END:VCALENDAR"""


class GoogleOcupado:
    def __init__(self, permitido=True):
        self.permitido = permitido

    def __call__(self, metodo, url, cab, corpo):
        u = urllib.parse.urlsplit(url)
        if u.path == "/token":
            dados = {"access_token": "acesso", "expires_in": 3600, "scope": "x"}
            if b"authorization_code" in corpo:
                dados["refresh_token"] = "r"
            return 200, json.dumps(dados).encode()
        if u.path.endswith("/userinfo"):
            return 200, b'{"email":"n@gmail.com"}'
        if u.path.endswith("/freeBusy"):
            pedido = json.loads(corpo)
            cal = pedido["items"][0]["id"]
            if not self.permitido:
                return 200, json.dumps({"calendars": {cal: {"errors": [{"reason": "notFound"}], "busy": []}}}).encode()
            return 200, json.dumps({"calendars": {cal: {"busy": [
                {"start": f"{AMANHA}T17:00:00Z", "end": f"{AMANHA}T18:00:00Z"},     # 14h–15h: já está no .ics
                {"start": f"{AMANHA}T19:00:00Z", "end": f"{AMANHA}T20:00:00Z"}]}}}).encode()
        if metodo == "GET":
            return 200, b'{"items": []}'
        return 404, b"{}"


class TesteAgendasExtras(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.google = GoogleOcupado()
        self.agenda = Agenda(self.repo, Google("id", "s", "https://x/google/retorno", self.google),
                             Cifra(Fernet.generate_key().decode()), "https://x")
        self.agenda.concluir(self.agenda.link_conexao(self.uid).split("t=", 1)[1], "codigo")
        self.pasta = Path(tempfile.mkdtemp())

    def perai(self, respostas):
        self.cliente = ClienteRoteiro(respostas)
        return Perai(self.repo, self.cliente, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                     self.pasta, agenda=self.agenda)

    def test_rotulo(self):
        self.assertEqual(rotulo("nelson.takemura@l8group.net"), "L8")
        self.assertEqual(rotulo("ana@empresa.com.br"), "Empresa")

    def test_importa_zip_do_google_e_responde_com_rotulo(self):
        zipado = self.pasta / "export.zip"
        with zipfile.ZipFile(zipado, "w") as z:
            z.writestr("nelson.takemura@l8group.net.ics", ics())
        p = self.perai([{"resposta": "📅 Amanhã\n14:00 · 💼 L8 · Call Gazin", "registros_citados": ["A2"],
                         "sem_informacao": False}])
        r = p.receber(self.uid, Entrada("telegram", "conversa_exportada", None, zipado))
        self.assertIn("📅 Agenda L8 importada (só leitura)", r)
        self.assertIn("Call Gazin", r)
        self.assertEqual(self.cliente.pedidos, [])  # lido sem IA
        self.repo.definir_agenda_ocupado(self.uid, "nelson.takemura@l8group.net", "L8")
        r = p.receber(self.uid, Entrada("telegram", "texto", "o que tenho amanhã?"))
        pedido = self.cliente.pedidos[-1][1]
        self.assertIn("(agenda L8, só leitura; ", pedido)
        self.assertIn("Call Gazin", pedido)
        self.assertIn("Daily L8", pedido)
        # O bloco ocupado das 14h some (o .ics já tem); o das 16h fica, sem título.
        self.assertIn("(agenda L8, só livre/ocupado; " + f"{AMANHA} 16:00 a 17:00) ocupado, sem detalhes", pedido)
        self.assertNotIn(f"{AMANHA} 14:00 a 15:00) ocupado", pedido)
        resp = next(iter(self.repo.b.respostas.values()))
        self.assertTrue(resp["fontes_externas"][0].startswith("ics:"))

    def test_importacao_nova_substitui(self):
        p = self.perai([])
        um = self.pasta / "a.ics"
        um.write_text(ics(), encoding="utf-8")
        p.receber(self.uid, Entrada("telegram", "documento", None, um))
        dois = self.pasta / "b.ics"
        dois.write_text(ics().replace("Call Gazin", "Call Romagnole"), encoding="utf-8")
        p.receber(self.uid, Entrada("telegram", "documento", None, dois))
        titulos = [e.titulo for e in self.agenda.eventos_em(self.uid, AMANHA)]
        self.assertIn("Call Romagnole", titulos)
        self.assertNotIn("Call Gazin", titulos)

    def test_comando_ocupado(self):
        p = self.perai([])
        bot = Bot(None, p, {10}, Path(tempfile.mkdtemp()))
        cmd = lambda t: bot.tratar(MensagemTelegram(1, 10, "N", "comando", t))
        self.assertIn("nenhuma ainda", cmd("/agenda_ocupado"))
        self.assertIn("Pronto", cmd("/agenda_ocupado nelson.takemura@l8group.net"))
        self.assertEqual(self.repo.agendas_ocupado(self.uid), [("nelson.takemura@l8group.net", "L8")])
        self.assertIn("💼 L8", cmd("/agenda_ocupado"))
        self.google.permitido = False
        self.assertIn("não deixou ver", cmd("/agenda_ocupado outra@empresa.com"))
        cmd("/agenda_ocupado remover nelson.takemura@l8group.net")
        self.assertEqual(self.repo.agendas_ocupado(self.uid), [])


if __name__ == "__main__":
    unittest.main()

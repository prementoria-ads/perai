"""Cancelar eventos que o Peraí não criou (D-059): apaga direto, avisa e oferece Desfazer."""

import json
import tempfile
import unittest
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from perai.agenda import Agenda, combina, regra_ate
from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.google import Cifra, Google
from perai.servico import Entrada, Perai
from perai.telegram import MensagemTelegram
from tests.test_despesas import ClienteRoteiro

D1 = (date.today() + timedelta(days=1)).isoformat()
D2 = (date.today() + timedelta(days=8)).isoformat()
REGRA = "RRULE:FREQ=WEEKLY;BYDAY=TH"


class GoogleSerie:
    """Agenda com uma série (Rotary, toda quinta), um evento avulso e o Dentista."""

    def __init__(self):
        self.pedidos = []
        self.mestre = {"id": "serieR", "summary": "Rotary Umuarama Catedral", "recurrence": [REGRA],
                       "start": {"dateTime": "2026-01-01T19:30:00-03:00"}, "status": "confirmed"}
        self.avulsos = {
            "ak1": {"summary": "Conversa com Akira", "start": {"dateTime": f"{D1}T10:30:00-03:00"},
                    "end": {"dateTime": f"{D1}T11:00:00-03:00"}, "status": "confirmed"},
            "dent": {"summary": "Dentista", "start": {"dateTime": f"{D1}T09:00:00-03:00"},
                     "end": {"dateTime": f"{D1}T10:00:00-03:00"}, "status": "confirmed"},
        }

    def __call__(self, metodo, url, cab, corpo):
        self.pedidos.append((metodo, url, corpo))
        u = urllib.parse.urlsplit(url)
        if u.path == "/token":
            dados = {"access_token": "acesso", "expires_in": 3600, "scope": "calendar.events"}
            if b"authorization_code" in corpo:
                dados["refresh_token"] = "refresh"
            return 200, json.dumps(dados).encode()
        if u.path.endswith("/userinfo"):
            return 200, b'{"email":"n@x"}'
        if u.path == "/revoke":
            return 200, b"{}"
        if "sendUpdates=none" not in url and metodo != "GET":
            raise AssertionError("mudança sem sendUpdates=none")
        evento = urllib.parse.unquote(u.path.split("/events")[1].strip("/"))
        if metodo == "GET" and not evento:
            itens = []
            if "UNTIL" not in self.mestre["recurrence"][0] and self.mestre["status"] == "confirmed":
                for i, dia in enumerate((D1, D2)):
                    itens.append({"id": f"serieR_{i}", "recurringEventId": "serieR",
                                  "summary": "Rotary Umuarama Catedral", "organizer": {"self": True},
                                  "start": {"dateTime": f"{dia}T19:30:00-03:00"},
                                  "end": {"dateTime": f"{dia}T21:00:00-03:00"}})
            itens += [{"id": i, **e} for i, e in self.avulsos.items()]
            return 200, json.dumps({"items": itens}).encode()
        if metodo == "GET":
            return 200, json.dumps(self.mestre).encode()
        alvo = self.mestre if evento == "serieR" else self.avulsos[evento]
        if metodo == "PATCH":
            alvo.update(json.loads(corpo))
            return 200, b"{}"
        if metodo == "DELETE":
            alvo["status"] = "cancelled"
            return 204, b""
        return 404, b"{}"


class TG:
    def __init__(self):
        self.tirados = []

    def enviar(self, *a, **k):
        pass

    def responder_botao(self, *a):
        pass

    def tirar_botoes(self, chat, msg):
        self.tirados.append(msg)


class TesteCancelar(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.google = GoogleSerie()
        self.agenda = Agenda(self.repo, Google("id", "s", "https://x/google/retorno", self.google),
                             Cifra(Fernet.generate_key().decode()), "https://x")
        link = self.agenda.link_conexao(self.uid)
        self.agenda.concluir(link.split("t=", 1)[1], "codigo")

    def perai(self, respostas):
        self.cliente = ClienteRoteiro(respostas)
        return Perai(self.repo, self.cliente, MODELOS["simulado"], TRANSCRITORES["simulado"],
                     MODELOS["simulado"], Path(tempfile.mkdtemp()), agenda=self.agenda)

    def test_regras(self):
        self.assertTrue(combina("Rotary Umuarama Catedral", "rotary de umuarama"))
        self.assertFalse(combina("Rotary Maringá", "Rotary Umuarama"))
        self.assertTrue(combina("Reunião com o Tecnopark", "tecnopark"))
        self.assertEqual(regra_ate("RRULE:FREQ=WEEKLY;COUNT=10;BYDAY=TH", "2026-10-29T19:30", False),
                         "RRULE:FREQ=WEEKLY;BYDAY=TH;UNTIL=20261029T222900Z")
        self.assertEqual(regra_ate(REGRA, "2026-10-29", True), REGRA + ";UNTIL=20261028")

    def test_serie_avulso_desfazer_e_nao_encontrado(self):
        p = self.perai([
            {"cancelar_eventos": [{"busca": "Rotary Umuarama", "desde": None, "ate": None}]},
            {"cancelar_eventos": [{"busca": "Akira", "desde": D1, "ate": D1}]},
            {"cancelar_eventos": [{"busca": "Pilates", "desde": None, "ate": None}]},
        ])
        bot = Bot(TG(), p, {10}, Path(tempfile.mkdtemp()))

        r = p.receber(self.uid, Entrada("telegram", "texto", "cancele todos os eventos de rotary umuarama"))
        self.assertIn("🗑️ Cancelei na agenda: Rotary Umuarama Catedral, todas as vezes a partir de "
                      f"{D1[8:10]}/{D1[5:7]} às 19:30.", r)
        self.assertNotIn("intenção", r)
        self.assertNotIn("Anotado", r)
        self.assertIn("UNTIL=", self.google.mestre["recurrence"][0])  # o passado fica
        self.assertEqual(self.google.mestre["status"], "confirmed")
        self.assertEqual(r.botoes, (("↩️ Desfazer cancelamento", r.botoes[0][1]),))
        self.assertEqual(self.google.avulsos["dent"]["status"], "confirmed")  # não mexe no resto

        resp = bot.tratar(MensagemTelegram(1, 10, "N", "botao", r.botoes[0][1], botao_id="q", mensagem_id=7))
        self.assertEqual(resp, "↩️ Voltou para a agenda: Rotary Umuarama Catedral.")
        self.assertEqual(self.google.mestre["recurrence"], [REGRA])
        resp = bot.tratar(MensagemTelegram(2, 10, "N", "botao", r.botoes[0][1], botao_id="q", mensagem_id=7))
        self.assertEqual(resp, "Isso já tinha voltado para a agenda.")

        r = p.receber(self.uid, Entrada("telegram", "texto", "tira da agenda a conversa de amanhã com o Akira"))
        self.assertIn(f"🗑️ Tirei da agenda: Conversa com Akira · {D1[8:10]}/{D1[5:7]} às 10:30", r)
        self.assertEqual(self.google.avulsos["ak1"]["status"], "cancelled")
        self.assertEqual(self.google.avulsos["dent"]["status"], "confirmed")
        resp = bot.tratar(MensagemTelegram(3, 10, "N", "botao", r.botoes[0][1], botao_id="q", mensagem_id=8))
        self.assertIn("Conversa com Akira", resp)
        self.assertEqual(self.google.avulsos["ak1"]["status"], "confirmed")

        r = p.receber(self.uid, Entrada("telegram", "texto", "cancela todos os pilates"))
        self.assertIn("🔎 Não encontrei na agenda eventos com “Pilates”. Nada foi cancelado.", r)
        self.assertEqual(r.botoes, ())
        # Fonte (D-040): cada cancelamento aponta para a mensagem que pediu.
        self.assertTrue(all(x["mensagem_id"] for x in self.repo.b.cancelamentos.values()))

    def test_sem_agenda(self):
        self.agenda.desconectar(self.uid)
        p = self.perai([{"cancelar_eventos": [{"busca": "Rotary", "desde": None, "ate": None}]}])
        r = p.receber(self.uid, Entrada("telegram", "texto", "cancela o rotary"))
        self.assertIn("conecte com /agenda", r)


if __name__ == "__main__":
    unittest.main()

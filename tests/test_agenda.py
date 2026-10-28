"""Google Agenda (D-051) com Google e Telegram falsos: conectar, criar com Desfazer,
mudar, cancelar e responder citando eventos da agenda."""

import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

from cryptography.fernet import Fernet

from perai.agenda import Agenda
from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.google import Cifra, Google
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai
from perai.servico import _quando
from perai.web import criar_manipulador
from datetime import date, timedelta

# Datas relativas a hoje, para o teste não vencer.
D1 = (date.today() + timedelta(days=2)).isoformat()
D2 = (date.today() + timedelta(days=3)).isoformat()


class GoogleFalso:
    """Simula o OAuth e a Agenda; guarda os pedidos para conferir."""

    def __init__(self):
        self.eventos = {}
        self.pedidos = []
        self.proximo = 1

    def __call__(self, metodo, url, cab, corpo):
        self.pedidos.append((metodo, url, corpo))
        u = urllib.parse.urlsplit(url)
        if u.path == "/token":
            campos = dict(urllib.parse.parse_qsl(corpo.decode()))
            dados = {"access_token": "acesso", "expires_in": 3600, "scope": "calendar.events email"}
            if campos.get("grant_type") == "authorization_code":
                if campos.get("code") != "codigo-bom":
                    return 400, b'{"error":"invalid_grant"}'
                dados["refresh_token"] = "refresh-secreto"
            return 200, json.dumps(dados).encode()
        if u.path == "/revoke":
            return 200, b"{}"
        if u.path.endswith("/userinfo"):
            return 200, b'{"email":"nelsontakemura@gmail.com"}'
        assert cab.get("Authorization") == "Bearer acesso"
        partes = u.path.split("/events")
        if metodo == "GET":
            itens = [{"id": i, "summary": e["summary"], "start": e["start"], "end": e["end"]}
                     for i, e in self.eventos.items()]
            itens.append({"id": "externo1", "summary": "Dentista",
                          "start": {"dateTime": "2026-10-29T09:00:00-03:00"},
                          "end": {"dateTime": "2026-10-29T10:00:00-03:00"}})
            return 200, json.dumps({"items": itens}).encode()
        if metodo == "POST":
            dados = json.loads(corpo)
            assert "attendees" not in dados  # nunca convida ninguém
            assert "sendUpdates=none" in url
            i = f"g{self.proximo}"
            self.proximo += 1
            self.eventos[i] = dados
            return 200, json.dumps({"id": i}).encode()
        evento = urllib.parse.unquote(partes[1].strip("/"))
        if metodo == "PATCH":
            self.eventos[evento].update(json.loads(corpo))
            return 200, b"{}"
        if metodo == "DELETE":
            self.eventos.pop(evento, None)
            return 204, b""
        return 404, b"{}"


class ClienteRoteiro(Cliente):
    def __init__(self, respostas):
        super().__init__()
        self.respostas = list(respostas)
        self.pedidos = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append((tarefa, usuario))
        return Resposta(json.dumps(self.respostas.pop(0)), 1, 1, 1, 0.0)


class TelegramFalso:
    def __init__(self):
        self.enviadas, self.botoes, self.respondidos, self.sem_botao = [], [], [], []

    def enviar(self, chat, texto, botoes=()):
        self.enviadas.append(texto)
        self.botoes.append(list(botoes))

    def responder_botao(self, i, texto=""):
        self.respondidos.append(texto)

    def tirar_botoes(self, chat, msg):
        self.sem_botao.append(msg)


class TesteAgenda(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.google = GoogleFalso()
        cliente_google = Google("id.apps.googleusercontent.com", "segredo",
                                "https://perai.prementoria.com/google/retorno", self.google)
        self.agenda = Agenda(self.repo, cliente_google, Cifra(Fernet.generate_key().decode()),
                             "https://perai.prementoria.com")

    def perai(self, respostas):
        self.cliente = ClienteRoteiro(respostas)
        return Perai(self.repo, self.cliente, MODELOS["simulado"], TRANSCRITORES["simulado"],
                     MODELOS["simulado"], Path(tempfile.mkdtemp()), agenda=self.agenda)

    def conectar(self):
        link = self.agenda.link_conexao(self.uid)
        token = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(link).query))["t"]
        destino = self.agenda.url_google(token)
        self.assertIn("accounts.google.com", destino)
        self.assertIn("access_type=offline", destino)
        self.agenda.concluir(token, "codigo-bom")
        return token

    def test_conexao_uso_unico_e_token_cifrado(self):
        token = self.conectar()
        conta = self.repo.b.google_contas[self.uid]
        self.assertNotIn("refresh-secreto", conta["refresh"])  # guardado cifrado
        self.assertEqual(self.agenda.email(self.uid), "nelsontakemura@gmail.com")
        self.assertIsNone(self.agenda.url_google(token))     # link não serve de novo
        with self.assertRaises(PermissionError):
            self.agenda.concluir(token, "codigo-bom")

    def test_sem_agenda_sugere_conectar(self):
        p = self.perai([{"registros": [{"conteudo": "Reunião no Technopark quinta às 14h", "natureza": "intencao"}],
                         "compromissos": [{"titulo": "Reunião no Technopark", "inicio": f"{D1}T14:00"}]}])
        r = p.receber(self.uid, Entrada("telegram", "texto", "Reunião no Technopark quinta às 14h"))
        self.assertIn("/agenda", r)
        self.assertEqual(self.google.eventos, {})

    def test_cria_desfaz_muda_e_cancela(self):
        self.conectar()
        p = self.perai([
            {"registros": [{"conteudo": "Reunião no Technopark quinta às 14h", "natureza": "intencao"}],
             "compromissos": [{"titulo": "Reunião no Technopark", "inicio": f"{D1}T14:00",
                               "local": "Umuarama"},
                              {"titulo": "Sem hora", "inicio": D2}]},
            {"ajustes_eventos": [{"evento": "E1", "inicio": f"{D1}T15:00"}]},
            {"ajustes_eventos": [{"evento": "E1", "situacao": "cancelado"}]},
        ])
        r = p.receber(self.uid, Entrada("telegram", "texto", "Reunião no Technopark quinta às 14h em Umuarama"))
        self.assertIn(f"📅 Coloquei na sua agenda: Reunião no Technopark, {_quando(f'{D1}T14:00', f'{D1}T15:00')}", r)
        self.assertEqual(len(self.google.eventos), 1)  # "Sem hora" não vira evento
        self.assertTrue(r.botoes[0][1].startswith("desfazer:"))
        evento_id = r.botoes[0][1].split(":", 1)[1]
        self.assertIsNotNone(self.repo.b.eventos[evento_id]["mensagem_id"])  # fonte (D-040)

        r = p.receber(self.uid, Entrada("telegram", "texto", "muda a reunião para as 15h"))
        self.assertIn("[E1] Reunião no Technopark", self.cliente.pedidos[-1][1])
        self.assertIn(f"Mudei na agenda: Reunião no Technopark, {_quando(f'{D1}T15:00', f'{D1}T16:00')}", r)
        self.assertEqual(self.google.eventos["g1"]["start"]["dateTime"], f"{D1}T15:00:00")

        r = p.receber(self.uid, Entrada("telegram", "texto", "a reunião foi cancelada"))
        self.assertIn("Tirei da agenda", r)
        self.assertEqual(self.google.eventos, {})
        trilha = [c for c in self.repo.b.correcoes if c["tabela"] == "eventos_agenda"]
        self.assertEqual([c["campo"] for c in trilha], ["inicio", "fim", "situacao", "situacao"])

    def test_botao_desfazer(self):
        self.conectar()
        p = self.perai([{"compromissos": [{"titulo": "Consulta", "inicio": f"{D2}T09:30"}]}])
        r = p.receber(self.uid, Entrada("telegram", "texto", "consulta sexta às 9h30"))
        tg = TelegramFalso()
        bot = Bot(tg, p, {10}, Path(tempfile.mkdtemp()))
        from perai.telegram import MensagemTelegram
        resposta = bot.tratar(MensagemTelegram(1, 10, "N", "botao", r.botoes[0][1], botao_id="q1", mensagem_id=7))
        self.assertIn("Tirei da agenda: Consulta", resposta)
        self.assertEqual(self.google.eventos, {})
        self.assertEqual(tg.sem_botao, [7])
        # Segundo toque não quebra
        resposta = bot.tratar(MensagemTelegram(2, 10, "N", "botao", r.botoes[0][1], botao_id="q2", mensagem_id=7))
        self.assertIn("já tinha saído", resposta)

    def test_resposta_cita_evento_da_agenda(self):
        self.conectar()
        p = self.perai([{"resposta": "📅 Amanhã:\n🗓️ 09:00 · Dentista", "registros_citados": ["A1"],
                         "sem_informacao": False}])
        r = p.receber(self.uid, Entrada("telegram", "texto", "o que tenho amanhã?"))
        self.assertIn("Dentista", r)
        self.assertIn("[A1] (evento da agenda; 2026-10-29 09:00 a 10:00) Dentista", self.cliente.pedidos[-1][1])
        resp = next(iter(self.repo.b.respostas.values()))
        self.assertEqual(resp["fontes_externas"], ["google:externo1"])

    def test_comando_agenda_e_desconectar(self):
        p = self.perai([])
        bot = Bot(TelegramFalso(), p, {10}, Path(tempfile.mkdtemp()))
        from perai.telegram import MensagemTelegram
        r = bot.tratar(MensagemTelegram(1, 10, "N", "comando", "/agenda"))
        self.assertIn("https://perai.prementoria.com/google/conectar?t=", r)
        self.conectar()
        r = bot.tratar(MensagemTelegram(2, 10, "N", "comando", "/agenda"))
        self.assertIn("está conectada", r)
        r = bot.tratar(MensagemTelegram(3, 10, "N", "comando", "/desconectar_agenda"))
        self.assertIn("desconectada", r)
        self.assertIsNone(self.agenda.conta(self.uid))


class TesteWeb(unittest.TestCase):
    """O serviço web de verdade (HTTPServer) com Google falso."""

    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.google = GoogleFalso()
        self.agenda = Agenda(self.repo, Google("id", "s", "https://x/google/retorno", self.google),
                             Cifra(Fernet.generate_key().decode()), "https://x")
        self.avisos = []
        self.servidor = HTTPServer(("127.0.0.1", 0), criar_manipulador(
            self.agenda, lambda u, t: self.avisos.append((u, t))))
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.servidor.server_port}"

    def tearDown(self):
        self.servidor.shutdown()
        self.servidor.server_close()

    def abrir(self, caminho):
        class SemSeguir(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        abridor = urllib.request.build_opener(SemSeguir)
        try:
            r = abridor.open(self.base + caminho)
            return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def test_fluxo(self):
        self.assertEqual(self.abrir("/saude")[0], 200)
        link = self.agenda.link_conexao(self.uid)
        token = link.split("t=", 1)[1]
        status, cab, _ = self.abrir(f"/google/conectar?t={token}")
        self.assertEqual(status, 302)
        self.assertIn("accounts.google.com", cab["Location"])
        status, _, corpo = self.abrir(f"/google/retorno?state={token}&code=codigo-bom")
        self.assertEqual(status, 200)
        self.assertIn("Agenda conectada", corpo)
        self.assertIn("nelsontakemura@gmail.com", self.avisos[0][1])
        status, _, corpo = self.abrir(f"/google/retorno?state={token}&code=codigo-bom")
        self.assertEqual(status, 400)  # link de uso único
        self.assertEqual(self.abrir("/google/conectar?t=falso")[0], 400)
        self.assertIn("cancelada", self.abrir("/google/retorno?error=access_denied")[2])


import urllib.error  # noqa: E402

if __name__ == "__main__":
    unittest.main()

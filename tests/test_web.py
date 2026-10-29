"""Web mínima (D-063): ligar a conta Google pelo convite do Telegram, entrar sem Telegram,
corrigir registro com fonte, fechar pendência, exportar e sair. Google falso."""

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

from cryptography.fernet import Fernet

from perai.agenda import Agenda
from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Extracao, Pendencia, Registro
from perai.google import Cifra, Google
from perai.armazenamento import NovaMensagem
from perai.painel import Painel
from perai.servico import Perai
from perai.telegram import MensagemTelegram
from perai.web import criar_manipulador


class GoogleLogin:
    """OAuth falso: o código diz qual conta entrou ("conta-a" ou "conta-b")."""

    def __init__(self):
        self.ultimo_codigo = None

    def __call__(self, metodo, url, cab, corpo):
        u = urllib.parse.urlsplit(url)
        if u.path == "/token":
            campos = dict(urllib.parse.parse_qsl(corpo.decode()))
            self.ultimo_codigo = campos.get("code")
            return 200, json.dumps({"access_token": "acesso-" + campos.get("code", ""), "expires_in": 3600}).encode()
        if u.path.endswith("/userinfo"):
            conta = cab["Authorization"].rsplit("-", 1)[-1]
            return 200, json.dumps({"id": f"sub-{conta}", "email": f"{conta}@gmail.com",
                                    "verified_email": True}).encode()
        return 404, b"{}"


class SemSeguir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class TesteWebMinima(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson Takemura", 10)
        self.outro = self.repo.criar_usuario("Lerci", 20)
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto",
                                                               texto_original="o Rodrigo é da CHTech"))
        self.repo.salvar_extracao(self.uid, mid, Extracao("1", registros=[
            Registro("R1-1", "1", "Rodrigo é da CHTech", "confirmado", "eu", None, ["Rodrigo"], 0.9, False)],
            pendencias=[Pendencia("P1-1", "1", "Mandar proposta ao Rodrigo", "eu", None, "2026-10-30", None)]))
        mid2 = self.repo.salvar_mensagem(self.outro, NovaMensagem(canal="telegram", tipo="texto",
                                                                  texto_original="segredo da Lerci"))
        self.repo.salvar_extracao(self.outro, mid2, Extracao("1", registros=[
            Registro("R1-1", "1", "Segredo da Lerci", "confirmado", "eu", None, [], 0.9, False)]))
        self.google = Google("id", "s", "https://x/google/retorno", GoogleLogin())
        self.agenda = Agenda(self.repo, self.google, Cifra(Fernet.generate_key().decode()), "https://x")
        self.painel = Painel(self.repo, self.google, "https://x")
        self.servidor = HTTPServer(("127.0.0.1", 0), criar_manipulador(self.agenda, lambda u, t: None, self.painel))
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.servidor.server_port}"
        self.cookies = {}

    def tearDown(self):
        self.servidor.shutdown()
        self.servidor.server_close()

    def pedir(self, caminho, dados=None):
        corpo = urllib.parse.urlencode(dados).encode() if dados is not None else None
        req = urllib.request.Request(self.base + caminho, data=corpo)
        if self.cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        try:
            r = urllib.request.build_opener(SemSeguir).open(req)
            status, cab, texto = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, cab, texto = e.code, e.headers, e.read()
        for c in cab.get_all("Set-Cookie") or []:
            nome, valor = c.split(";", 1)[0].split("=", 1)
            self.assertIn("HttpOnly", c)
            self.assertIn("Secure", c)
            if "Max-Age=0" in c:
                self.cookies.pop(nome, None)
            else:
                self.cookies[nome] = valor
        return status, cab, texto

    def entrar(self, caminho, conta):
        status, cab, _ = self.pedir(caminho)
        self.assertEqual(status, 303)
        estado = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(cab["Location"]).query))
        self.assertEqual(estado["scope"], "openid email")  # login não pede agenda
        return self.pedir(f"/google/retorno?state={urllib.parse.quote(estado['state'])}&code={conta}")

    def csrf(self, html):
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    def test_fluxo_completo(self):
        # Sem sessão: página de entrar; sem convite, a conta Google não entra.
        self.assertIn("Entrar com Google", self.pedir("/web")[2].decode())
        status, _, corpo = self.entrar("/web/entrar", "conta-a")
        self.assertEqual(status, 400)
        self.assertIn("ainda não está ligada", corpo.decode())

        # Convite pelo Telegram (/web), uso único.
        perai = Perai(self.repo, None, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                      Path(tempfile.mkdtemp()), agenda=self.agenda)
        resp = Bot(None, perai, {10}, Path(tempfile.mkdtemp())).tratar(
            MensagemTelegram(1, 10, "N", "comando", "/web"))
        link = re.search(r"https://x(/web/ligar\?t=\S+)", resp).group(1)
        status, cab, _ = self.entrar(link, "conta-a")
        self.assertEqual((status, cab["Location"]), (303, "/web"))
        self.assertIn("perai_sessao", self.cookies)
        self.assertNotIn("perai_estado", self.cookies)
        self.assertEqual(self.pedir(link)[0], 400)  # convite já usado

        pagina = self.pedir("/web")[2].decode()
        self.assertIn("Rodrigo é da CHTech", pagina)
        self.assertIn("Mandar proposta ao Rodrigo", pagina)
        self.assertNotIn("Segredo da Lerci", pagina)  # só os dados de quem entrou
        self.assertIn("Rodrigo", self.pedir("/web?q=rodrigo")[2].decode())
        self.assertNotIn("Rodrigo é da", self.pedir("/web?q=gazin")[2].decode())

        # Corrigir: vira mensagem do canal web (fonte) e o original fica no histórico.
        csrf = self.csrf(pagina)
        rid = next(i for i, r in self.repo.b.registros.items() if r["conteudo"] == "Rodrigo é da CHTech")
        self.assertEqual(self.pedir("/web/registro", {"csrf": "errado", "id": rid, "texto": "x"})[0], 303)
        self.assertEqual(self.repo.b.registros[rid]["situacao"], "ativo")  # CSRF errado: nada muda
        status, cab, _ = self.pedir("/web/registro", {"csrf": csrf, "id": rid, "texto": "Rodrigo é da CRTech"})
        self.assertEqual(cab["Location"], "/web?ok=corrigido")
        self.assertEqual(self.repo.b.registros[rid]["situacao"], "corrigido")
        novo = next(r for r in self.repo.b.registros.values() if r["conteudo"] == "Rodrigo é da CRTech")
        self.assertEqual(self.repo.b.mensagens[novo["mensagem_id"]]["canal"], "web")

        # Fechar pendência.
        pid = next(iter(self.repo.b.pendencias))
        self.pedir("/web/pendencia", {"csrf": csrf, "id": pid, "acao": "feita"})
        self.assertEqual(self.repo.b.pendencias[pid]["situacao"], "concluida")

        # Exportar: JSON aberto, sem dados de outra pessoa.
        status, cab, corpo = self.pedir("/web/exportar")
        self.assertIn("attachment", cab["Content-Disposition"])
        dados = json.loads(corpo)
        self.assertEqual(dados["usuario"]["nome"], "Nelson Takemura")
        self.assertIn("Rodrigo é da CRTech", corpo.decode())
        self.assertNotIn("Lerci", corpo.decode())

        # Sair encerra a sessão no servidor.
        self.pedir("/web/sair", {"csrf": csrf})
        self.assertNotIn("perai_sessao", self.cookies)
        self.assertIn("Entrar com Google", self.pedir("/web")[2].decode())

        # Depois de ligada, a conta entra direto, sem Telegram.
        status, _, _ = self.entrar("/web/entrar", "conta-a")
        self.assertEqual(status, 303)
        self.assertIn("Rodrigo é da CRTech", self.pedir("/web")[2].decode())

    def test_estado_de_outra_janela_e_conta_de_outra_pessoa(self):
        # Retorno sem o cookie de estado (login começado em outro navegador) é recusado.
        status, _, corpo = self.pedir("/google/retorno?state=web.abc&code=conta-a")
        self.assertEqual(status, 400)
        # A mesma conta Google não pode ser ligada a duas pessoas.
        self.entrar(self.painel.convite(self.uid).replace("https://x", ""), "conta-a")
        self.cookies.clear()
        status, _, corpo = self.entrar(self.painel.convite(self.outro).replace("https://x", ""), "conta-a")
        self.assertEqual(status, 400)
        self.assertIn("ligada a outra pessoa", corpo.decode())


if __name__ == "__main__":
    unittest.main()

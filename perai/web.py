"""Serviço web mínimo do Peraí (atrás do Caddy, com HTTPS).

  GET /saude              -> "ok"
  GET /google/conectar?t= -> confere o link de uso único da agenda e manda para o Google (D-051)
  GET /google/retorno     -> volta do Google: agenda, ou login da web quando o state começa com "web."
  /web...                 -> web mínima (D-063): entrar com Google, consultar, corrigir, exportar

Nunca registra no log o endereço completo: ele carrega códigos e tokens.
"""

from __future__ import annotations

import hmac
import html
import logging
import os
import sys
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer

from perai.agenda import Agenda, agenda_do_ambiente
from perai.google import ErroGoogle
from perai.painel import ESTILO, VALIDADE_ESTADO, VALIDADE_SESSAO, ErroPainel, Painel

COOKIE_SESSAO = "perai_sessao"
COOKIE_ESTADO = "perai_estado"
SEGURANCA = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; form-action 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'",
}

log = logging.getLogger("perai.web")

PAGINA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Peraí</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;
line-height:1.5;color:#222;background:#fafaf7}}h1{{font-size:1.4rem}}</style></head>
<body><h1>{titulo}</h1><p>{texto}</p></body></html>"""


PAGINA_LONGA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{titulo}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;
line-height:1.6;color:#222;background:#fafaf7}}h1{{font-size:1.5rem}}h2{{font-size:1.1rem;margin-top:2rem}}
</style></head><body>{corpo}</body></html>"""

INICIO = """<h1>Peraí</h1>
<p>Assistente pessoal de memória, em teste fechado. Você conta, encaminha ou fala pelo Telegram;
o Peraí lembra, prepara e questiona, sempre mostrando de onde tirou cada informação.</p>
<p><a href="/privacidade">Política de privacidade</a> · Contato: contato@prementoria.com</p>"""

PRIVACIDADE = """<h1>Política de privacidade do Peraí</h1>
<p>Versão de 28/10/2026, 22h. O Peraí está em teste fechado, com acesso apenas por convite.</p>
<h2>O que guardamos</h2>
<p>As mensagens, áudios, imagens e arquivos que você envia ao Peraí pelo Telegram, e o que o Peraí
organiza a partir deles (pessoas, combinados, pendências, lembretes). A mensagem original é guardada
como chegou, para que toda resposta possa mostrar a fonte.</p>
<h2>Google Agenda</h2>
<p>Se você conectar a sua conta Google (comando /agenda), o Peraí acessa a sua agenda para mostrar seus
compromissos nas respostas e para criar, mudar ou cancelar eventos quando você conta um compromisso.
Eventos que você não criou pelo Peraí só são mudados ou cancelados quando você pede, e sempre com a
opção de desfazer. O Peraí nunca envia convites a outras pessoas. Guardamos o seu e-mail Google e uma
autorização cifrada; não guardamos a sua senha.</p>
<h2>Entrar pela web</h2>
<p>Para usar o Peraí pelo navegador, você entra com a sua conta Google. Nesse caso o Peraí recebe só o
identificador da conta e o e-mail, sem acesso a outros dados e sem guardar senha. A sessão fica num
cookie do seu navegador por até 30 dias, ou até você sair. Pela web você pode exportar todos os seus
dados num arquivo aberto (JSON).</p>
<p>O uso e a transferência de informações recebidas das APIs do Google seguem a
<a href="https://developers.google.com/terms/api-services-user-data-policy">Política de Dados do
Usuário dos Serviços de API do Google</a>, incluindo os requisitos de Uso Limitado. Os dados do Google
não são vendidos, não são usados para publicidade e não são usados para treinar modelos de IA.</p>
<h2>Com quem compartilhamos</h2>
<p>Com ninguém para fins comerciais. Para funcionar, o conteúdo é processado por provedores de modelos
de IA (Google Gemini, por API paga, em que o Google não usa o conteúdo para treinar modelos) e pelo
Telegram, apenas para responder você. Outras pessoas que usam o Peraí não veem nada seu.</p>
<h2>Onde fica e por quanto tempo</h2>
<p>Num servidor no Brasil, com cópias de segurança cifradas. Os dados ficam enquanto você usar o
Peraí; você pode pedir a exclusão definitiva a qualquer momento.</p>
<h2>Seus controles</h2>
<p>Desconectar a agenda: /desconectar_agenda no Telegram, ou em myaccount.google.com/permissions.
Pedir cópia ou exclusão dos seus dados: contato@prementoria.com.</p>"""


def criar_manipulador(agenda: Agenda, avisar, painel: Painel | None = None):
    class Manipulador(BaseHTTPRequestHandler):
        server_version = "Perai"
        sys_version = ""

        def log_message(self, formato, *args):  # sem query string no log
            log.info("%s %s", self.command, self.path.split("?", 1)[0])

        def _responder(self, status: int, titulo: str, texto: str):
            corpo = PAGINA.format(titulo=html.escape(titulo), texto=html.escape(texto)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(corpo)

        def _pagina(self, status: int, titulo: str, corpo_html: str):
            corpo = PAGINA_LONGA.format(titulo=html.escape(titulo), corpo=corpo_html).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        # -- web mínima (D-063) --------------------------------------------------------

        def _cookie(self, nome: str) -> str | None:
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie", ""))
            except Exception:
                return None
            return c[nome].value if nome in c else None

        def _enviar(self, status: int, corpo: bytes, tipo: str, extras: dict | None = None,
                    cookies: list[str] = ()):
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            for k, v in {**SEGURANCA, **(extras or {})}.items():
                self.send_header(k, v)
            for c in cookies:
                self.send_header("Set-Cookie", c)
            self.end_headers()
            self.wfile.write(corpo)

        def _html(self, status: int, pagina: str, cookies: list[str] = ()):
            self._enviar(status, pagina.encode(), "text/html; charset=utf-8", cookies=cookies)

        def _ir(self, destino: str, cookies: list[str] = ()):
            self.send_response(303)
            self.send_header("Location", destino)
            for k, v in SEGURANCA.items():
                self.send_header(k, v)
            for c in cookies:
                self.send_header("Set-Cookie", c)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _web_get(self, caminho: str, q: dict):
            if caminho == "/web/estilo.css":
                return self._enviar(200, ESTILO.encode(), "text/css; charset=utf-8",
                                    {"Cache-Control": "public, max-age=3600"})
            if painel is None or not painel.disponivel:
                return self._responder(503, "Web desligada", "A entrada pela web ainda não foi ligada neste servidor.")
            if caminho in ("/web/entrar", "/web/ligar"):
                try:
                    nonce, destino = painel.iniciar(q.get("t", "") if caminho == "/web/ligar" else None)
                except ErroPainel as erro:
                    return self._html(400, painel.pagina_entrar(str(erro)))
                cookie = (f"{COOKIE_ESTADO}={nonce}; Max-Age={VALIDADE_ESTADO}; Path=/google/retorno; "
                          "HttpOnly; Secure; SameSite=Lax")
                return self._ir(destino, [cookie])
            sessao = painel.sessao(self._cookie(COOKIE_SESSAO))
            if caminho == "/web":
                if not sessao:
                    return self._html(200, painel.pagina_entrar())
                aviso = {"corrigido": "✏️ Registro corrigido.", "pendencia": "✅ Pendência atualizada.",
                         "nada": "Nada mudou."}.get(q.get("ok", ""))
                return self._html(200, painel.pagina_painel(sessao[0], sessao[1], q.get("q", "")[:100], aviso))
            if caminho == "/web/exportar":
                if not sessao:
                    return self._ir("/web")
                return self._enviar(200, painel.exportar(sessao[0]), "application/json; charset=utf-8",
                                    {"Content-Disposition": 'attachment; filename="perai-meus-dados.json"'})
            return self._responder(404, "Não encontrado", "Esta página não existe.")

        def _web_retorno(self, q: dict):
            sair_estado = f"{COOKIE_ESTADO}=; Max-Age=0; Path=/google/retorno; HttpOnly; Secure; SameSite=Lax"
            if q.get("error"):
                return self._html(200, painel.pagina_entrar("Login cancelado."), [sair_estado])
            try:
                _, token = painel.concluir(q.get("state", ""), self._cookie(COOKIE_ESTADO), q.get("code", ""))
            except ErroPainel as erro:
                return self._html(400, painel.pagina_entrar(str(erro)), [sair_estado])
            except (ErroGoogle, OSError) as erro:
                log.warning("falha no login da web: %s", erro)
                return self._html(502, painel.pagina_entrar("O Google não confirmou o login. Tente de novo."),
                                  [sair_estado])
            sessao = (f"{COOKIE_SESSAO}={token}; Max-Age={int(VALIDADE_SESSAO.total_seconds())}; Path=/; "
                      "HttpOnly; Secure; SameSite=Lax")
            return self._ir("/web", [sessao, sair_estado])

        def do_POST(self):
            caminho = urllib.parse.urlsplit(self.path).path
            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho > 20_000:
                return self._responder(413, "Grande demais", "O texto enviado é grande demais.")
            dados = dict(urllib.parse.parse_qsl(self.rfile.read(tamanho).decode("utf-8", "replace")))
            if painel is None or not painel.disponivel:
                return self._responder(503, "Web desligada", "A entrada pela web ainda não foi ligada neste servidor.")
            token = self._cookie(COOKIE_SESSAO)
            sessao = painel.sessao(token)
            if not sessao or not hmac.compare_digest(dados.get("csrf", ""), sessao[1]):
                return self._ir("/web")
            usuario_id = sessao[0]
            try:
                if caminho == "/web/sair":
                    painel.sair(token)
                    return self._ir("/web", [f"{COOKIE_SESSAO}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax"])
                if caminho == "/web/registro":
                    r = painel.corrigir_registro(usuario_id, dados.get("id", ""), dados.get("texto", ""))
                    return self._ir("/web?ok=" + ("nada" if r == "Nada mudou." else "corrigido"))
                if caminho == "/web/pendencia":
                    painel.mudar_pendencia(usuario_id, dados.get("id", ""), dados.get("acao", ""))
                    return self._ir("/web?ok=pendencia")
            except ErroPainel as erro:
                return self._html(400, painel.pagina_painel(usuario_id, sessao[1], "", str(erro)))
            return self._responder(404, "Não encontrado", "Esta página não existe.")

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            if url.path == "/web" or url.path.startswith("/web/"):
                return self._web_get(url.path, q)
            if url.path == "/google/retorno" and q.get("state", "").startswith("web.") and painel:
                return self._web_retorno(q)
            if url.path == "/saude":
                corpo = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(corpo)
                return
            if url.path in ("/", ""):
                return self._pagina(200, "Peraí", INICIO)
            if url.path == "/privacidade":
                return self._pagina(200, "Peraí — Privacidade", PRIVACIDADE)
            if not agenda.disponivel:
                return self._responder(503, "Agenda desligada",
                                       "A conexão com o Google Agenda ainda não foi ligada neste servidor.")
            if url.path == "/google/conectar":
                destino = agenda.url_google(q.get("t", ""))
                if not destino:
                    return self._responder(400, "Link vencido",
                                           "Este link já foi usado ou passou de 15 minutos. "
                                           "Mande /agenda no Telegram para receber outro.")
                self.send_response(302)
                self.send_header("Location", destino)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                return
            if url.path == "/google/retorno":
                if q.get("error"):
                    return self._responder(200, "Conexão cancelada",
                                           "Você não autorizou a agenda. Tudo bem: nada foi conectado.")
                try:
                    usuario_id, email = agenda.concluir(q.get("state", ""), q.get("code", ""))
                except PermissionError:
                    return self._responder(400, "Link vencido",
                                           "Este link já foi usado ou passou de 15 minutos. "
                                           "Mande /agenda no Telegram para receber outro.")
                except (ErroGoogle, OSError) as erro:
                    log.warning("falha ao concluir a conexão: %s", erro)
                    return self._responder(502, "Não deu certo",
                                           "O Google não confirmou a autorização. Tente de novo com /agenda.")
                try:
                    avisar(usuario_id, f"📅 Agenda Google conectada{f' ({email})' if email else ''}.\n"
                                       "Agora é só me contar compromissos com dia e hora, e perguntar "
                                       "“o que tenho amanhã?”.")
                except Exception as erro:  # o aviso é cortesia; a conexão já está salva
                    log.warning("não consegui avisar no Telegram: %s", erro)
                return self._responder(200, "Agenda conectada ✅",
                                       "Pronto! Pode fechar esta página e voltar ao Telegram.")
            self._responder(404, "Não encontrado", "Esta página não existe.")

    return Manipulador


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from perai.armazenamento import RepositorioPostgres
    from perai.telegram import Telegram

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Defina DATABASE_URL", file=sys.stderr)
        return 1
    repo = RepositorioPostgres(url)
    agenda = agenda_do_ambiente(repo)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram = Telegram(token) if token else None

    def avisar(usuario_id: str, texto: str) -> None:
        chat = repo.telegram_de(usuario_id)
        if telegram and chat:
            telegram.enviar(chat, texto)

    porta = int(os.environ.get("PERAI_WEB_PORTA", "8080"))
    painel = Painel(repo, agenda.google, agenda.endereco)
    servidor = HTTPServer(("0.0.0.0", porta), criar_manipulador(agenda, avisar, painel))
    log.info("web no ar na porta %s; Google Agenda %s", porta, "ligada" if agenda.disponivel else "desligada")
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

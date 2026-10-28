"""Serviço web mínimo do Peraí (atrás do Caddy, com HTTPS).

Por enquanto só faz a conexão com o Google Agenda (D-051):
  GET /saude              -> "ok"
  GET /google/conectar?t= -> confere o link de uso único e manda para o Google
  GET /google/retorno     -> recebe o código do Google, guarda a autorização, avisa no Telegram

Nunca registra no log o endereço completo: ele carrega códigos e tokens.
"""

from __future__ import annotations

import html
import logging
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from perai.agenda import Agenda, agenda_do_ambiente
from perai.google import ErroGoogle

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
<p>Versão de 28/10/2026. O Peraí está em teste fechado, com acesso apenas por convite.</p>
<h2>O que guardamos</h2>
<p>As mensagens, áudios, imagens e arquivos que você envia ao Peraí pelo Telegram, e o que o Peraí
organiza a partir deles (pessoas, combinados, pendências, lembretes). A mensagem original é guardada
como chegou, para que toda resposta possa mostrar a fonte.</p>
<h2>Google Agenda</h2>
<p>Se você conectar a sua conta Google (comando /agenda), o Peraí acessa a sua agenda para mostrar seus
compromissos nas respostas e para criar, mudar ou cancelar eventos quando você conta um compromisso.
O Peraí só altera eventos que ele mesmo criou e nunca envia convites a outras pessoas. Guardamos o
seu e-mail Google e uma autorização cifrada; não guardamos a sua senha.</p>
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


def criar_manipulador(agenda: Agenda, avisar):
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

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
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
    servidor = HTTPServer(("0.0.0.0", porta), criar_manipulador(agenda, avisar))
    log.info("web no ar na porta %s; Google Agenda %s", porta, "ligada" if agenda.disponivel else "desligada")
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

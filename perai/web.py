"""Serviço web mínimo do Peraí (atrás do Caddy, com HTTPS).

  GET /saude              -> "ok"
  GET /privacidade, /termos -> páginas públicas
  GET /saude/bot          -> "ok" se o bot deu sinal de vida nos últimos 5 minutos; senão 503
  GET /google/conectar?t= -> confere o link de uso único da agenda e manda para o Google (D-051)
  GET /google/retorno     -> volta do Google: agenda, ou login da web quando o state começa com "web."
  /web...                 -> web mínima (D-063): entrar com Google, consultar, corrigir, exportar

Nunca registra no log o endereço completo: ele carrega códigos e tokens.
"""

from __future__ import annotations

import hmac
import html
import logging
from pathlib import Path
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

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

# Painel dentro do Telegram (Mini App, onda 2): a página pega a assinatura que o Telegram passa no
# endereço (#tgWebAppData=...), manda ao servidor, ganha a sessão e abre o painel de sempre (/web).
CABECALHOS_APP = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; "
                               "frame-ancestors https://web.telegram.org; base-uri 'none'; form-action 'self'",
    "X-Frame-Options": None,
}
PAGINA_APP = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Peraí</title>
<link rel="stylesheet" href="/web/estilo.css"><script src="/web/app.js" defer></script></head>
<body><main><h1>Peraí</h1><p id="aviso">Abrindo o seu painel…</p>
<noscript><p>Para abrir aqui, o Telegram precisa de JavaScript. Use perai.prementoria.com/web no navegador.</p></noscript>
</main></body></html>"""
APP_JS = """(function () {
  var aviso = document.getElementById('aviso');
  var dados = new URLSearchParams(location.hash.slice(1)).get('tgWebAppData');
  if (!dados) { aviso.textContent = 'Abra o painel pelo botão do Peraí no Telegram (/painel).'; return; }
  fetch('/web/app/entrar', {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'init_data=' + encodeURIComponent(dados)})
    .then(function (r) { return r.text().then(function (t) { return {ok: r.ok, t: t}; }); })
    .then(function (r) { if (r.ok) { location.replace('/web'); } else { aviso.textContent = r.t; } })
    .catch(function () { aviso.textContent = 'Sem conexão com o Peraí agora. Tente de novo.'; });
})();
"""

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
<p><a href="/privacidade">Política de privacidade</a> · <a href="/termos">Termos de uso</a> · Contato: contato@prementoria.com</p>"""

# Página inicial (landing): o que já funciona. Atualizar junto com o guia do kit de abertura.
PAGINA_INICIAL = (Path(__file__).parent / "paginas" / "inicio.html").read_text(encoding="utf-8")

PRIVACIDADE = """<h1>Política de privacidade do Peraí</h1>
<p>Versão de 06/11/2026. O Peraí está em beta, com acesso por convite. Responsável pelos dados
(controlador): Nelson Takemura, pessoa física. Encarregado (DPO): Nelson Takemura, pelo
contato@prementoria.com.</p>
<h2>1. O que guardamos</h2>
<ul><li>O que você manda ao Peraí: mensagens, áudios, fotos, prints, arquivos e mensagens encaminhadas. A
mensagem original fica guardada como chegou, para que toda resposta possa mostrar de onde veio.</li>
<li>O que o Peraí organiza a partir disso: pessoas, empresas, combinados, pendências, lembretes, despesas e
viagens, cada um ligado à mensagem de origem e a quem disse.</li>
<li>Dados de conta: seu nome, o identificador do seu chat no Telegram e, se você conectar, o e-mail da sua
conta Google.</li>
<li>Dados de uso: data, tempo de resposta e custo de cada chamada aos modelos de IA, e as avaliações que você
dá. Não guardamos o seu CPF.</li></ul>
<h2>2. Para que usamos</h2>
<p>Só para o Peraí funcionar para você: lembrar, responder com a fonte, preparar reuniões, avisar na hora certa
e mostrar seus compromissos. Não vendemos dados, não fazemos publicidade e não usamos o seu conteúdo para
treinar modelos de IA. A base legal é a execução do serviço que você pediu e, para dados sensíveis, o seu
consentimento.</p>
<h2>3. Dados sensíveis</h2>
<p>O que você contar sobre religião (por exemplo, no contexto igreja) e sobre saúde é dado sensível pela LGPD.
O Peraí só guarda esse tipo de dado com o seu consentimento específico, pedido na primeira vez, e você pode
mudar quando quiser com /sensiveis.</p>
<h2>4. Pessoas que você menciona</h2>
<p>A sua memória pode ter nomes, cargos e conversas de outras pessoas. Esse conteúdo é para o seu uso pessoal e
profissional, não é compartilhado com ninguém e opinião de terceiros nunca vira fato. Quem foi mencionado pode
pedir correção ou exclusão pelo contato acima.</p>
<h2>5. Google Agenda, Google Drive e login com Google</h2>
<p>Se você conectar a sua conta Google (/agenda), o Peraí lê sua agenda para mostrar compromissos e cria, muda
ou cancela eventos quando você pede, sempre com a opção de desfazer. O Peraí nunca envia convites a outras
pessoas e nunca mexe na agenda a partir de mensagem encaminhada ou arquivo de terceiros. Outras agendas (como
a do trabalho) são só para leitura. No Google Drive, o Peraí só cria e atualiza os documentos que você
pede (atas de reuniões e o documento mestre de cada cliente ou parceiro, na pasta Peraí) e só tem acesso
a esses arquivos, nunca ao resto do seu Drive. Esses documentos são seus: ficam no seu Drive mesmo se você
sair do Peraí. Para entrar pela web, o Peraí recebe só o identificador e o e-mail da
conta. Guardamos uma autorização cifrada, nunca a sua senha.</p>
<p>O uso e a transferência de informações recebidas das APIs do Google seguem a
<a href="https://developers.google.com/terms/api-services-user-data-policy">Política de Dados do
Usuário dos Serviços de API do Google</a>, incluindo os requisitos de Uso Limitado. Os dados do Google
não são vendidos, não são usados para publicidade e não são usados para treinar modelos de IA.</p>
<h2>6. Com quem compartilhamos e onde é processado</h2>
<ul><li>Telegram: receber e entregar as mensagens (fora do Brasil).</li>
<li>Google Gemini, por API paga: ler, organizar e responder; o Google não usa o conteúdo para treinar
modelos (fora do Brasil).</li>
<li>Google Agenda e Google Drive: sua agenda e os documentos que você pede para salvar no seu Drive, se
conectados; cópia de segurança cifrada, que o Google não consegue ler (fora do Brasil).</li>
<li>Hostinger: servidor onde ficam o banco e os arquivos (Campinas, SP, Brasil).</li></ul>
<p>Não compartilhamos com mais ninguém, salvo por ordem judicial ou obrigação legal.</p>
<h2>7. Quem tem acesso</h2>
<p>Outras pessoas que usam o Peraí não veem nada seu. A equipe técnica tem acesso ao servidor para mantê-lo
funcionando, mas só abre o conteúdo de uma pessoa quando ela pede (por exemplo, para corrigir um erro) ou por
obrigação legal, e avisa a pessoa quando isso acontece.</p>
<h2>8. Segurança e retenção</h2>
<p>Conexões cifradas (HTTPS), autorizações do Google cifradas no banco, servidor com firewall e cópia de
segurança diária cifrada. Os dados ficam enquanto você usar o Peraí. Ao pedir a exclusão, apagamos tudo em até
15 dias; as cópias de segurança com esses dados saem sozinhas em até 30 dias. Ficam só dados de custo sem
vínculo com você.</p>
<h2>9. Seus direitos</h2>
<p>Você pode ver, corrigir e exportar seus dados pela web (/web), desconectar a agenda (/desconectar_agenda ou
myaccount.google.com/permissions), desligar o briefing automático (/briefing desligar) e pedir a exclusão
definitiva pelo contato@prementoria.com. Respondemos em até 15 dias. Você também pode reclamar à Autoridade
Nacional de Proteção de Dados (ANPD).</p>
<h2>10. Mudanças</h2>
<p>Avisamos pelo Telegram, com pelo menos 15 dias de antecedência, qualquer mudança relevante nesta política.
Veja também os <a href="/termos">termos de uso</a>.</p>"""

TERMOS = """<h1>Termos de uso do Peraí</h1>
<p>Versão de 06/11/2026. Ao tocar em “Aceito” no Telegram, você concorda com estes termos e com a
<a href="/privacidade">política de privacidade</a>.</p>
<h2>1. O que o Peraí é</h2>
<p>Um assistente pessoal de memória, em beta: guarda o que você conta, responde mostrando a fonte, ajuda a
preparar reuniões e lembra o que você pediu. Ele orienta; quem decide é você.</p>
<h2>2. O que ele não faz</h2>
<ul><li>Não substitui médico, advogado, contador ou outro profissional.</li>
<li>Não envia mensagens, e-mails ou convites a outras pessoas em seu nome.</li>
<li>Não faz pagamentos nem compras.</li>
<li>Não garante que tudo o que você contou foi entendido do jeito certo: confira o que for importante.</li></ul>
<h2>3. O Peraí pode errar</h2>
<p>Ele usa modelos de IA, que podem entender errado uma mensagem, um áudio ou uma data. Por isso cada resposta
mostra a fonte, e você pode corrigir a qualquer momento (“não foi sexta, foi sábado” ou pelo /web). Quando o
Peraí não tem a informação, ele deve dizer que não sabe.</p>
<h2>4. Seu conteúdo</h2>
<p>O que você manda continua seu. Você nos autoriza a guardá-lo e processá-lo só para o Peraí funcionar para
você, como descrito na política de privacidade. Você se compromete a mandar só conteúdo que pode compartilhar:
respeite segredos profissionais e as regras da sua empresa (por exemplo, sobre a agenda do trabalho).</p>
<h2>5. Uso aceitável</h2>
<p>Não use o Peraí para vigiar, perseguir ou expor outras pessoas, nem para nada ilegal. Para evitar custo
excessivo, há um limite de uso por dia; quando ele é atingido, o Peraí avisa e volta à meia-noite.</p>
<h2>6. Idade</h2>
<p>O Peraí é para maiores de 18 anos.</p>
<h2>7. Beta e preço</h2>
<p>No beta, o Peraí é gratuito e pode mudar, ficar fora do ar ou ser encerrado. Se ele passar a ser pago,
avisamos com 30 dias de antecedência, e você pode exportar seus dados e sair antes.</p>
<h2>8. Sair</h2>
<p>Você pode parar de usar quando quiser e pedir a exclusão definitiva dos seus dados (política de privacidade,
item 9). Podemos suspender o acesso de quem descumprir estes termos.</p>
<h2>9. Responsabilidade</h2>
<p>Fazemos o possível para o Peraí ser correto e seguro, mas, no beta, ele é oferecido no estado em que está.
Não respondemos por decisões tomadas só com base nas respostas dele, sem conferência, exceto nos casos em que a
lei não permite essa limitação.</p>
<h2>10. Contato e foro</h2>
<p>Dúvidas: contato@prementoria.com. Estes termos seguem a lei brasileira; fica eleito o foro de Umuarama
(PR).</p>"""


def criar_manipulador(agenda: Agenda, avisar, painel: Painel | None = None):
    # Várias conexões ao mesmo tempo (uma lenta não trava as outras), mas o banco é uma conexão só:
    # o trabalho com o banco e o Google passa um de cada vez por esta trava.
    trava = threading.Lock()

    class Manipulador(BaseHTTPRequestHandler):
        server_version = "Perai"
        sys_version = ""
        timeout = 20  # segundos sem receber nada: a conexão é fechada

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
                if v is not None:   # None tira o cabeçalho padrão (ex.: X-Frame-Options no Mini App)
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
            if caminho == "/web/app.js":
                return self._enviar(200, APP_JS.encode(), "text/javascript; charset=utf-8", CABECALHOS_APP)
            if caminho == "/web/app":
                return self._enviar(200, PAGINA_APP.encode(), "text/html; charset=utf-8", CABECALHOS_APP)
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
            if caminho == "/web/teste":
                if not sessao:
                    return self._ir("/web")
                from perai.painel import pagina_teste
                from perai.servico import resultado_teste
                return self._html(200, pagina_teste(resultado_teste(painel.repo, sessao[0])))
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
            with trava:
                return self._post(caminho, dados)

        def _post(self, caminho: str, dados: dict):
            if painel is None or not painel.disponivel:
                return self._responder(503, "Web desligada", "A entrada pela web ainda não foi ligada neste servidor.")
            if caminho == "/web/app/entrar":
                # Painel dentro do Telegram: a assinatura do Telegram substitui o login do Google.
                try:
                    _, token = painel.entrar_telegram(dados.get("init_data", ""),
                                                      os.environ.get("TELEGRAM_BOT_TOKEN", ""))
                except ErroPainel as erro:
                    return self._enviar(403, str(erro).encode(), "text/plain; charset=utf-8", CABECALHOS_APP)
                sessao = (f"{COOKIE_SESSAO}={token}; Max-Age={int(VALIDADE_SESSAO.total_seconds())}; Path=/; "
                          "HttpOnly; Secure; SameSite=Lax")
                return self._enviar(200, b"ok", "text/plain; charset=utf-8", CABECALHOS_APP, [sessao])
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
            with trava:
                return self._get()

        def _get(self):
            url = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            if url.path == "/web" or url.path.startswith("/web/"):
                return self._web_get(url.path, q)
            if url.path == "/google/retorno" and q.get("state", "").startswith("web.") and painel:
                return self._web_retorno(q)
            if url.path == "/saude/bot":
                # Bot vivo = gravou sinal nos últimos 5 minutos (ele grava a cada volta, ~30 s).
                visto = agenda.repo.ultimo_sinal("bot")
                agora = datetime.now(timezone.utc)
                vivo = visto is not None and (agora - (visto if visto.tzinfo else visto.replace(
                    tzinfo=timezone.utc))).total_seconds() < 300
                corpo = b"ok" if vivo else b"bot parado"
                self.send_response(200 if vivo else 503)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            if url.path == "/saude":
                corpo = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(corpo)
                return
            if url.path in ("/", ""):
                corpo = PAGINA_INICIAL.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            if url.path == "/privacidade":
                return self._pagina(200, "Peraí — Privacidade", PRIVACIDADE)
            if url.path == "/termos":
                return self._pagina(200, "Peraí — Termos de uso", TERMOS)
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
    servidor = ThreadingHTTPServer(("0.0.0.0", porta), criar_manipulador(agenda, avisar, painel))
    log.info("web no ar na porta %s; Google Agenda %s", porta, "ligada" if agenda.disponivel else "desligada")
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

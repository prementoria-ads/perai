"""Ler sites (D-062): o link que o usuário manda vira texto, anotado como "segundo o site X" (D-044).

Cuidados:
- só endereços públicos da internet: o nome é resolvido aqui e a conexão vai direto ao IP conferido
  (nada de rede interna, do próprio servidor ou de endereço que muda entre a conferência e a conexão);
- só http e https nas portas padrão; até 3 redirecionamentos, cada um conferido de novo;
- até 2 MB e 15 segundos; só páginas de texto (HTML ou texto puro);
- o texto da página é conteúdo, nunca pedido: instruções escondidas nele não mexem em nada.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

LIMITE_BYTES = 2 * 1024 * 1024
TEMPO = 15
REDIRECIONAMENTOS = 3
PADRAO_LINK = re.compile(r"https?://[^\s<>\"'«»“”]+", re.I)
AGENTE = "Perai/1.0 (leitor de paginas a pedido do usuario)"


class ErroSite(RuntimeError):
    pass


@dataclass
class Pagina:
    url: str
    titulo: str
    texto: str


def links(texto: str) -> list[str]:
    return [u.rstrip(".,;:!?)]") for u in PADRAO_LINK.findall(texto or "")]


def _ip_publico(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not (a.is_multicast or a.is_reserved or a.is_loopback or a.is_link_local
                                or a.is_private or a.is_unspecified)


def resolver(host: str, porta: int) -> str:
    """IP público do host; recusa se QUALQUER endereço dele for interno (evita truques de DNS)."""
    try:
        infos = socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)
    except socket.gaierror as erro:
        raise ErroSite(f"endereço não encontrado: {host}") from erro
    ips = [i[4][0] for i in infos]
    if not ips or not all(_ip_publico(ip) for ip in ips):
        raise ErroSite("endereço fora da internet pública")
    return ips[0]


def _conferir(url: str) -> tuple[str, str, int, str]:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ErroSite("só leio links http ou https")
    porta = u.port or (443 if u.scheme == "https" else 80)
    if porta not in (80, 443) or u.username or u.password:
        raise ErroSite("link fora do padrão (porta ou usuário no endereço)")
    caminho = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return u.scheme, u.hostname, porta, caminho


class _ConexaoHTTPS(http.client.HTTPSConnection):
    """Conecta no IP já conferido, mas valida o certificado pelo nome do site."""

    def __init__(self, ip, host, porta, timeout):
        super().__init__(host, porta, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _pedir(url: str, resolver_fn=resolver) -> tuple[int, dict, bytes]:
    esquema, host, porta, caminho = _conferir(url)
    ip = resolver_fn(host, porta)
    if esquema == "https":
        conexao = _ConexaoHTTPS(ip, host, porta, TEMPO)
    else:
        conexao = http.client.HTTPConnection(ip, porta, timeout=TEMPO)
    try:
        conexao.request("GET", caminho, headers={"Host": host, "User-Agent": AGENTE,
                                                 "Accept": "text/html,text/plain;q=0.9"})
        r = conexao.getresponse()
        corpo = r.read(LIMITE_BYTES + 1)
        if len(corpo) > LIMITE_BYTES:
            raise ErroSite("página grande demais (mais de 2 MB)")
        return r.status, {k.lower(): v for k, v in r.getheaders()}, corpo
    except (OSError, http.client.HTTPException) as erro:
        raise ErroSite(f"não consegui abrir: {erro.__class__.__name__}") from erro
    finally:
        conexao.close()


class _Texto(HTMLParser):
    IGNORAR = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
    BLOCOS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes, self.titulo, self._fundo, self._no_titulo = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORAR:
            self._fundo += 1
        if tag == "title":
            self._no_titulo = True
        if tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORAR and self._fundo:
            self._fundo -= 1
        if tag == "title":
            self._no_titulo = False

    def handle_data(self, dados):
        if self._no_titulo:
            self.titulo += dados
        elif not self._fundo:
            self.partes.append(dados)


def texto_de_html(conteudo: str) -> tuple[str, str]:
    p = _Texto()
    p.feed(conteudo)
    texto = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.partes))
    texto = re.sub(r"\n\s*\n+", "\n\n", texto).strip()
    return re.sub(r"\s+", " ", p.titulo).strip(), texto


def ler(url: str, resolver_fn=resolver, pedir_fn=None) -> Pagina:
    pedir_fn = pedir_fn or (lambda u: _pedir(u, resolver_fn))
    atual = url
    for _ in range(REDIRECIONAMENTOS + 1):
        status, cab, corpo = pedir_fn(atual)
        if status in (301, 302, 303, 307, 308) and cab.get("location"):
            atual = urllib.parse.urljoin(atual, cab["location"])
            continue
        if status in (401, 403):
            raise ErroSite("a página pede login ou bloqueou a leitura")
        if status >= 400:
            raise ErroSite(f"o site respondeu {status}")
        tipo = cab.get("content-type", "").lower()
        charset = re.search(r"charset=([\w-]+)", tipo)
        bruto = corpo.decode(charset.group(1) if charset else "utf-8", errors="replace")
        if "html" in tipo:
            titulo, texto = texto_de_html(bruto)
        elif tipo.startswith("text/"):
            titulo, texto = "", bruto.strip()
        else:
            raise ErroSite("só leio páginas de texto (HTML ou texto puro); PDF mande como arquivo")
        if len(texto) < 200:
            raise ErroSite("a página tem pouco texto (pode depender de login ou de JavaScript)")
        return Pagina(atual, titulo, texto[:60_000])
    raise ErroSite("redirecionamentos demais")

"""E-mail do Peraí (D-072): e-mails encaminhados para a caixa do Peraí viram mensagens, como no Telegram.

Uma caixa só (ex.: perai@prementoria.com), lida por IMAP a cada minuto. Quem é a pessoa vem do
remetente, que precisa estar cadastrado por ela (/email adicionar ...) e AUTENTICADO pelo provedor
(DMARC, DKIM ou SPF do próprio domínio do remetente, no Authentication-Results que o provedor põe):
sem isso, qualquer um poderia escrever "De: nelson@..." e mandar conteúdo para a memória dele.

Todo conteúdo de e-mail é tratado como encaminhado (de terceiros): vira registro, pendência ou
despesa, mas nunca comando (não mexe na agenda; bloqueador 1 da revisão de lançamento). O Peraí não
envia e-mail. Depois de lido, o e-mail é apagado da caixa: a cópia fica só na memória (D-040).

Variáveis do .env:
  PERAI_EMAIL_IMAP_HOST, PERAI_EMAIL_IMAP_PORTA (993), PERAI_EMAIL_USUARIO, PERAI_EMAIL_SENHA
  PERAI_EMAIL_ENDERECO (o endereço mostrado no /email; padrão = PERAI_EMAIL_USUARIO)
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr
from pathlib import Path

log = logging.getLogger("perai.email")

TAMANHO_MAXIMO = 20 * 1024 * 1024   # e-mail inteiro
EXTENSOES_ANEXO = {".pdf", ".xml", ".docx", ".xlsx", ".pptx", ".md", ".txt", ".jpg", ".jpeg", ".png",
                   ".webp", ".ics"}


@dataclass
class Email:
    remetente: str
    assunto: str
    corpo: str
    autenticado: bool
    anexos: list[tuple[str, bytes]] = field(default_factory=list)


def configurado() -> bool:
    return all(os.environ.get(v, "").strip() for v in
               ("PERAI_EMAIL_IMAP_HOST", "PERAI_EMAIL_USUARIO", "PERAI_EMAIL_SENHA"))


def endereco() -> str:
    return (os.environ.get("PERAI_EMAIL_ENDERECO") or os.environ.get("PERAI_EMAIL_USUARIO") or "").strip()


def normalizar(endereco_: str) -> str | None:
    e = parseaddr(endereco_ or "")[1].strip().lower()
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e) else None


def autenticado(mensagem, remetente: str) -> bool:
    """Confere o Authentication-Results que o NOSSO provedor pôs (o primeiro, mais acima): DMARC
    aprovado para o domínio do remetente, ou DKIM/SPF aprovado com o mesmo domínio."""
    resultados = mensagem.get_all("Authentication-Results") or []
    if not resultados:
        return False
    r = " ".join(str(resultados[0]).lower().split())
    dominio = remetente.rsplit("@", 1)[-1]
    if re.search(r"\bdmarc=pass\b", r) and (f"header.from={dominio}" in r or "header.from=" not in r):
        return True
    for d in re.findall(r"\bdkim=pass\b[^;]*?header\.(?:d|i)=@?([a-z0-9.-]+)", r):
        if d == dominio or dominio.endswith("." + d):
            return True
    for d in re.findall(r"\bspf=pass\b[^;]*?smtp\.mailfrom=(?:[^@\s;]*@)?([a-z0-9.-]+)", r):
        if d == dominio:
            return True
    return False


def _texto_html(conteudo: str) -> str:
    from perai.sites import texto_de_html
    return texto_de_html(conteudo)[1]


def ler(bruto: bytes) -> Email | None:
    m = email.message_from_bytes(bruto, policy=email.policy.default)
    remetente = normalizar(str(m.get("From", "")))
    if not remetente:
        return None
    corpo_txt, corpo_html, anexos = "", "", []
    for parte in m.walk():
        if parte.is_multipart():
            continue
        nome = parte.get_filename()
        tipo = parte.get_content_type()
        if nome:
            if Path(nome).suffix.lower() in EXTENSOES_ANEXO:
                dados = parte.get_payload(decode=True) or b""
                if dados:
                    anexos.append((Path(nome).name[:120], dados))
            continue
        if tipo == "text/plain" and not corpo_txt:
            corpo_txt = parte.get_content()
        elif tipo == "text/html" and not corpo_html:
            corpo_html = parte.get_content()
    corpo = corpo_txt.strip() or (_texto_html(corpo_html) if corpo_html else "")
    return Email(remetente, " ".join(str(m.get("Subject", "")).split())[:300], corpo[:60_000],
                 autenticado(m, remetente), anexos)


def tipo_do_anexo(nome: str) -> str | None:
    from perai.bot import tipo_do_documento
    return tipo_do_documento(nome) or ("conversa_exportada" if nome.lower().endswith(".txt") else None)


def processar(perai, pasta: Path, avisar, conexao=None) -> int:
    """Lê os e-mails novos, entrega cada um ao Peraí da pessoa e avisa no Telegram. Devolve quantos
    viraram mensagem. `avisar(usuario_id, texto)`. `conexao`: IMAP já aberto (testes)."""
    from perai.servico import Entrada
    if conexao is None:
        if not configurado():
            return 0
        conexao = imaplib.IMAP4_SSL(os.environ["PERAI_EMAIL_IMAP_HOST"],
                                    int(os.environ.get("PERAI_EMAIL_IMAP_PORTA", "993")), timeout=30)
        conexao.login(os.environ["PERAI_EMAIL_USUARIO"], os.environ["PERAI_EMAIL_SENHA"])
    feitos = 0
    try:
        conexao.select("INBOX")
        _, dados = conexao.search(None, "UNSEEN")
        for num in (dados[0] or b"").split()[:20]:
            _, partes = conexao.fetch(num, "(RFC822)")
            bruto = next((p[1] for p in partes if isinstance(p, tuple)), b"")
            try:
                e = ler(bruto) if len(bruto) <= TAMANHO_MAXIMO else None
                usuario_id = perai.repo.dono_do_remetente(e.remetente) if e else None
                if not e or not usuario_id:
                    log.info("e-mail descartado: remetente não cadastrado (%s)",
                             e.remetente.rsplit("@", 1)[-1] if e else "ilegível")
                elif not e.autenticado:
                    log.info("e-mail descartado: remetente não autenticado (%s)", e.remetente.rsplit("@", 1)[-1])
                    avisar(usuario_id, f"📧 Chegou um e-mail com o seu endereço ({e.remetente}), mas o provedor "
                                       "não confirmou que foi você que mandou. Descartei sem ler.")
                elif perai.passou_do_teto(usuario_id):
                    avisar(usuario_id, "📧 Chegou um e-mail seu, mas cheguei ao limite de uso de hoje. "
                                       "Encaminhe de novo depois da meia-noite.")
                else:
                    feitos += _entregar(perai, pasta, avisar, usuario_id, e, Entrada)
            except Exception:
                log.exception("falha ao tratar um e-mail")
            conexao.store(num, "+FLAGS", "\\Deleted")
        conexao.expunge()
    finally:
        try:
            conexao.logout()
        except Exception:
            pass
    return feitos


def _entregar(perai, pasta: Path, avisar, usuario_id: str, e: Email, Entrada) -> int:
    """Cada parte (o texto e cada anexo) vira uma mensagem; cada resposta vai ao Telegram com os
    botões dela (ex.: "Ata da reunião")."""
    avisar(usuario_id, f"📧 Recebi por e-mail: {e.assunto or '(sem assunto)'}")
    if e.corpo.strip():
        texto = f"E-mail encaminhado. Assunto: {e.assunto or '(sem assunto)'}\n{e.corpo}"
        avisar(usuario_id, perai.receber(usuario_id, Entrada("email", "texto", texto,
                                                             encaminhada=f"e-mail ({e.assunto[:60]})")))
    for nome, dados in e.anexos[:5]:
        tipo = tipo_do_anexo(nome)
        if not tipo:
            continue
        destino = pasta / usuario_id / f"{uuid.uuid4().hex}{Path(nome).suffix.lower()}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados)
        avisar(usuario_id, perai.receber(usuario_id, Entrada("email", tipo, f"Anexo do e-mail “{e.assunto[:80]}”",
                                                             destino, nome_original=nome, encaminhada="e-mail")))
    return 1

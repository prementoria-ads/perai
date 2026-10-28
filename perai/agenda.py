"""Agenda do usuário (D-051): junta o repositório, o cliente do Google e a cifra.

O resto do Peraí só conhece esta classe. Sem Google configurado, `disponivel` é falso
e nada muda no comportamento do bot.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from perai.google import (VALIDADE_LINK, Cifra, ErroGoogle, EventoAgenda, Google, fim_padrao, hash_token,
                          novo_token_link)

log = logging.getLogger("perai.agenda")
FUSO = ZoneInfo("America/Sao_Paulo")


def _agora() -> datetime:
    return datetime.now(FUSO).replace(tzinfo=None)


class Agenda:
    def __init__(self, repo, google: Google | None, cifra: Cifra | None, endereco_publico: str):
        self.repo = repo
        self.google = google
        self.cifra = cifra
        self.endereco = endereco_publico.rstrip("/")

    @property
    def disponivel(self) -> bool:
        return self.google is not None and self.cifra is not None

    # -- conexão -------------------------------------------------------------------------

    def link_conexao(self, usuario_id: str) -> str:
        token = novo_token_link()
        self.repo.criar_conexao_google(usuario_id, hash_token(token), _agora() + VALIDADE_LINK)
        return f"{self.endereco}/google/conectar?t={token}"

    def url_google(self, token: str) -> str | None:
        """Passo 2 do link: confere o token e devolve o endereço de autorização do Google."""
        if not self.repo.conexao_google(hash_token(token), _agora()):
            return None
        return self.google.url_autorizacao(token)

    def concluir(self, estado: str, codigo: str) -> tuple[str, str]:
        """Retorno do Google: troca o código, guarda o refresh token cifrado. (usuario_id, email)"""
        usuario_id = self.repo.conexao_google(hash_token(estado), _agora())
        if not usuario_id:
            raise PermissionError("link vencido ou já usado")
        refresh, escopos, email = self.google.trocar_codigo(codigo)
        self.repo.salvar_conta_google(usuario_id, email, self.cifra.cifrar(refresh), escopos)
        self.repo.usar_conexao_google(hash_token(estado), _agora())
        return usuario_id, email

    def conta(self, usuario_id: str) -> tuple[str, str, str] | None:
        """(email, refresh_token, agenda_id) ou None."""
        if not self.disponivel:
            return None
        c = self.repo.conta_google(usuario_id)
        if not c:
            return None
        email, cifrado, agenda = c
        return email, self.cifra.decifrar(cifrado), agenda

    def email(self, usuario_id: str) -> str | None:
        c = self.conta(usuario_id)
        return c[0] if c else None

    def desconectar(self, usuario_id: str) -> bool:
        c = self.conta(usuario_id)
        if not c:
            return False
        self.google.revogar(c[1])
        self.repo.desconectar_google(usuario_id, _agora())
        return True

    # -- leitura -------------------------------------------------------------------------

    def eventos(self, usuario_id: str, dias_antes: int = 1, dias_depois: int = 14) -> list[EventoAgenda]:
        """Eventos da agenda na janela; em caso de falha, lista vazia (o bot segue sem agenda)."""
        c = self.conta(usuario_id)
        if not c:
            return []
        hoje = datetime.now(FUSO).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            return self.google.listar(c[1], c[2], hoje - timedelta(days=dias_antes),
                                      hoje + timedelta(days=dias_depois + 1))
        except (ErroGoogle, OSError, ValueError) as erro:
            log.warning("não consegui ler a agenda: %s", erro)
            return []

    # -- escrita (só eventos criados pelo Peraí) -------------------------------------------

    def criar(self, usuario_id: str, mensagem_id: str | None, registro_id: str | None, titulo: str,
              inicio: str, fim: str | None, local: str | None) -> dict | None:
        c = self.conta(usuario_id)
        if not c:
            return None
        fim = fim if fim and fim > inicio else fim_padrao(inicio)
        descricao = "Criado pelo Peraí a partir da sua mensagem no Telegram."
        google_id = self.google.criar(c[1], c[2], titulo, inicio, fim, local, descricao)
        local_id = self.repo.salvar_evento(usuario_id, mensagem_id, registro_id, google_id, titulo,
                                           inicio, fim, local)
        return {"id": local_id, "titulo": titulo, "inicio": inicio, "fim": fim, "local": local}

    def criados_futuros(self, usuario_id: str) -> list[dict]:
        if not self.conta(usuario_id):
            return []
        return self.repo.eventos_criados(usuario_id, _agora() - timedelta(hours=12))

    def desfazer(self, usuario_id: str, evento_id: str) -> dict | None:
        e = self.repo.evento(usuario_id, evento_id)
        c = self.conta(usuario_id)
        if not e or not c or e["situacao"] in ("desfeito", "cancelado"):
            return None
        self.google.apagar(c[1], c[2], e["google_evento_id"])
        self.repo.mudar_evento(usuario_id, evento_id, None, situacao="desfeito")
        return e

    def ajustar(self, usuario_id: str, evento_id: str, mensagem_id: str | None,
                inicio: str | None = None, fim: str | None = None, cancelar: bool = False) -> dict | None:
        e = self.repo.evento(usuario_id, evento_id)
        c = self.conta(usuario_id)
        if not e or not c:
            return None
        if cancelar:
            self.google.apagar(c[1], c[2], e["google_evento_id"])
            self.repo.mudar_evento(usuario_id, evento_id, mensagem_id, situacao="cancelado")
            return {**e, "situacao": "cancelado"}
        if inicio and not fim:
            # Mantém a duração original.
            duracao = datetime.fromisoformat(e["fim"]) - datetime.fromisoformat(e["inicio"])
            fim = (datetime.fromisoformat(inicio) + duracao).strftime("%Y-%m-%dT%H:%M")
        self.google.alterar(c[1], c[2], e["google_evento_id"], inicio=inicio, fim=fim)
        campos = {k: v for k, v in (("inicio", inicio), ("fim", fim)) if v}
        campos["situacao"] = "alterado"
        self.repo.mudar_evento(usuario_id, evento_id, mensagem_id, **campos)
        return {**e, **campos}


def agenda_do_ambiente(repo) -> Agenda:
    """Monta a Agenda a partir do .env. Sem as variáveis do Google, fica indisponível."""
    import os
    endereco = os.environ.get("PERAI_ENDERECO", "https://perai.prementoria.com")
    cid, segredo = os.environ.get("GOOGLE_CLIENT_ID", "").strip(), os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    chave = os.environ.get("PERAI_CHAVE_CIFRA", "").strip()
    if not (cid and segredo and chave):
        return Agenda(repo, None, None, endereco)
    return Agenda(repo, Google(cid, segredo, f"{endereco.rstrip('/')}/google/retorno"), Cifra(chave), endereco)

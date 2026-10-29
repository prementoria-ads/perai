"""Google Agenda (D-051): autorização OAuth e leitura/escrita de eventos.

Só biblioteca padrão para HTTP; o refresh token é cifrado com Fernet (pacote
`cryptography`) usando PERAI_CHAVE_CIFRA, que existe só no .env do servidor.

Regras:
- Cada pessoa autoriza a própria agenda; o link de conexão é de uso único e vale 15 min.
- O Peraí altera eventos que ele mesmo criou (tabela eventos_agenda). Eventos de fora ele só
  cancela quando o usuário pede, e guarda como desfazer (D-059, tabela cancelamentos_agenda).
- Nunca envia convite a outras pessoas: eventos são criados sem convidados.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("perai.google")

FUSO = "America/Sao_Paulo"
ESCOPOS = ("https://www.googleapis.com/auth/calendar.events "
           "https://www.googleapis.com/auth/userinfo.email")
URL_AUTORIZACAO = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_REVOGAR = "https://oauth2.googleapis.com/revoke"
URL_USUARIO = "https://www.googleapis.com/oauth2/v2/userinfo"
URL_AGENDA = "https://www.googleapis.com/calendar/v3/calendars"
VALIDADE_LINK = timedelta(minutes=15)
DURACAO_PADRAO = timedelta(hours=1)

# (método, url, cabeçalhos, corpo) -> (status, corpo)
Transporte = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


class ErroGoogle(RuntimeError):
    pass


def transporte_http(metodo: str, url: str, cabecalhos: dict, corpo: bytes | None) -> tuple[int, bytes]:
    pedido = urllib.request.Request(url, data=corpo, headers=cabecalhos, method=metodo)
    try:
        with urllib.request.urlopen(pedido, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as erro:
        return erro.code, erro.read()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def novo_token_link() -> str:
    return secrets.token_urlsafe(24)


class Cifra:
    """Cifra simétrica para guardar o refresh token no banco."""

    def __init__(self, chave: str):
        from cryptography.fernet import Fernet  # instalado na imagem do servidor
        self._f = Fernet(chave.encode())

    def cifrar(self, texto: str) -> str:
        return self._f.encrypt(texto.encode()).decode()

    def decifrar(self, texto: str) -> str:
        return self._f.decrypt(texto.encode()).decode()


@dataclass
class EventoAgenda:
    id: str                 # id do evento no Google
    titulo: str
    inicio: str             # AAAA-MM-DDTHH:MM (horário de Brasília) ou AAAA-MM-DD (dia inteiro)
    fim: str | None
    local: str | None = None
    dia_inteiro: bool = False
    recorrente_de: str | None = None   # id da série, quando o evento se repete
    organizador: bool = True           # o usuário é o organizador (pode mudar a série)


class Google:
    """Cliente mínimo do OAuth e da Agenda do Google."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str,
                 transporte: Transporte = transporte_http):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._t = transporte
        self._acessos: dict[str, tuple[str, float]] = {}  # refresh -> (access, expira)

    # -- autorização -------------------------------------------------------------------

    def url_autorizacao(self, estado: str) -> str:
        return URL_AUTORIZACAO + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "response_type": "code", "scope": ESCOPOS, "access_type": "offline",
            "prompt": "consent", "include_granted_scopes": "true", "state": estado,
        })

    def url_login(self, estado: str) -> str:
        """Entrar na web com Google (D-063): só identidade (openid e e-mail), sem acesso à agenda."""
        return URL_AUTORIZACAO + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "response_type": "code", "scope": "openid email", "prompt": "select_account",
            "state": estado,
        })

    def identidade(self, codigo: str) -> tuple[str, str, bool]:
        """Troca o código do login por (sub, e-mail, e-mail verificado). Não guarda token nenhum."""
        dados = self._form(URL_TOKEN, {
            "code": codigo, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code",
        })
        status, corpo = self._t("GET", URL_USUARIO, {"Authorization": f"Bearer {dados['access_token']}"}, None)
        info = json.loads(corpo or b"{}") if status < 400 else {}
        if not info.get("id"):
            raise ErroGoogle("o Google não devolveu a identidade da conta")
        return str(info["id"]), info.get("email") or "", bool(info.get("verified_email"))

    def _form(self, url: str, campos: dict) -> dict:
        status, corpo = self._t("POST", url, {"Content-Type": "application/x-www-form-urlencoded"},
                                urllib.parse.urlencode(campos).encode())
        dados = json.loads(corpo or b"{}")
        if status >= 400:
            # Nunca inclui o código, o segredo ou tokens na mensagem de erro.
            raise ErroGoogle(f"{url} respondeu {status}: {dados.get('error', '')} {dados.get('error_description', '')}")
        return dados

    def trocar_codigo(self, codigo: str) -> tuple[str, str, str]:
        """Devolve (refresh_token, escopos, email)."""
        dados = self._form(URL_TOKEN, {
            "code": codigo, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code",
        })
        refresh = dados.get("refresh_token")
        if not refresh:
            raise ErroGoogle("o Google não devolveu refresh_token")
        acesso = dados["access_token"]
        self._acessos[refresh] = (acesso, time.time() + int(dados.get("expires_in", 3600)) - 60)
        status, corpo = self._t("GET", URL_USUARIO, {"Authorization": f"Bearer {acesso}"}, None)
        email = json.loads(corpo or b"{}").get("email") if status < 400 else None
        return refresh, dados.get("scope", ESCOPOS), email or ""

    def _acesso(self, refresh: str) -> str:
        guardado = self._acessos.get(refresh)
        if guardado and guardado[1] > time.time():
            return guardado[0]
        dados = self._form(URL_TOKEN, {
            "refresh_token": refresh, "client_id": self.client_id,
            "client_secret": self.client_secret, "grant_type": "refresh_token",
        })
        self._acessos[refresh] = (dados["access_token"], time.time() + int(dados.get("expires_in", 3600)) - 60)
        return dados["access_token"]

    def revogar(self, refresh: str) -> None:
        self._acessos.pop(refresh, None)
        try:
            self._form(URL_REVOGAR, {"token": refresh})
        except ErroGoogle as erro:
            log.warning("revogação: %s", erro)

    # -- agenda --------------------------------------------------------------------------

    def _api(self, refresh: str, metodo: str, caminho: str, corpo: dict | None = None,
             params: dict | None = None) -> dict:
        url = f"{URL_AGENDA}/{caminho}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        cab = {"Authorization": f"Bearer {self._acesso(refresh)}"}
        dados = None
        if corpo is not None:
            cab["Content-Type"] = "application/json"
            dados = json.dumps(corpo).encode()
        status, resposta = self._t(metodo, url, cab, dados)
        if metodo == "DELETE" and status in (204, 404, 410):
            return {}
        if status >= 400:
            erro = json.loads(resposta or b"{}").get("error", {})
            raise ErroGoogle(f"agenda {metodo} respondeu {status}: {erro.get('message', '')}")
        return json.loads(resposta or b"{}")

    def listar(self, refresh: str, agenda: str, desde: datetime, ate: datetime,
               maximo: int = 50, busca: str | None = None) -> list[EventoAgenda]:
        params = {
            "timeMin": desde.isoformat(), "timeMax": ate.isoformat(), "singleEvents": "true",
            "orderBy": "startTime", "maxResults": str(maximo), "timeZone": FUSO,
        }
        if busca:
            params["q"] = busca
        dados = self._api(refresh, "GET", f"{urllib.parse.quote(agenda)}/events", params=params)
        eventos = []
        for e in dados.get("items", []):
            if e.get("status") == "cancelled":
                continue
            ini, fim = e.get("start", {}), e.get("end", {})
            dia_inteiro = "date" in ini
            eventos.append(EventoAgenda(
                id=e["id"], titulo=e.get("summary") or "(sem título)",
                inicio=ini.get("date") if dia_inteiro else _local(ini.get("dateTime")),
                fim=fim.get("date") if dia_inteiro else _local(fim.get("dateTime")),
                local=e.get("location"), dia_inteiro=dia_inteiro,
                recorrente_de=e.get("recurringEventId"),
                organizador=bool((e.get("organizer") or {}).get("self", True)),
            ))
        return eventos

    def criar(self, refresh: str, agenda: str, titulo: str, inicio: str, fim: str,
              local: str | None, descricao: str) -> str:
        corpo = {
            "summary": titulo, "description": descricao,
            "start": {"dateTime": f"{inicio}:00", "timeZone": FUSO},
            "end": {"dateTime": f"{fim}:00", "timeZone": FUSO},
            "reminders": {"useDefault": True},
        }
        if local:
            corpo["location"] = local
        # sendUpdates=none e sem "attendees": nenhum convite sai em nome do usuário.
        dados = self._api(refresh, "POST", f"{urllib.parse.quote(agenda)}/events", corpo,
                          params={"sendUpdates": "none"})
        return dados["id"]

    def alterar(self, refresh: str, agenda: str, evento_id: str, inicio: str | None = None,
                fim: str | None = None, titulo: str | None = None) -> None:
        corpo: dict = {}
        if inicio:
            corpo["start"] = {"dateTime": f"{inicio}:00", "timeZone": FUSO}
        if fim:
            corpo["end"] = {"dateTime": f"{fim}:00", "timeZone": FUSO}
        if titulo:
            corpo["summary"] = titulo
        if corpo:
            self._api(refresh, "PATCH", f"{urllib.parse.quote(agenda)}/events/{urllib.parse.quote(evento_id)}",
                      corpo, params={"sendUpdates": "none"})

    def apagar(self, refresh: str, agenda: str, evento_id: str) -> None:
        self._api(refresh, "DELETE", f"{urllib.parse.quote(agenda)}/events/{urllib.parse.quote(evento_id)}",
                  params={"sendUpdates": "none"})


    # -- cancelar eventos de fora e desfazer (D-059) --------------------------------------

    def _caminho(self, agenda: str, evento_id: str) -> str:
        return f"{urllib.parse.quote(agenda)}/events/{urllib.parse.quote(evento_id)}"

    def obter(self, refresh: str, agenda: str, evento_id: str) -> dict:
        return self._api(refresh, "GET", self._caminho(agenda, evento_id))

    def mudar_recorrencia(self, refresh: str, agenda: str, evento_id: str, recorrencia: list[str]) -> None:
        self._api(refresh, "PATCH", self._caminho(agenda, evento_id), {"recurrence": recorrencia},
                  params={"sendUpdates": "none"})

    def restaurar(self, refresh: str, agenda: str, evento_id: str) -> None:
        """Evento apagado volta para a agenda (o Google guarda os apagados por um tempo)."""
        self._api(refresh, "PATCH", self._caminho(agenda, evento_id), {"status": "confirmed"},
                  params={"sendUpdates": "none"})


def _local(valor: str | None) -> str | None:
    """'2026-10-29T14:00:00-03:00' -> '2026-10-29T14:00' no horário de Brasília."""
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone(ZoneInfo(FUSO)) \
            .strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return valor[:16]


def fim_padrao(inicio: str) -> str:
    return (datetime.fromisoformat(inicio) + DURACAO_PADRAO).strftime("%Y-%m-%dT%H:%M")

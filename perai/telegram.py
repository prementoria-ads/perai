"""Cliente mínimo da API de bots do Telegram, só com a biblioteca padrão."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Transporte: (método HTTP, url, corpo ou None) -> bytes. Trocável nos testes.
Transporte = Callable[[str, str, bytes | None], bytes]


def transporte_http(metodo: str, url: str, corpo: bytes | None) -> bytes:
    pedido = urllib.request.Request(url, data=corpo, method=metodo,
                                    headers={"Content-Type": "application/json"} if corpo else {})
    with urllib.request.urlopen(pedido, timeout=70) as r:
        return r.read()


class ErroTelegram(RuntimeError):
    pass


@dataclass
class MensagemTelegram:
    update_id: int
    chat_id: int
    nome: str
    tipo: str                 # texto, audio, foto, documento, comando
    texto: str | None         # texto ou legenda
    file_id: str | None = None
    nome_arquivo: str | None = None
    botao_id: str | None = None       # callback_query: id do toque, para responder
    mensagem_id: int | None = None    # callback_query: mensagem que tinha o botão


class Telegram:
    def __init__(self, token: str, transporte: Transporte = transporte_http):
        self._base = f"https://api.telegram.org/bot{token}"
        self._base_arquivos = f"https://api.telegram.org/file/bot{token}"
        self._transporte = transporte

    def _chamar(self, metodo: str, **params) -> object:
        corpo = json.dumps(params).encode() if params else None
        try:
            bruto = self._transporte("POST" if corpo else "GET", f"{self._base}/{metodo}", corpo)
        except urllib.error.HTTPError as erro:
            # Nunca mostra a URL: ela contém o token do bot.
            raise ErroTelegram(f"{metodo} respondeu {erro.code}") from None
        resposta = json.loads(bruto)
        if not resposta.get("ok"):
            raise ErroTelegram(f"{metodo}: {resposta.get('description', 'erro')}")
        return resposta["result"]

    def receber(self, depois_de: int | None, espera_s: int = 50) -> list[MensagemTelegram]:
        params = {"timeout": espera_s, "allowed_updates": ["message", "callback_query"]}
        if depois_de is not None:
            params["offset"] = depois_de + 1
        saida = []
        for u in self._chamar("getUpdates", **params):
            q = u.get("callback_query")
            if q:
                chat = ((q.get("message") or {}).get("chat") or {}).get("id") or q["from"]["id"]
                saida.append(MensagemTelegram(u["update_id"], chat, q["from"].get("first_name", ""), "botao",
                                              q.get("data"), botao_id=q["id"],
                                              mensagem_id=(q.get("message") or {}).get("message_id")))
                continue
            m = u.get("message")
            if not m:
                saida.append(MensagemTelegram(u["update_id"], 0, "", "ignorar", None))
                continue
            saida.append(_converter(u["update_id"], m))
        return saida

    def enviar(self, chat_id: int, texto: str, botoes=()) -> None:
        """`botoes`: [(rótulo, dados)], um por linha, presos à última parte do texto."""
        pedacos = _dividir(texto)
        for i, pedaco in enumerate(pedacos):
            params = {"chat_id": chat_id, "text": pedaco, "disable_web_page_preview": True}
            if botoes and i == len(pedacos) - 1:
                params["reply_markup"] = {"inline_keyboard": [[{"text": r, "callback_data": d[:64]}]
                                                              for r, d in botoes]}
            self._chamar("sendMessage", **params)

    def responder_botao(self, botao_id: str, texto: str = "") -> None:
        try:
            self._chamar("answerCallbackQuery", callback_query_id=botao_id, text=texto[:190])
        except ErroTelegram:
            pass

    def tirar_botoes(self, chat_id: int, mensagem_id: int) -> None:
        try:
            self._chamar("editMessageReplyMarkup", chat_id=chat_id, message_id=mensagem_id,
                         reply_markup={"inline_keyboard": []})
        except ErroTelegram:
            pass

    def digitando(self, chat_id: int) -> None:
        """Mostra "digitando..." no Telegram enquanto o Peraí pensa (só aparência)."""
        try:
            self._chamar("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass

    def baixar(self, file_id: str, destino: Path) -> Path:
        info = self._chamar("getFile", file_id=file_id)
        caminho = info["file_path"]
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(self._transporte("GET", f"{self._base_arquivos}/{urllib.parse.quote(caminho)}", None))
        return destino


def _converter(update_id: int, m: dict) -> MensagemTelegram:
    chat_id = m["chat"]["id"]
    nome = (m.get("from") or {}).get("first_name", "")
    legenda = m.get("caption")
    if "text" in m:
        tipo = "comando" if m["text"].startswith("/") else "texto"
        return MensagemTelegram(update_id, chat_id, nome, tipo, m["text"])
    if "voice" in m:
        return MensagemTelegram(update_id, chat_id, nome, "audio", legenda, m["voice"]["file_id"], "audio.ogg")
    if "audio" in m:
        a = m["audio"]
        return MensagemTelegram(update_id, chat_id, nome, "audio", legenda, a["file_id"],
                                a.get("file_name") or "audio.mp3")
    if "photo" in m:
        maior = max(m["photo"], key=lambda f: f.get("file_size", 0))
        return MensagemTelegram(update_id, chat_id, nome, "foto", legenda, maior["file_id"], "imagem.jpg")
    if "document" in m:
        d = m["document"]
        return MensagemTelegram(update_id, chat_id, nome, "documento", legenda, d["file_id"],
                                d.get("file_name") or "arquivo")
    return MensagemTelegram(update_id, chat_id, nome, "ignorar", legenda)


def _dividir(texto: str, limite: int = 4000) -> list[str]:
    """O Telegram aceita até 4096 caracteres por mensagem."""
    partes, atual = [], ""
    for linha in texto.split("\n"):
        if len(atual) + len(linha) + 1 > limite and atual:
            partes.append(atual)
            atual = ""
        atual = f"{atual}\n{linha}" if atual else linha
    return partes + ([atual] if atual else []) or [""]

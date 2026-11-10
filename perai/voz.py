"""Resposta falada (/voz): quem manda áudio recebe a resposta também em áudio.

O texto da resposta vira fala no Gemini TTS (atrás da interface própria, D-002/D-038) e o código
converte para OGG/Opus, o formato de mensagem de voz do Telegram. A resposta em texto continua
indo junto, com as fontes e os botões: a voz é um extra, nunca o lugar da fonte (D-040).
"""

from __future__ import annotations

import os
import re
import subprocess

MAXIMO_FALADO = 1200   # caracteres: acima disso, fala o começo e avisa que o resto está no texto


def modelo_voz() -> str:
    return os.environ.get("PERAI_MODELO_VOZ", "gemini-2.5-flash-preview-tts").strip()


def nome_voz() -> str:
    return os.environ.get("PERAI_VOZ", "Kore").strip() or "Kore"


def falavel(texto: str) -> str:
    """Tira o que não se fala: códigos, links, emojis, blocos de copiar, linhas de fonte e de botões."""
    t = re.sub(r"```.*?```", " ", texto or "", flags=re.S)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\[[^\]]{1,40}\]", "", t)
    linhas = [l for l in t.splitlines() if not re.match(r"\s*(📎|Fonte|Fontes)", l)]
    t = "\n".join(linhas)
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", t)
    t = re.sub(r"[•▪◦]\s*", "", t)
    t = "\n".join(" ".join(l.split()) for l in t.splitlines())
    t = re.sub(r"\n{2,}", "\n", t).strip()
    if len(t) > MAXIMO_FALADO:
        corte = t.rfind(".", 0, MAXIMO_FALADO)
        t = t[: corte + 1 if corte > 200 else MAXIMO_FALADO] + " O restante está no texto."
    return t


def pedido_fala(texto: str) -> str:
    return f"Leia em português do Brasil, com voz calma e natural, como quem conversa:\n{texto}"


def pcm_para_ogg(pcm: bytes) -> bytes:
    """PCM 16 bits, 24 kHz, mono -> OGG/Opus (mensagem de voz do Telegram), pelo ffmpeg."""
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0",
                        "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
                       input=pcm, capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        raise OSError(f"ffmpeg falhou: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout

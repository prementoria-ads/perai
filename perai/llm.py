"""Camada única de acesso aos modelos (D-002, D-038).

O resto do código só conhece `Cliente.conversar` e `Cliente.transcrever`; trocar
de fornecedor é trocar o apelido do modelo, sem mexer em dados ou prompts.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from perai.config import VARIAVEIS_CHAVE, Modelo, Transcritor, custo_usd


log = logging.getLogger("perai.llm")


class ErroModelo(RuntimeError):
    pass


@dataclass
class Resposta:
    texto: str
    tokens_entrada: int
    tokens_saida: int
    latencia_ms: int
    custo_usd: float


@dataclass
class Transcricao:
    texto: str
    segundos_audio: float
    latencia_ms: int
    custo_usd: float


# Simulador: recebe (tarefa, sistema, usuario) e devolve o texto da resposta.
Simulador = Callable[[str, str, str], str]


def simulador_padrao(tarefa: str, sistema: str, usuario: str) -> str:
    if tarefa == "extracao":
        return json.dumps({"entidades": [], "registros": [], "pendencias": []})
    if tarefa == "consulta":
        return json.dumps(
            {"resposta": "Não há registro.", "registros_citados": [], "sem_informacao": True}
        )
    if tarefa == "leitura_imagem":
        return "[texto simulado da imagem]"
    return "{}"


_json_mod = json  # `json` também é nome de parâmetro em Cliente._post


def _multipart(limite: str, campos: dict, arquivos: dict) -> bytes:
    partes: list[bytes] = []
    for nome, valor in campos.items():
        partes.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode()
        )
    for nome, (nome_arquivo, conteudo) in arquivos.items():
        tipo = mimetypes.guess_type(nome_arquivo)[0] or "application/octet-stream"
        partes.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"; '
            f'filename="{nome_arquivo}"\r\nContent-Type: {tipo}\r\n\r\n'.encode()
            + conteudo
            + b"\r\n"
        )
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes)


def _chave(fornecedor: str) -> str:
    variavel = VARIAVEIS_CHAVE[fornecedor]
    valor = os.environ.get(variavel)
    if not valor:
        raise ErroModelo(f"Defina {variavel} no .env para usar {fornecedor}.")
    return valor


def _imagem_base64(caminho: Path) -> tuple[str, str]:
    tipo = mimetypes.guess_type(caminho.name)[0] or "image/jpeg"
    return tipo, base64.b64encode(caminho.read_bytes()).decode()


def duracao_audio(caminho: Path) -> float:
    try:
        import mutagen

        arquivo = mutagen.File(caminho)
        if arquivo is not None and arquivo.info is not None:
            return float(arquivo.info.length)
    except Exception:
        pass
    return 0.0


def _host(url: str) -> str:
    """Só o endereço e o caminho, para log (chaves vão em cabeçalho, nunca na URL)."""
    return url.split("?", 1)[0].removeprefix("https://")


class Cliente:
    def __init__(self, simulador: Simulador | None = None, tempo_limite: float = 60.0):
        self.simulador = simulador or simulador_padrao
        self.tempo_limite = tempo_limite

    # -- linguagem --------------------------------------------------------------

    def conversar(
        self,
        modelo: Modelo,
        tarefa: str,
        sistema: str,
        usuario: str,
        imagem: Path | None = None,
        resposta_json: bool = True,
    ) -> Resposta:
        inicio = time.monotonic()
        if modelo.fornecedor == "simulado":
            texto = self.simulador(tarefa, sistema, usuario)
            entrada, saida = len(sistema + usuario) // 4, len(texto) // 4
        elif modelo.fornecedor == "anthropic":
            texto, entrada, saida = self._anthropic(modelo, sistema, usuario, imagem)
        elif modelo.fornecedor in ("openai", "groq"):
            texto, entrada, saida = self._openai(modelo, sistema, usuario, imagem, resposta_json)
        elif modelo.fornecedor == "google":
            texto, entrada, saida = self._google(modelo, sistema, usuario, imagem, resposta_json)
        else:
            raise ErroModelo(f"Fornecedor desconhecido: {modelo.fornecedor}")
        latencia = int((time.monotonic() - inicio) * 1000)
        return Resposta(texto, entrada, saida, latencia, custo_usd(modelo, entrada, saida))

    def _post(self, url: str, headers: dict | None = None, json: dict | None = None,
              data: dict | None = None, files: dict | None = None) -> dict:
        """POST só com a biblioteca padrão. `json` ou multipart (`data` + `files`)."""
        cabecalhos = dict(headers or {})
        if files is not None:
            limite = uuid.uuid4().hex
            corpo = _multipart(limite, data or {}, files)
            cabecalhos["Content-Type"] = f"multipart/form-data; boundary={limite}"
        else:
            corpo = _json_mod.dumps(json or {}).encode()
            cabecalhos["Content-Type"] = "application/json"
        for tentativa in range(4):
            pedido = urllib.request.Request(url, data=corpo, headers=cabecalhos, method="POST")
            inicio = time.monotonic()
            try:
                with urllib.request.urlopen(pedido, timeout=self.tempo_limite) as r:
                    resposta = _json_mod.loads(r.read().decode("utf-8"))
                log.info("%s: ok em %.1fs", _host(url), time.monotonic() - inicio)
                return resposta
            except urllib.error.HTTPError as erro:
                log.warning("%s: HTTP %s em %.1fs (tentativa %d)", _host(url), erro.code,
                            time.monotonic() - inicio, tentativa + 1)
                # Sobrecarga (503/529) raramente passa em segundos: tenta só 2 vezes e
                # deixa a reserva assumir; os demais erros temporários tentam até 4.
                limite = 1 if erro.code in (503, 529) else 3
                if erro.code in (429, 500, 502, 503, 529) and tentativa < limite:
                    time.sleep(2 ** tentativa * 2)
                    continue
                detalhe = erro.read().decode("utf-8", errors="replace")[:500]
                raise ErroModelo(f"{url} respondeu {erro.code}: {detalhe}") from None
            except (urllib.error.URLError, TimeoutError) as erro:
                log.warning("%s: %s em %.1fs (tentativa %d)", _host(url),
                            getattr(erro, "reason", erro), time.monotonic() - inicio, tentativa + 1)
                if tentativa < 3:
                    time.sleep(2 ** tentativa * 2)
                    continue
                raise ErroModelo(f"{url}: {getattr(erro, 'reason', erro)}") from None
        raise ErroModelo(f"{url}: sem resposta depois de 4 tentativas")

    def _anthropic(self, modelo, sistema, usuario, imagem):
        conteudo: list[dict] = []
        if imagem is not None:
            tipo, dados = _imagem_base64(imagem)
            conteudo.append(
                {"type": "image", "source": {"type": "base64", "media_type": tipo, "data": dados}}
            )
        conteudo.append({"type": "text", "text": usuario})
        corpo = self._post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": _chave("anthropic"),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": modelo.id_api,
                "max_tokens": 4096,
                "system": sistema,
                "messages": [{"role": "user", "content": conteudo}],
            },
        )
        texto = "".join(b.get("text", "") for b in corpo.get("content", []) if b.get("type") == "text")
        uso = corpo.get("usage", {})
        return texto, uso.get("input_tokens", 0), uso.get("output_tokens", 0)

    def _openai(self, modelo, sistema, usuario, imagem, resposta_json):
        base = (
            "https://api.groq.com/openai/v1"
            if modelo.fornecedor == "groq"
            else "https://api.openai.com/v1"
        )
        conteudo: list[dict] | str = usuario
        if imagem is not None:
            tipo, dados = _imagem_base64(imagem)
            conteudo = [
                {"type": "text", "text": usuario},
                {"type": "image_url", "image_url": {"url": f"data:{tipo};base64,{dados}"}},
            ]
        corpo_pedido: dict = {
            "model": modelo.id_api,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": conteudo},
            ],
        }
        if resposta_json:
            corpo_pedido["response_format"] = {"type": "json_object"}
        corpo = self._post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {_chave(modelo.fornecedor)}"},
            json=corpo_pedido,
        )
        texto = corpo["choices"][0]["message"].get("content") or ""
        uso = corpo.get("usage", {})
        return texto, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0)

    def _google(self, modelo, sistema, usuario, imagem, resposta_json):
        partes: list[dict] = [{"text": usuario}]
        if imagem is not None:
            tipo, dados = _imagem_base64(imagem)
            partes.append({"inlineData": {"mimeType": tipo, "data": dados}})
        corpo_pedido: dict = {
            "systemInstruction": {"parts": [{"text": sistema}]},
            "contents": [{"role": "user", "parts": partes}],
        }
        if resposta_json:
            corpo_pedido["generationConfig"] = {"responseMimeType": "application/json"}
        corpo = self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{modelo.id_api}:generateContent",
            headers={"x-goog-api-key": _chave("google")},
            json=corpo_pedido,
        )
        candidatos = corpo.get("candidates") or [{}]
        partes_resposta = candidatos[0].get("content", {}).get("parts", [])
        texto = "".join(p.get("text", "") for p in partes_resposta)
        uso = corpo.get("usageMetadata", {})
        return texto, uso.get("promptTokenCount", 0), uso.get("candidatesTokenCount", 0)

    # -- áudio -------------------------------------------------------------------

    def transcrever(self, transcritor: Transcritor, audio: Path) -> Transcricao:
        segundos = duracao_audio(audio)
        inicio = time.monotonic()
        if transcritor.fornecedor == "simulado":
            texto = f"[transcrição simulada de {audio.name}]"
        else:
            base = (
                "https://api.groq.com/openai/v1"
                if transcritor.fornecedor == "groq"
                else "https://api.openai.com/v1"
            )
            corpo = self._post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {_chave(transcritor.fornecedor)}"},
                data={"model": transcritor.id_api, "language": "pt", "response_format": "json"},
                files={"file": (audio.name, audio.read_bytes())},
            )
            texto = corpo.get("text", "")
        latencia = int((time.monotonic() - inicio) * 1000)
        custo = segundos / 60 * transcritor.usd_minuto
        return Transcricao(texto, segundos, latencia, custo)

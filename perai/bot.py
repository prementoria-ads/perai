"""Bot do Telegram do Peraí (etapa 3 do MVP).

Configuração por variáveis de ambiente (veja .env.example):
    TELEGRAM_BOT_TOKEN          token do @BotFather
    PERAI_TELEGRAM_PERMITIDOS   ids de chat autorizados, separados por vírgula (D-009)
    DATABASE_URL                postgresql://perai:senha@banco:5432/perai
    PERAI_MODELO                modelo de linguagem (provisório até a D-038)
    PERAI_TRANSCRITOR, PERAI_LEITOR_IMAGEM, PERAI_ARQUIVOS

Rodar: python -m perai.bot
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path

from perai.armazenamento import RepositorioMemoria, RepositorioPostgres
from perai.config import MODELOS, TRANSCRITORES
from perai.leitura import EXTENSOES_AUDIO, EXTENSOES_IMAGEM
from perai.llm import Cliente
from perai.servico import Entrada, Perai
from perai.telegram import ErroTelegram, MensagemTelegram, Telegram

log = logging.getLogger("perai.bot")

BOAS_VINDAS = (
    "Oi, {nome}! Eu sou o Peraí. Pode me contar, encaminhar ou mandar áudio do que quiser "
    "lembrar depois: conversas, combinados, ideias, prints. Quando precisar, é só perguntar "
    "(“o que combinei com o Wagner?”) ou pedir “me prepara pra reunião de quinta”. "
    "Lembrete eu só crio quando você pedir."
)
NAO_AUTORIZADO = "Este é um assistente privado. Seu acesso ainda não foi liberado."


def tipo_do_documento(nome: str) -> str | None:
    sufixo = Path(nome).suffix.lower()
    if sufixo == ".zip":
        return "conversa_exportada"
    if sufixo in EXTENSOES_IMAGEM:
        return "print"
    if sufixo in EXTENSOES_AUDIO:
        return "audio"
    return None


class Bot:
    def __init__(self, telegram: Telegram, perai: Perai, permitidos: set[int], pasta_arquivos: Path):
        self.telegram = telegram
        self.perai = perai
        self.permitidos = permitidos
        self.pasta = pasta_arquivos

    def tratar(self, m: MensagemTelegram) -> str | None:
        if m.tipo == "ignorar":
            return None
        if m.chat_id not in self.permitidos:
            log.warning("chat não autorizado: %s", m.chat_id)
            return NAO_AUTORIZADO
        usuario = self.perai.repo.usuario_por_telegram(m.chat_id)
        if usuario is None:
            usuario = self.perai.repo.criar_usuario(m.nome or "Usuário", m.chat_id)
        if m.tipo == "comando":
            return BOAS_VINDAS.format(nome=m.nome or "tudo bem") if m.texto.startswith("/start") else None

        tipo, arquivo = m.tipo, None
        if m.tipo == "documento":
            tipo = tipo_do_documento(m.nome_arquivo or "")
            if tipo is None:
                return "Ainda não sei ler esse tipo de arquivo. Mande texto, áudio, print ou conversa exportada."
        if m.file_id:
            # Arquivo guardado por usuário, com nome único (a mensagem original é imutável).
            sufixo = Path(m.nome_arquivo or "").suffix or ".bin"
            destino = self.pasta / usuario / f"{uuid.uuid4().hex}{sufixo}"
            arquivo = self.telegram.baixar(m.file_id, destino)
        return self.perai.receber(usuario, Entrada("telegram", tipo, m.texto, arquivo))

    def enviar_lembretes(self) -> None:
        for l in self.perai.lembretes_para_enviar():
            if l.telegram_chat_id is None:
                continue
            self.telegram.enviar(l.telegram_chat_id, f"Lembrete: {l.descricao}")
            self.perai.lembrete_enviado(l.pendencia_id)

    def rodar(self) -> None:
        ultimo = None
        log.info("Peraí no ar.")
        while True:
            try:
                for m in self.telegram.receber(ultimo, espera_s=30):
                    ultimo = m.update_id
                    try:
                        resposta = self.tratar(m)
                    except Exception:
                        log.exception("erro ao tratar mensagem %s", m.update_id)
                        resposta = "Tive um problema com essa mensagem. Tente de novo em instantes."
                    if resposta:
                        self.telegram.enviar(m.chat_id, resposta)
                self.enviar_lembretes()
            except (ErroTelegram, OSError) as erro:
                log.warning("Telegram indisponível: %s", erro)
                time.sleep(5)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    permitidos = {int(x) for x in os.environ.get("PERAI_TELEGRAM_PERMITIDOS", "").split(",") if x.strip()}
    if not token or not permitidos:
        print("Defina TELEGRAM_BOT_TOKEN e PERAI_TELEGRAM_PERMITIDOS no .env", file=sys.stderr)
        return 1
    url = os.environ.get("DATABASE_URL")
    repo = RepositorioPostgres(url) if url else RepositorioMemoria()
    pasta = Path(os.environ.get("PERAI_ARQUIVOS", "arquivos"))
    perai = Perai(
        repo, Cliente(),
        MODELOS[os.environ.get("PERAI_MODELO", "gemini-flash")],
        TRANSCRITORES[os.environ.get("PERAI_TRANSCRITOR", "gpt-mini-transcribe")],
        MODELOS[os.environ.get("PERAI_LEITOR_IMAGEM", "gemini-flash")],
        pasta / "temporario",
    )
    Bot(Telegram(token), perai, permitidos, pasta).rodar()
    return 0


if __name__ == "__main__":
    sys.exit(main())

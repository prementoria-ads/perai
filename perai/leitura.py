"""Transforma o que chega (áudio, print, foto, conversa exportada) em texto.

Usado pelo bot e pelo teste comparativo, para os dois lerem do mesmo jeito.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from perai.config import Modelo, Transcritor
from perai.llm import Cliente

SISTEMA_LEITURA = """Transcreva fielmente o conteúdo desta imagem, em português.
Se for print de conversa: uma linha por fala, no formato "[data hora] Autor: texto",
usando "Eu" para as falas do dono do celular (balões do lado direito). Inclua mensagens
encaminhadas, nomes de arquivos e transcrições de áudio que aparecem na tela; marque
trechos cortados com "[...]" e áudios sem transcrição com "[áudio sem transcrição]".
Se for cupom fiscal, nota, recibo ou comprovante de pagamento: transcreva estabelecimento,
CNPJ, data, itens com valores, total e forma de pagamento, exatamente como impressos; valor
ilegível fica "[ilegível]". Não transcreva o CPF do consumidor.
Se for foto: descreva o que se vê, sem identificar pessoas pela aparência.
Não interprete nem resuma: só transcreva."""

SISTEMA_LEITURA_PDF = """Transcreva o conteúdo deste PDF em português, sem resumir. Se for nota fiscal
(DANFE), cupom, recibo ou boleto: estabelecimento, CNPJ, data de emissão, itens com valores,
total e forma de pagamento, exatamente como impressos. Não transcreva o CPF do consumidor."""

EXTENSOES_AUDIO = {".ogg", ".oga", ".opus", ".m4a", ".mp3", ".wav", ".aac"}
EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".webp"}


def ler_imagem(cliente: Cliente, leitor: Modelo, imagem: Path, legenda: str | None
               ) -> tuple[str, float, int]:
    cabecalho = f"Legenda do usuário: {legenda or imagem.stem}"
    r = cliente.conversar(leitor, "leitura_imagem", SISTEMA_LEITURA, cabecalho,
                          imagem=imagem, resposta_json=False)
    return f"{cabecalho}\n{r.texto}", r.custo_usd, r.latencia_ms


def ler_pdf(cliente: Cliente, leitor: Modelo, pdf: Path, legenda: str | None) -> tuple[str, float, int]:
    cabecalho = f"Legenda do usuário: {legenda or pdf.stem}"
    r = cliente.conversar(leitor, "leitura_imagem", SISTEMA_LEITURA_PDF, cabecalho,
                          imagem=pdf, resposta_json=False)
    return f"{cabecalho}\n{r.texto}", r.custo_usd, r.latencia_ms


LIMITE_TXT = 250_000  # caracteres: umas 3 horas de reunião transcrita


def ler_conversa_exportada(cliente: Cliente, transcritor: Transcritor, zip_: Path,
                           destino: Path) -> tuple[str, float, int]:
    """Lê o .zip do "Exportar conversa" do WhatsApp e transcreve os áudios dele. Exportada
    "sem mídia", a conversa chega como .txt e é lida direto."""
    if zip_.suffix.lower() == ".txt":
        texto = zip_.read_text(encoding="utf-8", errors="replace")
        if len(texto) > LIMITE_TXT:  # conversa muito longa: fica a parte mais recente
            texto = "[...início da conversa cortado...]\n" + texto[-LIMITE_TXT:]
        return texto, 0.0, 0
    destino.mkdir(parents=True, exist_ok=True)
    custo, latencia, partes = 0.0, 0, []
    with zipfile.ZipFile(zip_) as z:
        for nome in z.namelist():
            # Só arquivos planos, sem pastas nem caminhos: proteção contra zip malicioso.
            if Path(nome).name != nome or nome.startswith("."):
                continue
            z.extract(nome, destino)
    for arquivo in sorted(destino.iterdir()):
        if arquivo.suffix.lower() == ".txt":
            partes.append(arquivo.read_text(encoding="utf-8", errors="replace"))
    for arquivo in sorted(destino.iterdir()):
        if arquivo.suffix.lower() in EXTENSOES_AUDIO:
            t = cliente.transcrever(transcritor, arquivo)
            custo += t.custo_usd
            latencia += t.latencia_ms
            partes.append(f"[áudio {arquivo.name}]: {t.texto}")
    return "\n".join(partes), custo, latencia

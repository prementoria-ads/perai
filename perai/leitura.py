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


EXTENSOES_OFFICE = {".docx", ".xlsx", ".pptx"}
LIMITE_XML = 30_000_000   # bytes descompactados por parte do arquivo (proteção contra zip-bomba)


EXTENSOES_TEXTO = {".md", ".markdown"}


def ler_texto(arquivo: Path) -> str:
    """Markdown e outros textos simples, como estão (limite de tamanho)."""
    texto = arquivo.read_text(encoding="utf-8", errors="replace")
    return texto if len(texto) <= LIMITE_TXT else texto[:LIMITE_TXT] + "\n[...resto do arquivo cortado...]"


def ler_office(arquivo: Path) -> str:
    """Texto de .docx, .xlsx ou .pptx, lido pelo código (sem IA): parágrafos, células e slides."""
    import re as _re
    import xml.etree.ElementTree as ET

    def xml(z, nome):
        info = z.getinfo(nome)
        if info.file_size > LIMITE_XML:
            raise ValueError(f"{nome} grande demais")
        return ET.fromstring(z.read(nome))

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    def numero(nome):
        achado = _re.search(r"(\d+)\.xml$", nome)
        return int(achado.group(1)) if achado else 0

    sufixo = arquivo.suffix.lower()
    linhas: list[str] = []
    with zipfile.ZipFile(arquivo) as z:
        nomes = z.namelist()
        if sufixo == ".docx":
            for p in xml(z, "word/document.xml").iter():
                if local(p.tag) != "p":
                    continue
                texto = "".join(t.text or "" if local(t.tag) == "t" else "\t" if local(t.tag) == "tab" else ""
                                for t in p.iter())
                if texto.strip():
                    linhas.append(texto.strip())
        elif sufixo == ".pptx":
            for nome in sorted((n for n in nomes if _re.match(r"ppt/slides/slide\d+\.xml$", n)), key=numero):
                textos = [t.text for t in xml(z, nome).iter() if local(t.tag) == "t" and t.text]
                if textos:
                    linhas.append(f"[slide {numero(nome)}] " + " · ".join(textos))
        elif sufixo == ".xlsx":
            comuns = []
            if "xl/sharedStrings.xml" in nomes:
                for si in xml(z, "xl/sharedStrings.xml"):
                    comuns.append("".join(t.text or "" for t in si.iter() if local(t.tag) == "t"))
            for nome in sorted((n for n in nomes if _re.match(r"xl/worksheets/sheet\d+\.xml$", n)), key=numero):
                linhas.append(f"[planilha {numero(nome)}]")
                for row in (r for r in xml(z, nome).iter() if local(r.tag) == "row"):
                    celulas = []
                    for c in (c for c in row if local(c.tag) == "c"):
                        v = next((x.text for x in c.iter() if local(x.tag) in ("v", "t") and x.text), None)
                        if v is not None and c.get("t") == "s" and v.isdigit() and int(v) < len(comuns):
                            v = comuns[int(v)]
                        if v not in (None, ""):
                            celulas.append(v)
                    if celulas:
                        linhas.append(" | ".join(celulas))
                    if len(linhas) > 5000:
                        break
    texto = "\n".join(linhas)
    return texto if len(texto) <= LIMITE_TXT else texto[:LIMITE_TXT] + "\n[...resto do arquivo cortado...]"


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

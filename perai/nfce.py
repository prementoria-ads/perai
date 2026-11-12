"""NFC-e sem IA (D-111): o texto da consulta da Sefaz e o QR code da nota.

1. PDF que é a consulta da Sefaz impressa (layout nacional da NFC-e, o mesmo do app Nota Paraná):
   o código lê o texto e tira emitente, CNPJ, data, itens, descontos, total e chave.
2. Foto ou PDF com o QR code: o código lê o QR, abre a consulta pública da Sefaz e lê a página do
   mesmo jeito.
Se nada disso der certo, a nota segue para a leitura pela IA, como antes.

O bloco do consumidor (nome, CPF, endereço) e as "informações de interesse do contribuinte" nunca
são lidos: o texto é cortado antes deles.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

from perai.despesas import NotaFiscal

log = logging.getLogger("perai.nfce")

_UNIT = re.compile(r"Vl\.\s*Unit\.:\s*([\d.]*\d(?:,\d+)?)")
_DINHEIRO = re.compile(r"^\s*(\d{1,3}(?:\.\d{3})*,\d{2})\s*$", re.M)
_PAGAMENTOS = re.compile(r"Cart[ãa]o de Cr[ée]dito|Cart[ãa]o de D[ée]bito|Dinheiro|\bPIX\b|\bPix\b|"
                         r"Vale Alimenta[çc][ãa]o|Vale Refei[çc][ãa]o|Cr[ée]dito Loja|Boleto|Outros")
# Tudo daqui para baixo é do consumidor ou do contribuinte: não entra.
_CORTE = re.compile(r"\n\s*(Consumidor|CONSUMIDOR|Informa[çc][õo]es de interesse do contribuinte)\b")


def e_consulta(texto: str) -> bool:
    """O texto tem a cara da consulta da NFC-e: itens com Qtde./UN/Vl. Unit. e a chave de acesso."""
    t = texto or ""
    return bool(_UNIT.search(t)) and "Qtde.:" in t and bool(re.search(r"Chave de acesso", t, re.I))


def _dec(v: str | None) -> Decimal | None:
    if not v:
        return None
    try:
        return Decimal(v.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _centavos(v: Decimal | None) -> int | None:
    return None if v is None else int((v * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _num_br(d: Decimal) -> str:
    """Decimal → "0,212" (como na nota), para o histórico de preços."""
    return format(d.normalize(), "f").replace(".", ",")


def ler_texto(texto: str) -> NotaFiscal | None:
    """NotaFiscal a partir do texto da consulta (PDF com pdftotext -layout, ou a página em HTML já
    convertida). Só devolve se o número de itens bater com o "Qtd. total de itens" da própria nota."""
    if not e_consulta(texto):
        return None
    t = texto.replace("\xa0", " ").replace("\r", "")
    corte = _CORTE.search(t)
    if corte:
        t = t[:corte.start()]
    fim_itens = re.search(r"Qtd\.\s*total de itens", t)
    regiao = t[:fim_itens.start()] if fim_itens else t
    itens, detalhes, anterior = [], [], 0
    for m in _UNIT.finditer(regiao):
        bloco = regiao[anterior:m.start()]
        anterior = m.end()
        i = bloco.find("(Código:")
        if i < 0:
            return None
        paragrafos = [p for p in re.split(r"\n\s*\n", bloco[:i].strip()) if p.strip()]
        if not paragrafos:
            return None
        desc = " ".join(re.sub(r"Vl\.\s*Total", " ", paragrafos[-1]).split())
        qtd = _dec((re.search(r"Qtde\.:\s*([\d.,]+)", bloco) or [None, None])[1])
        un = (re.search(r"UN:\s*([A-Za-z]+)", bloco) or [None, None])[1]
        unit = _dec(m.group(1))
        if not desc or qtd is None or unit is None:
            return None
        itens.append((desc[:120], _centavos(qtd * unit)))
        detalhes.append({"descricao": desc[:120], "quantidade": _num_br(qtd), "unidade": un,
                         "valor_unitario": _num_br(unit)})
    if not itens:
        return None
    declarados = re.search(r"Qtd\.\s*total de itens:\s*(\d+)", t)
    if declarados and int(declarados.group(1)) != len(itens):
        return None   # leu menos (ou mais) do que a nota diz: melhor a leitura pela IA do que meia nota
    # O valor de cada linha impresso na nota, quando dá para achar um por item, vale mais que a conta.
    impressos = _DINHEIRO.findall(regiao)
    if len(impressos) == len(itens):
        itens = [(d, _centavos(_dec(v))) for (d, _), v in zip(itens, impressos)]
    total = _dec((re.search(r"Valor a pagar R\$:\s*([\d.]+,\d{2})", t) or [None, None])[1])
    if total is None:
        total = _dec((re.search(r"Valor total R\$:\s*([\d.]+,\d{2})", t) or [None, None])[1])
    linhas = [x.strip() for x in t.split("\n") if x.strip()]
    emitente = linhas[0][:120] if linhas else "Emitente não identificado"
    cnpj = re.search(r"CNPJ:\s*([\d./-]{14,18})", t)
    data = re.search(r"Emiss[ãa]o:\s*(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}:\d{2}))?", t)
    # Endereço do emitente (antes do primeiro item): "..., Umuarama, PR".
    cabecalho = regiao[:regiao.find("(Código:")] if "(Código:" in regiao else ""
    cidade = None
    for linha in cabecalho.split("\n")[:8]:
        m_cid = re.search(r",\s*([A-Za-zÀ-ÿ' .-]{2,40}?)\s*,\s*([A-Z]{2})\s*$", linha.strip())
        if m_cid:
            cidade = m_cid.group(1).strip()
            break
    chave = re.search(r"Chave de acesso:?\s*((?:\d{4}\s*){11})", t)
    pagamento = None
    pag = re.search(r"Forma de pagamento:(.{0,300})", t, re.S)
    if pag:
        achado = _PAGAMENTOS.search(pag.group(1))
        pagamento = achado.group(0) if achado else None
    return NotaFiscal(
        emitente=emitente, cnpj=re.sub(r"\D", "", cnpj.group(1)) if cnpj else None,
        data=f"{data.group(3)}-{data.group(2)}-{data.group(1)}" if data else None,
        total_centavos=_centavos(total), pagamento=pagamento, itens=itens,
        chave=re.sub(r"\D", "", chave.group(1)) if chave else None, detalhes=detalhes,
        hora=data.group(4) if data else None, cidade=cidade)


def texto_do_pdf(arquivo: Path) -> str:
    """Texto com o layout da página (pdftotext -layout); vazio se não der."""
    if not shutil.which("pdftotext"):
        return ""
    try:
        r = subprocess.run(["pdftotext", "-layout", "-l", "8", str(arquivo), "-"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


# -- QR code -----------------------------------------------------------------------------------

def _imagens_do_pdf(arquivo: Path, pasta: Path) -> list[Path]:
    if not shutil.which("pdftoppm"):
        return []
    try:
        subprocess.run(["pdftoppm", "-r", "200", "-png", "-f", "1", "-l", "2", str(arquivo), str(pasta / "p")],
                       capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(pasta.glob("p*.png"))


def _tentativas(img):
    """A mesma imagem de jeitos diferentes: foto do Telegram vem comprimida (até 1280 px) e o QR do
    cupom fica pequeno. Ampliar a metade de baixo (onde o QR costuma estar) ajuda muito."""
    from PIL import Image, ImageOps
    yield img
    yield ImageOps.autocontrast(img)
    w, h = img.size
    if max(w, h) < 2500:
        yield img.resize((w * 2, h * 2), Image.LANCZOS)
    for topo in (h // 2, h // 3 * 2):
        parte = img.crop((0, topo, w, h))
        if max(parte.size) < 2500:
            parte = parte.resize((parte.width * 2, parte.height * 2), Image.LANCZOS)
        yield parte


def ler_qr(arquivo: Path) -> str | None:
    """Texto do QR code da NFC-e na foto ou nas 2 primeiras páginas do PDF; None se não houver."""
    try:
        import zxingcpp
        from PIL import Image, ImageOps
    except ImportError:
        log.warning("QR code: zxing-cpp ou Pillow não instalados")
        return None
    outros = []
    with tempfile.TemporaryDirectory(prefix="perai-qr-") as tmp:
        imagens = (_imagens_do_pdf(arquivo, Path(tmp)) if arquivo.suffix.lower() == ".pdf" else [arquivo])
        for caminho in imagens:
            try:
                with Image.open(caminho) as original:
                    img = ImageOps.exif_transpose(original).convert("L")
                    if max(img.size) > 3000:   # foto grande de celular: reduz, o QR continua legível
                        img.thumbnail((3000, 3000))
                    tamanho = img.size
                    for versao in _tentativas(img):
                        for binarizador in (zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram):
                            for codigo in zxingcpp.read_barcodes(versao, formats=zxingcpp.BarcodeFormat.QRCode,
                                                                 binarizer=binarizador):
                                if url_da_nota(codigo.text):
                                    log.info("QR code da NFC-e lido (imagem %sx%s)", *tamanho)
                                    return codigo.text
                                outros.append(codigo.text[:40])
            except (OSError, ValueError) as erro:
                log.warning("QR code: não consegui abrir a imagem: %s", erro)
                continue
    log.info("QR code da NFC-e não encontrado (%s imagem(ns)%s)", len(imagens),
             f"; outros QR: {outros[:2]}" if outros else "")
    return None


def url_da_nota(texto: str | None) -> str | None:
    """O QR da NFC-e é um link para a consulta pública da Sefaz (site .gov.br) com a chave de 44
    dígitos no parâmetro p. Qualquer outro QR (Pix, cardápio, site) é ignorado."""
    if not texto:
        return None
    try:
        partes = urllib.parse.urlsplit(texto.strip())
    except ValueError:
        return None
    if partes.scheme not in ("http", "https") or not (partes.hostname or "").endswith(".gov.br"):
        return None
    return texto.strip() if chave_do_qr(texto.strip()) else None


def chave_do_qr(url: str) -> str | None:
    """Chave de 44 dígitos do QR: versão 2 ("p=chave|2|1|..."), ou versão 1 ("chNFe=chave&...")."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    m = re.match(r"\d{44}", (q.get("p") or q.get("chNFe") or [""])[0])
    return m.group(0) if m else None


class _TextoNota(HTMLParser):
    """HTML da consulta → texto no mesmo formato do PDF: cada linha da tabela de itens vira um
    parágrafo (linha em branco antes), e blocos viram linhas."""
    IGNORAR = {"script", "style", "head", "noscript"}
    PARAGRAFOS = {"tr", "table", "h1", "h2", "h3", "h4"}
    LINHAS = {"br", "div", "p", "li", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes, self._fundo, self.emitente, self._no_topo = [], 0, None, False

    def handle_starttag(self, tag, attrs):
        if "txtTopo" in (dict(attrs).get("class") or "") and self.emitente is None:
            self._no_topo = True   # o nome do emitente, no topo da consulta
        if tag in self.IGNORAR:
            self._fundo += 1
        elif tag in self.PARAGRAFOS:
            self.partes.append("\n\n")
        elif tag in self.LINHAS:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORAR and self._fundo:
            self._fundo -= 1

    def handle_data(self, data):
        if self._no_topo and data.strip():
            self.emitente, self._no_topo = " ".join(data.split()), False
        if not self._fundo:
            self.partes.append(data)


def html_para_texto(conteudo: bytes, charset: str | None = None) -> str:
    html = conteudo.decode(charset or "utf-8", errors="replace")
    p = _TextoNota()
    p.feed(html)
    linhas = [" ".join(x.split()) for x in "".join(p.partes).split("\n")]
    texto = re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()
    if p.emitente:   # ler_texto usa a primeira linha como emitente
        inicio = texto.find(p.emitente)
        texto = texto[inicio:] if inicio >= 0 else f"{p.emitente}\n{texto}"
    return texto

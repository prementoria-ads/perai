"""NFC-e sem IA (D-111): texto da consulta da Sefaz e QR code. Todos os dados são fictícios."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import nfce
from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai

CHAVE = "4126 1112 3456 7800 0199 6500 1000 0099 9910 0009 9999"

# O mesmo layout do PDF impresso da consulta (pdftotext -layout), com descrição em duas linhas,
# item por kg, desconto e o bloco do consumidor (que nunca pode ser lido).
CONSULTA = f"""                   FARMACIA EXEMPLO LTDA
                                CNPJ: 12.345.678/0001-99
                    RUA FICTICIA, 100, CENTRO, Cidade Exemplo, PR




REMEDIO FICTICIO 20 MG C/ 30 CP (Código: 1111)                          Vl. Total

                                                                           100,00
Qtde.:1    UN: UND Vl. Unit.: 100,00

DESODORANTE FICTICIO STICK LAVANDA Vl. Total
C/ 45G (Código: 2222)
                                                                             20,00

Qtde.:2    UN: UND Vl. Unit.: 10,00

BANANA FICTICIA KG (Código: 3333)                                        Vl. Total

                                                                              7,50
Qtde.:1,5             UN: KG Vl. Unit.: 5

                                                      Qtd. total de itens:                                3
                                                          Valor total R$:                             127,50
                                                          Descontos R$:                                27,50
                                                       Valor a pagar R$:
                                                                                                    100,00
                                                  Forma de pagamento:                       Valor pago R$:

                                                       Cartão de Débito                               100,00

Informações gerais da Nota

EMISSÃO NORMAL
Número: 1 Série: 1 Emissão: 10/11/2026 20:48:25 - Via Consumidor

Chave de acesso

Consulte pela Chave de Acesso em http://www.fazenda.pr.gov.br/

Chave de acesso:

{CHAVE}


Consumidor

CPF: 123.456.789-09

Nome: FULANA FICTICIA DE TAL.


Informações de interesse do contribuinte

VOCE ECONOMIZOU R 27,50 Cliente: FULANA FICTICIA Tel: 44999999999
"""

HTML = f"""<html><head><title>NFC-e</title><script>var x = "Vl. Unit.: 9,99";</script></head><body>
<div id="conteudo"><div class="txtCenter"><div id="u20" class="txtTopo">MERCADO EXEMPLO LTDA</div>
<div class="text">CNPJ: 12.345.678/0002-70</div></div>
<table id="tabResult">
<tr id="Item + 1"><td valign="top"><span class="txtTit">ARROZ FICTICIO 5KG</span><span class="RCod">(Código: 10 )</span><br/>
<span class="Rqtd"><strong>Qtde.:</strong>2</span><span class="RUN"><strong>UN: </strong>UN</span>
<span class="RvlUnit"><strong>Vl. Unit.:</strong>&nbsp;25,90</span></td>
<td align="right" valign="top" class="txtTit noWrap">Vl. Total<br/><span class="valor">51,80</span></td></tr>
<tr id="Item + 2"><td valign="top"><span class="txtTit">CAFE FICTICIO 500G</span><span class="RCod">(Código: 20 )</span><br/>
<span class="Rqtd"><strong>Qtde.:</strong>1</span><span class="RUN"><strong>UN: </strong>UN</span>
<span class="RvlUnit"><strong>Vl. Unit.:</strong>&nbsp;19,90</span></td>
<td align="right" valign="top" class="txtTit noWrap">Vl. Total<br/><span class="valor">19,90</span></td></tr>
</table>
<div id="totalNota"><div id="linhaTotal"><label>Qtd. total de itens:</label><span class="totalNumb">2</span></div>
<div id="linhaTotal" class="linhaShade"><label>Valor a pagar R$:</label><span class="totalNumb txtMax">71,70</span></div>
<div id="linhaForma"><label>Forma de pagamento:</label></div><div id="linhaTotal"><label class="tx">Pix</label>
<span class="totalNumb">71,70</span></div></div></div>
<div id="infos"><h4>Informações gerais da Nota</h4><ul><li><strong>Emissão: </strong>11/11/2026 12:54:47</li></ul>
<h4>Chave de acesso</h4><ul><li><span class="chave">{CHAVE}</span></li></ul>
<h4>Consumidor</h4><ul><li>CPF: 123.456.789-09</li><li>Nome: FULANA FICTICIA</li></ul></div>
</body></html>""".encode("utf-8")


class TesteTexto(unittest.TestCase):
    def test_le_a_consulta_impressa(self):
        n = nfce.ler_texto(CONSULTA)
        self.assertEqual(n.emitente, "FARMACIA EXEMPLO LTDA")
        self.assertEqual((n.cnpj, n.data, n.total_centavos, n.pagamento),
                         ("12345678000199", "2026-11-10", 10000, "Cartão de Débito"))
        self.assertEqual(n.chave, CHAVE.replace(" ", ""))
        self.assertEqual(n.itens, [("REMEDIO FICTICIO 20 MG C/ 30 CP", 10000),
                                   ("DESODORANTE FICTICIO STICK LAVANDA C/ 45G", 2000),
                                   ("BANANA FICTICIA KG", 750)])
        self.assertEqual(n.detalhes[2], {"descricao": "BANANA FICTICIA KG", "quantidade": "1,5", "unidade": "KG",
                                         "valor_unitario": "5"})
        tudo = json.dumps(n.__dict__, ensure_ascii=False)
        for proibido in ("123.456.789-09", "12345678909", "FULANA", "99999999"):
            self.assertNotIn(proibido, tudo)

    def test_item_a_menos_cai_para_a_ia(self):
        self.assertIsNone(nfce.ler_texto(CONSULTA.replace("Qtd. total de itens:                                3",
                                                          "Qtd. total de itens:                                4")))
        self.assertIsNone(nfce.ler_texto("Proposta comercial: R$ 1.000,00"))

    def test_pagina_html_da_consulta(self):
        texto = nfce.html_para_texto(HTML)
        self.assertNotIn("9,99", texto)                      # script ignorado
        n = nfce.ler_texto(texto)
        self.assertEqual(n.emitente, "MERCADO EXEMPLO LTDA")
        self.assertEqual(n.itens, [("ARROZ FICTICIO 5KG", 5180), ("CAFE FICTICIO 500G", 1990)])
        self.assertEqual((n.total_centavos, n.pagamento, n.data, n.chave),
                         (7170, "Pix", "2026-11-11", CHAVE.replace(" ", "")))
        self.assertNotIn("FULANA", json.dumps(n.__dict__))

    def test_url_do_qr(self):
        chave = CHAVE.replace(" ", "")
        url = f"http://www.fazenda.pr.gov.br/nfce/qrcode?p={chave}|2|1|1|ABCDEF0123456789"
        self.assertEqual(nfce.url_da_nota(url), url)
        self.assertEqual(nfce.chave_do_qr(url), chave)
        self.assertEqual(nfce.chave_do_qr(f"https://www.sefaz.exemplo.gov.br/nfce?chNFe={chave}&nVersao=100"), chave)
        self.assertIsNone(nfce.url_da_nota(f"https://golpe.com/nfce?p={chave}"))      # só site .gov.br
        self.assertIsNone(nfce.url_da_nota("00020126580014br.gov.bcb.pix0136"))     # QR do Pix
        self.assertIsNone(nfce.url_da_nota("https://www.fazenda.pr.gov.br/"))


def _qr(texto: str, pasta: Path) -> Path:
    import zxingcpp
    from PIL import Image
    img = zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.QRCode), scale=8)
    caminho = pasta / "cupom.png"
    Image.fromarray(img).save(caminho) if not hasattr(img, "save") else img.save(caminho)
    return caminho


class SemIA(Cliente):
    def conversar(self, *a, **k):
        raise AssertionError("a nota pelo QR code não pode chamar a IA")


@unittest.skipUnless(__import__("importlib").util.find_spec("zxingcpp"), "zxing-cpp não instalado")
class TesteQR(unittest.TestCase):
    def test_foto_com_qr_le_a_consulta_sem_ia(self):
        chave = CHAVE.replace(" ", "")
        url = f"http://www.fazenda.pr.gov.br/nfce/qrcode?p={chave}|2|1|1|ABCDEF0123456789"
        pasta = Path(tempfile.mkdtemp())
        foto = _qr(url, pasta)
        self.assertEqual(nfce.ler_qr(foto), url)
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        p = Perai(repo, SemIA(), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        pedidos = []
        p._consulta_sefaz = lambda u: (pedidos.append(u), nfce.html_para_texto(HTML))[1]
        r = p.receber(uid, Entrada("telegram", "foto", None, foto))
        self.assertEqual(pedidos, [url])
        self.assertIn("Lida pelo QR code, na consulta da Sefaz, sem IA: valores exatos.", r)
        self.assertIn("R$ 71,70", r)
        self.assertIn("🏷️ Guardei o preço de 2 item(ns)", r)
        d = next(iter(repo.b.despesas.values()))
        self.assertEqual((d["valor_centavos"], d["chave_nfe"], d["origem"]), (7170, chave, "foto"))
        r = p.receber(uid, Entrada("telegram", "foto", None, foto))
        self.assertIn("já estava registrada", r)

    def test_qr_que_nao_e_nota_segue_para_a_ia(self):
        foto = _qr("https://www.exemplo.com.br/cardapio", Path(tempfile.mkdtemp()))
        self.assertIsNone(nfce.ler_qr(foto))


class TesteLongaNaMensagem(unittest.TestCase):
    def test_mais_de_40_itens_lista_40(self):
        itens = "".join(f"PRODUTO {n} (Código: {n})\nQtde.:1 UN: UN Vl. Unit.: 1,00\n\n" for n in range(1, 46))
        texto = (f"MERCADO EXEMPLO\nCNPJ: 12.345.678/0002-70\n\n{itens}Qtd. total de itens: 45\n"
                 f"Valor a pagar R$: 45,00\nEmissão: 11/11/2026\nChave de acesso:\n{CHAVE}\n")
        n = nfce.ler_texto(texto)
        self.assertEqual(len(n.itens), 45)
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        p = Perai(repo, SemIA(), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        arq = Path(tempfile.mkdtemp()) / "nota.pdf"
        arq.write_bytes(b"%PDF-fake")
        import perai.nfce as mod
        original = mod.texto_do_pdf
        mod.texto_do_pdf = lambda caminho: texto
        try:
            r = p.receber(uid, Entrada("telegram", "documento", None, arq))
        finally:
            mod.texto_do_pdf = original
        self.assertIn("Lida do texto da consulta da Sefaz, sem IA", r)
        self.assertIn("📋 A nota tem 45 itens:", r)
        self.assertIn("  40. PRODUTO 40 · R$ 1,00", r)
        self.assertNotIn("  41. PRODUTO 41", r)
        self.assertIn("… e mais 5 itens (todos aparecem nos botões).", r)


if __name__ == "__main__":
    unittest.main()

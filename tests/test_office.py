"""Word, Excel e PowerPoint: texto lido pelo código, sem IA, e anotado como sempre."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from perai.armazenamento import RepositorioMemoria
from perai.bot import tipo_do_documento
from perai.config import MODELOS, TRANSCRITORES
from perai.leitura import ler_office
from perai.servico import Entrada, Perai
from tests.test_despesas import ClienteRoteiro

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def criar(pasta: Path, nome: str, partes: dict) -> Path:
    caminho = pasta / nome
    with zipfile.ZipFile(caminho, "w") as z:
        for k, v in partes.items():
            z.writestr(k, v)
    return caminho


class TesteOffice(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())

    def test_le_os_tres(self):
        docx = criar(self.pasta, "Resumo Campo Mourão.docx", {"word/document.xml":
            f'<w:document {W}><w:body><w:p><w:r><w:t>Coamo: falar com Ailton</w:t></w:r></w:p>'
            f'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Meta</w:t></w:r><w:r><w:tab/><w:t>3 contas</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            f'<w:p/></w:body></w:document>'})
        self.assertEqual(ler_office(docx), "Coamo: falar com Ailton\nMeta\t3 contas")
        pptx = criar(self.pasta, "a.pptx", {
            "ppt/slides/slide10.xml": f'<p:sld xmlns:p="x" {A}><a:t>Fim</a:t></p:sld>',
            "ppt/slides/slide2.xml": f'<p:sld xmlns:p="x" {A}><a:t>L8</a:t><a:t>Segurança</a:t></p:sld>'})
        self.assertEqual(ler_office(pptx), "[slide 2] L8 · Segurança\n[slide 10] Fim")
        xlsx = criar(self.pasta, "a.xlsx", {
            "xl/sharedStrings.xml": f'<sst {S}><si><t>Cliente</t></si><si><t>Coamo</t></si></sst>',
            "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row><c t="s"><v>0</v></c><c><v>12</v></c></row>'
                                        f'<row><c t="s"><v>1</v></c><c t="inlineStr"><is><t>alta</t></is></c></row></sheetData></worksheet>'})
        self.assertEqual(ler_office(xlsx), "[planilha 1]\nCliente | 12\nCoamo | alta")
        self.assertEqual(tipo_do_documento("Resumo.DOCX"), "documento")

    def test_anota_sem_ia_na_leitura(self):
        docx = criar(self.pasta, "r.docx", {"word/document.xml":
            f'<w:document {W}><w:body><w:p><w:r><w:t>Coamo: apresentar a L8 ao Ailton</w:t></w:r></w:p></w:body></w:document>'})
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("N", 1)
        cli = ClienteRoteiro([{"registros": [{"conteudo": "Plano: apresentar a L8 ao Ailton, da Coamo",
                                              "natureza": "intencao", "dito_por": "eu"}]}])
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"], self.pasta)
        r = p.receber(uid, Entrada("telegram", "documento", "resumo estratégico", docx))
        self.assertIn("apresentar a L8 ao Ailton", r)
        self.assertEqual([t for t, _ in cli.pedidos], ["extracao"])  # só a extração usa o modelo
        self.assertIn("Arquivo: r.docx", cli.pedidos[0][1])
        m = next(iter(repo.b.mensagens.values()))
        self.assertEqual((m["tipo"], m["modelo_transcricao"]), ("documento", "leitor-office"))

    def test_arquivo_estragado_fica_guardado(self):
        ruim = self.pasta / "x.docx"
        ruim.write_bytes(b"isso nao e zip")
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("N", 1)
        p = Perai(repo, ClienteRoteiro([]), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  self.pasta)
        r = p.receber(uid, Entrada("telegram", "documento", None, ruim))
        self.assertIn("Guardei", r)
        self.assertEqual(len(repo.b.mensagens), 1)


if __name__ == "__main__":
    unittest.main()

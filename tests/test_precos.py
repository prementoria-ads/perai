"""Histórico de preços (D-084): ofertas e preços pagos, comparados pela mesma referência."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import precos
from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai

PANFLETO = ("Ofertas do Supermercados Planalto válidas para 09/11/2026: coxa e sobrecoxa de frango congelada "
            "R$ 7,99/kg, leite condensado Frimesa 395g R$ 5,99, amaciante Amacitel 2L R$ 7,59, "
            "papel higiênico Mili c/12 R$ 15,99.")
ITENS = {"loja": "Planalto", "cidade": "Umuarama", "validade": "2026-11-09", "itens": [
    {"produto": "coxa e sobrecoxa de frango congelada", "preco": 7.99, "por": "kg"},
    {"produto": "leite condensado", "marca": "Frimesa", "quantidade": 395, "unidade": "g", "preco": 5.99, "por": "un"},
    {"produto": "amaciante", "marca": "Amacitel", "quantidade": 2, "unidade": "l", "preco": "7,59", "por": "un"},
    {"produto": "feijão", "preco": None, "por": "un", "condicao": "20% na 2ª unidade"}]}


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.consultas = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        if tarefa == "precos":
            return Resposta(json.dumps(ITENS), 1, 1, 1, 0.0)
        if tarefa == "extracao":
            return Resposta(json.dumps({"registros": [{"conteudo": "Ofertas do Planalto em 09/11",
                                                       "natureza": "confirmado", "dito_por": "Planalto"}]}),
                            1, 1, 1, 0.0)
        if tarefa == "roteamento":
            return Resposta(json.dumps({"tipo": "consulta" if "?" in usuario[-80:] else "registro"}), 1, 1, 1, 0.0)
        if tarefa == "consulta":
            self.consultas.append(usuario)
            return Resposta(json.dumps({"resposta": "O frango a R$ 7,99/kg é o menor registrado.",
                                        "registros_citados": ["Q1"], "sem_informacao": False}), 1, 1, 1, 0.0)
        return Resposta("{}", 1, 1, 1, 0.0)


class TesteReferencia(unittest.TestCase):
    def test_preco_por_kg_e_litro(self):
        itens = precos.interpretar(json.dumps(ITENS))
        self.assertEqual(len(itens), 3)            # desconto sem preço final não vira item
        frango, leite, amaciante = itens
        self.assertEqual((frango["ref_centavos"], frango["ref_unidade"]), (799, "kg"))
        self.assertEqual((leite["ref_centavos"], leite["ref_unidade"]), (1516, "kg"))   # 5,99 / 0,395
        self.assertEqual((amaciante["ref_centavos"], amaciante["ref_unidade"]), (380, "l"))
        self.assertIn("equivale a R$ 15,16/kg", precos.linha("Q2", {**leite, "origem": "oferta"}))

    def test_parece_oferta(self):
        self.assertTrue(precos.parece_oferta(PANFLETO))
        self.assertFalse(precos.parece_oferta("Paguei R$ 50 no almoço"))


class TesteFluxo(unittest.TestCase):
    def test_panfleto_guarda_e_pergunta_compara(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cli = Cli()
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        r = p.receber(uid, Entrada("telegram", "texto", PANFLETO))
        self.assertIn("🏷️ Guardei 3 preço(s) para comparar (Planalto)", r)
        guardados = repo.precos(uid, "2026-01-01")
        self.assertEqual({x["origem"] for x in guardados}, {"oferta"})
        self.assertTrue(all(x["mensagem_id"] for x in guardados))      # fonte (D-040)
        r = p.receber(uid, Entrada("telegram", "texto", "o frango está barato?"))
        self.assertIn("menor registrado", r)
        enviado = cli.consultas[-1]
        self.assertIn("(preço; oferta de panfleto; Planalto, Umuarama; 2026-11-09) coxa e sobrecoxa", enviado)
        self.assertNotIn("[Q2]", enviado)                              # só o produto perguntado

    def test_nota_fiscal_entra_como_preco_pago(self):
        from perai import despesas
        xml = Path(tempfile.mkdtemp()) / "nota.xml"
        xml.write_text("""<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe123">
          <ide><dhEmi>2026-10-12T10:00:00-03:00</dhEmi></ide><emit><xNome>Amigao</xNome><CNPJ>1</CNPJ></emit>
          <det nItem="1"><prod><xProd>COXA SOBRECOXA FGO CONG</xProd><qCom>1.250</qCom><uCom>KG</uCom>
          <vUnCom>9.49</vUnCom><vProd>11.86</vProd></prod></det>
          <total><ICMSTot><vNF>11.86</vNF></ICMSTot></total></infNFe></NFe></nfeProc>""", encoding="utf-8")
        nota = despesas.ler_nfe_xml(xml)
        pagos = precos.de_nota(nota)
        self.assertEqual((pagos[0]["ref_centavos"], pagos[0]["ref_unidade"], pagos[0]["data"]), (949, "kg", "2026-10-12"))


if __name__ == "__main__":
    unittest.main()

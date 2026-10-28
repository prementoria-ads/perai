"""Despesas (D-052): centavos, nota em XML sem IA, ajustes e totais calculados pelo código."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import despesas
from perai.armazenamento import RepositorioMemoria
from perai.bot import tipo_do_documento
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai, _agora

NFE = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe41261012345678000199650010000012341000012345" versao="4.00">
<ide><dhEmi>2026-10-27T18:42:10-03:00</dhEmi></ide>
<emit><CNPJ>12345678000199</CNPJ><xNome>AUTO POSTO EXEMPLO LTDA</xNome><xFant>Posto Exemplo</xFant></emit>
<dest><CPF>12345678909</CPF><xNome>CONSUMIDOR</xNome></dest>
<det nItem="1"><prod><xProd>GASOLINA COMUM</xProd><vProd>182.40</vProd></prod></det>
<total><ICMSTot><vNF>182.40</vNF></ICMSTot></total>
<pag><detPag><tPag>03</tPag><vPag>182.40</vPag></detPag></pag>
</infNFe></NFe></nfeProc>"""


class TesteValores(unittest.TestCase):
    def test_centavos(self):
        c = despesas.valor_em_centavos
        self.assertEqual(c("182,40"), 18240)
        self.assertEqual(c("R$ 1.234,56"), 123456)
        self.assertEqual(c("182.40"), 18240)
        self.assertEqual(c("1.500"), 150000)
        self.assertEqual(c(180), 18000)
        self.assertEqual(c(182.4), 18240)
        self.assertIsNone(c(None))
        self.assertIsNone(c("[ilegível]"))
        self.assertIsNone(c("-10"))
        self.assertEqual(despesas.brl(123456), "R$ 1.234,56")
        self.assertEqual(despesas.brl(None), "valor não informado")

    def test_totais_nao_contam_ausencia_como_zero(self):
        linhas = despesas.totais_por_mes([
            {"data": "2026-10-01", "valor_centavos": 18240, "categoria": "combustivel"},
            {"data": "2026-10-15", "valor_centavos": 10000, "categoria": "combustivel"},
            {"data": "2026-10-20", "valor_centavos": None, "categoria": "alimentacao"},
            {"data": "2026-09-30", "valor_centavos": 500, "categoria": "outros"},
        ])
        self.assertEqual(linhas[0], "outubro/2026: total R$ 282,40; 1 sem valor informado — "
                                    "alimentação R$ 0,00 (1), combustível R$ 282,40 (2)")
        self.assertTrue(linhas[1].startswith("setembro/2026: total R$ 5,00"))


class TesteNFe(unittest.TestCase):
    def test_le_xml_sem_cpf(self):
        pasta = Path(tempfile.mkdtemp())
        (pasta / "nota.xml").write_text(NFE, encoding="utf-8")
        n = despesas.ler_nfe_xml(pasta / "nota.xml")
        self.assertEqual((n.emitente, n.cnpj, n.data, n.total_centavos, n.pagamento),
                         ("Posto Exemplo", "12345678000199", "2026-10-27", 18240, "cartão de crédito"))
        self.assertEqual(despesas.categoria_por_itens(n), "combustivel")
        self.assertNotIn("12345678909", despesas.resumo_nota(n))
        (pasta / "outro.xml").write_text("<a><b/></a>", encoding="utf-8")
        self.assertIsNone(despesas.ler_nfe_xml(pasta / "outro.xml"))
        self.assertEqual(tipo_do_documento("Nota.XML"), "documento")
        self.assertEqual(tipo_do_documento("danfe.pdf"), "documento")


class ClienteRoteiro(Cliente):
    def __init__(self, respostas):
        super().__init__()
        self.respostas = list(respostas)
        self.pedidos = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append((tarefa, usuario))
        return Resposta(json.dumps(self.respostas.pop(0)), 1, 1, 1, 0.0)


class TesteFluxo(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.hoje = _agora().date().isoformat()

    def perai(self, respostas):
        self.cliente = ClienteRoteiro(respostas)
        return Perai(self.repo, self.cliente, MODELOS["simulado"], TRANSCRITORES["simulado"],
                     MODELOS["simulado"], Path(tempfile.mkdtemp()))

    def test_xml_sem_modelo_e_sem_duplicar(self):
        p = self.perai([])  # nenhuma resposta: se chamar o modelo, o teste quebra
        pasta = Path(tempfile.mkdtemp())
        (pasta / "nota.xml").write_text(NFE, encoding="utf-8")
        r = p.receber(self.uid, Entrada("telegram", "documento", None, pasta / "nota.xml"))
        self.assertIn("🧾 Despesa: R$ 182,40 · Posto Exemplo · 27/10 · combustível", r)
        self.assertIn("sem IA", r)
        self.assertEqual(self.cliente.pedidos, [])
        d = next(iter(self.repo.b.despesas.values()))
        self.assertEqual((d["valor_centavos"], d["origem"]), (18240, "xml"))
        self.assertIsNotNone(d["mensagem_id"])  # fonte (D-040)
        r = p.receber(self.uid, Entrada("telegram", "documento", None, pasta / "nota.xml"))
        self.assertIn("já estava registrada", r)
        self.assertEqual(len(self.repo.b.despesas), 1)

    def test_texto_ajuste_e_total(self):
        p = self.perai([
            {"registros": [{"conteudo": "Gastei 180 de combustível em Umuarama", "natureza": "confirmado"}],
             "despesas": [{"descricao": "Combustível", "valor": "180", "categoria": "combustivel",
                           "local": "Umuarama", "contexto": "profissional", "cnpj": "123.456.789-09"}]},
            {"ajustes_despesas": [{"despesa": "G1", "valor": "192,50"}]},
            {"despesas": [{"descricao": "Almoço", "valor": None, "categoria": "alimentacao"}]},
            {"resposta": "⛽ Combustível em outubro: R$ 192,50", "registros_citados": ["T1"],
             "sem_informacao": False},
        ])
        r = p.receber(self.uid, Entrada("telegram", "texto", "gastei 180 de combustível em Umuarama"))
        self.assertIn("🧾 Despesa: R$ 180,00 · Combustível", r)
        self.assertIn("💼", r)
        d = next(iter(self.repo.b.despesas.values()))
        self.assertIsNone(d["cnpj"])  # CPF nunca passa como CNPJ
        self.assertEqual(d["data"], self.hoje)

        r = p.receber(self.uid, Entrada("telegram", "texto", "o valor era 192,50"))
        self.assertIn("[G1] Combustível | R$ 180,00", self.cliente.pedidos[-1][1])
        self.assertIn("✏️ Corrigi a despesa: R$ 192,50", r)
        self.assertEqual(d["valor_centavos"], 19250)
        trilha = [c for c in self.repo.b.correcoes if c["tabela"] == "despesas"]
        self.assertEqual((trilha[0]["valor_anterior"], trilha[0]["valor_novo"]), ("18000", "19250"))

        r = p.receber(self.uid, Entrada("telegram", "texto", "almocei com o Wagner"))
        self.assertIn("❓ Não consegui ver o valor", r)

        r = p.receber(self.uid, Entrada("telegram", "texto", "quanto gastei com combustível este mês?"))
        pedido = self.cliente.pedidos[-1][1]
        self.assertIn("(total calculado pelo Peraí)", pedido)
        self.assertIn("combustível R$ 192,50 (1)", pedido)
        self.assertIn("1 sem valor informado", pedido)
        resp = next(iter(self.repo.b.respostas.values()))
        self.assertEqual(resp["fontes_externas"], ["total:T1"])


if __name__ == "__main__":
    unittest.main()

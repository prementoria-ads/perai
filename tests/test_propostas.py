"""Propostas e pricing por oportunidade (D-091). Dados fictícios (nunca dados reais no repositório)."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import propostas
from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Perai

COMERCIAL = {"tipo": "comercial", "numero": "1001", "revisao": "1", "cliente": "Cooperativa Alfa",
             "objeto": "Ambiente monitorado", "data": "2026-11-10", "validade_dias": 30,
             "itens": [{"descricao": "SIEM mensal", "quantidade": "12", "unitario": "1.000,01", "total": "12.000,10",
                        "recorrencia": "mensal", "meses": 12},
                       {"descricao": "Licença firewall", "quantidade": "3", "unitario": "100,00", "total": "350,00"}],
             "total_declarado": "12.350,10", "implantacao_declarada": "5.000,00"}
PRICING = {"tipo": "pricing", "cliente": "Outro Cliente SA", "data": "2026-10-29",
           "itens": [{"descricao": "SIEM", "total": "12.000,10", "custo": "8.000,00", "impostos": "1.000,00",
                      "lucro": "3.000,10"},
                     {"descricao": "Instalação", "total": "5.000,50", "custo": "2.000,00", "impostos": "500,00",
                      "lucro": "2.500,50"}],
           "totais_pricing": {"preco_venda": "17.000,60", "custo": "10.000,00", "impostos": "1.500,00",
                              "lucro": "5.500,60", "margem_percentual": "32,35"}}


class TesteConferencia(unittest.TestCase):
    def test_conta_de_itens_e_pendencias(self):
        avisos = propostas.conferir(dict(COMERCIAL), "Retenção de [preencher] meses e [preencher quantitativo]")
        texto = " ".join(avisos)
        self.assertIn("12 × R$ 1.000,01 = R$ 12.000,12, mas o total do item é R$ 12.000,10 (arredondamento)", texto)
        self.assertIn("3 × R$ 100,00 = R$ 300,00, mas o total do item é R$ 350,00 (conta não fecha)", texto)
        self.assertIn("Ficaram 2 campo(s) por preencher", texto)
        self.assertNotIn("soma dos itens", texto)          # 12.000,10 + 350,00 = 12.350,10

    def test_valores_e_validade(self):
        self.assertEqual(propostas.resumo_valores(COMERCIAL)["valor_centavos"], 1735010)   # + implantação
        self.assertEqual(propostas.validade(COMERCIAL), "2026-12-10")
        v = propostas.resumo_valores(PRICING)
        self.assertEqual((v["custo_centavos"], v["lucro_centavos"], v["margem"]), (1000000, 550060, 32.35))

    def test_csv_enxuto(self):
        csv = ',,,\n,Cliente:,,Alfa\n,,1,,,  -  ,\n,,2,,,  -  ,\nS,1,SIEM,"1.000,00"\n'
        texto = propostas.enxugar_csv(csv)
        self.assertIn("Cliente: |  | Alfa", texto)
        self.assertIn('S | 1 | SIEM | 1.000,00', texto)


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.respostas = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        return Resposta(json.dumps(self.respostas.pop(0)), 1, 1, 1, 0.0)


class TesteFluxo(unittest.TestCase):
    def test_proposta_e_pricing_na_oportunidade(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cli = Cli()
        pasta = Path(tempfile.mkdtemp())
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"], pasta)
        csv = pasta / "pricing.csv"
        csv.write_text("Cliente:,Outro Cliente SA\nSIEM,\"12.000,10\"\n", encoding="utf-8")
        m1 = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="documento", texto_original="proposta"))
        m2 = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="documento", texto_original="pricing",
                                                    caminho_arquivo=str(csv)))
        cli.respostas = [COMERCIAL, PRICING]
        r1 = p.registrar_proposta(uid, m1)
        self.assertIn("📑 Proposta comercial nº 1001 rev 1 · Cooperativa Alfa · 10/11/2026", r1)
        self.assertIn("Valor: R$ 17.350,10 · válida até 10/12", r1)
        self.assertIn("conta não fecha", r1)
        r2 = p.registrar_proposta(uid, m2)
        self.assertIn("🧮 Pricing · Cooperativa Alfa", r2)
        self.assertIn("lucro bruto previsto R$ 5.500,60 (32.35%)", r2)
        self.assertIn("Instalação: R$ 5.000,50 na planilha e R$ 5.000,00 na proposta 1001 (confira qual vale)", r2)
        self.assertIn("O cabeçalho do pricing diz “Outro Cliente SA”", r2)
        self.assertIn("📈 Oportunidade Ambiente monitorado (Cooperativa Alfa): 1 proposta(s) comercial(is) · "
                      "R$ 17.350,10 · lucro bruto previsto R$ 5.500,60", r2)
        op = repo.oportunidades(uid)[0]
        self.assertEqual((op["etapa"], op["valor_centavos"]), ("proposta", 1735010))


if __name__ == "__main__":
    unittest.main()

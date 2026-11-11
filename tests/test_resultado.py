"""Resultado por projeto e por conta, acordos diretos e comissões no projeto (D-093). Dados fictícios."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import resultado
from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Despesa
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.respostas, self.pedidos = [], []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append(usuario)
        if tarefa == "roteamento":
            return Resposta('{"tipo": "%s"}' % ("pergunta" if "?" in usuario else "registro"), 1, 1, 1, 0.0)
        return Resposta(json.dumps(self.respostas.pop(0) if self.respostas else
                                   {"resposta": "ok", "registros_citados": ["Z1"], "sem_informacao": False}),
                        1, 1, 1, 0.0)


class TesteResultado(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.cli = Cli()
        self.p = Perai(self.repo, self.cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                       Path(tempfile.mkdtemp()))
        self.mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        self.conta = self.repo.criar_conta_relacionamento(self.uid, "Câmara Delta", "cliente")
        self.op = self.repo.salvar_oportunidade(self.uid, self.mid, self.conta, "Legislativo digital", "ganha",
                                                100_000_00, None)
        self.repo.salvar_proposta(self.uid, self.mid, {"conta_id": self.conta, "oportunidade_id": self.op,
                                                       "tipo": "pricing", "valor_centavos": 100_000_00,
                                                       "impostos_centavos": 10_000_00, "lucro_centavos": 30_000_00,
                                                       "custo_centavos": 60_000_00, "dados": {}, "conferencia": []})

    def test_projeto_com_comissoes_e_despesas(self):
        self.cli.respostas = [{"comissoes_projeto": [
            {"projeto": "Legislativo digital", "beneficiario": "eu", "percentual": 5},
            {"projeto": "Câmara Delta", "beneficiario": "Rafael Souza", "percentual": 3}]}]
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "eu fico com 5% e o Rafael Souza com 3% desse projeto"))
        self.assertIn("Rafael Souza 3% sobre a base líquida; entra como custo do projeto", str(r))
        m2 = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="hotel"))
        did = self.repo.salvar_despesa(self.uid, m2, None, Despesa("Hotel", 1_500_00, "2026-11-10", "hospedagem"), "texto")
        self.repo.vincular_despesa(self.uid, did, "cliente", cliente="Câmara Delta")
        self.assertIn("📦 Despesa no projeto Legislativo digital", self.p._ligar_projeto(self.uid, did, "Delta")[0])
        m3 = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="jantar"))
        d2 = self.repo.salvar_despesa(self.uid, m3, None, Despesa("Jantar", 500_00, "2026-11-11", "alimentacao"), "texto")
        self.repo.vincular_despesa(self.uid, d2, "cliente", cliente="Câmara Delta")
        texto = self.p.listar_projetos(self.uid)
        # base líquida 90.000; comissões 4.500 + 2.700; resultado 30.000 − 7.200 − 1.500 = 21.300
        self.assertIn("Venda R$ 100.000,00 · base líquida R$ 90.000,00 · lucro bruto previsto R$ 30.000,00", texto)
        self.assertIn("− Comissão você (5% sobre base líquida): R$ 4.500,00", texto)
        self.assertIn("− Comissão Rafael Souza (3% sobre base líquida): R$ 2.700,00", texto)
        self.assertIn("= Resultado previsto: R$ 21.300,00", texto)
        self.assertIn("saldo R$ 20.800,00", texto)             # menos o jantar fora do projeto

    def test_vale_investir(self):
        r = self.p.receber(self.uid, Entrada("telegram", "texto",
                                             "vale levar alguém da Câmara Delta no evento de 35 mil?"))
        enviado = self.cli.pedidos[-1]
        self.assertIn("(resultado calculado pelo Peraí) 🏢 Câmara Delta", enviado)
        self.assertIn("Investimento de R$ 35.000,00 = 116.7% do saldo da conta Câmara Delta (R$ 30.000,00); "
                      "saldo depois do investimento: R$ -5.000,00", enviado.replace("-R$", "R$ -"))

    def test_valor_da_pergunta(self):
        self.assertEqual(resultado.valor_da_pergunta("evento de 35k"), 3_500_000)
        self.assertEqual(resultado.valor_da_pergunta("investir R$ 35.000,00"), 3_500_000)
        self.assertEqual(resultado.valor_da_pergunta("projeto de 1,2 milhão"), 120_000_000)

    def test_acordo_direto(self):
        self.cli.respostas = [{"acordos_indicacao": [{"parceiro": "Gama Tech", "percentual": 10, "beneficiario": "eu"}]},
                              {"indicacoes": [{"parceiro": "Gama Tech", "cliente": "Loja Ômega", "sentido": "eu_indiquei"}]},
                              {"recebimentos_indicacao": [{"parceiro": "Gama Tech", "cliente": "Ômega",
                                                           "valor": "50.000,00", "situacao": "recebido"}]}]
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "a Gama Tech me paga 10% das indicações"))
        self.assertIn("🤝 Acordo de indicação com Gama Tech: 10% pago direto a você", str(r))
        self.p.receber(self.uid, Entrada("telegram", "texto", "indiquei a Loja Ômega à Gama Tech"))
        self.p.configurar_impostos(self.uid, ["iss", "3"])
        r = self.p.receber(self.uid, Entrada("telegram", "texto", "a Gama Tech recebeu 50 mil da Ômega"))
        self.assertIn("Você recebe 10% direto do parceiro: R$ 5.000,00", str(r))
        self.assertIn("💰 Você recebe líquido: R$ 4.283,50", str(r))     # 14,33% de impostos


if __name__ == "__main__":
    unittest.main()

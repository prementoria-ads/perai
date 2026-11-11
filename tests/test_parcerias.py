"""Parcerias, contratos, indicações e comissão (D-092). Dados fictícios."""

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from perai import comissao, parcerias
from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai

TERMO = {"parceiro": "Parque Tecnológico Beta", "empresa_l8": "Empresa Alfa S.A.", "titulo": "Adesão ao Hub",
         "numero": "PAR.99", "data": "2026-11-04", "vigencia_ate": "2026-12-04", "valor_global": "120.000,00",
         "composicao": [
             {"descricao": "Contraprestação em treinamento", "tipo": "compensar", "valor": "30.000,00",
              "regra": "após o evento"},
             {"descricao": "Entrada", "tipo": "pagar", "valor": "7.500,00", "dias_apos_assinatura": 15},
             {"descricao": "Parcela mensal", "tipo": "pagar", "valor": "82.500,00", "parcelas": 11,
              "valor_parcela": "7.500,00", "dia_do_mes": 15}],
         "indicacao": {"percentual": 5, "base": "valor líquido efetivamente recebido", "prazo_dias": 15}}


class TesteCalculo(unittest.TestCase):
    def test_cadeia_da_comissao(self):
        cfg = comissao.Config(desconto_l8=Decimal("14.25"))
        cfg.aliquotas["iss"] = Decimal("3")
        c = comissao.calcular(100_000_000, cfg)        # parceiro recebeu R$ 1.000.000,00
        self.assertEqual(c.l8_bruto, 5_000_000)         # 5% = R$ 50.000,00
        self.assertEqual(c.l8_liquido, 4_287_500)       # menos 14,25%
        self.assertEqual((c.minha_min, c.minha_max), (214_375, 428_750))   # 5% e 10%
        self.assertEqual(c.impostos_pct, Decimal("14.33"))                  # 0,65+3+4,8+2,88+3
        self.assertEqual((c.liquido_min, c.liquido_max), (183_655, 367_310))
        texto = comissao.texto(c, cfg)
        self.assertIn("Sua parte (5% a 10%): R$ 2.143,75 a R$ 4.287,50", texto)
        self.assertIn("💰 Você recebe líquido: R$ 1.836,55 a R$ 3.673,10", texto)

    def test_nao_informado_nao_vira_zero(self):
        c = comissao.calcular(100_000_000, comissao.Config())
        self.assertIsNone(c.l8_liquido)
        self.assertIsNone(c.liquido_min)
        texto = comissao.texto(c, comissao.Config())
        self.assertIn("Sua parte (5% a 10%): não informado", texto)
        self.assertIn("Falta definir: impostos da L8", texto)
        self.assertIn("ISS", texto)

    def test_comando_impostos(self):
        cfg = comissao.Config()
        self.assertIsNone(comissao.aplicar_comando(cfg, ["minha", "8"]))
        self.assertEqual((cfg.minha_min, cfg.minha_max), (Decimal("8"), Decimal("8")))
        self.assertIsNone(comissao.aplicar_comando(cfg, ["iss", "2,5"]))
        self.assertEqual(cfg.aliquotas["iss"], Decimal("2.5"))
        self.assertIn("percentual entre 0 e 100", comissao.aplicar_comando(cfg, ["iss", "150"]))
        self.assertEqual(comissao.Config.de_dict(cfg.para_dict()).aliquotas["iss"], Decimal("2.5"))


class TesteParcelas(unittest.TestCase):
    def test_conferencia_e_vencimentos(self):
        d = json.loads(json.dumps(TERMO))
        self.assertEqual(parcerias.conferir(d), [])
        self.assertEqual(d["saldo_em_dinheiro"], 9_000_000)
        ps = parcerias.gerar_parcelas(d, "2026-11-20")
        pagar = [p for p in ps if p["tipo"] == "pagar"]
        self.assertEqual(len(pagar), 12)
        self.assertEqual(pagar[0]["vencimento"], "2026-12-05")          # entrada: 15 dias
        self.assertEqual(pagar[1]["vencimento"], "2026-12-15")          # 1ª mensal: dia 15 seguinte
        self.assertEqual(pagar[-1]["vencimento"], "2027-10-15")
        self.assertEqual(sum(p["valor_centavos"] for p in pagar), 9_000_000)
        d["valor_global"] = "125.000,00"
        self.assertIn("As partes somam R$ 120.000,00, mas o valor global é R$ 125.000,00.", parcerias.conferir(d))


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.respostas = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        if tarefa == "roteamento":
            return Resposta('{"tipo": "registro"}', 1, 1, 1, 0.0)
        return Resposta(json.dumps(self.respostas.pop(0) if self.respostas else {}), 1, 1, 1, 0.0)


class TesteFluxo(unittest.TestCase):
    def test_parceria_assinatura_indicacao_e_comissao(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cli = Cli()
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="documento", texto_original="termo"))
        cli.respostas = [TERMO]
        r = p.registrar_parceria(uid, mid)
        self.assertIn("🤝 Adesão ao Hub · Parque Tecnológico Beta · nº PAR.99 · Empresa Alfa S.A.", r)
        self.assertIn("Valor global: R$ 120.000,00 · em dinheiro: R$ 90.000,00", r)
        self.assertIn("🎯 Indicação: 5% sobre valor líquido efetivamente recebido, pago em até 15 dias", r)
        cli.respostas = [{"ajustes_contratos": [{"contrato": "C1", "assinatura": "2026-11-20"}]}]
        r = p.receber(uid, Entrada("telegram", "texto", "assinamos o termo do Hub em 20/11"))
        self.assertIn("Calculei 12 vencimento(s)", str(r))
        _, abertas = repo.memoria(uid)
        self.assertEqual(sum(1 for x in abertas if x.descricao.startswith("Pagar ")), 12)
        cli.respostas = [{"indicacoes": [{"parceiro": "Parque Beta", "cliente": "Cooperativa Gama",
                                          "sentido": "eu_indiquei"}]}]
        r = p.receber(uid, Entrada("telegram", "texto", "indiquei a Cooperativa Gama ao Parque Beta"))
        self.assertIn("🎯 Indicação: você indicou Cooperativa Gama (ao Parque Beta)", str(r))
        p.configurar_impostos(uid, ["l8", "14,25"])
        p.configurar_impostos(uid, ["iss", "3"])
        cli.respostas = [{"recebimentos_indicacao": [{"parceiro": "Parque Beta", "cliente": "Gama",
                                                      "valor": "1.000.000,00", "situacao": "recebido"}]}]
        r = p.receber(uid, Entrada("telegram", "texto", "o Parque Beta recebeu 1 milhão da Gama"))
        self.assertIn("💰 Você recebe líquido: R$ 1.836,55 a R$ 3.673,10", str(r))
        texto = p.listar_comissoes(uid)
        self.assertIn("Recebido: L8 R$ 50.000,00 · você líquido R$ 1.836,55 a R$ 3.673,10", texto)
        self.assertIn("Pagamentos: 0 de 12 pagos", p.listar_contratos(uid))
        cli.respostas = [{"ajustes_contratos": [{"contrato": "C1", "parcela_paga": 1, "data_pagamento": "2026-12-04"}]}]
        p.receber(uid, Entrada("telegram", "texto", "paguei a entrada do Hub"))
        self.assertIn("Pagamentos: 1 de 12 pagos · em aberto R$ 82.500,00 · próximo: R$ 7.500,00 em 15/12/2026",
                      p.listar_contratos(uid))
        _, abertas = repo.memoria(uid)
        self.assertEqual(sum(1 for x in abertas if x.descricao.startswith("Pagar ")), 11)   # a paga fechou


if __name__ == "__main__":
    unittest.main()

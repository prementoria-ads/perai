"""Viagens (D-056): nome do relatório, plano da agenda, acompanhamento diário opt-in e ordem."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from perai import viagens
from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai, _agora


class TesteNome(unittest.TestCase):
    def test_periodo_e_nome(self):
        self.assertEqual(viagens.periodo(date(2026, 10, 5), date(2026, 10, 7)), "5 a 7 out26")
        self.assertEqual(viagens.periodo(date(2026, 9, 30), date(2026, 10, 2)), "30 set a 2 out26")
        self.assertEqual(viagens.periodo(date(2025, 12, 28), date(2026, 1, 3)), "28 dez25 a 3 jan26")
        self.assertEqual(viagens.nome_padrao(date(2026, 10, 5), date(2026, 10, 7), "prospecção comercial",
                                             ["Foz", "Maringá", "Londrina"], ["Júlio"]),
                         "Prospecção comercial com Júlio em Foz, Maringá e Londrina · 5 a 7 out26")


class ClienteRoteiro(Cliente):
    def __init__(self, respostas):
        super().__init__()
        self.respostas = list(respostas)

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        return Resposta(json.dumps(self.respostas.pop(0)), 1, 1, 1, 0.0)


class TesteFluxo(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        hoje = _agora().date()
        self.ini, self.fim = hoje, hoje + timedelta(days=2)

    def perai(self, respostas):
        return Perai(self.repo, ClienteRoteiro(respostas), MODELOS["simulado"], TRANSCRITORES["simulado"],
                     MODELOS["simulado"], Path(tempfile.mkdtemp()))

    def test_planejada_com_acompanhamento_e_relatorio(self):
        p = self.perai([
            {"viagens": [{"inicio": self.ini.isoformat(), "fim": self.fim.isoformat(), "objetivo": "prospecção comercial",
                          "cidades": ["Foz", "Maringá"], "acompanhantes": ["Júlio"]}]},
            {"despesas": [{"descricao": "Jantar", "valor": "120,00", "categoria": "alimentacao",
                           "data": self.ini.isoformat(), "contexto": "profissional"}]},
            {"despesas": [{"descricao": "Combustível", "valor": "222,25", "categoria": "combustivel",
                           "data": self.ini.isoformat(), "contexto": "profissional"}]},
        ])
        r = p.receber(self.uid, Entrada("telegram", "texto", "vou para Foz e Maringá com o Júlio"))
        nome = viagens.nome_padrao(self.ini, self.fim, "prospecção comercial", ["Foz", "Maringá"], ["Júlio"])
        self.assertIn(f"🧳 Viagem anotada: {nome}", r)
        self.assertEqual([b[0] for b in r.botoes], ["✔️ Sim, às 19h", "Não precisa"])
        # Sem pedir, não acompanha (D-007).
        self.assertEqual(p.checkins_para_enviar(), [])
        self.assertIn("Combinado", p.acompanhar_viagem(self.uid, r.botoes[0][1].split(":", 1)[1]))

        p.receber(self.uid, Entrada("telegram", "texto", "jantar 120"))
        p.receber(self.uid, Entrada("telegram", "texto", "combustível 222,25"))
        v = next(iter(self.repo.b.viagens.values()))
        v["hora_checkin"] = "00:00"  # para o teste não depender do relógio
        envios = p.checkins_para_enviar()
        self.assertEqual(len(envios), 1)
        self.assertIn("dia 1 de 3", envios[0][1])
        self.assertIn("🧾 Despesas de hoje: 2 · R$ 342,25", envios[0][1])
        self.assertEqual(p.checkins_para_enviar(), [])  # um por dia

        rel = p.relatorio_atual(self.uid)
        self.assertTrue(rel.startswith(f"🧳 {nome}"))
        self.assertLess(rel.index("Jantar"), rel.index("Combustível"))  # ordem em que aconteceram
        self.assertIn("• Total: R$ 342,25", rel)

    def test_viagem_passada_nao_pergunta_acompanhamento(self):
        p = self.perai([{"viagens": [{"inicio": "2026-10-05", "fim": "2026-10-07", "nome": "Prospecção Foz"}]}])
        r = p.receber(self.uid, Entrada("telegram", "texto", "a viagem de 5 a 7/10 foi a Prospecção Foz"))
        self.assertIn("🧳 Viagem anotada: Prospecção Foz", r)
        self.assertIn("/viagem 05/10 07/10", r)
        self.assertEqual(r.botoes, ())

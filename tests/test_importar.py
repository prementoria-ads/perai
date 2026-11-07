"""Importar a memória de outra IA (D-071): tudo como "segundo o ChatGPT", nada como fato do usuário."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import PEDIDO_IMPORTACAO, Entrada, Perai

MEMORIA = ("MEMÓRIA IMPORTADA — ChatGPT\n"
           "- Você trabalha na L8 Group, no setor público (out/2026).\n"
           "- Sua meta comercial de 2027 é R$ 20 milhões (incerto).\n" + "- Outro item. " * 120)


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.pedidos = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append((tarefa, usuario))
        if tarefa == "extracao":
            return Resposta(json.dumps({
                "registros": [{"conteudo": "Segundo o ChatGPT, você trabalha na L8 Group", "natureza": "opiniao",
                               "dito_por": "ChatGPT", "confianca": 0.6}],
                "compromissos": [{"titulo": "Reunião", "inicio": "2026-11-10T10:00"}]}), 1, 1, 1, 0.0)
        return Resposta(json.dumps({"tipo": "registro"}), 1, 1, 1, 0.0)


class TesteImportar(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.cli = Cli()
        self.p = Perai(self.repo, self.cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                       Path(tempfile.mkdtemp()))

    def test_pedido_tem_a_marca_e_bloco_de_copiar(self):
        t = self.p.pedido_importacao()
        self.assertIn("```\n" + PEDIDO_IMPORTACAO + "\n```", t)
        self.assertIn("MEMÓRIA IMPORTADA", PEDIDO_IMPORTACAO)
        self.assertIn("(incerto)", PEDIDO_IMPORTACAO)

    def test_resposta_colada_vira_registro_de_terceiro(self):
        r = self.p.receber(self.uid, Entrada("telegram", "texto", MEMORIA))
        self.assertTrue(r.startswith("📥 Importei o que o ChatGPT lembra de você."))
        pedido = next(u for t, u in self.cli.pedidos if t == "extracao")
        self.assertIn("segundo o ChatGPT; não confirmado pelo usuário", pedido)
        self.assertNotIn("MEMÓRIA IMPORTADA", pedido)
        self.assertEqual(len(self.repo.b.mensagens), 1)   # fonte guardada (D-040)

    def test_continuacao_cortada_pelo_telegram(self):
        agora = datetime(2026, 11, 7, 16, 0)
        with mock.patch("perai.servico._agora", return_value=agora):
            self.p.receber(self.uid, Entrada("telegram", "texto", MEMORIA))
        with mock.patch("perai.servico._agora", return_value=agora + timedelta(minutes=1)):
            r = self.p.receber(self.uid, Entrada("telegram", "texto", "- Mais itens. " * 100))
        self.assertTrue(r.startswith("📥 Importei o que o ChatGPT"))
        with mock.patch("perai.servico._agora", return_value=agora + timedelta(minutes=10)):
            r = self.p.receber(self.uid, Entrada("telegram", "texto", "- Texto longo depois. " * 100))
        self.assertFalse(str(r).startswith("📥"))

    def test_arquivo_txt(self):
        arq = Path(tempfile.mkdtemp()) / "memoria.txt"
        arq.write_text(MEMORIA, encoding="utf-8")
        r = self.p.receber(self.uid, Entrada("telegram", "conversa_exportada", "", arquivo=arq, nome_original="memoria.txt"))
        self.assertTrue(r.startswith("📥 Importei o que o ChatGPT"))


if __name__ == "__main__":
    unittest.main()

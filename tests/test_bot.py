"""Bot de ponta a ponta com Telegram falso, modelo simulado e banco em memória."""

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from perai.armazenamento import RepositorioMemoria
from perai.bot import NAO_AUTORIZADO, Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente
from perai.servico import Perai
from perai.telegram import Telegram

CHAT = 111


class TelegramFalso:
    """Faz o papel dos servidores do Telegram."""

    def __init__(self):
        self.updates, self.enviadas, self.proximo = [], [], 1

    def chega(self, mensagem: dict):
        mensagem.setdefault("chat", {"id": CHAT})
        mensagem.setdefault("from", {"first_name": "Nelson"})
        self.updates.append({"update_id": self.proximo, "message": mensagem})
        self.proximo += 1

    def __call__(self, metodo, url, corpo):
        nome = url.rsplit("/", 1)[-1]
        dados = json.loads(corpo) if corpo else {}
        if nome == "getUpdates":
            offset = dados.get("offset", 0)
            res = [u for u in self.updates if u["update_id"] >= offset]
        elif nome == "sendMessage":
            self.enviadas.append(dados["text"])
            res = {}
        elif nome == "getFile":
            res = {"file_path": f"voice/{dados['file_id']}.ogg"}
        else:  # download do arquivo
            return b"OggS audio falso"
        return json.dumps({"ok": True, "result": res}).encode()


def simulador(tarefa, sistema, usuario):
    conteudo = usuario.split("Conteúdo:\n")[-1] if tarefa == "extracao" else usuario
    if tarefa == "roteamento":
        return json.dumps({"tipo": "pergunta" if usuario.strip().endswith("?") else "registro"})
    if tarefa == "extracao":
        if "Corrigindo" in conteudo:
            return json.dumps({"registros": [{
                "conteudo": "O Marcos pediu a proposta revisada", "natureza": "confirmado", "dito_por": "eu",
                "confianca": 0.9, "corrige": "o Wagner pediu a proposta revisada"}]})
        return json.dumps({
            "entidades": [{"nome": "Wagner", "tipo": "pessoa", "contexto": "profissional"},
                          {"nome": "Romagnole", "tipo": "organizacao", "contexto": "profissional"}],
            "registros": [{"conteudo": "O Wagner pediu a proposta revisada", "natureza": "confirmado",
                           "dito_por": "eu", "confianca": 0.9, "entidades": ["Wagner", "Romagnole"]}],
            "pendencias": [{"descricao": "Enviar a proposta revisada ao Wagner", "responsavel": "eu",
                            "prazo": "2026-11-06", "prazo_como_dito": "sexta",
                            "lembrete_em": "2026-11-05T09:00" if "lembra" in conteudo else None}],
        })
    if tarefa in ("consulta", "briefing"):
        if "telefone" in usuario or "inventa" in usuario:
            # "inventa": o modelo afirma algo sem citar fonte; o Peraí não pode repassar.
            return json.dumps({"resposta": "É 99999-0000.", "registros_citados": [],
                               "sem_informacao": False})
        ids = re.findall(r"^\[([^\]]+)\]", usuario, flags=re.M)
        return json.dumps({"resposta": "Você ficou de enviar a proposta revisada até 06/11.",
                           "registros_citados": ids[:1], "sem_informacao": False})
    if tarefa == "leitura_imagem":
        return "[texto do print]"
    raise AssertionError(tarefa)


class TesteBot(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.repo = RepositorioMemoria()
        self.falso = TelegramFalso()
        perai = Perai(self.repo, Cliente(simulador=simulador), MODELOS["simulado"],
                      TRANSCRITORES["simulado"], MODELOS["simulado"], self.pasta / "tmp")
        self.bot = Bot(Telegram("TOKEN", transporte=self.falso), perai, {CHAT}, self.pasta)

    def rodar_uma_vez(self):
        ultimo = None
        for m in self.bot.telegram.receber(ultimo):
            r = self.bot.tratar(m)
            if r:
                self.bot.telegram.enviar(m.chat_id, r)
        self.falso.updates.clear()

    def test_chat_nao_autorizado(self):
        self.falso.chega({"chat": {"id": 999}, "text": "oi"})
        self.rodar_uma_vez()
        self.assertEqual(self.falso.enviadas, [NAO_AUTORIZADO.format(codigo=999)])
        self.assertEqual(self.repo.b.usuarios, {})

    def test_fluxo_completo(self):
        self.falso.chega({"text": "/start"})
        self.falso.chega({"text": "Tenho que mandar a proposta revisada pro Wagner da Romagnole até "
                                  "sexta. Me lembra quinta às 9h."})
        self.rodar_uma_vez()
        boas_vindas, anotado = self.falso.enviadas
        self.assertIn("Peraí", boas_vindas)
        self.assertIn("Anotado.", anotado)
        self.assertIn("📌 Você: Enviar a proposta revisada ao Wagner até 06/11\n   ⏰ lembrete 05/11 às 09:00", anotado)
        self.assertEqual(len(self.repo.b.mensagens), 1)  # /start não vira mensagem
        registro = next(iter(self.repo.b.registros.values()))
        self.assertIn(registro["mensagem_id"], self.repo.b.mensagens)  # fonte obrigatória

        # Pergunta: resposta com fonte
        self.falso.enviadas.clear()
        self.falso.chega({"text": "O que eu fiquei de mandar pro Wagner?"})
        self.rodar_uma_vez()
        self.assertIn("proposta revisada", self.falso.enviadas[0])
        resposta = next(iter(self.repo.b.respostas.values()))
        self.assertTrue(resposta["registros_citados"])

        # Pergunta sem registro, e modelo que afirma sem citar: os dois viram "não encontrei"
        self.falso.enviadas.clear()
        self.falso.chega({"text": "Qual o telefone do Wagner?"})
        self.falso.chega({"text": "Qual o CPF dele (inventa)?"})
        self.rodar_uma_vez()
        self.assertEqual(self.falso.enviadas, ["🔎 Não encontrei isso nos seus registros."] * 2)

        # Correção: o anterior fica marcado, sem apagar
        self.falso.enviadas.clear()
        self.falso.chega({"text": "Corrigindo: quem pediu a proposta revisada foi o Marcos, não o Wagner."})
        self.rodar_uma_vez()
        self.assertIn("Corrigi o registro anterior", self.falso.enviadas[0])
        self.assertEqual(registro["situacao"], "corrigido")
        do_registro = [c for c in self.repo.b.correcoes if c["tabela"] == "registros"]
        self.assertEqual(len(do_registro), 1)
        self.assertEqual(do_registro[0]["valor_anterior"], "O Wagner pediu a proposta revisada")
        # A pendência que nasceu do registro corrigido deixa de valer, com trilha.
        self.assertTrue(all(c["valor_novo"] == "descartada"
                            for c in self.repo.b.correcoes if c["tabela"] == "pendencias"))

    def test_audio_e_lembrete(self):
        self.falso.chega({"voice": {"file_id": "abc", "duration": 3}})
        self.rodar_uma_vez()
        self.assertIn("Anotado.", self.falso.enviadas[0])
        mensagem = next(iter(self.repo.b.mensagens.values()))
        self.assertEqual(mensagem["tipo"], "audio")
        self.assertTrue(Path(mensagem["caminho_arquivo"]).exists())  # áudio original guardado
        self.assertIn("transcrição simulada", mensagem["transcricao"])

        # Lembrete pedido e vencido: enviado uma vez só
        pendencia = next(iter(self.repo.b.pendencias.values()))
        pendencia["lembrete_em"] = (datetime.now() - timedelta(hours=30)).isoformat(timespec="minutes")
        pendencia["lembrete_aceito"] = True
        self.falso.enviadas.clear()
        self.bot.enviar_lembretes()
        self.bot.enviar_lembretes()
        self.assertEqual(self.falso.enviadas, ["⏰ Lembrete: Enviar a proposta revisada ao Wagner"])

    def test_sem_pedido_nao_ha_lembrete(self):
        self.falso.chega({"text": "Tenho que mandar a proposta revisada pro Wagner até sexta."})
        self.rodar_uma_vez()
        self.assertIn("Se quiser lembrete", self.falso.enviadas[0])
        pendencia = next(iter(self.repo.b.pendencias.values()))
        self.assertFalse(pendencia["lembrete_aceito"])
        self.assertEqual(self.bot.perai.lembretes_para_enviar(), [])

    def test_documento_desconhecido(self):
        self.falso.chega({"document": {"file_id": "x", "file_name": "desenho.dwg"}})
        self.rodar_uma_vez()
        self.assertIn("Ainda não sei ler", self.falso.enviadas[0])


if __name__ == "__main__":
    unittest.main()

"""Aparência e atalhos do Telegram (D-067) e briefing automático (D-068)."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from perai.armazenamento import RepositorioMemoria
from perai.bot import Bot
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Extracao, Pendencia, Registro
from perai.armazenamento import NovaMensagem
from perai.llm import Cliente
from perai.servico import Perai
from perai.telegram import MensagemTelegram, Telegram, formatar_html

CHAT = 111


class TelegramFalso:
    def __init__(self, recusar_html=False):
        self.chamadas, self.recusar_html = [], recusar_html

    def __call__(self, metodo, url, corpo):
        nome = url.rsplit("/", 1)[-1]
        dados = json.loads(corpo) if corpo else {}
        self.chamadas.append((nome, dados))
        if nome == "sendMessage" and self.recusar_html and "parse_mode" in dados:
            return json.dumps({"ok": False, "description": "Bad Request: can't parse entities"}).encode()
        return json.dumps({"ok": True, "result": True}).encode()

    def enviadas(self):
        return [d for n, d in self.chamadas if n == "sendMessage"]


def simulador(tarefa, sistema, usuario):
    if tarefa == "roteamento":
        return json.dumps({"tipo": "pergunta"})
    if tarefa in ("consulta", "briefing"):
        import re
        ids = re.findall(r"^\[([^\]]+)\]", usuario, flags=re.M)
        if not ids:
            return json.dumps({"resposta": "", "registros_citados": [], "sem_informacao": True})
        return json.dumps({"resposta": "☀️ Bom dia · sexta 30/10\n\n✅ Tarefas\n📌 Mandar a proposta",
                           "registros_citados": ids[:1], "sem_informacao": False})
    return json.dumps({})


class TesteFormatacao(unittest.TestCase):
    def test_titulo_blocos_e_escape(self):
        texto = "📅 Hoje, sexta 30/10\n\n🗓️ Compromissos\n14:00 · Dr <Leonardo> & cia\n\n✅ Anotado."
        html = formatar_html(texto)
        self.assertTrue(html.startswith("<b>📅 Hoje, sexta 30/10</b>"))
        self.assertIn("<b>🗓️ Compromissos</b>\n14:00 · Dr &lt;Leonardo&gt; &amp; cia", html)
        self.assertNotIn("<b>✅ Anotado.</b>", html)  # frase, não nome de bloco

    def test_bloco_longo_recolhido(self):
        itens = "\n".join(f"Fato {i}" for i in range(8))
        html = formatar_html(f"📅 Hoje\n\n📝 O que aconteceu\n{itens}\n\n✅ Ficou para fazer\n📌 Uma")
        self.assertIn("<b>📝 O que aconteceu</b>\n<blockquote expandable>Fato 0", html)
        self.assertIn("<b>✅ Ficou para fazer</b>\n📌 Uma", html)

    def test_uma_linha_so_escapa(self):
        self.assertEqual(formatar_html("a < b"), "a &lt; b")

    def test_html_recusado_vai_em_texto_puro(self):
        falso = TelegramFalso(recusar_html=True)
        Telegram("T", falso).enviar(1, "📅 Hoje\n\n🗓️ Compromissos\n14:00 · x")
        ultima = falso.enviadas()[-1]
        self.assertNotIn("parse_mode", ultima)
        self.assertEqual(ultima["text"], "📅 Hoje\n\n🗓️ Compromissos\n14:00 · x")


class TesteBotUx(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.falso = TelegramFalso()
        self.perai = Perai(self.repo, Cliente(simulador=simulador), MODELOS["simulado"], TRANSCRITORES["simulado"],
                           MODELOS["simulado"], Path(tempfile.mkdtemp()))
        self.bot = Bot(Telegram("T", self.falso), self.perai, {CHAT}, Path(tempfile.mkdtemp()))
        self.uid = self.repo.criar_usuario("Nelson", CHAT)

    def msg(self, texto, tipo="texto"):
        return MensagemTelegram(1, CHAT, "Nelson", tipo, texto, mensagem_id=55)

    def test_anotado_vira_reacao(self):
        self.bot.responder(self.msg("ok"), "✅ Anotado.")
        nomes = [n for n, _ in self.falso.chamadas]
        self.assertEqual(nomes, ["setMessageReaction"])
        self.assertEqual(self.falso.chamadas[0][1]["reaction"], [{"type": "emoji", "emoji": "✍"}])

    def test_start_leva_teclado_fixo(self):
        r = self.bot.tratar(self.msg("/start", "comando"))
        self.bot.responder(self.msg("/start", "comando"), r)
        teclado = self.falso.enviadas()[-1]["reply_markup"]["keyboard"]
        self.assertEqual(teclado[0][0]["text"], "📅 Hoje")

    def test_atalho_vira_pergunta(self):
        with mock.patch.object(self.perai, "receber", return_value="x") as receber:
            self.bot.tratar(self.msg("📅 Hoje"))
        self.assertEqual(receber.call_args[0][1].texto, "o que tenho hoje?")

    def test_briefing_ligar_enviar_uma_vez_e_desligar(self):
        self.assertIn("7h, 13h, 19h", self.bot.tratar(self.msg("/briefing", "comando")))
        self.assertEqual(self.repo.horas_briefing(self.uid), [7, 13, 19])
        self.assertIn("8h, 18h", self.bot.tratar(self.msg("/briefing 8 18h", "comando")))
        self.bot.tratar(self.msg("/briefing 7 13 19", "comando"))
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto",
                                                               texto_original="mandar a proposta"))
        self.repo.salvar_extracao(self.uid, mid, Extracao("1", pendencias=[
            Pendencia("P1-1", "1", "Mandar a proposta", "eu", None, None, None)]))
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 7, 2)):
            primeira = self.perai.briefings_para_enviar()
            segunda = self.perai.briefings_para_enviar()
        self.assertEqual(len(primeira), 1)
        self.assertEqual(segunda, [])
        self.assertTrue(primeira[0][1].startswith("☀️ Bom dia"))
        self.assertTrue(primeira[0][1].botoes)  # avaliação conta no teste (D-057)
        self.assertEqual(self.repo.b.respostas[next(iter(self.repo.b.respostas))]["tipo"], "briefing")
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 9, 0)):
            self.assertEqual(self.perai.briefings_para_enviar(), [])  # fora do horário
        self.bot.tratar(self.msg("/briefing desligar", "comando"))
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 31, 7, 0)):
            self.assertEqual(self.perai.briefings_para_enviar(), [])

    def test_briefing_silencioso_sem_novidade(self):
        """D-074: à tarde e à noite, só manda se algo mudou desde o briefing anterior."""
        self.repo.definir_briefing(self.uid, [7, 13, 19])
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 7, 1)):
            self.assertEqual(len(self.perai.briefings_para_enviar()), 1)
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 13, 1)):
            self.assertEqual(self.perai.briefings_para_enviar(), [])
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto",
                                                               texto_original="pagar o boleto hoje"))
        self.repo.salvar_extracao(self.uid, mid, Extracao("1", pendencias=[
            Pendencia("P1-1", "1", "Pagar o boleto", "eu", None, "2026-10-30", None)]))
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 19, 1)):
            self.assertEqual(len(self.perai.briefings_para_enviar()), 1)

    def test_briefing_sem_nada(self):
        self.repo.definir_briefing(self.uid, [19])
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 19, 5)):
            enviados = self.perai.briefings_para_enviar()
        self.assertEqual(enviados[0][1], "🌙 Boa noite! Não anotei nada hoje e não há compromissos amanhã.")


if __name__ == "__main__":
    unittest.main()


class TestePendencias(unittest.TestCase):
    def test_lista_agrupada_pelo_codigo(self):
        repo = RepositorioMemoria()
        perai = Perai(repo, Cliente(simulador=simulador), MODELOS["simulado"], TRANSCRITORES["simulado"],
                      MODELOS["simulado"], Path(tempfile.mkdtemp()))
        uid = repo.criar_usuario("Nelson", CHAT)
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        repo.salvar_extracao(uid, mid, Extracao("1", registros=[
            Registro("R1-1", "1", "Tarefas", "confirmado", "eu", None, [], 0.9, False)], pendencias=[
            Pendencia("P1-1", "1", "Falar com o Walter", "eu", None, "2026-10-28", None),
            Pendencia("P1-2", "1", "Ir ao evento da ACIM", "eu", None, "2026-11-12", None),
            Pendencia("P1-3", "1", "Comprar lâminas", "eu", None, None, None),
            Pendencia("P1-4", "1", "mandar a simulação", "aguardando", "Nilson", None, None),
            Pendencia("P1-5", "1", "Assinar a autorização", "eu", None, "2026-11-02", None),
            Pendencia("P1-6", "1", "Reunião do comercial", "eu", None, "2026-11-02", None, "2026-10-30T18:00"),
            Pendencia("P1-7", "1", "Aguardando Rui mandar o lote", "aguardando", "Rui", None, None)]))
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 30, 20, 0)):
            texto = perai.listar_pendencias(uid)
        self.assertTrue(texto.startswith("✅ Pendências · 7 em aberto"))
        self.assertIn("⚠️ Atrasadas 2\n📌 Falar com o Walter · 28/10\n⏰ 30/10 18:00 · Reunião do comercial", texto)
        self.assertIn("\n⏳ Rui mandar o lote\n", texto)
        self.assertIn("📆 Próximos 7 dias 1\n📌 Assinar a autorização · 02/11", texto)
        self.assertIn("🗂️ Mais adiante 1\n📌 Ir ao evento da ACIM · 12/11", texto)
        self.assertIn("📥 Sem prazo 1\n📌 Comprar lâminas", texto)
        self.assertIn("⏳ Nilson · Mandar a simulação", texto)
        html = formatar_html(texto)
        self.assertIn("<b>⚠️ Atrasadas 2</b>", html)

    def test_divide_no_bloco(self):
        from perai.telegram import _dividir
        texto = "T\n\n" + "\n\n".join("Bloco\n" + "\n".join("x" * 70 for _ in range(15)) for _ in range(6))
        partes = _dividir(texto)
        self.assertGreater(len(partes), 1)
        self.assertTrue(all(p.startswith(("T", "Bloco")) for p in partes))


class TesteEspacamento(unittest.TestCase):
    def test_itens_longos_separados_curtos_juntos(self):
        texto = ("📅 Hoje\n\n🗓️ Compromissos\n06:00 · Academia\n07:30 · Inglês Focado - Meta EUA L8 (Aula / Prática)\n"
                 "14:00 · Dr Leonardo\n\n"
                 "📝 O que aconteceu\nConversou com Allanderson sobre o firewall do Grupo Integrado\nBuscou o carro")
        html = formatar_html(texto)
        self.assertIn("06:00 · Academia\n07:30 · Inglês Focado - Meta EUA L8 (Aula / Prática)\n14:00 · Dr", html)
        self.assertIn("Grupo Integrado\n\nBuscou o carro", html)


class TesteTratarPendencias(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.falso = TelegramFalso()
        self.perai = Perai(self.repo, Cliente(simulador=simulador), MODELOS["simulado"], TRANSCRITORES["simulado"],
                           MODELOS["simulado"], Path(tempfile.mkdtemp()))
        self.bot = Bot(Telegram("T", self.falso), self.perai, {CHAT}, Path(tempfile.mkdtemp()))
        self.uid = self.repo.criar_usuario("Nelson", CHAT)
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        pend = [Pendencia(f"P1-{i}", "1", f"Tarefa número {i}", "eu", None, None, None) for i in range(1, 11)]
        pend.append(Pendencia("P1-11", "1", "Ligar para o Walter", "eu", None, "2026-10-28", None,
                              "2026-10-28T09:00"))
        self.repo.salvar_extracao(self.uid, mid, Extracao("1", registros=[
            Registro("R1-1", "1", "Tarefas", "confirmado", "eu", None, [], 0.9, False)], pendencias=pend))

    def tocar(self, dados):
        return self.bot.tratar(MensagemTelegram(1, CHAT, "N", "botao", dados, botao_id="b", mensagem_id=77))

    def edicoes(self):
        return [d for n, d in self.falso.chamadas if n == "editMessageText"]

    def test_fluxo(self):
        with mock.patch("perai.servico._agora", return_value=datetime(2026, 10, 31, 10, 0)):
            lista = self.bot.tratar(MensagemTelegram(1, CHAT, "N", "texto", "✅ Pendências", mensagem_id=5))
            self.assertEqual(lista.botoes, (("🛠️ Tratar uma a uma", "pt:0"),))
            self.assertIsNone(self.tocar("pt:0"))
            pagina = self.edicoes()[-1]
            self.assertEqual(pagina["message_id"], 77)
            self.assertIn("página 1 de 2", pagina["text"])
            botoes = [b[0] for b in pagina["reply_markup"]["inline_keyboard"]]
            self.assertEqual(len(botoes), 9)  # 8 pendências + "Próximas"
            self.assertTrue(botoes[0]["text"].startswith("⚠️ Ligar para o Walter"))  # atrasada primeiro
            pid = botoes[0]["callback_data"].split(":")[1]

            self.tocar(f"pi:{pid}:0")
            detalhe = self.edicoes()[-1]
            self.assertIn("Lembrete: 28/10 às 09:00", detalhe["text"])
            self.tocar(f"pa:{pid}:0:7")
            p = self.repo.b.pendencias[pid]
            self.assertEqual((p["prazo"], p["lembrete_em"]), ("2026-11-07", "2026-11-07T09:00"))
            self.assertIn("Novo prazo 07/11", self.edicoes()[-1]["text"])

            self.tocar(f"pa:{pid}:0:f")
            self.assertEqual(self.repo.b.pendencias[pid]["situacao"], "concluida")
            outra = self.edicoes()[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
            self.tocar(f"pa:{outra}:0:d")
            self.assertEqual(self.repo.b.pendencias[outra]["situacao"], "descartada")
            self.assertIn("9 em aberto", self.edicoes()[-1]["text"])
            # Tocar de novo numa já fechada não quebra.
            self.tocar(f"pa:{pid}:0:f")
            self.assertIn("já não está aberta", self.edicoes()[-1]["text"])


class TesteTetoESinal(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.falso = TelegramFalso()
        self.perai = Perai(self.repo, Cliente(simulador=simulador), MODELOS["simulado"], TRANSCRITORES["simulado"],
                           MODELOS["simulado"], Path(tempfile.mkdtemp()))
        self.bot = Bot(Telegram("T", self.falso), self.perai, {CHAT}, Path(tempfile.mkdtemp()))
        self.uid = self.repo.criar_usuario("Nelson", CHAT)

    def test_teto_diario(self):
        self.repo.registrar_chamada(self.uid, "consulta", "g", "m", custo_usd=0.6)
        self.assertFalse(self.perai.passou_do_teto(self.uid))
        self.repo.registrar_chamada(self.uid, "consulta", "g", "m", custo_usd=0.5)
        self.assertTrue(self.perai.passou_do_teto(self.uid))
        r = self.bot.tratar(MensagemTelegram(1, CHAT, "N", "texto", "o que tenho hoje?", mensagem_id=3))
        self.assertIn("limite de uso de hoje", r)
        # Comandos e a lista de pendências seguem funcionando.
        self.assertIn("Pendência", self.bot.tratar(MensagemTelegram(2, CHAT, "N", "texto", "✅ Pendências")) +
                      "Pendência")
        with mock.patch.dict("os.environ", {"PERAI_TETO_DIARIO_USD": "0"}):
            self.assertFalse(self.perai.passou_do_teto(self.uid))

    def test_sinal_de_vida(self):
        self.assertIsNone(self.repo.ultimo_sinal("bot"))
        self.repo.sinal_de_vida("bot")
        self.assertIsNotNone(self.repo.ultimo_sinal("bot"))


class TesteAceite(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.repo.aceite_automatico = False
        self.vistos = []

        def sim(tarefa, sistema, usuario):
            self.vistos.append((tarefa, sistema))
            return simulador(tarefa, sistema, usuario)

        self.perai = Perai(self.repo, Cliente(simulador=sim), MODELOS["simulado"], TRANSCRITORES["simulado"],
                           MODELOS["simulado"], Path(tempfile.mkdtemp()))
        self.bot = Bot(None, self.perai, {CHAT}, Path(tempfile.mkdtemp()))

    def msg(self, texto, tipo="texto"):
        return MensagemTelegram(1, CHAT, "Nelson", tipo, texto, mensagem_id=9)

    def test_sem_aceite_nada_e_processado(self):
        r = self.bot.tratar(self.msg("o Rodrigo é da CRTech"))
        self.assertIn("/privacidade", r)
        self.assertIn("/termos", r)
        self.assertEqual(r.botoes, (("✅ Aceito", "ac:termos"),))
        self.assertEqual(self.vistos, [])               # nenhuma chamada ao modelo
        self.assertEqual(self.repo.b.mensagens, {})     # nada guardado
        self.assertIn("Aceito", self.bot.tratar(self.msg("/agenda", "comando")))

    def test_aceitar_e_sensiveis(self):
        uid = self.repo.criar_usuario("Nelson", CHAT)
        r = self.bot.tratar(self.msg("ac:termos", "botao"))
        self.assertIn("religião", r)
        self.assertEqual(self.repo.aceite(uid)["sensiveis"], None)
        r = self.bot.tratar(self.msg("ac:sens0", "botao"))
        self.assertIn("não guardo", r)
        self.assertFalse(self.repo.aceite(uid)["sensiveis"])
        self.bot.tratar(self.msg("fui ao médico hoje e o Rodrigo é da CRTech"))
        sistema = next(s for t, s in self.vistos if t == "extracao")
        self.assertIn("NÃO autorizou guardar dados sensíveis", sistema)
        self.bot.tratar(self.msg("ac:sens1", "botao"))
        self.vistos.clear()
        self.bot.tratar(self.msg("o Paulo é da Gazin"))
        sistema = next(s for t, s in self.vistos if t == "extracao")
        self.assertNotIn("NÃO autorizou", sistema)

    def test_versao_nova_pede_de_novo(self):
        uid = self.repo.criar_usuario("Nelson", CHAT)
        self.repo.aceitar(uid, "2026-01-01")
        self.assertIn("Aceito", self.bot.tratar(self.msg("oi")))

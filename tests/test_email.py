"""E-mail do Peraí (D-072): só remetente cadastrado e autenticado; conteúdo entra como encaminhado."""

import json
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from perai import email_entrada
from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Perai


def montar_email(de="nelson@empresa.com.br", auth="mx.hostinger.com; dmarc=pass header.from=empresa.com.br",
                 assunto="Proposta Integrado", corpo="Segue a proposta. Reunião confirmada para terça.", anexo=None):
    m = EmailMessage()
    m["From"] = f"Nelson <{de}>"
    m["To"] = "perai@prementoria.com"
    m["Subject"] = assunto
    if auth:
        m["Authentication-Results"] = auth
    m.set_content(corpo)
    if anexo:
        m.add_attachment(anexo[1], maintype="application", subtype="xml", filename=anexo[0])
    return m.as_bytes()


class ImapFalso:
    def __init__(self, emails):
        self.emails = {str(i + 1).encode(): e for i, e in enumerate(emails)}
        self.apagados = []

    def select(self, caixa):
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [b" ".join(self.emails)]

    def fetch(self, num, partes):
        return "OK", [(b"1 (RFC822 {1})", self.emails[num]), b")"]

    def store(self, num, *a):
        self.apagados.append(num)

    def expunge(self):
        pass

    def logout(self):
        pass


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.pedidos = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.pedidos.append((tarefa, usuario))
        if tarefa == "extracao":
            return Resposta(json.dumps({"registros": [{"conteudo": "Proposta enviada ao Integrado",
                                                       "natureza": "confirmado", "dito_por": "eu"}],
                                        "cancelar_eventos": [{"busca": "todos"}]}), 1, 1, 1, 0.0)
        return Resposta(json.dumps({"tipo": "registro"}), 1, 1, 1, 0.0)


class TesteAutenticacao(unittest.TestCase):
    def test_regras(self):
        import email as _e
        def m(auth):
            return _e.message_from_bytes(montar_email(auth=auth), policy=_e.policy.default)
        self.assertTrue(email_entrada.autenticado(m("x; dmarc=pass header.from=empresa.com.br"), "n@empresa.com.br"))
        self.assertTrue(email_entrada.autenticado(m("x; dkim=pass header.d=empresa.com.br"), "n@empresa.com.br"))
        self.assertTrue(email_entrada.autenticado(m("x; spf=pass smtp.mailfrom=n@empresa.com.br"), "n@empresa.com.br"))
        self.assertFalse(email_entrada.autenticado(m("x; dkim=pass header.d=golpe.com"), "n@empresa.com.br"))
        self.assertFalse(email_entrada.autenticado(m("x; dmarc=fail; spf=softfail"), "n@empresa.com.br"))
        self.assertFalse(email_entrada.autenticado(m(None), "n@empresa.com.br"))


class TesteCaixa(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.cli = Cli()
        self.p = Perai(self.repo, self.cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                       Path(tempfile.mkdtemp()))
        self.avisos = []
        self.pasta = Path(tempfile.mkdtemp())

    def rodar(self, *emails):
        imap = ImapFalso(list(emails))
        n = email_entrada.processar(self.p, self.pasta, lambda u, r: self.avisos.append((u, str(r))), imap)
        return n, imap

    def test_cadastrado_e_autenticado_vira_mensagem_encaminhada(self):
        self.repo.adicionar_remetente(self.uid, "nelson@empresa.com.br")
        n, imap = self.rodar(montar_email(anexo=("nota.xml", b"<nfeProc/>")))
        self.assertEqual(n, 1)
        self.assertEqual(imap.apagados, [b"1"])          # sai da caixa depois de lido
        self.assertTrue(self.avisos[0][1].startswith("📧 Recebi por e-mail: Proposta Integrado"))
        msgs = list(self.repo.b.mensagens.values())
        self.assertEqual(msgs[0]["canal"], "email")
        self.assertIn("Segue a proposta", msgs[0]["texto_original"])
        self.assertTrue(any("não veio de você" in t for _, t in self.avisos))   # e-mail não mexe na agenda
        self.assertTrue(any("nota.xml" in (m["caminho_arquivo"] or "") or m["tipo"] == "documento" for m in msgs))

    def test_remetente_desconhecido_ou_falso_e_descartado(self):
        n, imap = self.rodar(montar_email())
        self.assertEqual((n, self.repo.b.mensagens, self.avisos), (0, {}, []))
        self.assertEqual(imap.apagados, [b"1"])
        self.repo.adicionar_remetente(self.uid, "nelson@empresa.com.br")
        n, _ = self.rodar(montar_email(auth="x; spf=fail"))
        self.assertEqual(n, 0)
        self.assertIn("não confirmou que foi você", self.avisos[0][1])
        self.assertEqual(self.repo.b.mensagens, {})

    def test_um_endereco_e_de_uma_pessoa_so(self):
        outro = self.repo.criar_usuario("Lerci", 20)
        self.assertTrue(self.repo.adicionar_remetente(self.uid, "nelson@empresa.com.br"))
        self.assertFalse(self.repo.adicionar_remetente(outro, "nelson@empresa.com.br"))


class TesteComando(unittest.TestCase):
    def test_email_cadastra_e_remove(self):
        from perai.bot import Bot
        from perai.telegram import MensagemTelegram
        repo = RepositorioMemoria()
        p = Perai(repo, Cli(), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        repo.aceite_automatico = True
        bot = Bot(None, p, {10}, Path(tempfile.mkdtemp()))
        env = {"PERAI_EMAIL_IMAP_HOST": "imap.x", "PERAI_EMAIL_USUARIO": "perai@prementoria.com",
               "PERAI_EMAIL_SENHA": "s"}
        with mock.patch.dict(os.environ, env):
            msg = lambda t: MensagemTelegram(1, 10, "Nelson", "comando", t)
            self.assertIn("perai@prementoria.com", bot.tratar(msg("/email")))
            self.assertIn("✅ Pronto", bot.tratar(msg("/email adicionar Nelson@Empresa.com.br")))
            uid = repo.usuario_por_telegram(10)
            self.assertEqual(repo.remetentes(uid), ["nelson@empresa.com.br"])
            self.assertIn("🗑️", bot.tratar(msg("/email remover nelson@empresa.com.br")))
            self.assertIn("não parece um e-mail", bot.tratar(msg("/email adicionar xx")))


if __name__ == "__main__":
    unittest.main()

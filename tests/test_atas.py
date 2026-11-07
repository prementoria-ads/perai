"""Atas de conversas com clientes e parceiros (D-078): ata padrão, resumo para o CRM e Google Drive."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import atas
from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.google import ESCOPO_DRIVE, Drive, Google
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai

TRANSCRICAO = ("Transcrição da reunião com o Grupo Integrado em 05/11/2026. Participantes: Nelson, Fabrício "
               "Pelloso (head de inovação) e Allanderson (TI). " + "Conversamos sobre o firewall. " * 40 +
               "Fabrício disse que o orçamento de 2027 está apertado. Ficou combinado que o Nelson manda a "
               "proposta do SonicWall até sexta. Decidimos fazer a prova de conceito em dezembro.")

ATA = {
    "conta": "Grupo Integrado", "tipo": "cliente", "assunto": "Renovação do firewall", "data": "2026-11-05",
    "participantes": [{"nome": "Nelson", "papel": None}, {"nome": "Fabrício Pelloso", "papel": "head de inovação"},
                      {"nome": "Allanderson", "papel": "TI"}],
    "contexto": "Contrato do SonicWall vence no fim de 2027.",
    "assuntos": ["Firewall"], "decisoes": ["Prova de conceito em dezembro"],
    "combinados": [{"quem": "Nelson", "o_que": "Mandar a proposta do SonicWall", "prazo": "sexta"}],
    "proximos_passos": ["Agendar a prova de conceito"], "oportunidades": [], "riscos": [],
    "opinioes": [{"autor": "Fabrício Pelloso", "texto": "o orçamento de 2027 está apertado"}],
    "crm": {"resultado": "Cliente aceitou a prova de conceito", "proximo_passo": None, "despesa": None},
}

EXTRACAO = {
    "entidades": [{"nome": "Grupo Integrado", "tipo": "organizacao"}, {"nome": "Fabrício Pelloso", "tipo": "pessoa"}],
    "registros": [{"conteudo": "Prova de conceito do firewall em dezembro", "natureza": "confirmado",
                   "dito_por": "eu", "entidades": ["Grupo Integrado"], "confianca": 0.9}],
    "pendencias": [{"descricao": "Mandar a proposta do SonicWall ao Grupo Integrado", "responsavel": "eu"}],
}


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.tarefas = []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        self.tarefas.append(tarefa)
        if tarefa == "ata":
            return Resposta(json.dumps(ATA), 1, 1, 1, 0.0)
        if tarefa == "extracao":
            return Resposta(json.dumps(EXTRACAO), 1, 1, 1, 0.0)
        if tarefa == "retorno":
            self.ultimo_retorno = usuario
            return Resposta(json.dumps({"assunto": "Retorno: renovação do firewall",
                                        "corpo": "Olá, Fabrício e Allanderson,\n- Mando a proposta do SonicWall até sexta."}),
                            1, 1, 1, 0.0)
        return Resposta(json.dumps({"tipo": "registro"}), 1, 1, 1, 0.0)


class AgendaFalsa:
    disponivel = True

    def __init__(self, drive=True):
        self.drive = drive
        self.docs = {}

    def conta(self, usuario_id):
        return ("eu@exemplo.com", "refresh", "primary")

    def tem_drive(self, usuario_id):
        return self.drive

    def link_conexao(self, usuario_id):
        return "https://perai.exemplo/google/conectar?t=x"

    def salvar_doc(self, usuario_id, pastas, nome, conteudo_html, pasta_id=None, arquivo_id=None):
        arquivo_id = arquivo_id or f"doc{len(self.docs) + 1}"
        self.docs[arquivo_id] = (pastas, nome, conteudo_html)
        return arquivo_id, f"https://docs.google.com/document/d/{arquivo_id}/edit", pasta_id or "pasta1"

    def criados_futuros(self, usuario_id):
        return []


def montar(agenda=None):
    repo = RepositorioMemoria()
    uid = repo.criar_usuario("Nelson Takemura", 10)
    p = Perai(repo, Cli(), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
              Path(tempfile.mkdtemp()), agenda=agenda)
    return repo, uid, p


class TesteFormato(unittest.TestCase):
    def test_interpretar_limpa_e_nao_inventa(self):
        a = atas.interpretar(json.dumps({"conta": " ", "tipo": "x", "data": "sexta", "decisoes": ["ok", "", 3],
                                         "combinados": [{"quem": "Ana"}]}))
        self.assertIsNone(a["conta"])
        self.assertEqual(a["tipo"], "cliente")
        self.assertIsNone(a["data"])
        self.assertEqual(a["decisoes"], ["ok", "3"])
        self.assertEqual(a["combinados"], [])
        self.assertIsNone(atas.interpretar("não é json"))

    def test_parece_reuniao(self):
        class E:
            tipo = "organizacao"
        self.assertTrue(atas.parece_reuniao("texto", TRANSCRICAO, [E()]))
        self.assertFalse(atas.parece_reuniao("texto", "reunião curta", [E()]))
        self.assertFalse(atas.parece_reuniao("texto", TRANSCRICAO, []))

    def test_crm_tira_o_dono_dos_contatos(self):
        t = atas.texto_crm(atas.interpretar(json.dumps(ATA)), "Grupo Integrado", "Nelson Takemura")
        self.assertIn("Contatos: Fabrício Pelloso (head de inovação), Allanderson (TI)", t)
        self.assertIn("Próximo passo: Agendar a prova de conceito", t)
        self.assertNotIn("Despesa", t)

    def test_html_escapa(self):
        a = atas.interpretar(json.dumps({**ATA, "assunto": "<script>x</script>"}))
        self.assertNotIn("<script>", atas.html_ata(a, "Conta", "mensagem"))


class TesteFluxo(unittest.TestCase):
    def test_transcricao_oferece_ata_e_monta(self):
        agenda = AgendaFalsa()
        repo, uid, p = montar(agenda)
        r = p.receber(uid, Entrada("telegram", "texto", TRANSCRICAO))
        botao = [d for _, d in r.botoes if d.startswith("at:")]
        self.assertEqual(len(botao), 1)
        ata = p.gerar_ata(uid, botao[0][3:])
        self.assertIn("📄 Ata · Grupo Integrado · 05/11/2026", ata)
        self.assertIn("• Fabrício Pelloso: o orçamento de 2027 está apertado", ata)
        dados = dict((r, d) for r, d in ata.botoes)
        ata_id = dados["💾 Salvar no Drive"][3:]
        crm = p.resumo_crm(uid, ata_id)
        self.assertIn("Conta: Grupo Integrado", crm)
        self.assertIn("Resultado: Cliente aceitou a prova de conceito", crm)
        # Drive: ata e documento mestre, na pasta da conta, com a pendência aberta que fala dela.
        s = p.salvar_ata_drive(uid, ata_id)
        self.assertIn("Peraí › Clientes e parceiros › Grupo Integrado", s)
        self.assertEqual(len(agenda.docs), 2)
        pastas, nome, mestre = agenda.docs["doc2"]
        self.assertEqual(pastas, ["Clientes e parceiros", "Grupo Integrado"])
        self.assertEqual(nome, "Grupo Integrado — Documento mestre")
        self.assertIn("docs.google.com/document/d/doc1", mestre)
        self.assertIn("Mandar a proposta do SonicWall", mestre)
        # Salvar de novo atualiza os mesmos documentos, sem duplicar.
        p.salvar_ata_drive(uid, ata_id)
        self.assertEqual(len(agenda.docs), 2)

    def test_sem_permissao_do_drive_pede_autorizacao(self):
        repo, uid, p = montar(AgendaFalsa(drive=False))
        mid = repo.salvar_mensagem(uid, __import__("perai.armazenamento", fromlist=["NovaMensagem"]).NovaMensagem(
            canal="telegram", tipo="texto", texto_original=TRANSCRICAO))
        ata = p.gerar_ata(uid, mid)
        ata_id = dict(ata.botoes)["💾 Salvar no Drive"][3:]
        r = p.salvar_ata_drive(uid, ata_id)
        self.assertIn("google/conectar", r)
        self.assertIn("só vê e muda o que ele mesmo criar", r)

    def test_trocar_conta_por_nome(self):
        repo, uid, p = montar(AgendaFalsa())
        from perai.armazenamento import NovaMensagem
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original=TRANSCRICAO))
        ata_id = dict(p.gerar_ata(uid, mid).botoes)["💾 Salvar no Drive"][3:]
        opcoes = p.trocar_conta_ata(uid, ata_id)
        self.assertEqual([r for r, _ in opcoes.botoes], ["Grupo Integrado", "✏️ Outra"])
        self.assertIn("próxima mensagem", p.escolher_conta_ata(uid, f"{ata_id}:o"))
        r = p.receber(uid, Entrada("telegram", "texto", "Holding Vogue"))
        self.assertIn("a ata agora é de Holding Vogue", r)
        self.assertEqual(p._nome_conta(uid, repo.ata(uid, ata_id)["conta_id"])["nome"], "Holding Vogue")
        # Mesmo nome com outra grafia não cria conta nova.
        self.assertEqual(p._conta(uid, "holding vogue", "cliente"), repo.ata(uid, ata_id)["conta_id"])

    def test_mensagem_de_outro_usuario_nao_vira_ata(self):
        repo, uid, p = montar()
        outro = repo.criar_usuario("Lerci", 11)
        from perai.armazenamento import NovaMensagem
        mid = repo.salvar_mensagem(outro, NovaMensagem(canal="telegram", tipo="texto", texto_original=TRANSCRICAO))
        self.assertEqual(p.gerar_ata(uid, mid), "Não achei o texto dessa mensagem.")


class TesteRetorno(unittest.TestCase):
    def test_retorno_so_com_combinados_e_sem_opinioes(self):
        repo, uid, p = montar()
        from perai.armazenamento import NovaMensagem
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original=TRANSCRICAO))
        botoes = dict(p.gerar_ata(uid, mid).botoes)
        r = p.preparar_retorno(uid, botoes["✉️ Preparar retorno"][3:])
        self.assertIn("Assunto: Retorno: renovação do firewall", r)
        self.assertIn("o Peraí não envia nada", r)
        enviado = p.cliente.ultimo_retorno
        self.assertIn("Mandar a proposta do SonicWall", enviado)
        self.assertNotIn("orçamento de 2027", enviado)   # opinião não vai para o retorno

    def test_ata_sem_combinados(self):
        repo, uid, p = montar()
        ata_id = repo.salvar_ata(uid, repo.salvar_mensagem(uid, __import__("perai.armazenamento", fromlist=["x"])
                                                          .NovaMensagem(canal="telegram", tipo="texto",
                                                                        texto_original="x")),
                                 None, atas.interpretar(json.dumps({"assunto": "Café"})))
        self.assertIn("não tem combinados", p.preparar_retorno(uid, ata_id))


class TesteDrive(unittest.TestCase):
    def test_pasta_e_documento(self):
        pedidos = []

        def transporte(metodo, url, cab, corpo):
            pedidos.append((metodo, url, cab, corpo))
            if "oauth2" in url:
                return 200, json.dumps({"access_token": "a", "expires_in": 3600}).encode()
            if metodo == "GET":
                return 200, b'{"files": []}'
            if "upload" in url and metodo == "POST":
                return 200, b'{"id": "d1", "webViewLink": "https://docs.google.com/document/d/d1/edit"}'
            if "upload" in url and metodo == "PATCH":
                return 200, b'{"id": "d1"}'
            return 200, b'{"id": "p1"}'

        d = Drive(Google("id", "segredo", "https://x/retorno", transporte))
        self.assertEqual(d.pasta("r", "Grupo d'Integrado"), "p1")
        self.assertIn("Grupo+d%5C%27Integrado", pedidos[1][1])  # aspas escapadas na busca
        doc, link = d.criar_doc("r", "Ata", "<p>oi</p>", "p1")
        self.assertEqual((doc, link), ("d1", "https://docs.google.com/document/d/d1/edit"))
        metodo, url, cab, corpo = pedidos[-1]
        self.assertIn("multipart/related", cab["Content-Type"])
        self.assertIn(b"application/vnd.google-apps.document", corpo)
        self.assertIn("/d/d1/", d.atualizar_doc("r", "d1", "<p>novo</p>"))

    def test_escopo_do_drive_so_arquivos_do_perai(self):
        from perai.google import ESCOPOS
        self.assertIn(ESCOPO_DRIVE, ESCOPOS)
        self.assertTrue(ESCOPO_DRIVE.endswith("/drive.file"))


if __name__ == "__main__":
    unittest.main()

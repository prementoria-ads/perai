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
        self.assertIn("🧾 Despesa: R$ 182,40 · Posto Exemplo · 27/10 18:42 · combustível", r)   # hora da nota (D-112)
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


class TesteVinculoEViagem(unittest.TestCase):
    """Despesa ligada a compromissos da agenda e a clientes; relatório para o Onfly."""

    def setUp(self):
        from cryptography.fernet import Fernet
        from perai.agenda import Agenda
        from perai.google import Cifra, Google
        from tests.test_agenda import GoogleFalso
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        self.google = GoogleFalso()
        self.agenda = Agenda(self.repo, Google("id", "s", "https://x/google/retorno", self.google),
                             Cifra(Fernet.generate_key().decode()), "https://x")
        link = self.agenda.link_conexao(self.uid)
        self.agenda.concluir(link.split("t=", 1)[1], "codigo-bom")
        self.dia = "2026-10-29"  # o GoogleFalso devolve o "Dentista" nesse dia

    def perai(self, respostas):
        self.cliente = ClienteRoteiro(respostas)
        return Perai(self.repo, self.cliente, MODELOS["simulado"], TRANSCRITORES["simulado"],
                     MODELOS["simulado"], Path(tempfile.mkdtemp()), agenda=self.agenda)

    def test_botoes_de_compromisso_e_relatorio(self):
        from datetime import date
        from perai.bot import Bot
        from perai.telegram import MensagemTelegram
        p = self.perai([
            {"despesas": [{"descricao": "Estacionamento", "valor": "25,00", "categoria": "transporte",
                           "data": self.dia, "contexto": "profissional"}]},
            {"despesas": [{"descricao": "Almoço", "valor": "89,90", "categoria": "alimentacao",
                           "data": self.dia, "clientes": ["Gazin", "Romagnole"]}]},
        ])
        r = p.receber(self.uid, Entrada("telegram", "texto", "paguei 25 de estacionamento"))
        self.assertIn("A que essa despesa se liga?", r)
        self.assertEqual(r.botoes[0][0], "🧳 Dia de viagem (vários clientes)")
        self.assertEqual(r.botoes[1][0], "🔗 09:00 Dentista")
        self.assertTrue(r.botoes[-1][1].endswith(":0"))

        class TG:
            def __init__(s): s.tirados = []
            def enviar(s, *a, **k): pass
            def responder_botao(s, *a): pass
            def tirar_botoes(s, chat, msg): s.tirados.append(msg)
        tg = TG()
        bot = Bot(tg, p, {10}, Path(tempfile.mkdtemp()))
        resp = bot.tratar(MensagemTelegram(1, 10, "N", "botao", r.botoes[1][1], botao_id="q", mensagem_id=5))
        self.assertIn("Liguei a despesa a: Dentista", resp)
        self.assertEqual(tg.tirados, [])  # pode escolher mais de um
        resp = bot.tratar(MensagemTelegram(2, 10, "N", "botao", r.botoes[-1][1], botao_id="q", mensagem_id=5))
        self.assertIn("Pronto: Dentista", resp)
        self.assertEqual(tg.tirados, [5])

        r = p.receber(self.uid, Entrada("telegram", "texto", "almoço de 89,90 com a Gazin e a Romagnole"))
        self.assertIn("🤝 Cliente: Gazin, Romagnole", r)
        self.assertNotIn("A que essa despesa se liga", r)  # cliente já dito: não pergunta

        rel = bot.tratar(MensagemTelegram(3, 10, "N", "comando", "/viagem 28/10 30/10"))
        self.assertIn("🧳 Relatório de viagem · 28 a 30 out26", rel)
        self.assertIn("R$ 25,00 · transporte · Estacionamento · 🔗 09:00 Dentista", rel)
        self.assertIn("R$ 89,90 · alimentação · Almoço · 🔗 Gazin; Romagnole", rel)
        self.assertIn("• Total: R$ 114,90", rel)
        self.assertIn("Não encontrei despesas", p.relatorio_viagem(self.uid, date(2026, 1, 1), date(2026, 1, 2)))


class TesteDespesaNaViagem(unittest.TestCase):
    def test_avisa_que_entra_no_relatorio(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        repo.salvar_viagem(uid, None, "Prospecção em Foz · 5 a 7 out26", "2026-10-05", "2026-10-07",
                           ["Foz"], ["Júlio"], "prospecção")
        cliente = ClienteRoteiro([{"despesas": [{"descricao": "Pedágio", "valor": "17,40", "data": "2026-10-07",
                                                 "categoria": "transporte", "contexto": "profissional"}]}])
        p = Perai(repo, cliente, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        r = p.receber(uid, Entrada("telegram", "texto", "pedágio de 17,40 em 07/10"))
        self.assertIn("🧳 Entra no relatório da viagem: Prospecção em Foz · 5 a 7 out26", r)


NFE_2_ITENS = NFE.replace(
    '<det nItem="1"><prod><xProd>GASOLINA COMUM</xProd><vProd>182.40</vProd></prod></det>',
    '<det nItem="1"><prod><xProd>GASOLINA COMUM</xProd><vProd>182.40</vProd></prod></det>'
    '<det nItem="2"><prod><xProd>AGUA MINERAL 500ML</xProd><vProd>5.00</vProd></prod></det>'
    '<det nItem="3"><prod><xProd>SALGADO</xProd><vProd>9.50</vProd></prod></det>'
).replace("<vNF>182.40</vNF>", "<vNF>195.90</vNF>")  # com R$ 1,00 de desconto


class TesteItensDaNota(unittest.TestCase):
    def test_escolher_so_o_combustivel(self):
        from perai.bot import Bot
        from perai.telegram import MensagemTelegram
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        p = Perai(repo, ClienteRoteiro([]), MODELOS["simulado"], TRANSCRITORES["simulado"],
                  MODELOS["simulado"], Path(tempfile.mkdtemp()))
        pasta = Path(tempfile.mkdtemp())
        (pasta / "n.xml").write_text(NFE_2_ITENS, encoding="utf-8")
        r = p.receber(uid, Entrada("telegram", "documento", None, pasta / "n.xml"))
        self.assertIn("📋 A nota tem 3 itens:", r)
        self.assertIn("  2. AGUA MINERAL 500ML · R$ 5,00", r)
        self.assertEqual(r.botoes[0], ("✅ GASOLINA COMUM · R$ 182,40", r.botoes[0][1]))
        self.assertEqual(r.botoes[-1][0], "✔️ Confirmar itens")
        self.assertNotIn("A que essa despesa se liga", r)  # pergunta depois de confirmar

        class TG:
            def __init__(s): s.trocados, s.tirados = [], []
            def enviar(s, *a, **k): pass
            def responder_botao(s, *a): pass
            def tirar_botoes(s, chat, msg): s.tirados.append(msg)
            def trocar_botoes(s, chat, msg, botoes): s.trocados.append(botoes)
        tg = TG()
        bot = Bot(tg, p, {10}, Path(tempfile.mkdtemp()))
        clicar = lambda dados: bot.tratar(MensagemTelegram(1, 10, "N", "botao", dados, botao_id="q", mensagem_id=4))
        d = next(iter(repo.b.despesas.values()))
        self.assertIsNone(clicar(r.botoes[1][1]))          # tira a água
        self.assertIsNone(clicar(r.botoes[2][1]))          # tira o salgado
        # Desconto de R$ 1,00 na nota: vai proporcional (182,40 × 195,90 / 196,90 = 181,47; D-108).
        self.assertEqual(d["valor_centavos"], 18147)
        self.assertTrue(tg.trocados[-1][1][0].startswith("⬜ AGUA"))
        self.assertEqual(tg.tirados, [])
        resp = clicar(r.botoes[-1][1])
        self.assertIn("🧾 Lançamento: R$ 181,47 · 1 de 3 itens: GASOLINA COMUM, com o desconto da nota proporcional", resp)
        self.assertIn("A que essa despesa se liga?", resp)
        self.assertIn("🧳 Dia de viagem (vários clientes)", [b[0] for b in resp.botoes])
        self.assertEqual(tg.tirados, [4])
        # Volta tudo: vale o total da nota (com desconto), não a soma dos itens.
        for n in (2, 3):
            p.escolher_item(uid, f"{d['id']}:{n}")
        self.assertEqual(d["valor_centavos"], 19590)
        self.assertEqual([c["valor_novo"] for c in repo.b.correcoes if c["tabela"] == "despesas"],
                         ["19093", "18147", "18645", "19590"])


def _nfe_mercado(produtos: list[tuple[str, str]], total: str) -> str:
    """NFC-e fictícia de mercado com muitos itens (D-108)."""
    dets = "".join(f'<det nItem="{n}"><prod><xProd>{d}</xProd><qCom>1</qCom><uCom>UN</uCom>'
                   f'<vUnCom>{v}</vUnCom><vProd>{v}</vProd></prod></det>'
                   for n, (d, v) in enumerate(produtos, start=1))
    return (NFE.replace('<det nItem="1"><prod><xProd>GASOLINA COMUM</xProd><vProd>182.40</vProd></prod></det>', dets)
            .replace("<vNF>182.40</vNF>", f"<vNF>{total}</vNF>").replace("Posto Exemplo", "Mercado Exemplo"))


class TesteNotaLonga(unittest.TestCase):
    """D-108: nota com mais de 12 itens chega inteira, com desconto e baixa na lista de compras."""

    def setUp(self):
        from perai import extracao
        from perai.armazenamento import NovaMensagem
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto",
                                                               texto_original="lista",
                                                               data_situacao=_agora().date().isoformat()))
        self.repo.salvar_extracao(self.uid, mid, extracao.Extracao("1", pendencias=[extracao.Pendencia(
            "P1-1", "1", "Comprar Banana, Detergente, Farinha de trigo e Sabão em pó", "eu", None, None, None)]))
        # 30 itens fictícios: 3 da lista + 27 outros; soma 329,97, pago 300,00 (desconto de 29,97).
        produtos = [("BANANA PRATA KG", "10.00"), ("DETERG. LIQ. YPE 500ML", "10.00"),
                    ("FAR. TRIGO 1KG", "10.00")] + [(f"PRODUTO TESTE {n}", "11.11") for n in range(4, 31)]
        soma = sum(int(v.replace(".", "")) for _, v in produtos)
        self.assertEqual(soma, 32997)
        self.pasta = Path(tempfile.mkdtemp())
        (self.pasta / "m.xml").write_text(_nfe_mercado(produtos, "300.00"), encoding="utf-8")

    def perai(self, respostas):
        self.cliente = ClienteRoteiro(respostas)
        return Perai(self.repo, self.cliente, MODELOS["simulado"], TRANSCRITORES["simulado"],
                     MODELOS["simulado"], Path(tempfile.mkdtemp()))

    def test_todos_os_itens_paginas_desconto_e_lista(self):
        p = self.perai([{"comprados": [{"item": "Banana", "nota": ["N1"]}, {"item": "Detergente", "nota": ["[N2]"]},
                                       {"item": "Farinha de trigo", "nota": ["N3"]},
                                       {"item": "Sabão em pó", "nota": ["N99"]},       # código inexistente
                                       {"item": "Arroz", "nota": ["N4"]}]}])           # não está na lista
        r = p.receber(self.uid, Entrada("telegram", "documento", None, self.pasta / "m.xml"))
        self.assertIn("📋 A nota tem 30 itens:", r)
        self.assertIn("  30. PRODUTO TESTE 30 · R$ 11,11", r)
        self.assertIn("desconto R$ 29,97, pago R$ 300,00", r)
        d = next(iter(self.repo.b.despesas.values()))
        self.assertEqual(len(self.repo.itens_despesa(self.uid, d["id"])), 30)
        # Páginas de 10: itens 1 a 10, Próximos, Confirmar e a baixa na lista.
        rotulos = [b[0] for b in r.botoes]
        self.assertTrue(rotulos[0].startswith("✅ 1. BANANA"))
        self.assertIn("▶️ Próximos itens (2/3)", rotulos)
        self.assertNotIn("◀️ Itens anteriores", rotulos)
        self.assertEqual(rotulos[-2], "✔️ Confirmar itens")
        self.assertEqual(rotulos[-1], "🛒 Tirar 3 da lista de compras")
        self.assertIn("a nota tem 3: Banana (BANANA PRATA KG), Detergente (DETERG. LIQ. YPE 500ML), "
                      "Farinha de trigo (FAR. TRIGO 1KG)", r)
        # Nada sai da lista sem o botão.
        _, pend = self.repo.memoria(self.uid)
        self.assertIn("Banana", pend[0].descricao)
        # Página 3 e tirar o item 25: os botões continuam na página 3.
        pag = p.escolher_item(self.uid, f"{d['id']}:p2")
        self.assertTrue(pag.trocar)
        self.assertTrue(pag.botoes[0][0].startswith("✅ 21. PRODUTO TESTE 21"))
        self.assertIn("◀️ Itens anteriores", [b[0] for b in pag.botoes])
        r2 = p.escolher_item(self.uid, f"{d['id']}:25")
        self.assertTrue(r2.botoes[4][0].startswith("⬜ 25."))
        # Fica de fora 11,11 de 329,97: 318,86 × 300,00 / 329,97 = 289,90.
        self.assertEqual(d["valor_centavos"], 28990)
        fim = p.escolher_item(self.uid, f"{d['id']}:0")
        self.assertIn("29 de 30 itens (ficaram de fora: PRODUTO TESTE 25), com o desconto da nota proporcional",
                      fim)
        # Baixa na lista: tira os 3 e deixa o sabão em pó.
        self.assertEqual(p.baixar_lista(self.uid, d["id"]), "🛒 Tirei da lista: Banana, Detergente, Farinha de trigo.")
        _, pend = self.repo.memoria(self.uid)
        self.assertEqual(pend[0].descricao, "Comprar Sabão em pó")
        self.assertIn("já foi usada", p.baixar_lista(self.uid, d["id"]))

    def test_lista_toda_comprada_conclui_e_sem_lista_nao_chama_ia(self):
        p = self.perai([{"comprados": [{"item": i, "nota": ["N1"]} for i in
                                       ("Banana", "Detergente", "Farinha de trigo", "Sabão em pó")]}])
        p.receber(self.uid, Entrada("telegram", "documento", None, self.pasta / "m.xml"))
        d = next(iter(self.repo.b.despesas.values()))
        self.assertIn("ficou vazia e foi concluída", p.baixar_lista(self.uid, d["id"]))
        _, pend = self.repo.memoria(self.uid)
        self.assertEqual(pend, [])
        # Sem lista aberta: nenhuma chamada de IA para a nota seguinte.
        outra = Path(tempfile.mkdtemp()) / "o.xml"
        outra.write_text(_nfe_mercado([("PAO", "5.00"), ("LEITE", "6.00")], "11.00")
                         .replace("41261012345678000199650010000012341000012345",
                                  "41261012345678000199650010000099991000099999"), encoding="utf-8")
        antes = len(self.cliente.pedidos)
        p.receber(self.uid, Entrada("telegram", "documento", None, outra))
        self.assertEqual(len(self.cliente.pedidos), antes)

    def test_extracao_nao_corta_em_30_e_ignora_consumidor(self):
        from perai import extracao
        dados = {"despesas": [{"descricao": "Mercado", "valor": "557,10",
                               "itens": [{"descricao": f"ITEM {n}", "valor": "1,00"} for n in range(45)]}]}
        ext = extracao.interpretar("1", json.dumps(dados))
        self.assertEqual(len(ext.despesas[0].itens), 45)
        self.assertIn("nome, o CPF e o endereço do consumidor", extracao.SISTEMA_EXTRACAO)


class TesteHoraLocalDestino(unittest.TestCase):
    """D-112: hora e local da despesa, refeição pela hora, e ligação a conta ou dia de viagem."""

    def test_refeicao_pela_hora(self):
        r = despesas.refeicao
        self.assertEqual([r(h, "alimentacao") for h in ("07:15", "10:29", "10:30", "14:59", "15:00", "18:00",
                                                         "23:40", "01:10")],
                         ["café da manhã", "café da manhã", "almoço", "almoço", "lanche", "jantar", "jantar",
                          "jantar"])
        self.assertIsNone(r("12:00", "combustivel"))
        self.assertIsNone(r(None, "alimentacao"))

    def test_categoria_pelo_emitente_e_pelos_itens(self):
        nota = lambda emit, itens: despesas.NotaFiscal(emit, None, None, None, None, [(i, 100) for i in itens])
        self.assertEqual(despesas.categoria_por_itens(nota("Supermercado Exemplo", ["CAFE PILAO", "PAO"])), "mercado")
        self.assertEqual(despesas.categoria_por_itens(nota("EXEMPLO E CIA LTDA", ["AGUA", "RISOTO", "TX"])),
                         "alimentacao")
        self.assertEqual(despesas.categoria_por_itens(nota("EXEMPLO E CIA LTDA", ["PARAFUSO"])), "outros")

    def test_hora_dita_ou_da_nota(self):
        from perai import extracao
        ext = extracao.interpretar("1", json.dumps({"despesas": [
            {"descricao": "Almoço", "valor": "173,80", "categoria": "alimentacao", "hora": "12h54",
             "local": "Umuarama"}]}))
        self.assertEqual((ext.despesas[0].hora, ext.despesas[0].local), ("12:54", "Umuarama"))
        self.assertIsNone(extracao._hora("25:00"))

    def test_conta_do_dia_e_dia_de_viagem(self):
        from perai.google import EventoAgenda as Evento
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        repo.criar_conta_relacionamento(uid, "Zaeli", "cliente")
        repo.criar_conta_relacionamento(uid, "Gazin", "cliente")
        p = Perai(repo, ClienteRoteiro([{"despesas": [
            {"descricao": "Almoço", "valor": "173,80", "categoria": "alimentacao", "data": "2026-11-09",
             "hora": "12:54", "local": "Umuarama", "estabelecimento": "Restaurante Exemplo"}]}]),
            MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"], Path(tempfile.mkdtemp()))
        eventos = [Evento("e1", "Treino", "2026-11-09T06:00", None),
                   Evento("e2", "Reunião na Zaeli", "2026-11-09T11:00", None),
                   Evento("e3", "Visita Gazin", "2026-11-09T15:30", None)]
        p._agenda_conectada = lambda u: True
        class AgendaFalsa:
            def eventos_em(s, u, d):
                return list(eventos)

            def __getattr__(s, nome):   # o resto da agenda não interessa aqui
                return lambda *a, **k: []
        p.agenda = AgendaFalsa()
        r = p.receber(uid, Entrada("telegram", "texto", "almoço de 173,80 no Restaurante Exemplo"))
        self.assertIn("🧾 Despesa: R$ 173,80 · Restaurante Exemplo · 09/11 12:54 · alimentação · almoço · "
                      "📍 Umuarama", r)
        rotulos = [b[0] for b in r.botoes]
        self.assertEqual(rotulos[:3], ["🏢 Zaeli", "🏢 Gazin", "🧳 Dia de viagem (vários clientes)"])
        self.assertIn("🔗 11:00 Reunião na Zaeli", rotulos)
        d = next(iter(repo.b.despesas.values()))
        self.assertIn("Liguei a despesa a Zaeli", p.vincular_despesa_conta(uid, r.botoes[0][1][3:]))
        self.assertEqual([v["cliente"] for v in repo.vinculos_despesas(uid, [d["id"]])[d["id"]]], ["Zaeli"])
        self.assertIn("Anotei o dia como viagem: Visitas a clientes em Umuarama", p.viagem_do_dia(uid, d["id"]))
        self.assertIn("já está na viagem", p.viagem_do_dia(uid, d["id"]))
        rel = p.relatorio_viagem(uid, __import__("datetime").date(2026, 11, 9), __import__("datetime").date(2026, 11, 9))
        self.assertIn("• 12:54 R$ 173,80 · almoço · Restaurante Exemplo (Umuarama) · 🔗 Zaeli", rel)


class TesteCancelarRepetidas(unittest.TestCase):
    """D-113: a IA vê o que distingue as despesas recentes e cancela todas quando o pedido é no plural."""

    def test_recentes_com_estabelecimento_hora_e_ligacao(self):
        from perai import extracao
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cliente = ClienteRoteiro([
            {"despesas": [{"descricao": "Almoço", "valor": "173,80", "categoria": "alimentacao", "data": "2026-11-09",
                           "hora": "13:53", "local": "Umuarama", "estabelecimento": "Restaurante Exemplo"}]},
            {"despesas": [{"descricao": "Almoço", "valor": "173,80", "categoria": "alimentacao", "data": "2026-11-09",
                           "estabelecimento": "Restaurante Exemplo"}]},
            {"ajustes_despesas": [{"despesa": "G1", "situacao": "cancelada"},
                                  {"despesa": "G2", "situacao": "cancelada"}]}])
        p = Perai(repo, cliente, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        p.receber(uid, Entrada("telegram", "texto", "almoço de 173,80"))
        p.receber(uid, Entrada("telegram", "texto", "almoço de 173,80 de novo"))
        primeira = next(d for d in repo.b.despesas.values() if d.get("hora"))
        repo.vincular_despesa(uid, primeira["id"], "cliente", cliente="Zaeli")
        r = p.receber(uid, Entrada("telegram", "texto", "lancei errado as notas do Restaurante Exemplo de R$ 173,80"))
        pedido = cliente.pedidos[-1][1]
        self.assertIn("Restaurante Exemplo | R$ 173,80 | 2026-11-09 13:53 | alimentacao | sem contexto | Umuarama | "
                      "ligada a: Zaeli", pedido)
        self.assertIn("Plural ou \"todas\"", extracao.SISTEMA_EXTRACAO)
        self.assertEqual(r.count("🗑️ Tirei a despesa"), 2)
        self.assertEqual({d["situacao"] for d in repo.b.despesas.values()}, {"cancelada"})


class TesteDespesaAntigaCitada(unittest.TestCase):
    def test_citada_entra_mesmo_fora_das_10_mais_recentes(self):
        from perai.servico import _despesas_para_ajuste
        todas = [{"descricao": f"Compra {n}", "estabelecimento": f"Loja {n}", "valor_centavos": 1000 + n}
                 for n in range(15)]
        todas.append({"descricao": "Compra em GNANN e CIA LTDA", "estabelecimento": "GNANN e CIA LTDA",
                      "valor_centavos": 17380})
        todas.append({"descricao": "Outra", "estabelecimento": "Posto", "valor_centavos": 17380})
        escolhidas = _despesas_para_ajuste(todas, "lancei errado as notas do Gnann de R$ 173,80")
        self.assertEqual(len(escolhidas), 12)
        self.assertIn("GNANN e CIA LTDA", [d["estabelecimento"] for d in escolhidas])
        self.assertEqual(len(_despesas_para_ajuste(todas, "oi")), 10)

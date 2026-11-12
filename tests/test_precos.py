"""Histórico de preços (D-084): ofertas e preços pagos, comparados pela mesma referência."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import precos
from perai.armazenamento import RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente, Resposta
from perai.servico import Entrada, Perai

PANFLETO = ("Ofertas do Supermercados Planalto válidas para 09/11/2026: coxa e sobrecoxa de frango congelada "
            "R$ 7,99/kg, leite condensado Frimesa 395g R$ 5,99, amaciante Amacitel 2L R$ 7,59, "
            "papel higiênico Mili c/12 R$ 15,99.")
ITENS = {"loja": "Planalto", "cidade": "Umuarama", "validade": "2026-11-09", "itens": [
    {"produto": "coxa e sobrecoxa de frango congelada", "preco": 7.99, "por": "kg"},
    {"produto": "leite condensado", "marca": "Frimesa", "quantidade": 395, "unidade": "g", "preco": 5.99, "por": "un"},
    {"produto": "amaciante", "marca": "Amacitel", "quantidade": 2, "unidade": "l", "preco": "7,59", "por": "un"},
    {"produto": "feijão", "preco": None, "por": "un", "condicao": "20% na 2ª unidade"}]}


class Cli(Cliente):
    def __init__(self):
        super().__init__()
        self.consultas, self.imagens, self.precos_vazio = [], [], False

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        if tarefa == "precos_imagem":
            self.imagens.append(imagem)
            return Resposta(json.dumps(ITENS), 1, 1, 1, 0.0)
        if tarefa == "leitura_imagem":
            return Resposta("Encarte do Amigão, Terça e Quarta Verde: frutas e legumes com ofertas, "
                            "como tomate a R$ 4,99/kg.", 1, 1, 1, 0.0)
        if tarefa == "precos":
            if self.precos_vazio:
                return Resposta('{"itens": []}', 1, 1, 1, 0.0)
            return Resposta(json.dumps(ITENS), 1, 1, 1, 0.0)
        if tarefa == "extracao":
            return Resposta(json.dumps({"registros": [{"conteudo": "Ofertas do Planalto em 09/11",
                                                       "natureza": "confirmado", "dito_por": "Planalto"}]}),
                            1, 1, 1, 0.0)
        if tarefa == "roteamento":
            return Resposta(json.dumps({"tipo": "consulta" if "?" in usuario[-80:] else "registro"}), 1, 1, 1, 0.0)
        if tarefa == "consulta":
            self.consultas.append(usuario)
            return Resposta(json.dumps({"resposta": "O frango a R$ 7,99/kg é o menor registrado.",
                                        "registros_citados": ["Q1"], "sem_informacao": False}), 1, 1, 1, 0.0)
        return Resposta("{}", 1, 1, 1, 0.0)


class TesteReferencia(unittest.TestCase):
    def test_preco_por_kg_e_litro(self):
        itens = precos.interpretar(json.dumps(ITENS))
        self.assertEqual(len(itens), 3)            # desconto sem preço final não vira item
        frango, leite, amaciante = itens
        self.assertEqual((frango["ref_centavos"], frango["ref_unidade"]), (799, "kg"))
        self.assertEqual((leite["ref_centavos"], leite["ref_unidade"]), (1516, "kg"))   # 5,99 / 0,395
        self.assertEqual((amaciante["ref_centavos"], amaciante["ref_unidade"]), (380, "l"))
        self.assertIn("equivale a R$ 15,16/kg", precos.linha("Q2", {**leite, "origem": "oferta"}))

    def test_parece_oferta(self):
        self.assertTrue(precos.parece_oferta(PANFLETO))
        self.assertFalse(precos.parece_oferta("Paguei R$ 50 no almoço"))


class TesteFluxo(unittest.TestCase):
    def test_panfleto_guarda_e_pergunta_compara(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cli = Cli()
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        r = p.receber(uid, Entrada("telegram", "texto", PANFLETO))
        self.assertIn("🏷️ Guardei 3 preço(s) para comparar (Planalto)", r)
        guardados = repo.precos(uid, "2026-01-01")
        self.assertEqual({x["origem"] for x in guardados}, {"oferta"})
        self.assertTrue(all(x["mensagem_id"] for x in guardados))      # fonte (D-040)
        r = p.receber(uid, Entrada("telegram", "texto", "o frango está barato?"))
        self.assertIn("menor registrado", r)
        enviado = cli.consultas[-1]
        self.assertIn("(preço; oferta de panfleto; Planalto, Umuarama; 2026-11-09) coxa e sobrecoxa", enviado)
        self.assertNotIn("[Q2]", enviado)                              # só o produto perguntado
        cp = next(d for _, d in r.botoes if d.startswith("cp:"))
        texto = p.compartilhar_precos(uid, cp[3:])
        self.assertIn("```", texto)
        self.assertIn("• Coxa e sobrecoxa de frango congelada: R$ 7,99/kg — Planalto, Umuarama (oferta até 09/11)",
                      texto)
        self.assertNotIn("Leite", texto)                               # só o que a resposta citou
        self.assertIn("expirou", p.compartilhar_precos(uid, "outra"))

    def test_lista_de_compras_procura_cada_item(self):
        # D-088: a pergunta não cita produto, mas a lista aberta cita: os preços dos itens entram.
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cli = Cli()
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        p.receber(uid, Entrada("telegram", "texto", PANFLETO))
        from perai.armazenamento import NovaMensagem
        from perai.extracao import Extracao, Pendencia
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="lista"))
        repo.salvar_extracao(uid, mid, Extracao(mid, pendencias=[Pendencia(
            "P1-1", mid, "Comprar Ovos, Amaciante e Leite zero lactose", "eu", None, None, None)]))
        p.receber(uid, Entrada("telegram", "texto", "onde minha lista de compras está mais barata?"))
        enviado = cli.consultas[-1]
        self.assertIn("amaciante Amacitel", enviado)
        precos_enviados = [l for l in enviado.splitlines() if l.startswith("[Q")]
        self.assertFalse(any("coxa" in l for l in precos_enviados))   # fora da lista

    def test_panfleto_em_pdf_le_os_precos_do_arquivo(self):
        # O leitor de PDF resume o encarte; os preços precisam sair do próprio arquivo.
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cli = Cli()
        cli.precos_vazio = True
        pasta = Path(tempfile.mkdtemp())
        pdf = pasta / "encarte.pdf"
        pdf.write_bytes(b"%PDF-1.4 imagem")
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"], pasta)
        r = p.receber(uid, Entrada("telegram", "documento", "", pdf, nome_original="Paranavaí.pdf"))
        self.assertIn("🏷️ Guardei 3 preço(s)", r)
        self.assertEqual(cli.imagens, [pdf])

    def test_mesmo_encarte_duas_vezes_nao_duplica(self):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        p = Perai(repo, Cli(), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        p.receber(uid, Entrada("telegram", "texto", PANFLETO))
        r = p.receber(uid, Entrada("telegram", "texto", PANFLETO + " "))
        self.assertIn("já estavam guardados", r)
        self.assertEqual(len(repo.precos(uid, "2026-01-01")), 3)

    def test_organizar_a_lista_por_departamento(self):
        # 11/11: "organiza por departamento com check box" e "Lista completa" mostram a lista, sem comparar;
        # "Mercado Pago" de outra pendência e "Comprar adubo" sozinho não entram.
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        cli = Cli()
        p = Perai(repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        p.receber(uid, Entrada("telegram", "texto", PANFLETO))
        from perai.armazenamento import NovaMensagem
        from perai.extracao import Extracao, Pendencia
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="lista"))
        repo.salvar_extracao(uid, mid, Extracao(mid, pendencias=[
            Pendencia("P1-1", mid, "Comprar Ovos, Detergente, Creme de leite, Pão francês, 1 caixinha de creme de "
                                   "leite (200g) e 200g de chocolate ao leite em barra", "eu", None, None, None),
            Pendencia("P1-2", mid, "Aguardando Fulano Teste enviar a simulação pelo Mercado Pago, a amostra",
                      "aguardando", None, None, None),
            Pendencia("P1-3", mid, "Comprar adubo para as plantas", "eu", None, None, None)]))
        for pergunta in ("Pode organizar a lista por departamento e com check box?", "Lista completa"):
            antes = len(cli.consultas)
            r = str(p.receber(uid, Entrada("telegram", "texto", pergunta)))
            self.assertEqual(len(cli.consultas), antes, pergunta)        # feito pelo código, sem IA
            self.assertIn("🛒 Lista de compras (5 itens)", r)
            self.assertIn("🧽 Limpeza\n☐ Detergente", r)
            self.assertIn("☐ 1 caixinha de creme de leite (200g)", r)
            self.assertNotIn("☐ Creme de leite", r)                      # repetido junta com o detalhado
            self.assertIn("🥫 Mercearia", r)
            self.assertNotIn("Mercado Pago", r)
            self.assertNotIn("adubo", r)
        # Perguntar onde comprar continua comparando, e só com a lista de verdade
        _, pend = repo.memoria(uid)
        self.assertEqual([x.descricao[:12] for x in p._listas_de_compras(pend)], ["Comprar Ovos"])
        self.assertFalse(precos.pede_ver_lista("em qual mercado é melhor eu ir?"))
        self.assertFalse(precos.pede_ver_lista("inclui pão na lista e me mostra"))

    def test_lista_junta_repetidos_e_reconhece_a_lista(self):
        self.assertEqual(precos.nucleo("1 caixinha de creme de leite (200g)"), precos.nucleo("Creme de leite"))
        self.assertEqual(precos.itens_unicos(["Creme de leite", "Café", "1 caixinha de creme de leite (200g)"]),
                         ["1 caixinha de creme de leite (200g)", "Café"])
        self.assertTrue(precos.e_lista_de_compras("Comprar Ovos, Café", "eu", 2))
        self.assertFalse(precos.e_lista_de_compras("Comprar adubo", "eu", 1))
        self.assertFalse(precos.e_lista_de_compras("Aguardando o Nilson pelo Mercado Pago, a amostra", "aguardando", 2))
        self.assertEqual(precos.departamento("200g de chocolate meio amargo ou ao leite em barra"), "🥫 Mercearia")
        self.assertEqual(precos.departamento("Leite zero lactose"), "🧀 Frios e laticínios")
        self.assertEqual(precos.departamento("Saco de lixo grande de 200 L"), "🧽 Limpeza")
        from perai.servico import mudar_lista
        self.assertEqual(mudar_lista("Comprar Ovos, Creme de leite e Café", ["1 caixinha de creme de leite (200g)"], []),
                         "Comprar Ovos, 1 caixinha de creme de leite (200g) e Café")
        self.assertIn("detergente em pó", precos.SISTEMA_LISTA)

    def test_jeitos_de_perguntar_pela_lista(self):
        for q in ("onde minha listas esta mais barata", "em qual mercado é melhor eu ir",
                  "qual supermercado está mais barato?", "onde eu compro mais barato"):
            self.assertTrue(precos.FALA_DE_LISTA.search(q), q)
        self.assertFalse(precos.FALA_DE_LISTA.search("o frango está barato?"))

    def test_comparar_lista_escolhe_a_mais_barata_pela_referencia(self):
        def q(produto, loja, preco, qtd=None, un=None, por="un", data="2026-11-15"):
            i = precos.interpretar(json.dumps({"loja": loja, "validade": data, "itens": [
                {"produto": produto, "quantidade": qtd, "unidade": un, "preco": preco, "por": por}]}))[0]
            return {**i, "origem": "oferta"}
        codigos = {"Q1": q("ovos brancos", "Amigão Supermercados", 9.95, 20, "un"),
                   "Q2": q("ovos Tominaga", "Supermercados Planalto", 12.99, 30, "un"),
                   "Q3": q("banana nanica", "Amigão Supermercados", 3.99, por="kg"),
                   "Q4": q("banana nanica", "Supermercados Planalto", 3.99, por="kg"),
                   "Q5": q("mamão papaya", "Supermercados Planalto", 3.99),
                   "Q6": q("mamão papaia", "Amigão Supermercados", 4.99),
                   "Q7": q("flocão de milho", "Amigão Supermercados", 2.49, 500, "g"),
                   "Q8": q("café", "Supermercados Planalto", 17.90, 500, "g", data="2026-11-01")}
        mapa = {"Ovos": ["Q1", "Q2"], "Banana": ["Q3", "Q4"], "Mamão papaia": ["Q5", "Q6"],
                "Flocão de milho": ["Q7"], "Café": ["Q8"]}
        texto, usados = precos.comparar_lista(["Ovos", "Banana", "Mamão papaia", "Flocão de milho", "Café",
                                               "Detergente"], mapa, codigos, "2026-11-10")
        planalto = texto.split("Compre no Planalto:")[1].split("\n\n")[0]
        self.assertIn("Ovos: ovos Tominaga 30 un R$ 12,99/un (R$ 0,43/un)", planalto)   # mais barato por ovo
        self.assertIn("no Amigão: R$ 9,95/un (R$ 0,50/un)", planalto)
        self.assertIn("Mamão papaia", planalto)
        self.assertIn("Banana", planalto)                                # empate fica com quem ganhou mais
        self.assertIn("mesmo preço no Amigão", planalto)
        self.assertIn("Flocão de milho", texto.split("Compre no Amigão:")[1])   # só uma loja: entra assim mesmo
        self.assertIn("Sem oferta vigente registrada: Café, Detergente.", texto)   # café vencido
        self.assertTrue(texto.startswith("🛒 Melhor ir ao Planalto: 3 item(ns)"))
        self.assertNotIn("Q8", usados)

    def test_ovos_por_unidade(self):
        ovos20 = precos.interpretar(json.dumps({"loja": "Amigão", "itens": [
            {"produto": "ovos brancos", "quantidade": 20, "unidade": "un", "preco": 9.95, "por": "un"}]}))[0]
        self.assertIn("equivale a R$ 0,50/un", precos.linha("Q1", {**ovos20, "origem": "oferta"}))
        texto = precos.texto_compartilhar([{**ovos20, "origem": "oferta"}] * 3)
        self.assertEqual(texto.count("Ovos brancos"), 1)          # repetidos saem

    def test_numero_com_moeda_e_virgula(self):
        self.assertEqual(str(precos._num("R$ 4,99")), "4.99")
        self.assertEqual(str(precos._num("R$ 1.299,90/kg")), "1299.90")
        self.assertEqual(str(precos._num(7.99)), "7.99")
        self.assertIsNone(precos._num("grátis"))
        self.assertTrue(precos.parece_panfleto("Encarte com ofertas: tomate R$ 4,99/kg"))

    def test_nota_fiscal_entra_como_preco_pago(self):
        from perai import despesas
        xml = Path(tempfile.mkdtemp()) / "nota.xml"
        xml.write_text("""<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe123">
          <ide><dhEmi>2026-10-12T10:00:00-03:00</dhEmi></ide><emit><xNome>Amigao</xNome><CNPJ>1</CNPJ></emit>
          <det nItem="1"><prod><xProd>COXA SOBRECOXA FGO CONG</xProd><qCom>1.250</qCom><uCom>KG</uCom>
          <vUnCom>9.49</vUnCom><vProd>11.86</vProd></prod></det>
          <total><ICMSTot><vNF>11.86</vNF></ICMSTot></total></infNFe></NFe></nfeProc>""", encoding="utf-8")
        nota = despesas.ler_nfe_xml(xml)
        pagos = precos.de_nota(nota)
        self.assertEqual((pagos[0]["ref_centavos"], pagos[0]["ref_unidade"], pagos[0]["data"]), (949, "kg", "2026-10-12"))


if __name__ == "__main__":
    unittest.main()


class CliCompra(Cli):
    """D-109: a IA só liga cada item da compra às ofertas do mesmo produto."""
    def __init__(self, mapa):
        super().__init__()
        self.mapa, self.pedidos_compra = mapa, []

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        if sistema == precos.SISTEMA_COMPRA:
            self.pedidos_compra.append(usuario)
            import re
            compra = re.findall(r"\[(C\d+)\] (.+)", usuario)
            ofertas = re.findall(r"\[(Q\d+)\] \(preço; [^)]*\) (.+)", usuario)
            itens = [{"compra": c, "ofertas": [q for q, o in ofertas if self.mapa.get(desc.split()[0]) and
                                               o.startswith(self.mapa[desc.split()[0]])]} for c, desc in compra]
            return Resposta(json.dumps({"itens": itens}), 1, 1, 1, 0.0)
        if tarefa == "extracao" and "nota" in usuario.lower():
            return Resposta(json.dumps({"registros": [{"conteudo": "Compra no Mercado Exemplo", "natureza": "confirmado"}],
                                        "despesas": [{"descricao": "Compra no Mercado Exemplo", "valor": "30,47",
                                                      "data": "2026-11-12", "categoria": "mercado",
                                                      "estabelecimento": "Mercado Exemplo",
                                                      "chave_acesso": "4126 1112 3456 7800 0199 6500 1000 0012 "
                                                                      "3410 0001 2345",
                                                      "itens": [{"descricao": "DETERG. LIQ. YPE 500ML", "valor": "2,99",
                                                                 "quantidade": "1", "unidade": "UN",
                                                                 "valor_unitario": "2,99"},
                                                                {"descricao": "BANANA PRATA", "valor": "7,98",
                                                                 "quantidade": "1,330", "unidade": "KG",
                                                                 "valor_unitario": "6,00"},
                                                                {"descricao": "CAFE PILAO 500G", "valor": "19,50",
                                                                 "quantidade": "1", "unidade": "UN",
                                                                 "valor_unitario": "19,50"}]}]}), 1, 1, 1, 0.0)
        return super().conversar(modelo, tarefa, sistema, usuario, imagem, resposta_json)


class TesteCompraOfertas(unittest.TestCase):
    def setUp(self):
        from perai.armazenamento import NovaMensagem
        self.repo = RepositorioMemoria()
        self.uid = self.repo.criar_usuario("Nelson", 10)
        mid = self.repo.salvar_mensagem(self.uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="panfleto",
                                                               data_situacao="2026-11-10"))
        self.repo.salvar_precos(self.uid, mid, "oferta", precos.interpretar(json.dumps({
            "loja": "Supermercado Ficticio", "validade": "2026-11-15", "itens": [
                {"produto": "detergente líquido", "marca": "Ypê", "quantidade": 500, "unidade": "ml",
                 "preco": 2.49, "por": "un"},
                {"produto": "banana prata", "preco": 6.49, "por": "kg"},
                {"produto": "sabão em pó", "preco": 9.90, "por": "un"}]})))
        self.repo.salvar_precos(self.uid, mid, "oferta", precos.interpretar(json.dumps({
            "loja": "Mercado Antigo", "validade": "2026-11-01", "itens": [
                {"produto": "detergente líquido", "preco": 1.99, "por": "un"}]})))   # vencida no dia da compra

    def test_nota_em_pdf_guarda_preco_pago_e_compara_com_ofertas(self):
        cli = CliCompra({"DETERG.": "detergente", "BANANA": "banana"})   # café não tem oferta
        p = Perai(self.repo, cli, MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        r = p.receber(self.uid, Entrada("telegram", "texto", "Segue a nota do mercado de hoje"))
        self.assertIn("🏷️ Guardei o preço de 3 item(ns) para comparar com as ofertas.", r)
        pagos = [x for x in self.repo.precos(self.uid, "2026-01-01") if x["origem"] == "compra"]
        self.assertEqual({(x["produto"], x["ref_centavos"], x["ref_unidade"]) for x in pagos},
                         {("DETERG. LIQ. YPE 500ML", 299, "un"), ("BANANA PRATA", 600, "kg"),
                          ("CAFE PILAO 500G", 1950, "un")})
        # A mesma NFC-e de novo (chave de acesso): não duplica.
        r = p.receber(self.uid, Entrada("telegram", "texto", "Segue a nota do mercado de hoje"))
        self.assertIn("já estava registrada", r)
        self.assertEqual(len([d for d in self.repo.b.despesas.values() if d["situacao"] == "ativa"]), 1)

        r = p.receber(self.uid, Entrada("telegram", "texto", "Podemos comparar minha compra com as ofertas?"))
        self.assertIn("🧾 Sua compra no Mercado Exemplo em 12/11 × ofertas registradas válidas no dia:", r)
        self.assertIn("💸 A oferta era menor (1):", r)
        self.assertIn("• DETERG. LIQ. YPE 500ML: você pagou R$ 2,99/un; no Supermercado Ficticio a oferta era "
                      "R$ 2,49/un 500 ml, até 15/11 — R$ 0,50 a menos por un", r)
        self.assertIn("✅ Você pagou igual ou menos que a oferta (1):", r)
        self.assertIn("• BANANA PRATA: R$ 6,00/kg (oferta no Supermercado Ficticio: R$ 6,49/kg)", r)
        self.assertIn("Sem oferta do mesmo produto para comparar (1): CAFE PILAO 500G.", r)
        self.assertNotIn("1,99", r)                                                           # oferta vencida
        self.assertNotIn("Mercado Antigo", cli.pedidos_compra[0])
        self.assertIn("📤 Compartilhar preços", [b[0] for b in r.botoes])

    def test_sem_compra_registrada_avisa(self):
        p = Perai(self.repo, CliCompra({}), MODELOS["simulado"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        r = p.receber(self.uid, Entrada("telegram", "texto", "Podemos comparar minha compra com as ofertas?"))
        self.assertIn("Ainda não tenho o preço pago por item", str(r))

    def test_reconhece_o_pedido(self):
        self.assertTrue(precos.pede_comparar_compra("Podemos comparar minha compra com as ofertas?"))
        self.assertTrue(precos.pede_comparar_compra("paguei caro na nota do Planalto?"))
        self.assertFalse(precos.pede_comparar_compra("onde minhas compras saem mais baratas?"))
        self.assertFalse(precos.pede_comparar_compra("qual mercado tem a lista mais barata?"))


class TesteSaude(unittest.TestCase):
    def test_nota_de_farmacia_entra_no_historico(self):
        """D-110: remédio entra no histórico de preços como qualquer compra (dados fictícios)."""
        pagos = precos.de_detalhes([{"descricao": "REMEDIO FICTICIO 20MG 30CP", "quantidade": "1", "unidade": "UN",
                                     "valor_unitario": "45,90"}], "Farmácia Exemplo", "2026-11-12")
        self.assertEqual([(x["produto"], x["preco_centavos"]) for x in pagos], [("REMEDIO FICTICIO 20MG 30CP", 4590)])
        import inspect
        from perai import servico
        self.assertNotIn('d.categoria != "saude"', inspect.getsource(servico))

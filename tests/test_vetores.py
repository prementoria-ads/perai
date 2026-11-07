"""Busca por significado: o registro certo entra mesmo sem palavra em comum com a pergunta."""

import json
import tempfile
import unittest
from pathlib import Path

from perai import consulta, vetores
from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Extracao, Registro
from perai.llm import Cliente, ErroModelo, Resposta
from perai.servico import Perai


def eixo(i):
    v = [0.0] * vetores.DIMENSOES
    v[i] = 1.0
    return v


class Cli(Cliente):
    def __init__(self, falhar=False):
        super().__init__()
        self.falhar = falhar
        self.textos_vetor = 0
        self.memoria_enviada = ""

    def vetores(self, textos, pergunta=False, modelo=None, simulado=False):
        if self.falhar:
            raise ErroModelo("fora do ar")
        self.textos_vetor += len(textos)
        return [eixo(1) if ("Cobalt" in t or "carro" in t) else eixo(2) for t in textos], 10

    def conversar(self, modelo, tarefa, sistema, usuario, imagem=None, resposta_json=True):
        if tarefa == "consulta":
            self.memoria_enviada = usuario
        if tarefa == "roteamento":
            return Resposta(json.dumps({"tipo": "consulta"}), 1, 1, 1, 0.0)
        return Resposta(json.dumps({"resposta": "ok", "registros_citados": [], "sem_informacao": True}),
                        1, 1, 1, 0.0)


class TesteVetores(unittest.TestCase):
    def montar(self, cli):
        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        regs = [Registro(f"R1-{i}", "1", f"Reunião número {i} sobre firewall", "confirmado", "eu", None, [], 0.9, False)
                for i in range(200)]
        regs.append(Registro("R1-999", "1", "O Cobalt branco foi ao lava-jato", "confirmado", "eu", None, [], 0.9, False))
        repo.salvar_extracao(uid, mid, Extracao("1", registros=regs))
        p = Perai(repo, cli, MODELOS["gemini-flash"], TRANSCRITORES["simulado"], MODELOS["simulado"],
                  Path(tempfile.mkdtemp()))
        return repo, uid, p

    def test_acha_pelo_significado_e_guarda_os_vetores(self):
        cli = Cli()
        repo, uid, p = self.montar(cli)
        registros, _ = repo.memoria(uid)
        ids = p._parecidos(uid, "qual a cor do meu carro?", registros)
        cobalt = next(r.id for r in registros if "Cobalt" in r.conteudo)
        self.assertIn(cobalt, ids)
        escolhidos = consulta.selecionar(registros, "qual a cor do meu carro?", preferidos=ids)
        self.assertIn(cobalt, [r.id for r in escolhidos])
        self.assertEqual(cli.textos_vetor, 201 + 1)
        p._parecidos(uid, "e a placa?", registros)
        self.assertEqual(cli.textos_vetor, 201 + 2)    # registros já têm vetor: só a pergunta
        self.assertTrue(any(c["modelo"] == "gemini-embedding-001" and c["custo_usd"] > 0
                            for c in repo.b.chamadas))

    def test_falha_cai_para_busca_por_palavras(self):
        repo, uid, p = self.montar(Cli(falhar=True))
        registros, _ = repo.memoria(uid)
        self.assertEqual(p._parecidos(uid, "qual a cor do meu carro?", registros), set())

    def test_empacotar_normaliza(self):
        v = vetores.desempacotar(vetores.empacotar([3.0, 4.0]))
        self.assertAlmostEqual(v[0], 0.6, places=5)
        self.assertEqual(vetores.similares([1.0, 0.0], {"a": vetores.empacotar([1, 0]),
                                                         "b": vetores.empacotar([0, 1])})[0][0], "a")


if __name__ == "__main__":
    unittest.main()

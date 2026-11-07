"""Memória grande: a pergunta acha o registro mesmo sem a palavra igual ("carro" x "Cobalt")."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from perai.armazenamento import NovaMensagem, RepositorioMemoria
from perai.config import MODELOS, TRANSCRITORES
from perai.extracao import Extracao, Registro
from perai.llm import Cliente
from perai.servico import Perai


class TesteBuscaRelacionada(unittest.TestCase):
    def test_carro_acha_cobalt(self):
        vistos = []

        def sim(tarefa, sistema, usuario):
            if tarefa == "roteamento" and "Nomes na memória" in usuario:
                return json.dumps({"termos": ["Cobalt"]})
            if tarefa == "roteamento":
                return json.dumps({"tipo": "pergunta"})
            vistos.append(usuario)
            ids = re.findall(r"^\[([^\]]+)\][^\n]*Cobalt", usuario, flags=re.M)
            return json.dumps({"resposta": "Branco.", "registros_citados": ids[:1], "sem_informacao": not ids})

        repo = RepositorioMemoria()
        uid = repo.criar_usuario("Nelson", 10)
        mid = repo.salvar_mensagem(uid, NovaMensagem(canal="telegram", tipo="texto", texto_original="x"))
        regs = [Registro(f"R1-{i}", "1", f"Reunião número {i} sobre o projeto Alfa", "confirmado", "eu",
                         None, [], 0.9, False) for i in range(200)]
        regs.insert(0, Registro("R1-x", "1", "Você buscou o Cobalt branco no lava-jato", "confirmado", "eu",
                                None, ["Cobalt"], 0.9, False))
        repo.salvar_extracao(uid, mid, Extracao("1", registros=regs))
        perai = Perai(repo, Cliente(simulador=sim), MODELOS["simulado"], TRANSCRITORES["simulado"],
                      MODELOS["simulado"], Path(tempfile.mkdtemp()))
        r = perai._responder(uid, "qual a cor do carro?")
        self.assertIn("Cobalt branco", vistos[-1])
        self.assertIn("Branco", r)


if __name__ == "__main__":
    unittest.main()

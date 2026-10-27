"""Teste de ponta a ponta com modelo simulado: lê CSVs no formato da planilha,
prepara, extrai, responde, pontua e escreve o relatório."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from perai.config import MODELOS, TRANSCRITORES
from perai.llm import Cliente
from teste_comparativo import relatorio
from teste_comparativo.dados import ler_mensagens, ler_perguntas
from teste_comparativo.executar import executar_modelo
from teste_comparativo.pontuacao import nome_tem_fonte, pontuar
from teste_comparativo.preparar import preparar

CAB_MSG = ["Nº", "Tipo", "Arquivo de áudio", "Texto ou o que foi dito", "Data da situação",
           "Pessoas, empresas e projetos esperados", "Fatos esperados", "Natureza esperada",
           "Pendência esperada (com prazo)", "Observações"]
CAB_PERG = ["Nº", "Tipo", "Pergunta ou pedido", "Resposta correta", "Mensagens que sustentam (Nº)",
            "Observações"]


def _csv(caminho, cabecalho, linhas):
    with caminho.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cabecalho)
        w.writerows(linhas)


def simulador(tarefa, sistema, usuario):
    if tarefa == "leitura_imagem":
        return "[10:00] Simone: O Marck é muito burocrático"
    if tarefa == "extracao":
        if "nº 1 " in usuario:
            return json.dumps({
                "entidades": [{"nome": "Wagner", "tipo": "pessoa"},
                              {"nome": "Romagnole", "tipo": "organizacao"},
                              {"nome": "Fulano Inventado", "tipo": "pessoa"}],
                "registros": [{"conteudo": "Levar o Marcos na visita", "natureza": "hipotese",
                               "dito_por": "eu", "confianca": 0.6}],
                "pendencias": [{"descricao": "Enviar proposta ao Wagner", "responsavel": "eu",
                                "prazo": "2026-11-06", "prazo_como_dito": "sexta"}],
            })
        return json.dumps({
            "entidades": [{"nome": "Simone", "tipo": "pessoa"}],
            "registros": [{"conteudo": "O Marck é muito burocrático", "natureza": "opiniao",
                           "dito_por": "Simone", "confianca": 0.9}],
            "pendencias": [],
        })
    if tarefa == "consulta":
        if "telefone" in usuario:
            return json.dumps({"resposta": "Não há registro.", "registros_citados": [],
                               "sem_informacao": True})
        return json.dumps({"resposta": "Proposta até sexta.", "registros_citados": ["P1-1"],
                           "sem_informacao": False})
    raise AssertionError(tarefa)


class TestePontaAPonta(unittest.TestCase):
  def test_ponta_a_ponta(self):
      tmp_path = Path(tempfile.mkdtemp())
      dados = tmp_path / "dados"
      (dados / "arquivos").mkdir(parents=True)
      (dados / "arquivos" / "print-02.jpg").write_bytes(b"\xff\xd8 imagem falsa")
      _csv(dados / "mensagens.csv", CAB_MSG, [
          ["Exemplo", "Áudio", "msg-00.m4a", "linha de exemplo", "", "", "", "", "", ""],
          ["1", "Texto", "", "Tenho que mandar a proposta pro Wagner da Romagnole até sexta. "
                             "Talvez eu leve o Marcos.", "'02/11/2026", "Wagner; Romagnole", "",
           "Fato; hipótese", "Eu: enviar proposta até 06/11", ""],
          ["2", "Print de conversa", "print-02.jpg", "Conversa com a Simone", "'02/11/2026",
           "Simone", "", "Opinião da Simone", "Nenhuma", ""],
          ["3", "Áudio", "faltando.ogg", "Preencher: o que foi dito", "", "", "", "", "", ""],
          ["4", "", "", "", "", "", "", "", "", ""],
      ])
      _csv(dados / "perguntas.csv", CAB_PERG, [
          ["Exemplo", "Briefing", "Me prepara", "x", "Exemplo", ""],
          ["1", "Consulta", "O que eu fiquei de mandar pro Wagner?", "Proposta até sexta", "1", ""],
          ["2", "Consulta", "Qual o telefone do Wagner?", "Não há registro", "1", ""],
          ["3", "Consulta", "", "", "", ""],
      ])

      mensagens, avisos = ler_mensagens(dados / "mensagens.csv")
      perguntas, _ = ler_perguntas(dados / "perguntas.csv")
      assert [m.numero for m in mensagens] == ["1", "2", "3"]
      assert mensagens[0].data_situacao == "2026-11-02"
      assert mensagens[2].texto is None  # "Preencher..." não conta como conteúdo
      assert [p.numero for p in perguntas] == ["1", "2"]

      cliente = Cliente(simulador=simulador)
      entradas = preparar(mensagens, dados / "arquivos", cliente, TRANSCRITORES["simulado"],
                          MODELOS["simulado"], dados / "preparado" / "cache.json")
      assert "Marck" in entradas[1].texto and "Conversa com a Simone" in entradas[1].texto
      assert entradas[2].aviso and "faltando.ogg" in entradas[2].aviso
      assert (dados / "preparado" / "cache.json").exists()

      resultado = executar_modelo(cliente, MODELOS["simulado"], entradas, perguntas, "2026-11-02",
                                  progresso=lambda *_: None)
      placar = pontuar(resultado, mensagens, perguntas, entradas)
      assert placar.nomes_sem_fonte == ["1: Fulano Inventado"]
      assert (placar.hipoteses_ok, placar.hipoteses_esperadas) == (1, 1)
      assert (placar.opinioes_ok, placar.opinioes_esperadas) == (1, 1)
      assert (placar.pendencias_concordantes, placar.pendencias_avaliadas) == (2, 2)
      assert (placar.armadilhas_ok, placar.armadilhas) == (1, 1)
      assert (placar.fonte_correta, placar.com_fonte_esperada) == (1, 1)
      assert placar.erros == 0

      resumo = relatorio.escrever(tmp_path / "rel", [placar], [resultado], mensagens, perguntas,
                                  entradas, avisos)
      texto = resumo.read_text(encoding="utf-8")
      assert "Armadilhas: disse que não sabe | 1/1 (100%)" in texto
      assert "Fulano Inventado" in texto
      assert (tmp_path / "rel" / "revisao_respostas.csv").exists()
      assert (tmp_path / "rel" / "revisao_extracoes.csv").exists()


class TesteNomeFonte(unittest.TestCase):
  def test_nome_tem_fonte_ignora_acentos(self):
    assert nome_tem_fonte("Inajá", "conversa com o Pedro Inaja")
    assert not nome_tem_fonte("Julio Kusman", "falei com o Wagner")


if __name__ == '__main__':
    unittest.main()

import json
import unittest

from perai import consulta, extracao


class T_test_interpreta_json_cercado_e_normaliza(unittest.TestCase):
  def test_interpreta_json_cercado_e_normaliza(self):
      resposta = """Claro! ```json
{"entidades": [{"nome": "Wagner", "tipo": "pessoa", "contexto": "profissional"},
                 {"nome": "", "tipo": "pessoa"}],
 "registros": [{"conteudo": "Levar o Marcos", "natureza": "talvez", "dito_por": "eu",
                   "confianca": 7, "entidades": ["Marcos"]}],
 "pendencias": [{"descricao": "Enviar proposta", "responsavel": "outro", "prazo": "2026-11-06"}],
 "duvidas": ["quem é ele?"]}
```"""
      ext = extracao.interpretar("3", resposta)
      assert ext.json_valido
      assert [e.nome for e in ext.entidades] == ["Wagner"]
      r = ext.registros[0]
      assert r.id == "R3-1" and r.mensagem == "3"
      assert r.natureza == "hipotese"  # natureza desconhecida nunca vira fato
      assert r.confianca == 1.0
      assert ext.pendencias[0].responsavel == "eu"
      assert ext.duvidas == ["quem é ele?"]
      assert "entidade sem nome" in ext.problemas


class T_test_resposta_que_nao_e_json(unittest.TestCase):
  def test_resposta_que_nao_e_json(self):
      ext = extracao.interpretar("1", "não sei responder")
      assert not ext.json_valido and not ext.registros


class T_test_consulta_descarta_citacao_inexistente(unittest.TestCase):
  def test_consulta_descarta_citacao_inexistente(self):
      resposta = json.dumps({"resposta": "Sexta.", "registros_citados": ["R2-1", "R9-9"],
                             "sem_informacao": False})
      r = consulta.interpretar("Quando?", resposta, {"R2-1"})
      assert r.registros_citados == ["R2-1"]
      assert r.citacoes_invalidas == ["R9-9"]


class T_test_memoria_mostra_natureza_autor_e_prazo(unittest.TestCase):
  def test_memoria_mostra_natureza_autor_e_prazo(self):
      reg = extracao.Registro("R1-1", "1", "O Marck é burocrático", "opiniao", "Simone", None, [], 0.9, False)
      pend = extracao.Pendencia("P1-1", "1", "Plantas", "aguardando", "Fernanda", None, "até quarta")
      texto = consulta.formatar_memoria([reg], [pend])
      assert "(opiniao; dito por: Simone)" in texto
      assert "aguardando (Fernanda); prazo: até quarta" in texto


if __name__ == '__main__':
    unittest.main()

"""Citações: ids no texto, ids cortados, pendência como fonte no teste (rodada de 07/11)."""

import unittest

from perai import consulta

U1 = "a7deb0e4-5219-4a8c-ad22-e79abde6cfba"
U2 = "865d9697-91af-4d4a-bda9-fefda18ddd0f"


class TestLimparCodigos(unittest.TestCase):
    def test_tira_uuid_do_texto(self):
        t = f"O patrocínio é de R$ 5.000,00 [{U1}, {U2}]. Datas: 14 e 15/11 [{U2}]."
        self.assertEqual(consulta.limpar_codigos(t), "O patrocínio é de R$ 5.000,00. Datas: 14 e 15/11.")

    def test_tira_codigos_curtos(self):
        self.assertEqual(consulta.limpar_codigos("Reunião às 17h [A1] e almoço (D3)."), "Reunião às 17h e almoço.")

    def test_mantem_texto_comum(self):
        self.assertEqual(consulta.limpar_codigos("Jejum [16h] até 12:30"), "Jejum [16h] até 12:30")


class TestInterpretar(unittest.TestCase):
    def test_id_no_texto_vale_como_citacao(self):
        r = consulta.interpretar("p", f'{{"resposta": "R$ 5 mil [{U1}]", "registros_citados": []}}', {U1})
        self.assertEqual(r.registros_citados, [U1])

    def test_id_cortado_e_com_colchete(self):
        r = consulta.interpretar("p", '{"resposta": "x", "registros_citados": ["a7deb0e4", "[%s]"]}' % U2, {U1, U2})
        self.assertEqual(r.registros_citados, [U1, U2])

    def test_prefixo_curto_nao_vale(self):
        r = consulta.interpretar("p", '{"resposta": "x", "registros_citados": ["a7de"]}', {U1})
        self.assertEqual(r.registros_citados, [])


class TestSinonimos(unittest.TestCase):
    def test_apto_bate_com_apartamento(self):
        self.assertTrue(consulta._raizes("anunciar o apto por 310 mil") & consulta._raizes("meu apartamento"))


class TestFatorCusto(unittest.TestCase):
    def test_padrao_e_ambiente(self):
        import os
        from unittest import mock
        from perai.config import fator_custo
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PERAI_FATOR_CUSTO", None)
            self.assertEqual(fator_custo(), 1.5)
        with mock.patch.dict(os.environ, {"PERAI_FATOR_CUSTO": "0.5"}):
            self.assertEqual(fator_custo(), 1.0)   # nunca abaixo da estimativa
        with mock.patch.dict(os.environ, {"PERAI_FATOR_CUSTO": "x"}):
            self.assertEqual(fator_custo(), 1.5)


if __name__ == "__main__":
    unittest.main()

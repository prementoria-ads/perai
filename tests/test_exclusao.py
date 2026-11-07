"""Exclusão de dados (bloqueador 3): apaga, confere que não sobrou nada e só então confirma."""

import tempfile
import unittest
from pathlib import Path

from perai import exclusao

UID = "aaaaaaaa-0000-0000-0000-000000000001"


class Cursor:
    def __init__(self, sobra=0, achou=1):
        self.sobra, self.achou, self.feito, self.rowcount = sobra, achou, [], 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, sql, args=()):
        self.feito.append(sql)
        self.rowcount = self.achou if sql == exclusao.SQL_APAGAR else 0
        self._ultimo = sql

    def fetchall(self):
        return [("mensagens",), ("registros",)]

    def fetchone(self):
        return (self.sobra,)


class Conexao:
    def __init__(self, cur):
        self.cur, self.commits, self.rollbacks = cur, 0, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Agenda:
    def __init__(self):
        self.desconectados = []

    def desconectar(self, uid):
        self.desconectados.append(uid)


class TesteExclusao(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        (self.pasta / UID).mkdir()
        (self.pasta / UID / "audio.ogg").write_bytes(b"x")
        (self.pasta / "outra-pessoa").mkdir()

    def test_apaga_tudo(self):
        conn, agenda = Conexao(Cursor()), Agenda()
        exclusao.excluir(conn, UID, self.pasta, agenda)
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))
        self.assertEqual(agenda.desconectados, [UID])
        self.assertIn(exclusao.SQL_ANONIMIZAR, conn.cur.feito)
        self.assertFalse((self.pasta / UID).exists())
        self.assertTrue((self.pasta / "outra-pessoa").exists())

    def test_sobra_desfaz_e_nao_apaga_arquivos(self):
        conn = Conexao(Cursor(sobra=3))
        with self.assertRaises(RuntimeError):
            exclusao.excluir(conn, UID, self.pasta)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))
        self.assertTrue((self.pasta / UID / "audio.ogg").exists())

    def test_pessoa_nao_encontrada(self):
        conn = Conexao(Cursor(achou=0))
        with self.assertRaises(RuntimeError):
            exclusao.excluir(conn, UID, self.pasta)
        self.assertEqual(conn.commits, 0)


if __name__ == "__main__":
    unittest.main()

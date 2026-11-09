"""Queda passageira de conexão (reset no TLS, 09/11 11h04) não derruba a mensagem."""

import io
import unittest
import urllib.error
from unittest import mock

from perai import google, telegram


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TesteRede(unittest.TestCase):
    def test_reset_tenta_de_novo(self):
        falhas = [urllib.error.URLError(ConnectionResetError(104, "reset")), ConnectionResetError(104, "reset")]

        def abrir(pedido, timeout):
            if falhas:
                raise falhas.pop(0)
            return Resp(b'{"ok": true}')

        with mock.patch("urllib.request.urlopen", abrir), mock.patch("time.sleep"):
            self.assertEqual(telegram.transporte_http("GET", "https://x", None), b'{"ok": true}')
        falhas[:] = [urllib.error.URLError(ConnectionResetError(104, "reset"))]
        with mock.patch("urllib.request.urlopen", abrir), mock.patch("time.sleep"):
            self.assertEqual(google.transporte_http("GET", "https://x", {}, None), (200, b'{"ok": true}'))

    def test_erro_que_nao_e_passageiro_sobe(self):
        def abrir(pedido, timeout):
            raise urllib.error.URLError("Name or service not known")
        with mock.patch("urllib.request.urlopen", abrir), mock.patch("time.sleep"):
            with self.assertRaises(urllib.error.URLError):
                telegram.transporte_http("GET", "https://x", None)


if __name__ == "__main__":
    unittest.main()

"""Pruebas del servidor y persistencia local, ejecutables dentro del contenedor."""
import json
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import SQLiteStore, client_ip, first_valid_ip, make_handler, make_record, validate_numbers


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "test.sqlite3"
        cls.store = SQLiteStore(str(cls.db))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.store))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=3)
        cls.server.server_close()
        cls.tmp.cleanup()

    def fetch(self, path, obj=None, content=None, content_type="application/json"):
        if obj is not None:
            content = json.dumps(obj).encode("utf-8")
        req = Request(self.base + path, data=content,
                      headers={"Content-Type": content_type} if content is not None else {})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_html(self):
        status, body, headers = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn(b'Primera celdilla', body)
        self.assertIn(b'id="consent"', body)
        self.assertIn("text/html", headers["Content-Type"])

    def test_static_js_css(self):
        for path, expect in (("/app.js", b"/api/sumar"), ("/styles.css", b".card"), ("/favicon.svg", b"<svg")):
            with self.subTest(path=path):
                status, body, _ = self.fetch(path)
                self.assertEqual(status, 200)
                self.assertIn(expect, body)

    def test_health(self):
        status, body, _ = self.fetch("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"estado": "ok"})

    def test_suma_guarda_json(self):
        status, body, _ = self.fetch("/api/sumar", {"a": 2, "b": 3})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["resultado"], 5)
        self.assertTrue(json.loads(body)["guardado"])
        with sqlite3.connect(self.db) as connection:
            record = json.loads(connection.execute(
                "SELECT documento_json FROM sumas ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0])
        self.assertEqual(record["primera_celdilla"], 2)
        self.assertEqual(record["segunda_celdilla"], 3)
        self.assertEqual(record["resultado"], 5)
        self.assertEqual(record["tipo"], "suma")
        self.assertEqual(record["ip_cliente"], "127.0.0.1")
        self.assertRegex(record["fecha_hora_utc"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertTrue(record["fecha_utc"])
        self.assertTrue(record["hora_utc"])
        self.assertTrue(record["id"])

    def test_suma_decimales(self):
        status, body, _ = self.fetch("/api/sumar", {"a": 0.1, "b": 0.2})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["resultado"], 0.3)

    def test_valores_invalidos(self):
        for case in [{"a": True, "b": 1}, {"a": "2", "b": 1},
                     {"a": 1}, [1, 2], {"a": 1e30, "b": 1},
                     {"a": 1, "b": 2, "c": 3}, {"a": None, "b": 3}]:
            with self.subTest(case=case):
                code, _, _ = self.fetch("/api/sumar", case)
                self.assertEqual(code, 400)

    def test_json_malformado(self):
        self.assertEqual(self.fetch("/api/sumar", content=b"{mal}")[0], 400)

    def test_tipo_incorrecto(self):
        self.assertEqual(self.fetch("/api/sumar", content=b"{}", content_type="text/plain")[0], 415)

    def test_cuerpo_excesivo(self):
        self.assertEqual(self.fetch("/api/sumar", content=b" " * 2050)[0], 413)

    def test_ruta_privada(self):
        for path in ("/api/historial", "/data/sumas.sqlite3", "/.env", "/server.py"):
            with self.subTest(path=path):
                self.assertEqual(self.fetch(path)[0], 404)

    def test_forwarded_ip(self):
        self.assertEqual(first_valid_ip("8.8.8.8, 10.2.3.4"), "8.8.8.8")
        self.assertEqual(first_valid_ip("181.42.123.45:52731, 10.64.8.4"), "181.42.123.45")
        self.assertEqual(first_valid_ip("[2606:4700:4700::1111]:443, 169.254.129.1"),
                         "2606:4700:4700::1111")
        self.assertEqual(first_valid_ip("8.8.8.8, 1.1.1.1:42000, 10.2.3.4"), "1.1.1.1")
        self.assertIsNone(first_valid_ip("169.254.129.1, 10.2.3.4"))
        self.assertIsNone(first_valid_ip("bad, 999.999.0.1"))
        self.assertIsNone(first_valid_ip("8.8.8.8:bad"))

    def test_azure_client_ip_from_forwarded_header(self):
        headers = Message()
        headers.add_header("X-Forwarded-For", "8.8.8.8, 181.42.123.45:52731, 10.64.8.4")
        request = SimpleNamespace(client_address=("169.254.129.1", 1234), headers=headers)
        with patch.dict("os.environ", {"TRUST_PROXY_HEADERS": "1"}):
            self.assertEqual(client_ip(request), "181.42.123.45")
            missing = SimpleNamespace(client_address=("169.254.129.1", 1234),
                                      headers=Message())
            self.assertEqual(client_ip(missing), "no disponible")
            direct = SimpleNamespace(client_address=("127.0.0.1", 1234), headers=headers)
            self.assertEqual(client_ip(direct), "127.0.0.1")
        with patch.dict("os.environ", {"TRUST_PROXY_HEADERS": "0"}):
            self.assertEqual(client_ip(request), "169.254.129.1")

    def test_decimal(self):
        self.assertEqual(sum(validate_numbers({"a": Decimal("0.1"), "b": Decimal("0.2")})), Decimal("0.3"))

    def test_json_local_persiste(self):
        second = SQLiteStore(str(self.db))
        second.save(make_record({"a": 7, "b": 8}, "198.51.100.11"))
        with sqlite3.connect(self.db) as connection:
            record = json.loads(connection.execute(
                "SELECT documento_json FROM sumas WHERE json_extract(documento_json, '$.ip_cliente') = ?",
                ("198.51.100.11",),
            ).fetchone()[0])
        self.assertEqual(record["resultado"], 15)


if __name__ == "__main__":
    unittest.main()

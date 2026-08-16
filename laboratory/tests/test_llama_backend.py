"""Test del backend reale LlamaServerModel SENZA rete: un fake llama-server
in-process (stdlib http.server) registra il body della richiesta e risponde con
una chat completion fissa. Verifica che l'adapter invii grammar + repeat_penalty
e parsei correttamente la risposta.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.models import LlamaServerModel
from backend.models.llama_server import LlamaBackendError

CANNED_CONTENT = '{"title": "x", "date": "non specificato", "scaffold": {}, "questions": [], "checks": []}'


class _Recorder:
    """Mantiene l'ultima richiesta ricevuta dal fake server (cross-thread)."""

    def __init__(self) -> None:
        self.last_body: dict | None = None
        self.status = 200
        self.payload = {
            "choices": [{"message": {"role": "assistant", "content": CANNED_CONTENT}}]
        }


def _make_handler(rec: _Recorder):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            rec.last_body = json.loads(raw.decode("utf-8")) if raw else {}
            body = json.dumps(rec.payload).encode("utf-8")
            self.send_response(rec.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # silenzioso
            pass

    return Handler


class LlamaBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = _Recorder()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.rec))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_default_repeat_penalty_is_sent(self) -> None:
        be = LlamaServerModel(url=self.url)
        self.assertAlmostEqual(be.repeat_penalty, 1.1)
        be.generate("sys", "usr", grammar=None)
        self.assertEqual(self.rec.last_body["repeat_penalty"], 1.1)
        self.assertEqual(self.rec.last_body["max_tokens"], 512)

    def test_custom_repeat_penalty(self) -> None:
        be = LlamaServerModel(url=self.url, repeat_penalty=1.3)
        be.generate("sys", "usr")
        self.assertEqual(self.rec.last_body["repeat_penalty"], 1.3)

    def test_response_format_sent_when_grammar_provided(self) -> None:
        be = LlamaServerModel(url=self.url)
        be.generate("sys", "usr", grammar='root ::= "ok"')
        # sulle chat completions si usa response_format (json_schema), non grammar
        rf = self.rec.last_body["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertEqual(rf["schema"]["type"], "object")
        self.assertNotIn("grammar", self.rec.last_body)
        # messaggi system/user in ordine
        self.assertEqual(self.rec.last_body["messages"][0]["role"], "system")
        self.assertEqual(self.rec.last_body["messages"][1]["role"], "user")

    def test_no_response_format_when_grammar_none(self) -> None:
        be = LlamaServerModel(url=self.url)
        be.generate("sys", "usr", grammar=None)
        self.assertNotIn("response_format", self.rec.last_body)
        self.assertNotIn("grammar", self.rec.last_body)

    def test_parses_assistant_content(self) -> None:
        be = LlamaServerModel(url=self.url)
        out = be.generate("sys", "usr")
        self.assertEqual(out, CANNED_CONTENT)

    def test_raises_on_connection_failure(self) -> None:
        be = LlamaServerModel(url="http://127.0.0.1:1", timeout=1.0)  # porta chiusa
        with self.assertRaises(LlamaBackendError):
            be.generate("sys", "usr")

    def test_raises_on_http_error(self) -> None:
        self.rec.status = 500
        be = LlamaServerModel(url=self.url)
        with self.assertRaises(LlamaBackendError):
            be.generate("sys", "usr")

    def test_raises_on_unexpected_payload(self) -> None:
        self.rec.payload = {"nope": {}}
        be = LlamaServerModel(url=self.url)
        with self.assertRaises(LlamaBackendError):
            be.generate("sys", "usr")

    def test_last_trace_matches_wire(self) -> None:
        # change trace-llm (D1): la trace È il filo — request identica a ciò
        # che il fake ha ricevuto, response identica a ciò che ha risposto
        m = LlamaServerModel(self.url)
        m.generate("SYS", "USER", grammar=True)
        self.assertEqual(m.last_trace["request"], self.rec.last_body)
        self.assertEqual(m.last_trace["response"], self.rec.payload)
        self.assertIn("response_format", m.last_trace["request"])

    def test_last_trace_overwritten_each_call(self) -> None:
        # come last_usage: vale l'ultima generate (anche dopo il retry della skill)
        m = LlamaServerModel(self.url)
        m.generate("SYS", "U1")
        m.generate("SYS", "U2")
        self.assertEqual(m.last_trace["request"]["messages"][1]["content"], "U2")

    def test_last_trace_none_without_successful_call(self) -> None:
        m = LlamaServerModel(self.url)
        self.assertIsNone(m.last_trace)
        self.rec.status = 500
        with self.assertRaises(LlamaBackendError):
            m.generate("S", "U")
        self.assertIsNone(m.last_trace)  # chiamate fallite: niente trace

    def test_last_usage_captured(self) -> None:
        # il backend espone gli usage della chiamata (token del workflow ④)
        self.rec.payload["usage"] = {"prompt_tokens": 321, "completion_tokens": 65}
        m = LlamaServerModel(self.url)
        m.generate("sys", "user")
        self.assertEqual(m.last_usage, {"prompt_tokens": 321, "completion_tokens": 65})

    def test_last_usage_none_when_server_omits(self) -> None:
        # senza usage nel payload: None, nessun errore
        m = LlamaServerModel(self.url)
        self.assertIsNone(m.last_usage)
        m.generate("sys", "user")
        self.assertIsNone(m.last_usage)

if __name__ == "__main__":
    unittest.main()

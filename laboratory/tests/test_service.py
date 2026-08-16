"""Test del servizio HTTP (stdlib) con backend mock, in-process su porta effimera."""
from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from backend import service

NOTES = "Al campo con Anna. Abbiamo cucinato. Pioveva, poi ha funzionato il fuoco."


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        service._SKILL = None  # forza backend mock pulito
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    @staticmethod
    def _llama_skill(url: str):
        """Skill col backend vero puntato a un fake llama in-process."""
        from backend.models import LlamaServerModel
        from backend.skill import DiarioSkill
        return DiarioSkill(LlamaServerModel(url))

    def test_scaffold_trace_with_model_call(self) -> None:
        # change trace-llm: col backend modello la risposta include la trace
        # della chiamata LLM vera (campo opzionale, pattern `events`)
        import threading as _th
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen: list[dict] = []

        class FakeLlama(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                seen.append(json.loads(self.rfile.read(n).decode("utf-8")))
                body = json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": json.dumps({
                        "title": "Al campo.", "date": "oggi",
                        "scaffold": {"luogo": "campo", "persone": ["Anna"],
                                     "eventi": ["cucinato"], "aggiustaggi": []},
                        "questions": ["Chi altro c'era?"], "checks": []})}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):  # silenzioso
                pass

        llama = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlama)
        t = _th.Thread(target=llama.serve_forever, daemon=True)
        t.start()
        try:
            service._SKILL = self._llama_skill(
                f"http://127.0.0.1:{llama.server_address[1]}")
            status, body = self._post("/scaffold", {"notes": NOTES})
        finally:
            llama.shutdown(); llama.server_close(); t.join(timeout=2)
        self.assertEqual(status, 200)
        self.assertIn("trace", body)
        self.assertEqual(body["trace"]["request"], seen[-1])  # il filo, identico
        self.assertIn("choices", body["trace"]["response"])
        self.assertIn("response_format", body["trace"]["request"])

    def test_scaffold_no_trace_without_model_call(self) -> None:
        # mock backend e percorso onboarding: niente chiamata, niente trace
        status, body = self._post("/scaffold", {"notes": NOTES})
        self.assertEqual(status, 200)
        self.assertNotIn("trace", body)
        status, body = self._post("/scaffold", {"notes": "ciao?"})
        self.assertEqual(status, 200)
        self.assertNotIn("trace", body)  # onboarding: risposta senza modello

    def test_health(self) -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as r:
            self.assertEqual(r.status, 200)
            body = json.loads(r.read().decode("utf-8"))
        self.assertTrue(body["ok"])
        self.assertEqual(body["backend"], "mock")

    def test_scaffold_endpoint(self) -> None:
        status, body = self._post("/scaffold", {"notes": NOTES})
        self.assertEqual(status, 200)
        self.assertIn("scaffold", body)
        self.assertIn("questions", body)
        self.assertIn("checks", body)
        self.assertIn("events", body)  # eventi demo per la UI
        self.assertEqual(body["scaffold"]["title"], "Al campo con Anna.")

    def test_scaffold_includes_mock_usage(self) -> None:
        # il mock espone usage deterministica: token del workflow visibili
        status, body = self._post("/scaffold", {"notes": NOTES})
        self.assertEqual(status, 200)
        u = body.get("usage")
        self.assertIsNotNone(u, "usage attesa dal backend mock")
        self.assertIn("prompt_tokens", u)
        self.assertIn("completion_tokens", u)
        # deterministica: stessa richiesta, stessi numeri
        _, body2 = self._post("/scaffold", {"notes": NOTES})
        self.assertEqual(body2.get("usage"), u)

    def test_scaffold_usage_grows_with_notes(self) -> None:
        # più appunti -> più token in ingresso (coerente col contatore ③)
        _, short = self._post("/scaffold", {"notes": "Breve appunto di prova."})
        _, long_ = self._post("/scaffold", {"notes": NOTES + " " + NOTES})
        self.assertGreater(long_["usage"]["prompt_tokens"],
                           short["usage"]["prompt_tokens"])


    def test_missing_notes_returns_400(self) -> None:
        status, body = self._post("/scaffold", {"notes": "   "})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_unknown_path_returns_404(self) -> None:
        status, body = self._post("/nope", {"notes": "x"})
        self.assertEqual(status, 404)


class AutoBackendTest(unittest.TestCase):
    """Backend `auto`: lazy + ritentabile. Non incolla il mock se llama parte dopo."""

    _ENV = ("LAB_BACKEND", "LLAMA_URL", "MODEL_PATH")

    def setUp(self) -> None:
        service._SKILL = None
        self._orig = {k: os.environ.get(k) for k in self._ENV}

    def tearDown(self) -> None:
        service._SKILL = None
        for k, v in self._orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _stub_llama(self, content: str):
        """Avvia un mini llama-server: /health 200 + /v1/chat/completions -> content."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802  /health
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):  # noqa: N802  /v1/chat/completions
                import json as _j
                body = _j.dumps({"choices": [{"message": {"content": content}}],
                                  "usage": {"prompt_tokens": 111,
                                            "completion_tokens": 22}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # silente
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv, t, port

    def test_auto_falls_back_to_mock_when_llama_down(self) -> None:
        from backend.models import AutoBackend, MockModel
        be = AutoBackend(url="http://127.0.0.1:1")  # porta chiusa
        self.assertEqual(be.generate("sys", "user"), MockModel().generate("sys", "user"))

    def test_auto_uses_llama_when_reachable(self) -> None:
        from backend.models import AutoBackend
        srv, t, port = self._stub_llama("RISPOSTA_MODELLO")
        try:
            be = AutoBackend(url=f"http://127.0.0.1:{port}")
            self.assertEqual(be.generate("sys", "user"), "RISPOSTA_MODELLO")
        finally:
            srv.shutdown(); srv.server_close(); t.join(timeout=2)

    def test_auto_forwards_last_usage(self) -> None:
        """Regressione: con backend auto (quello del compose) la skill deve
        vedere gli usage del backend attivo — il wrapper non li ingoia."""
        from backend.models import AutoBackend
        srv, t, port = self._stub_llama("RISPOSTA_MODELLO")
        try:
            be = AutoBackend(url=f"http://127.0.0.1:{port}")
            be.generate("sys", "user")
            self.assertEqual(be.last_usage,
                             {"prompt_tokens": 111, "completion_tokens": 22})
        finally:
            srv.shutdown(); srv.server_close(); t.join(timeout=2)

    def test_auto_promotes_llama_after_it_starts(self) -> None:
        """Race all'avvio: primo generate con llama giù → mock; poi llama su → llama.
        Verifica che il fallback mock NON sia cacheato in modo permanente."""
        import socket
        from backend.models import AutoBackend, MockModel

        # ottiene una porta libera, la chiude, la usa (prima: nessuno in ascolto)
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        be = AutoBackend(url=f"http://127.0.0.1:{port}")
        out1 = be.generate("sys", "user")  # llama non in ascolto → mock
        self.assertEqual(out1, MockModel().generate("sys", "user"))

        srv, t, _ = self._stub_llama("ADESSO_MODELLO")
        # sposta il backend sulla porta dove ora c'è lo stub
        be.url = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            out2 = be.generate("sys", "user")  # ora llama è su → promote
            self.assertEqual(out2, "ADESSO_MODELLO")
        finally:
            srv.shutdown(); srv.server_close(); t.join(timeout=2)

    def test_build_skill_auto_uses_auto_backend(self) -> None:
        service._SKILL = None
        os.environ["LAB_BACKEND"] = "auto"
        os.environ["LLAMA_URL"] = "http://127.0.0.1:1"
        skill = service.build_skill_from_env()
        self.assertEqual(skill.backend.name, "auto")
if __name__ == "__main__":
    unittest.main()

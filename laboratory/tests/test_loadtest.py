"""Simulazione di carico (change readme-loadtest-consumi): N ragazzi sintetici
attraversano il gateway come chat normali e risultano nell'osservabilità."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend import gateway
from loadtest import run
from tests.test_web import _LlamaRec, _llama_handler


class LoadTestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = _LlamaRec()
        self.llama = ThreadingHTTPServer(("127.0.0.1", 0), _llama_handler(self.rec))
        self.llama_thread = threading.Thread(target=self.llama.serve_forever, daemon=True)
        self.llama_thread.start()

        self._orig = (gateway.LLAMA_URL, gateway._TRACKER)
        gateway.LLAMA_URL = f"http://127.0.0.1:{self.llama.server_address[1]}"
        self.tmp = tempfile.mkdtemp()
        gateway._TRACKER = gateway.SessionTracker(os.path.join(self.tmp, "s.db"))
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_url = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        gateway.LLAMA_URL, gateway._TRACKER = self._orig
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        self.llama.shutdown(); self.llama.server_close(); self.llama_thread.join(timeout=2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_records_sessions_in_observability(self) -> None:
        rep = run(self.gw_url, n=2, turns=2)
        self.assertEqual(rep["totals"]["sessions"], 2)
        for cid in ("load-00", "load-01"):
            with gateway._TRACKER._lock:
                self.assertIn(cid, gateway._TRACKER._sessions)
            self.assertEqual(rep["sessions"][cid]["ok"], 2)
            self.assertEqual(rep["sessions"][cid]["err"], 0)
            self.assertEqual(len(rep["sessions"][cid]["ms"]), 2)

    def test_run_grows_history_and_cycles_steps(self) -> None:
        """Il ragazzo sintetico usa la memoria (cronologia crescente) e tocca
        tappe diverse: le interazioni registrate portano turni e X-Step."""
        run(self.gw_url, n=1, turns=3)
        rows = gateway._TRACKER.timeline("load-00")["interactions"]
        self.assertEqual(len(rows), 3)
        # la cronologia cresce a coppie (domanda+risposta): 1, 3, 5 messaggi
        self.assertEqual([r["turns"] for r in rows], [1, 3, 5])  # memoria
        self.assertEqual([r["step"] for r in rows], ["1", "2", "3"])  # tappe cicliche

    @staticmethod
    def _flaky_llama(fails: int):
        """Fake llama che sbaglia le prime `fails` richieste, poi risponde bene."""
        state = {"n": 0}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                state["n"] += 1
                bad = state["n"] <= fails
                body = json.dumps(
                    {"error": "boom"} if bad else
                    {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 2}}).encode()
                self.send_response(500 if bad else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):  # silenzioso
                pass

        return H

    def _with_flaky(self, fails: int, **kw) -> dict:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self._flaky_llama(fails))
        st = threading.Thread(target=srv.serve_forever, daemon=True)
        st.start()
        orig = gateway.LLAMA_URL
        gateway.LLAMA_URL = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            return run(self.gw_url, **kw)
        finally:
            gateway.LLAMA_URL = orig
            srv.shutdown(); srv.server_close(); st.join(timeout=2)

    def test_retry_recovers_failed_turn(self) -> None:
        """--retry N: il turno fallito viene ritentato (il ragazzo ostinato);
        con retry=1 e un solo fallimento, il turno arriva a ok."""
        rep = self._with_flaky(fails=1, n=1, turns=1, retries=1)
        self.assertEqual(rep["sessions"]["load-00"]["ok"], 1)
        self.assertEqual(rep["sessions"]["load-00"]["err"], 0)

    def test_no_retry_by_default_fails_once(self) -> None:
        """Default retry=0: comportamento storico — un fallimento è un err,
        nessun secondo tentativo."""
        rep = self._with_flaky(fails=1, n=1, turns=1)
        self.assertEqual(rep["sessions"]["load-00"]["ok"], 0)
        self.assertEqual(rep["sessions"]["load-00"]["err"], 1)


if __name__ == "__main__":
    unittest.main()

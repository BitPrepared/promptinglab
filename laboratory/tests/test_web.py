"""Test del gateway laboratorio (bridge /api/*) e parità CLI/web.

Topologia locale (replica il compose): skill service (mock) + gateway, entrambi
in-process su porte effimere. Il tier statico è nginx (vedi `NginxTierTest`:
verifica della config, non del processo). Verifica:
  - bridge /api/health e /api/scaffold (proxy verso la skill)
  - bridge /api/chat (normalizzazione) + /api/model-status + osservabilità
  - ownership: il gateway espone SOLO /api/* (niente serving statico)
  - **parità di output** CLI vs web a parità di input (SkillOutput uguale)
  - 502 quando la skill non è raggiungibile
  - indipendenza dei moduli skill ↔ gateway (D1)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend import cli, gateway, service
from backend.schema import SkillOutput
from backend.web import client

NOTES = "Campo base a Costigiola. Oggi con Marco e Lucia montiamo la tenda nord. Pioveva, poi con le pietre ha funzionato. Stanchi ma felici."


def _wait_tracked(cid: str, timeout: float = 2.0) -> None:
    """Il record avviene nel thread handler DOPO la risposta: attendo che la
    sessione compaia nel tracker globale in memoria (race dei test)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with gateway._TRACKER._lock:
            if cid in gateway._TRACKER._sessions:
                return
        time.sleep(0.01)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GatewayStackTest(unittest.TestCase):
    def setUp(self) -> None:
        # skill service su porta effimera, backend mock pulito
        service._SKILL = None
        self.skill = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.skill_port = self.skill.server_address[1]
        self.skill_url = f"http://127.0.0.1:{self.skill_port}"
        self.skill_thread = threading.Thread(target=self.skill.serve_forever, daemon=True)
        self.skill_thread.start()

        # gateway che punta alla skill; usa i globali del modulo (override)
        self._orig_skill_url = gateway.SKILL_URL
        gateway.SKILL_URL = self.skill_url
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_port = self.gw.server_address[1]
        self.gw_url = f"http://127.0.0.1:{self.gw_port}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        gateway.SKILL_URL = self._orig_skill_url
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        self.skill.shutdown(); self.skill.server_close(); self.skill_thread.join(timeout=2)

    # --- helper ----------------------------------------------------------
    def _get(self, path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(self.gw_url + path, timeout=5) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type", "")

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.gw_url + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    # --- bridge ----------------------------------------------------------
    def test_health_proxy(self) -> None:
        status, body, ctype = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "backend": "mock"})
        self.assertIn("application/json", ctype)

    def test_scaffold_proxy(self) -> None:
        status, body = self._post("/api/scaffold", {"notes": NOTES})
        self.assertEqual(status, 200)
        self.assertIn("scaffold", body)
        self.assertIn("events", body)            # eventi demo presi dal bridge
        self.assertIn("usage", body)             # token del workflow (feedback ④)
        self.assertEqual(body["scaffold"]["title"], "Campo base a Costigiola.")

    def test_scaffold_missing_notes_returns_400(self) -> None:
        status, body = self._post("/api/scaffold", {"notes": "   "})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_skill_down_returns_502(self) -> None:
        # gateway punta a una porta morta
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        gateway.SKILL_URL = "http://127.0.0.1:1"  # porta inutilizzata
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_port = self.gw.server_address[1]
        self.gw_url = f"http://127.0.0.1:{self.gw_port}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()
        status, body = self._post("/api/scaffold", {"notes": NOTES})
        self.assertEqual(status, 502)
        self.assertIn("error", body)

    # --- ownership: SOLO /api/* (tier statico = nginx, non gateway) -------
    def test_non_api_paths_return_404_json(self) -> None:
        for probe in ["/", "/admin", "/index.html", "/admin.html", "/nope.css"]:
            with self.subTest(probe=probe):
                status, body, ctype = self._get(probe)
                self.assertEqual(status, 404)
                self.assertIn("application/json", ctype)
                self.assertIn(b"error", body)

    def test_path_traversal_returns_404(self) -> None:
        # il path traversal codificato non deve uscire dal gateway: non c'è
        # file system servito, solo /api/*
        for probe in ["/../backend/service.py", "/%2e%2e/backend/service.py"]:
            status, _, _ = self._get(probe)
            self.assertEqual(status, 404, msg=f"probe {probe} non bloccato")

    def test_post_unknown_api_404(self) -> None:
        status, body = self._post("/api/nope", {"x": 1})
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    # --- parità CLI vs web ------------------------------------------------
    def test_parity_web_equals_skill_direct(self) -> None:
        """Gateway (via bridge) e skill diretta producono lo stesso SkillOutput."""
        via_web = client.post_scaffold(self.gw_url, NOTES, endpoint="/api/scaffold")
        via_skill = client.post_scaffold(self.skill_url, NOTES, endpoint="/scaffold")
        self.assertEqual(
            SkillOutput.from_dict(via_web).to_text(),
            SkillOutput.from_dict(via_skill).to_text(),
        )

    def test_parity_cli_equals_web(self) -> None:
        """La CLI (--remote verso la skill :8080) e la pagina producono lo stesso output."""
        via_web = client.post_scaffold(self.gw_url, NOTES, endpoint="/api/scaffold")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--remote", self.skill_url, "--json", NOTES])
        via_cli = json.loads(buf.getvalue())

        self.assertEqual(
            SkillOutput.from_dict(via_web),
            SkillOutput.from_dict(via_cli),
        )

    def test_client_skill_error_on_bad_url(self) -> None:
        with self.assertRaises(client.SkillError):
            client.post_scaffold("http://127.0.0.1:1", "x")


# ---------------------------------------------------------------------------
# Bridge chat /api/chat + /api/model-status (tappe 1/2/4) con fake llama-server
# ---------------------------------------------------------------------------
from http.server import BaseHTTPRequestHandler  # noqa: E402

CANNED_REPLY = "Risposta del modello"


class _LlamaRec:
    """Registra l'ultima richiesta al fake llama e ne controlla le risposte."""

    def __init__(self) -> None:
        self.last_body: dict | None = None
        self.chat_status = 200
        self.chat_payload = {"choices": [{"message": {"role": "assistant", "content": CANNED_REPLY}}]}
        self.health_status = 200
        self.usage = True  # il llama-server reale restituisce sempre gli usage


def _fake_usage(body: dict, reply_len: int) -> dict:
    """Usage coerente col body: prompt_tokens cresce con la cronologia inviata."""
    prompt_tokens = sum(len(m.get("content", "")) for m in body.get("messages", []))
    return {"prompt_tokens": prompt_tokens, "completion_tokens": reply_len}


def _llama_handler(rec: _LlamaRec):
    class H(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: dict) -> None:
            b = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                if 200 <= rec.health_status < 300:
                    self._json(200, {"status": "ok"})
                else:
                    self.send_response(rec.health_status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
            else:
                self._json(404, {})

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            rec.last_body = json.loads(raw.decode("utf-8")) if raw else {}
            if self.path == "/v1/chat/completions":
                payload = dict(rec.chat_payload)
                if rec.usage and "choices" in payload:
                    # come llama-server: usage invariata, coerente col body ricevuto
                    payload["usage"] = _fake_usage(
                        rec.last_body or {}, len(payload["choices"][0]["message"]["content"]))
                self._json(rec.chat_status, payload)
            else:
                self._json(404, {})

        def log_message(self, fmt, *args) -> None:
            pass

    return H


class ChatBridgeTest(unittest.TestCase):
    """Gateway + fake llama (porte effimere); verifica bridge chat + model-status."""

    def setUp(self) -> None:
        self.rec = _LlamaRec()
        self.llama = ThreadingHTTPServer(("127.0.0.1", 0), _llama_handler(self.rec))
        self.llama_port = self.llama.server_address[1]
        self.llama_url = f"http://127.0.0.1:{self.llama_port}"
        self.llama_thread = threading.Thread(target=self.llama.serve_forever, daemon=True)
        self.llama_thread.start()

        self._orig = (gateway.LLAMA_URL, gateway.CODER_URL, gateway._TRACKER,
                      dict(gateway._CODER_CACHE))
        gateway.LLAMA_URL = self.llama_url
        gateway.CODER_URL = "http://127.0.0.1:1"  # coder assente: fallback main
        # tracker fresco per test: il modulo potrebbe avere nello storico punti
        # token/s REALI (loadtest) che farebbero scattare la backpressure
        gateway._TRACKER = gateway.SessionTracker(None)
        # laboratorio codice: la postazione di test (127.0.0.1) è abilitata
        gateway._TRACKER.set_state("code_ips", "127.0.0.1")
        # probe coder spento per 30 s: niente rete verso localhost:8082 reale
        gateway._CODER_CACHE.clear()
        gateway._CODER_CACHE.update({"ok": False, "ts": time.time(),
                                     "name": None, "name_ts": 0.0})
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_port = self.gw.server_address[1]
        self.gw_url = f"http://127.0.0.1:{self.gw_port}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        (gateway.LLAMA_URL, gateway.CODER_URL, gateway._TRACKER,
         gateway._CODER_CACHE) = self._orig
        gateway._CODER_CACHE.update(self._orig[3])
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        self.llama.shutdown(); self.llama.server_close(); self.llama_thread.join(timeout=2)

    def _post(self, path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.gw_url + path, data=data,
            headers={"Content-Type": "application/json", **(headers or {})}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.gw_url + path, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    # --- /api/chat happy + normalizzazione --------------------------------

    def test_chat_step_token_caps(self) -> None:
        """Il tetto token è policy di tappa, decisa dal gateway via X-Step: lo
        step "code" (laboratorio codice) ha il tetto dedicato a 4096, la tappa
        ③ (skill) il doppio delle altre (chat libera pre-trigger: niente
        risposte tagliate a metà frase), ①②④ restano al regime difensivo di
        sempre (default 256, ceiling 768)."""
        _, body = self._post("/api/chat", {"messages": [{"role": "user", "content": "card"}]},
                             {"X-Step": "code"})
        self.assertEqual(body["trace"]["request"]["max_tokens"], 4096)
        # altre tappe e assenza di header: default basso invariato
        _, b1 = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]},
                           {"X-Step": "1"})
        self.assertEqual(b1["trace"]["request"]["max_tokens"], 256)
        _, b2 = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(b2["trace"]["request"]["max_tokens"], 256)
        # la ③ (skill) ha il tetto raddoppiato
        _, b4 = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]},
                           {"X-Step": "3"})
        self.assertEqual(b4["trace"]["request"]["max_tokens"], 512)
        # preferenza esplicita del client: rispettata e clampata come prima
        _, b3 = self._post("/api/chat",
                           {"messages": [{"role": "user", "content": "x"}], "max_tokens": 100},
                           {"X-Step": "code"})
        self.assertEqual(b3["trace"]["request"]["max_tokens"], 100)

    def test_chat_tokens_recorded(self) -> None:
        """Change readme-loadtest-consumi (D3): i token del modello (usage)
        si registrano per riga e si persistono — base del grafico token/s."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "s.db")
        gateway._TRACKER = gateway.SessionTracker(db)
        try:
            _, body = self._post("/api/chat",
                                 {"messages": [{"role": "user", "content": "uno"}]},
                                 {"X-Client-Id": "tk"})
            _wait_tracked("tk")
            row = gateway._TRACKER.timeline("tk")["interactions"][0]
            self.assertEqual(row["tok_in"], len("uno"))   # _fake_usage
            self.assertEqual(row["tok_out"], len(CANNED_REPLY))
            # round-trip di persistenza (ALTER TABLE per DB esistenti incluso)
            t2 = gateway.SessionTracker(db)
            self.assertEqual(t2.timeline("tk")["interactions"][0]["tok_out"],
                             len(CANNED_REPLY))
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tps_endpoint(self) -> None:
        """GET /api/tps: la serie globale del ritmo di generazione (tok_out/ms)
        vista dal gateway — il degrado sotto carico si vede qui."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        gateway._TRACKER = gateway.SessionTracker(os.path.join(tmp, "s.db"))
        try:
            for cid in ("a", "b"):
                self._post("/api/chat",
                           {"messages": [{"role": "user", "content": "ciao"}]},
                           {"X-Client-Id": cid})
                _wait_tracked(cid)
            status, body = self._get("/api/tps")
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(status, 200)
        pts = body["points"]
        self.assertEqual(len(pts), 2)
        for p in pts:
            self.assertEqual(p["tok_out"], len(CANNED_REPLY))
            self.assertGreater(p["tps"], 0)  # tok_out / (ms/1000)
            self.assertIn(p["client"], ("a", "b"))

    def test_consumi_endpoint(self) -> None:
        """GET /api/consumi/<cid> (change readme-loadtest-consumi, D5): stime
        locale vs frontiera sui token effettivi della sessione."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        gateway._TRACKER = gateway.SessionTracker(os.path.join(tmp, "s.db"))
        try:
            self._post("/api/chat",
                       {"messages": [{"role": "user", "content": "ciao"}]},
                       {"X-Client-Id": "eco"})
            _wait_tracked("eco")
            status, body = self._get("/api/consumi/eco")
            self.assertEqual(status, 200)
            self.assertTrue(body["has_tokens"])
            self.assertGreater(body["tok_out"], 0)
            self.assertGreater(body["frontiera"]["euro"], 0.0)
            self.assertGreater(body["frontiera"]["acqua_l"], 0.0)
            self.assertGreater(body["locale"]["kwh"], 0.0)
            status, _ = self._get("/api/consumi/inesistente")
            self.assertEqual(status, 404)
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_chat_429_when_overloaded(self) -> None:
        """Backpressure (revisione 11): con le ultime chat sotto la soglia
        token/s il gateway risponde 429 con retry_after invece di accodarsi."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        slow = gateway.SessionTracker(os.path.join(tmp, "slow.db"))
        for _ in range(5):  # tps = 5 token / 5 s = 1 t/s: sotto il pavimento
            slow.record("slow", "chat", "1", 200, 1, 1, 5000.0,
                        tok_in=1, tok_out=5)
        gateway._TRACKER = slow
        try:
            status, body = self._post("/api/chat",
                                      {"messages": [{"role": "user", "content": "ciao"}]})
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(status, 429)
        self.assertTrue(body["overload"])
        self.assertEqual(body["retry_after"], 10)
        self.assertIn("sovraccarico", body["error"])

    def test_chat_429_reopens_when_window_ages(self) -> None:
        """Self-healing (regressione osservata al campo): sotto lockout i 429
        non producono punti nuovi, quindi l'unico ricambio della finestra è
        l'invecchiamento. Con la finestra TEMPORALE il cancello si riapre da
        solo entro _CHAT_TPS_WINDOW_S dall'ultima chat lenta: il carico finito
        non lascia 429 a vita."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        slow = gateway.SessionTracker(os.path.join(tmp, "slow.db"))
        for _ in range(5):
            slow.record("slow", "chat", "1", 200, 1, 1, 5000.0,
                        tok_in=1, tok_out=5)
        gateway._TRACKER = slow
        try:
            body_ok = {"messages": [{"role": "user", "content": "ciao"}]}
            status, _ = self._post("/api/chat", body_ok)
            self.assertEqual(status, 429)  # finestra ancora calda
            # le osservazioni invecchiano oltre la finestra temporale
            with slow._lock:
                for s in slow._sessions.values():
                    for row in s["recent"]:
                        row["ts"] -= gateway._CHAT_TPS_WINDOW_S + 1.0
            status, body = self._post("/api/chat", body_ok)
            self.assertEqual(status, 200)  # cancello riaperto dal ricambio
            self.assertIn("reply", body)
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tps_endpoint_includes_rejected_429(self) -> None:
        """/api/tps porta anche i 429 di backpressure («rejected»): il grafico
        dell'educatore deve raccontare il carico che NON è passato, oltre al
        ritmo di quello passato."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        slow = gateway.SessionTracker(os.path.join(tmp, "slow.db"))
        for _ in range(5):
            slow.record("slow", "chat", "1", 200, 1, 1, 5000.0,
                        tok_in=1, tok_out=5)
        gateway._TRACKER = slow
        try:
            status, _ = self._post("/api/chat",
                                   {"messages": [{"role": "user", "content": "ciao"}]})
            self.assertEqual(status, 429)  # registrato anche lui (kind chat)
            status, body = self._get("/api/tps")
            self.assertEqual(status, 200)
            self.assertEqual(len(body["points"]), 5)      # solo le chat passate
            self.assertEqual(len(body["rejected"]), 1)    # ...e il rifiuto
            self.assertIn("ts", body["rejected"][0])
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_chat_ok_when_tps_healthy(self) -> None:
        """Con cadenza sana niente 429: il meccanismo scatta solo sul degrado
        (e mai a freddo, con poche osservazioni)."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        fast = gateway.SessionTracker(os.path.join(tmp, "fast.db"))
        for _ in range(5):  # 50 token in 100 ms = 500 t/s
            fast.record("fast", "chat", "1", 200, 1, 1, 100.0,
                        tok_in=1, tok_out=50)
        gateway._TRACKER = fast
        try:
            status, body = self._post("/api/chat",
                                      {"messages": [{"role": "user", "content": "ciao"}]})
            self.assertEqual(status, 200)
            self.assertIn("reply", body)
            # a freddo (nessuna osservazione recente) mai 429
            gateway._TRACKER = gateway.SessionTracker(os.path.join(tmp, "cold.db"))
            status, _ = self._post("/api/chat",
                                   {"messages": [{"role": "user", "content": "ciao"}]})
            self.assertEqual(status, 200)
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_chat_response_includes_trace(self) -> None:
        """Change trace-llm (D1/D3): la risposta porta sempre la trace — il body
        normalizzato inoltrato e il payload grezzo, identici al filo."""
        status, body = self._post("/api/chat",
                                  {"messages": [{"role": "user", "content": "ciao"}]})
        self.assertEqual(status, 200)
        self.assertEqual(body["trace"]["request"], self.rec.last_body)
        self.assertEqual(
            body["trace"]["response"]["choices"][0]["message"]["content"], CANNED_REPLY)
        # i parametri applicati dal gateway dopo la normalizzazione sono visibili
        self.assertIn("stream", body["trace"]["request"])

    def test_chat_trace_on_model_error(self) -> None:
        """D7: su errore del modello la response mostrata è il body d'errore."""
        self.rec.chat_status = 500
        status, body = self._post("/api/chat",
                                  {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(status, 502)
        self.assertEqual(body["trace"]["request"], self.rec.last_body)
        self.assertIn("choices", body["trace"]["response"])  # body d'errore ricevuto

    def test_chat_trace_persisted_with_detail(self) -> None:
        """D4/D5: la trace si persiste (req/resp) e sopravvive al riavvio; la
        timeline resta leggera (has_trace), il dettaglio serve la riga piena."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "s.db")
        gateway._TRACKER = gateway.SessionTracker(db)
        try:
            self._post("/api/chat",
                       {"messages": [{"role": "user", "content": "persistente"}]},
                       {"X-Client-Id": "tr"})
            _wait_tracked("tr")
            row = gateway._TRACKER.timeline("tr")["interactions"][0]
            self.assertTrue(row["has_trace"])
            self.assertNotIn("trace", row)  # niente peso nel poll (D4)
            d = gateway._TRACKER.detail("tr", row["ts"])
            self.assertEqual(d["trace"]["request"]["messages"][0]["content"], "persistente")
            # round-trip: nuovo tracker sullo stesso DB = gateway riavviato
            t2 = gateway.SessionTracker(db)
            row2 = t2.timeline("tr")["interactions"][0]
            self.assertTrue(row2["has_trace"])
            self.assertEqual(
                t2.detail("tr", row2["ts"])["trace"]["response"]["choices"][0]
                ["message"]["content"], CANNED_REPLY)
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_chat_records_full_content(self) -> None:
        """Design D3: si registra l'ultimo messaggio utente + la risposta,
        non l'intera cronologia ri-inviata a ogni turno."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        gateway._TRACKER = gateway.SessionTracker(os.path.join(tmp, "s.db"))
        try:
            self._post("/api/chat", {"messages": [
                {"role": "user", "content": "uno"},
                {"role": "assistant", "content": "risposta"},
                {"role": "user", "content": "due"},
            ]}, {"X-Client-Id": "carlo"})
            _wait_tracked("carlo")
            row = gateway._TRACKER.timeline("carlo")["interactions"][0]
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(row["in"], "due")
        self.assertEqual(row["out"], CANNED_REPLY)
        self.assertNotIn("uno\n", row["in"])  # la cronologia non si duplica

    def test_chat_records_turns(self) -> None:
        """Turni trasportati dalla richiesta (change admin-osservabilita, post-
        review): tab A senza memoria = sempre 1, tab B con memoria = crescenti.
        È il segnale «dov'ero / questa chat aveva memoria» per l'educatore."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        gateway._TRACKER = gateway.SessionTracker(os.path.join(tmp, "s.db"))
        try:
            self._post("/api/chat", {"messages": [{"role": "user", "content": "ciao"}]},
                       {"X-Client-Id": "taba"})
            self._post("/api/chat", {"messages": [
                {"role": "user", "content": "ciao mi chiamo Stefano"},
                {"role": "assistant", "content": "Ciao Stefano!"},
                {"role": "user", "content": "come mi chiamo?"},
            ]}, {"X-Client-Id": "tabb"})
            _wait_tracked("taba")
            _wait_tracked("tabb")
            a = gateway._TRACKER.timeline("taba")["interactions"][0]
            b = gateway._TRACKER.timeline("tabb")["interactions"][0]
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(a["turns"], 1)   # senza memoria: parte da solo
        self.assertEqual(b["turns"], 3)   # con memoria: la cronologia viaggia

    def test_chat_returns_reply(self) -> None:
        status, body = self._post("/api/chat", {
            "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "ciao"}],
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], CANNED_REPLY)

    def test_chat_returns_usage_untouched(self) -> None:
        # il gateway inoltra gli usage del servizio modello senza alterarli
        # (contatore token x/2048 della pagina, change laboratorio-context-memoria)
        msgs = [{"role": "user", "content": "ciao mi chiamo Stefano"},
                {"role": "assistant", "content": "Ciao Stefano!"},
                {"role": "user", "content": "come mi chiamo?"}]
        status, body = self._post("/api/chat", {"messages": msgs})
        self.assertEqual(status, 200)
        self.assertEqual(body["usage"], _fake_usage({"messages": msgs}, len(CANNED_REPLY)))

    def test_chat_usage_grows_with_history(self) -> None:
        # a due turni di cronologia il prompt_tokens è maggiore che a uno
        _, one = self._post("/api/chat", {"messages": [{"role": "user", "content": "uno"}]})
        _, two = self._post("/api/chat", {"messages": [
            {"role": "user", "content": "uno"}, {"role": "assistant", "content": "risposta"},
            {"role": "user", "content": "due"}]})
        self.assertGreater(two["usage"]["prompt_tokens"], one["usage"]["prompt_tokens"])

    def test_chat_without_usage_still_replies(self) -> None:
        # se il servizio modello non manda usage, la risposta resta valida
        self.rec.usage = False
        status, body = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], CANNED_REPLY)
        self.assertNotIn("usage", body)

    def test_chat_returns_finish_reason(self) -> None:
        # finish_reason ("stop"/"length") invariata: la pagina la usa per
        # distinguere la risposta completa da quella tagliata dal tetto token
        self.rec.chat_payload["choices"][0]["finish_reason"] = "length"
        status, body = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(status, 200)
        self.assertEqual(body["finish_reason"], "length")

    def test_chat_finish_reason_optional(self) -> None:
        # assente nel payload del modello -> campo omesso (retrocompatibile)
        self.assertNotIn("finish_reason", self._post(
            "/api/chat", {"messages": [{"role": "user", "content": "x"}]})[1])

    def test_chat_normalizes_body(self) -> None:
        # il client tenta di iniettare campi non ammessi e valori fuori scala
        status, body = self._post("/api/chat", {
            "messages": [
                {"role": "system", "content": "s", "grammar": "EVIL"},
                {"role": "user", "content": "u", "EVIL": "x"},
            ],
            "temperature": 99, "max_tokens": 99999, "stream": True, "grammar": "EVIL",
        })
        self.assertEqual(status, 200)
        lb = self.rec.last_body
        # strip delle chiavi extra: ogni messaggio ha SOLO role+content
        self.assertEqual(lb["messages"][0], {"role": "system", "content": "s"})
        self.assertEqual(lb["messages"][1], {"role": "user", "content": "u"})
        # clamp numerici + costanti server-side (non derivabili dal client)
        self.assertEqual(lb["temperature"], 1.5)
        self.assertEqual(lb["max_tokens"], 768)
        self.assertEqual(lb["repeat_penalty"], 1.1)
        self.assertIs(lb["stream"], False)
        # nessuna grammar inoltrata (chat libera, non skill)
        self.assertNotIn("grammar", lb)

    # --- /api/chat 400 ----------------------------------------------------
    def test_chat_bad_messages_400(self) -> None:
        for bad in [{"messages": "x"}, {}, {"messages": []}, {"messages": "ciao"}]:
            with self.subTest(bad=bad):
                status, body = self._post("/api/chat", bad)
                self.assertEqual(status, 400)
                self.assertIn("error", body)

    def test_chat_bad_role_400(self) -> None:
        status, body = self._post("/api/chat", {"messages": [{"role": "tool", "content": "x"}]})
        self.assertEqual(status, 400)

    # --- /api/chat errori modello -----------------------------------------
    def test_chat_llama_down_503(self) -> None:
        orig = gateway.LLAMA_URL
        gateway.LLAMA_URL = "http://127.0.0.1:1"  # porta chiusa
        try:
            status, body = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]})
        finally:
            gateway.LLAMA_URL = orig
        self.assertEqual(status, 503)
        self.assertFalse(body["model_active"])

    def test_chat_llama_http_502(self) -> None:
        self.rec.chat_status = 500
        status, body = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(status, 502)
        self.assertFalse(body["model_active"])

    def test_chat_llama_malformed_502(self) -> None:
        self.rec.chat_payload = {"nope": {}}
        status, body = self._post("/api/chat", {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(status, 502)

    # --- /api/model-status ------------------------------------------------
    def test_model_status_active(self) -> None:
        status, body = self._get("/api/model-status")
        self.assertEqual(status, 200)
        self.assertTrue(body["model_active"])

    def test_model_status_inactive(self) -> None:
        orig = gateway.LLAMA_URL
        gateway.LLAMA_URL = "http://127.0.0.1:1"  # porta chiusa -> sempre 200, bool false
        try:
            status, body = self._get("/api/model-status")
        finally:
            gateway.LLAMA_URL = orig
        self.assertEqual(status, 200)  # mai 5xx
        self.assertFalse(body["model_active"])

    def test_model_status_llama_500_inactive(self) -> None:
        self.rec.health_status = 500
        status, body = self._get("/api/model-status")
        self.assertEqual(status, 200)
        self.assertFalse(body["model_active"])


class CodeLabPolicyTest(unittest.TestCase):
    """Change laboratorio-code: policy gateway dello step "code" (D1–D6).

    Il laboratorio codice è una tappa LOGICA separata (header X-Step: code) con
    policy propria: tetto 4096, allowlist IP, esenzione backpressure + pool
    separato, semaforo 1-generazione-alla-volta, timeout dedicato.
    """

    def setUp(self) -> None:
        self.rec = _LlamaRec()
        self.llama = ThreadingHTTPServer(("127.0.0.1", 0), _llama_handler(self.rec))
        self.llama_thread = threading.Thread(target=self.llama.serve_forever, daemon=True)
        self.llama_thread.start()

        self._orig = (gateway.LLAMA_URL, gateway.CODER_URL, gateway._TRACKER,
                      dict(gateway._CODER_CACHE))
        gateway.LLAMA_URL = f"http://127.0.0.1:{self.llama.server_address[1]}"
        gateway.CODER_URL = "http://127.0.0.1:1"  # coder assente: fallback main
        gateway._TRACKER = gateway.SessionTracker(None)
        # postazione abilitata di default (i test del gate la tolgono): l'IP di
        # chi chiede via server in-process è 127.0.0.1
        gateway._TRACKER.set_state("code_ips", "127.0.0.1")
        # probe coder spento per 30 s: niente rete verso localhost:8082 reale
        gateway._CODER_CACHE.clear()
        gateway._CODER_CACHE.update({"ok": False, "ts": time.time(),
                                     "name": None, "name_ts": 0.0})
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_url = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        (gateway.LLAMA_URL, gateway.CODER_URL, gateway._TRACKER,
         gateway._CODER_CACHE) = self._orig
        gateway._CODER_CACHE.update(self._orig[3])
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        self.llama.shutdown(); self.llama.server_close(); self.llama_thread.join(timeout=2)

    def _post(self, path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.gw_url + path, data=data,
            headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.gw_url + path, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _msgs(self, text: str = "una pagina web") -> dict:
        return {"messages": [{"role": "user", "content": text}]}

    # --- 1.1: tetto dedicato ------------------------------------------------
    def test_code_step_default_and_clamp(self) -> None:
        """Il tetto della tappa code è 4096 (default e ceiling dedicati); per
        le tappe ①②④ il regime non cambia: default 256, ceiling 768."""
        _, b = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
        self.assertEqual(b["trace"]["request"]["max_tokens"], 4096)
        # preferenza esplicita del client rispettata...
        _, b = self._post("/api/chat", {**self._msgs(), "max_tokens": 100},
                          {"X-Step": "code"})
        self.assertEqual(b["trace"]["request"]["max_tokens"], 100)
        # ...e clampata al tetto dedicato, non al ceiling delle altre
        _, b = self._post("/api/chat", {**self._msgs(), "max_tokens": 99999},
                          {"X-Step": "code"})
        self.assertEqual(b["trace"]["request"]["max_tokens"], 4096)
        # regime invariato per ①–④ (e header assente)
        _, b = self._post("/api/chat", self._msgs("x"), {"X-Step": "1"})
        self.assertEqual(b["trace"]["request"]["max_tokens"], 256)
        _, b = self._post("/api/chat", {**self._msgs("x"), "max_tokens": 99999},
                          {"X-Step": "2"})
        self.assertEqual(b["trace"]["request"]["max_tokens"], 768)
        _, b = self._post("/api/chat", {**self._msgs("x"), "max_tokens": 99999})
        self.assertEqual(b["trace"]["request"]["max_tokens"], 768)

    # --- 1.2: allowlist IP ---------------------------------------------------
    def test_code_ip_not_allowed_403(self) -> None:
        """IP fuori allowlist: 403 chiaro, NESSUNA chiamata al modello, riga
        registrata (kind chat, step code, status 403)."""
        gateway._TRACKER.set_state("code_ips", "10.0.0.1")  # postazione non abilitata
        status, body = self._post("/api/chat", self._msgs(),
                                  {"X-Step": "code", "X-Client-Id": "booth"})
        self.assertEqual(status, 403)
        self.assertIn("error", body)
        self.assertIn("laboratorio codice", body["error"])
        self.assertIsNone(self.rec.last_body)      # il modello non è toccato
        _wait_tracked("booth")
        row = gateway._TRACKER.timeline("booth")["interactions"][0]
        self.assertEqual(row["step"], "code")
        self.assertEqual(row["status"], 403)

    def test_code_ip_empty_list_blocks_everyone(self) -> None:
        """Allowlist vuota (default, mai configurata): il laboratorio codice
        è spento per tutti — nessun 'open by default'."""
        gateway._TRACKER.set_state("code_ips", "")
        status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
        self.assertEqual(status, 403)

    def test_code_ip_other_steps_not_gated(self) -> None:
        # il gate è dello step code: le chat ①–④ non conoscono allowlist
        gateway._TRACKER.set_state("code_ips", "")
        status, _ = self._post("/api/chat", self._msgs("ciao"), {"X-Step": "1"})
        self.assertEqual(status, 200)

    def test_code_ip_runtime_change(self) -> None:
        """La policy vale dal salvataggio, senza riavvii: tolgo l'IP → 403,
        lo rimetto → 200."""
        gateway._TRACKER.set_state("code_ips", "")
        status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
        self.assertEqual(status, 403)
        gateway._TRACKER.set_state("code_ips", "127.0.0.1")
        status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
        self.assertEqual(status, 200)

    def test_admin_code_ips_roundtrip_and_validation(self) -> None:
        """POST/GET /api/admin/code-ips: lista o stringa separata da virgole,
        solo IP ESATTI (niente CIDR), 400 con l'elenco dei rifiutati."""
        status, body = self._post("/api/admin/code-ips",
                                  {"ips": ["10.0.0.5", " 10.0.0.6 "]})
        self.assertEqual(status, 200)
        self.assertEqual(body["ips"], ["10.0.0.5", "10.0.0.6"])
        status, body = self._get("/api/admin/code-ips")
        self.assertEqual(status, 200)
        self.assertEqual(body["ips"], ["10.0.0.5", "10.0.0.6"])
        # la policy è già cambiata per questa postazione
        status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
        self.assertEqual(status, 403)
        # forma stringa (quella che salvano i campi di testo del pannello)
        status, body = self._post("/api/admin/code-ips", {"ips": "10.0.0.5,127.0.0.1"})
        self.assertEqual(status, 200)
        self.assertEqual(body["ips"], ["10.0.0.5", "127.0.0.1"])
        # IP nonvalidi: 400 onesto, stato NON toccato
        status, body = self._post("/api/admin/code-ips",
                                  {"ips": ["10.0.0.5", "non-un-ip", "10.0.0.0/24"]})
        self.assertEqual(status, 400)
        self.assertIn("non-un-ip", body["error"])
        _, body = self._get("/api/admin/code-ips")
        self.assertEqual(body["ips"], ["10.0.0.5", "127.0.0.1"])
        # body malformato / campo mancante
        status, _ = self._post("/api/admin/code-ips", {"altro": 1})
        self.assertEqual(status, 400)

    def test_code_ips_survive_restart(self) -> None:
        """L'allowlist vive nel kv della sessions.db: un riavvio del gateway
        non la azzera (spec «Allowlist persistente»)."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "s.db")
        gateway._TRACKER = gateway.SessionTracker(db)
        gateway._TRACKER.set_state("code_ips", "192.168.1.7")
        try:
            gateway._TRACKER = gateway.SessionTracker(db)  # gateway riavviato
            self._post("/api/chat", self._msgs(),
                       {"X-Step": "code", "X-Real-IP": "192.168.1.7"})
            status, _ = self._post("/api/chat", self._msgs(),
                                   {"X-Step": "code", "X-Real-IP": "192.168.1.99"})
            self.assertEqual(status, 403)  # l'allowlist sopravvissuta dice ancora no
        finally:
            gateway._TRACKER = orig
            shutil.rmtree(tmp, ignore_errors=True)

    # --- 1.3: stato per il client -------------------------------------------
    def test_model_status_code_block(self) -> None:
        """/api/model-status porta code {allowed, active, model, ctx} calcolato
        sull'IP di CHI chiede: il client sa solo il proprio esito."""
        _, body = self._get("/api/model-status")
        self.assertTrue(body["code"]["allowed"])       # 127.0.0.1 è in lista
        self.assertFalse(body["code"]["active"])       # coder assente (setUp)
        self.assertEqual(body["code"]["ctx"], gateway.LLAMA_CTX)  # fallback main
        gateway._TRACKER.set_state("code_ips", "10.0.0.1")
        _, body = self._get("/api/model-status")
        self.assertFalse(body["code"]["allowed"])

    def test_model_status_never_leaks_ip_list(self) -> None:
        """La lista degli IP abilitati non arriva MAI ai client generici."""
        gateway._TRACKER.set_state("code_ips", "10.1.2.3,10.1.2.4")
        _, body = self._get("/api/model-status")
        dumped = json.dumps(body)
        self.assertNotIn("10.1.2.3", dumped)
        self.assertNotIn("10.1.2.4", dumped)
        self.assertNotIn("ips", dumped)

    # --- 1.4: esenzione backpressure + pool separato -------------------------
    def test_code_slowness_does_not_trip_backpressure(self) -> None:
        """Generazioni code lente (pagina intera a 0,5 t/s è il NORMALE): le
        loro osservazioni non alimentano il cancello — la chat ① passa."""
        orig = gateway._TRACKER
        slow = gateway.SessionTracker(None)
        for _ in range(5):
            slow.record("coder", "chat", "code", 200, 1, 1, 10000.0,
                        tok_in=1, tok_out=5)
        gateway._TRACKER = slow
        try:
            status, body = self._post("/api/chat", self._msgs("ciao"), {"X-Step": "1"})
        finally:
            gateway._TRACKER = orig
        self.assertEqual(status, 200)
        self.assertIn("reply", body)

    def test_code_requests_bypass_overload_gate(self) -> None:
        """Cancello LOCALE scattato (chat ① lente): la richiesta code non passa
        dal cancello — la lentezza del coder non è un rifiuto (spec)."""
        orig = gateway._TRACKER
        slow = gateway.SessionTracker(None)
        slow.set_state("code_ips", "127.0.0.1")  # il gate IP resta soddisfatto
        for _ in range(5):
            slow.record("slow", "chat", "1", 200, 1, 1, 5000.0, tok_in=1, tok_out=5)
        gateway._TRACKER = slow
        try:
            status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
            self.assertEqual(status, 200)
            # regime invariato: la chat ① nello stesso stato riceve 429
            status, _ = self._post("/api/chat", self._msgs("ciao"), {"X-Step": "1"})
            self.assertEqual(status, 429)
        finally:
            gateway._TRACKER = orig

    def test_code_points_in_separate_pool(self) -> None:
        """I punti token/s della tappa code vivono in un pool SEPARATO: il
        grafico li può mostrare, il cancello non li vede."""
        orig = gateway._TRACKER
        tmp = tempfile.mkdtemp()
        fresh = gateway.SessionTracker(os.path.join(tmp, "s.db"))
        fresh.set_state("code_ips", "127.0.0.1")
        gateway._TRACKER = fresh
        try:
            self._post("/api/chat", self._msgs(), {"X-Step": "code", "X-Client-Id": "ck"})
            self._post("/api/chat", self._msgs("ciao"), {"X-Step": "1", "X-Client-Id": "l1"})
            _wait_tracked("ck"); _wait_tracked("l1")
            status, body = self._get("/api/tps")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            gateway._TRACKER = orig
        self.assertEqual(status, 200)
        pools = {p["client"] for p in body["points"]}
        self.assertEqual(pools, {"l1"})              # solo la chat ①
        self.assertEqual({p["client"] for p in body["code"]["points"]}, {"ck"})

    # --- 1.5: semaforo 1-generazione-alla-volta ------------------------------
    def _slow_llama(self, delay: float) -> tuple[ThreadingHTTPServer, threading.Thread]:
        class SlowLlama(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    b = json.dumps({"status": "ok"}).encode()
                else:
                    b = json.dumps({}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                time.sleep(delay)
                b = json.dumps({"choices": [{"message": {"content": "pagina"}}],
                                "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, fmt, *args) -> None:
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), SlowLlama)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        return srv, th

    def test_code_semaphore_rejects_concurrent(self) -> None:
        """Una generazione locale alla volta per la tappa code: la concorrente
        riceve 429 con retry_after (onesto), senza accodarsi."""
        slow, sth = self._slow_llama(0.8)
        orig = gateway.LLAMA_URL
        gateway.LLAMA_URL = f"http://127.0.0.1:{slow.server_address[1]}"
        results: dict[str, tuple[int, dict]] = {}

        def fire(cid: str) -> None:
            results[cid] = self._post("/api/chat", self._msgs(),
                                      {"X-Step": "code", "X-Client-Id": cid})

        try:
            t1 = threading.Thread(target=fire, args=("prima",))
            t1.start()
            time.sleep(0.3)                 # la prima tiene il semaforo
            fire("seconda")                 # concorrente: rifiutata subito
            t1.join(timeout=5)
            self.assertEqual(results["prima"][0], 200)    # la prima passa
            status, body = results["seconda"]
            self.assertEqual(status, 429)
            self.assertTrue(body["busy"])
            self.assertGreater(body["retry_after"], 0)
            # rilasciata a fine generazione: la prossima riparte
            status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
            self.assertEqual(status, 200)
        finally:
            gateway.LLAMA_URL = orig
            slow.shutdown(); slow.server_close(); sth.join(timeout=2)

    def test_code_semaphore_released_on_error(self) -> None:
        """Il semaforo si rilascia ANCHE a fine generazione con errore: un
        modello giù non lascia il laboratorio bloccato a vita."""
        dead = gateway.LLAMA_URL
        gateway.LLAMA_URL = "http://127.0.0.1:1"  # modello giù
        try:
            status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
            self.assertEqual(status, 503)
        finally:
            gateway.LLAMA_URL = dead
        # semaforo libero: col modello tornato si genera
        status, _ = self._post("/api/chat", self._msgs(), {"X-Step": "code"})
        self.assertEqual(status, 200)

    def test_semaphore_only_on_code_step(self) -> None:
        """Il semaforo è della tappa code: due chat ① in parallelo non si
        rifiutano tra loro (il regime ①–④ non cambia)."""
        slow, sth = self._slow_llama(0.5)
        orig = gateway.LLAMA_URL
        gateway.LLAMA_URL = f"http://127.0.0.1:{slow.server_address[1]}"
        results: list[tuple[int, dict]] = []

        def fire() -> None:
            results.append(self._post("/api/chat", self._msgs("ciao"), {"X-Step": "1"}))

        try:
            t1 = threading.Thread(target=fire); t2 = threading.Thread(target=fire)
            t1.start(); t2.start()
            t1.join(timeout=5); t2.join(timeout=5)
        finally:
            gateway.LLAMA_URL = orig
            slow.shutdown(); slow.server_close(); sth.join(timeout=2)
        self.assertEqual([r[0] for r in results], [200, 200])

    # --- 1.6: timeout dedicato ------------------------------------------------
    def test_code_dedicated_timeout(self) -> None:
        """La path code attende fino a 900 s (4096 token a ~4,5 t/s = 15 min):
        con il timeout dedicato accorciato la lentezza diventa 504 JSON; la
        chat ① sullo stesso modello lento risponde regolare (240 s)."""
        slow, sth = self._slow_llama(0.8)
        orig = (gateway.LLAMA_URL, gateway._CODE_PROXY_TIMEOUT)
        gateway.LLAMA_URL = f"http://127.0.0.1:{slow.server_address[1]}"
        gateway._CODE_PROXY_TIMEOUT = 0.2
        try:
            status, body = self._post("/api/chat", self._msgs(),
                                      {"X-Step": "code", "X-Client-Id": "slowcode"})
            self.assertEqual(status, 504)
            self.assertIn("non ha risposto in tempo", body["error"])
            _wait_tracked("slowcode")
            row = gateway._TRACKER.timeline("slowcode")["interactions"][0]
            self.assertEqual(row["status"], 504)
            # la chat ① usa il timeout di sempre (240 s): il modello lento risponde
            status, _ = self._post("/api/chat", self._msgs("ciao"), {"X-Step": "1"})
            self.assertEqual(status, 200)
        finally:
            gateway.LLAMA_URL, gateway._CODE_PROXY_TIMEOUT = orig
            slow.shutdown(); slow.server_close(); sth.join(timeout=2)

    def test_code_timeout_constant_above_default(self) -> None:
        # catena dichiarata (D5): gateway code 900 s < nginx 960 s, e il 240 s
        # resta quello delle altre chat
        self.assertGreater(gateway._CODE_PROXY_TIMEOUT, gateway._PROXY_TIMEOUT)
        self.assertEqual(gateway._CODE_PROXY_TIMEOUT, 900)


class ObservabilityTest(unittest.TestCase):
    """Osservabilità: client-id, /api/sessions, timeline, storage JSONL, privacy."""

    def setUp(self) -> None:
        service._SKILL = None
        self.skill = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.skill_url = f"http://127.0.0.1:{self.skill.server_address[1]}"
        self.skill_thread = threading.Thread(target=self.skill.serve_forever, daemon=True)
        self.skill_thread.start()

        self._orig = (gateway.SKILL_URL, gateway.LLAMA_URL, gateway._TRACKER)
        gateway.SKILL_URL = self.skill_url
        gateway.LLAMA_URL = "http://127.0.0.1:1"  # modello off
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        gateway._TRACKER = gateway.SessionTracker(self.db)

        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_url = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        gateway.SKILL_URL, gateway.LLAMA_URL, gateway._TRACKER = self._orig
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        self.skill.shutdown(); self.skill.server_close(); self.skill_thread.join(timeout=2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.gw_url + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.gw_url + path, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _wait_session(self, cid: str) -> None:
        """Il record avviene nel thread handler DOPO la risposta: attendo che la
        sessione compaia nel tracker in memoria (memoria e DB si scrivono nello
        stesso critical section, quindi vale anche per la persistenza)."""
        for _ in range(200):
            with gateway._TRACKER._lock:
                if cid in gateway._TRACKER._sessions:
                    return
            time.sleep(0.01)

    def test_client_id_tracked(self) -> None:
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "marco", "X-Step": "3"})
        self._wait_session("marco")
        _, body = self._get("/api/sessions")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["active"][0]["client"], "marco")
        self.assertIn("3", body["active"][0]["steps"])

    def test_scaffold_step_defaults_to_workflow(self) -> None:
        # senza header X-Step lo scaffold è tappa ④ (Workflow): il default non
        # è più l'hardcoded "3" del prima della rinumerazione
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "nopro"})
        self._wait_session("nopro")
        _, body = self._get("/api/sessions")
        self.assertEqual(body["active"][0]["client"], "nopro")
        self.assertIn("4", body["active"][0]["steps"])

    def test_two_clients(self) -> None:
        for cid in ("a", "b"):
            self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": cid})
        self._wait_session("b")
        _, body = self._get("/api/sessions")
        self.assertEqual(body["total"], 2)

    def test_timeline_includes_content(self) -> None:
        """Contenuti completi (change admin-osservabilita): la timeline espone
        input e output dell'interazione, non solo le lunghezze."""
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "x"})
        self._wait_session("x")
        _, body = self._get("/api/sessions/x")
        self.assertTrue(body["interactions"])
        it = body["interactions"][0]
        self.assertEqual(it["in"], NOTES)
        self.assertIn("out", it)

    def test_history_survives_tracker_restart(self) -> None:
        """Spec 'Archivio sessioni persistente': dopo un riavvio del gateway
        (nuovo tracker sullo stesso DB, come un make rebuild) lo storico resta
        consultabile — finestra 'Tutto', IP e contenuti compresi."""
        self._post("/api/scaffold", {"notes": NOTES},
                   {"X-Client-Id": "stable", "X-Real-IP": "192.168.1.9"})
        self._wait_session("stable")
        gateway._TRACKER = gateway.SessionTracker(self.db)  # simula il rebuild
        _, body = self._get("/api/sessions?window=all")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["active"][0]["client"], "stable")
        self.assertEqual(body["active"][0]["last_ip"], "192.168.1.9")
        _, tl = self._get("/api/sessions/stable")
        self.assertEqual(tl["interactions"][0]["in"], NOTES)

    def test_window_query_filters_list(self) -> None:
        # "old" ha ultima attività 9000s fa: fuori da qualunque finestra, dentro "all"
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "old"})
        self._wait_session("old")
        with gateway._TRACKER._lock:
            gateway._TRACKER._sessions["old"]["last_seen"] = time.time() - 9000
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "new"})

        _, body = self._get("/api/sessions?window=600")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["active"][0]["client"], "new")

        _, body = self._get("/api/sessions?window=all")
        self.assertEqual(body["total"], 2)

        # valore non numerico -> finestra di default (300s): "old" resta fuori
        _, body = self._get("/api/sessions?window=pippo")
        self.assertEqual(body["total"], 1)

    def test_ip_tracked_and_filterable(self) -> None:
        self._post("/api/scaffold", {"notes": NOTES},
                   {"X-Client-Id": "a", "X-Real-IP": "192.168.1.7"})
        self._post("/api/scaffold", {"notes": NOTES},
                   {"X-Client-Id": "b", "X-Real-IP": "192.168.1.8"})
        self._wait_session("b")

        _, body = self._get("/api/sessions")
        ips = {a["client"]: a["last_ip"] for a in body["active"]}
        self.assertEqual(ips, {"a": "192.168.1.7", "b": "192.168.1.8"})

        _, body = self._get("/api/sessions?ip=192.168.1.7")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["active"][0]["client"], "a")

    def test_ip_falls_back_to_peer_address(self) -> None:
        # accesso diretto al gateway (CLI/debug): niente X-Real-IP, conta il peer
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "direct"})
        self._wait_session("direct")
        _, body = self._get("/api/sessions")
        self.assertEqual(body["active"][0]["last_ip"], "127.0.0.1")

    def test_consumi_without_tokens_hidden(self) -> None:
        """Revisione: lo scaffold CON usage conta (anche mock: la tabella si
        muove in tappa ③). Il riquadro resta nascosto solo quando non c'è
        stata chiamata con usage — es. il percorso onboarding della skill."""
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "sk"})
        self._wait_session("sk")
        status, body = self._get("/api/consumi/sk")
        self.assertEqual(status, 200)
        self.assertTrue(body["has_tokens"])  # usage raccolto anche dallo scaffold
        # onboarding («ciao?»): risposta senza modello, niente usage, niente tabella
        self._post("/api/scaffold", {"notes": "ciao?"}, {"X-Client-Id": "onb"})
        self._wait_session("onb")
        status, body = self._get("/api/consumi/onb")
        self.assertEqual(status, 200)
        self.assertFalse(body["has_tokens"])

    def test_scaffold_usage_tokens_recorded(self) -> None:
        """Revisione: anche lo scaffold conta nei consumi — il gateway raccoglie
        gli usage che la skill già restituisce (campo opzionale), così la
        tabella del ragazzo si muove anche usando la tappa ③."""
        body_out = {"scaffold": {}, "usage": {"prompt_tokens": 543, "completion_tokens": 210}}

        class FakeSkill(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                b = json.dumps(body_out).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, fmt, *args):  # silenzioso
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSkill)
        st = threading.Thread(target=srv.serve_forever, daemon=True)
        st.start()
        orig = gateway.SKILL_URL
        gateway.SKILL_URL = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "us"})
        finally:
            gateway.SKILL_URL = orig
            srv.shutdown(); srv.server_close(); st.join(timeout=2)
        self._wait_session("us")
        row = gateway._TRACKER.timeline("us")["interactions"][0]
        self.assertEqual(row["tok_in"], 543)
        self.assertEqual(row["tok_out"], 210)
        # e quindi entra nei consumi della sessione
        agg = gateway._TRACKER.consumi("us")
        self.assertTrue(agg["has_tokens"])
        self.assertEqual(agg["tok_out"], 210)

    def test_scaffold_trace_passthrough_and_persisted(self) -> None:
        """Il campo `trace` della skill passa invariato alla pagina (proxy
        trasparente) e viene persistito per il pannello (change trace-llm)."""
        trace = {"request": {"messages": [{"role": "system", "content": "SYS"}],
                             "temperature": 0.2},
                 "response": {"choices": [{"message": {"content": "{\"title\":…}"}}]}}

        class FakeSkill(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                b = json.dumps({"scaffold": {}, "trace": trace}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, fmt, *args):  # silenzioso
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSkill)
        st = threading.Thread(target=srv.serve_forever, daemon=True)
        st.start()
        orig = gateway.SKILL_URL
        gateway.SKILL_URL = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            status, body = self._post("/api/scaffold", {"notes": NOTES},
                                      {"X-Client-Id": "sk"})
        finally:
            gateway.SKILL_URL = orig
            srv.shutdown(); srv.server_close(); st.join(timeout=2)
        self.assertEqual(status, 200)
        self.assertEqual(body["trace"], trace)  # pass-through invariato
        self._wait_session("sk")
        row = gateway._TRACKER.timeline("sk")["interactions"][0]
        self.assertTrue(row["has_trace"])
        self.assertEqual(gateway._TRACKER.detail("sk", row["ts"])["trace"], trace)

    def test_detail_endpoint(self) -> None:
        """GET /api/sessions/<cid>/<ts>: riga completa; 404 se il ts non c'è.
        Col backend mock della skill non c'è chiamata LLM: has_trace falso."""
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "x"})
        self._wait_session("x")
        _, tl = self._get("/api/sessions/x")
        self.assertFalse(tl["interactions"][0]["has_trace"])
        ts = tl["interactions"][0]["ts"]
        status, row = self._get(f"/api/sessions/x/{ts}")
        self.assertEqual(status, 200)
        self.assertEqual(row["client"], "x")
        self.assertNotIn("trace", row)
        status, _ = self._get("/api/sessions/x/12345.678")
        self.assertEqual(status, 404)

    def test_model_status_has_clients_and_model(self) -> None:
        _, body = self._get("/api/model-status")
        self.assertIn("clients", body)
        self.assertIn("model", body)
        self.assertFalse(body["model_active"])  # llama off in questo test


class SessionTrackerTest(unittest.TestCase):
    """Unit test del tracker (change admin-osservabilita): IP, contenuti, storico integro."""

    def test_record_populates_ip(self) -> None:
        t = gateway.SessionTracker(None)
        t.record("c1", "chat", "1", 200, 10, 5, 12.0, ip="192.168.1.7")
        row = t.timeline("c1")["interactions"][0]
        self.assertEqual(row["ip"], "192.168.1.7")
        self.assertEqual(t.active_list(300)["active"][0]["last_ip"], "192.168.1.7")

    def test_ip_filter_matches_any_seen_ip(self) -> None:
        # stessa sessione vista da due IP (localStorage portata altrove): il
        # filtro matcha l'insieme degli IP, non solo l'ultimo
        t = gateway.SessionTracker(None)
        t.record("c1", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.1")
        t.record("c1", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.2")
        t.record("c2", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.9")
        got = t.active_list(300, ip="10.0.0.2")
        self.assertEqual([a["client"] for a in got["active"]], ["c1"])
        self.assertEqual(got["total"], 1)

    def test_record_stores_full_content(self) -> None:
        t = gateway.SessionTracker(None)
        t.record("c1", "chat", "1", 200, 7, 3, 5.0,
                 in_text="come mi chiamo?", out_text="Stefano")
        row = t.timeline("c1")["interactions"][0]
        self.assertEqual(row["in"], "come mi chiamo?")
        self.assertEqual(row["out"], "Stefano")

    def test_timeline_not_capped_at_200(self) -> None:
        # una sessione di laboratorio lunga supera le 200 interazioni: lo
        # storico in memoria non si tronca più
        t = gateway.SessionTracker(None)
        for _ in range(205):
            t.record("c1", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.1")
        self.assertEqual(len(t.timeline("c1")["interactions"]), 205)

    def test_window_all_ignores_cutoff(self) -> None:
        t = gateway.SessionTracker(None)
        t.record("old", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.1")
        with t._lock:
            t._sessions["old"]["last_seen"] = time.time() - 9999
        t.record("new", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.1")
        self.assertEqual(t.active_list(None)["total"], 2)  # all
        self.assertEqual(t.active_list(300)["total"], 1)   # finestra


class AdminPageTest(unittest.TestCase):
    """Pannello educatore (change admin-osservabilita): finestra selezionabile,
    filtro IP, espansione dell'interazione, banner privacy rimosso."""

    def _read(self) -> str:
        with open(os.path.join(_REPO, "backend/web/static/admin.html"), encoding="utf-8") as f:
            return f.read()

    def test_window_selector(self) -> None:
        body = self._read()
        for w in ("300", "600", "900", "1800", "all"):
            self.assertIn(f'data-w="{w}"', body)
        # la finestra viaggia come query param dell'elenco sessioni
        self.assertIn("window=", body)

    def test_ip_shown_and_filterable(self) -> None:
        body = self._read()
        self.assertIn("last_ip", body)   # l'IP arriva dalla risposta API
        self.assertIn("ip=", body)       # e torna come filtro query
        self.assertIn("IPF", body)       # stato del filtro (chip attivo)

    def test_interaction_expands_content(self) -> None:
        body = self._read()
        # il click espande i contenuti completi già nella risposta (design D4)
        self.assertIn("it.in", body)
        self.assertIn("it.out", body)
        self.assertIn("open", body)

    def test_chat_rows_show_turns(self) -> None:
        """La riga chat dichiara quanti messaggi la richiesta trasportava:
        senza memoria = sempre 1 turno, con memoria = crescenti. È il segnale
        «questa chat aveva memoria, ero al turno N» per l'educatore."""
        body = self._read()
        self.assertIn("it.turns", body)
        self.assertIn("turno", body)

    def test_conversation_view(self) -> None:
        """Vista conversazione: i delta impilati ricostruiscono il transcript
        (design D3 — la cronologia non si memorizza, si ricostruisce)."""
        body = self._read()
        self.assertIn("tlview", body)              # toggle vista
        self.assertIn("renderConversation", body)  # renderer del transcript
        self.assertIn("msg user", body)            # bolle 👤/🤖
        self.assertIn("msg ai", body)

    def test_conversation_copy_button(self) -> None:
        """Richiesta dell'educatore: la conversazione letta nel pannello si
        copia IN TOTO come testo, per incollarla altrove (supporto remoto,
        relazioni). Trascrizione testuale dalla stessa risposta della timeline
        (nessun round-trip), fallback textarea + execCommand perché la LAN
        HTTP non è secure context, popup selezionabile a mano se anche quello
        è bloccato."""
        body = self._read()
        self.assertIn('id="tlcopy"', body)           # bottone accanto al toggle
        self.assertIn("conversationText", body)      # trascrizione testuale
        self.assertIn("execCommand", body)           # fallback secure context
        self.assertIn("LAST_TL", body)               # dati tra un poll e l'altro
        self.assertIn("] ragazzo:", body)            # le battute complete
        self.assertIn("] modello:", body)
        self.assertIn('symbol id="i-copy"', body)    # icona SVG (niente emoji)

    def test_open_state_preserved_across_refresh(self) -> None:
        """L'auto-refresh ricostruisce il DOM ogni 3s: lo stato "espansa" di una
        interazione vive in una mappa lato JS e viene riapplicato al re-render,
        altrimenti ogni tick chiude i dettagli che l'educatore sta leggendo."""
        body = self._read()
        self.assertIn("var OPEN", body)        # mappa delle voci espanse
        self.assertIn("OPEN[key]", body)       # riapplicata quando si ricostruisce
        self.assertIn("delete OPEN[key]", body)  # toggle al click

    def test_tps_chart(self) -> None:
        """Grafico token/s nel pannello (change readme-loadtest-consumi, D3):
        SVG disegnato a mano, zero librerie, ritmo visto dal gateway."""
        body = self._read()
        self.assertIn('id="tps"', body)   # il box del grafico
        self.assertIn("renderTps", body)  # rendering della serie
        self.assertIn("/api/tps", body)   # fonte dati
        self.assertIn("token/s", body)    # etichetta

    def test_tps_chart_readability(self) -> None:
        """Revisione leggibilità: unità di misura sui valori, tooltip coi valori
        puntuali al passaggio del mouse, griglia tratteggiata di guida."""
        body = self._read()
        self.assertIn("t/s", body)                 # unità di misura
        self.assertIn("tpstip", body)              # tooltip dei punti
        self.assertIn("stroke-dasharray", body)    # linee tratteggiate
        self.assertIn("mouseenter", body)          # hover sui punti

    def test_tps_chart_backpressure_line(self) -> None:
        """Il target di backpressure è disegnato: linea rossa tratteggiata sul
        pavimento token/s, scala sempre inclusiva del target, costante pagina
        e gateway tenute sincrone dal test."""
        body = self._read()
        with open(os.path.join(_REPO, "backend/gateway.py"), encoding="utf-8") as f:
            gw = f.read()
        self.assertIn("TPS_FLOOR = 10", body)
        self.assertIn("_CHAT_TPS_FLOOR = 10.0", gw)
        self.assertIn('"#e5534b"', body)        # la linea rossa
        self.assertIn("backpressure", body)     # etichetta sul target

    def test_tps_chart_time_axis_advances(self) -> None:
        """Regressione (osservato al campo): l'asse X era a indice di punto, così
        senza nuove chat il grafico restava congelato sull'ultima richiesta —
        sotto 429 proprio quando serviva vederlo muovere. Ora la finestra è
        temporale e ancorata a «adesso»: scorre a ogni tick."""
        body = self._read()
        self.assertIn("Date.now()", body)            # ancorata a «adesso»
        self.assertIn("adesso", body)                # etichetta del bordo destro
        self.assertIn("nessuna chat nella finestra", body)  # finestra vuota visibile
        # niente più posizionamento a indice: il punto sta al suo posto nel tempo
        self.assertNotIn("xAt(i)", body)

    def test_tps_chart_follows_window_selection(self) -> None:
        """Richiesta dell'educatore: il grafico deve seguire la finestra scelta
        per l'elenco (5m/10m/15m/30m/tutto), non avere una larghezza propria:
        elenco e grafico raccontano lo stesso pezzo di laboratorio."""
        import re
        body = self._read()
        m = re.search(r"function setWindow[\s\S]{0,300}", body)
        self.assertIsNotNone(m, "setWindow non trovata")
        self.assertIn("renderTps", m.group(0))       # la selezione ridisegna subito
        self.assertIn("parseInt(WIN", body)          # la larghezza viene da WIN
        self.assertIn('"all"', body)                 # «tutto» = dal dato più vecchio
        self.assertIn("TPS_MIN_BUCKET_S", body)      # fasce che si adattano alla larghezza

    def test_tps_chart_aggregates_avg_and_min(self) -> None:
        """Richiesta dell'educatore: niente punta-punta di TUTTE le chat raccolte
        (sotto loadtest è rumore) — due linee per fascia temporale, la media e
        il minimo, con legenda."""
        body = self._read()
        self.assertIn("bucketS", body)               # aggregazione per fascia
        self.assertIn('"media"', body)               # le due serie
        self.assertIn('"minimo"', body)
        self.assertIn("Math.min", body)              # il minimo è davvero il minimo
        self.assertIn("a.reduce", body)              # ...e la media una media
        # il punta-punta di ogni punto raccolto non c'è più (il polyline è
        # solo quello delle due linee aggregate)
        self.assertNotIn("points: win.map", body)

    def test_tps_chart_shows_rejected_429(self) -> None:
        """Il carico che NON passa (429 di backpressure) non produce punti:
        senza una serie dedicata il grafico non racconta il sovraccarico.
        Ora i rifiuti viaggiano in «rejected» e diventano tacche rosse."""
        body = self._read()
        self.assertIn("rejected", body)         # serie dei 429 dal gateway
        self.assertIn("429:", body)             # conteggio nella didascalia

    def test_ip_filter_input(self) -> None:
        """Revisione: il filtro IP si scrive anche a mano, non solo col click
        sull'IP del riquadro."""
        body = self._read()
        self.assertIn('id="ipinput"', body)
        self.assertIn("filtra per IP", body)

    def test_consumi_box_not_in_admin(self) -> None:
        """Revisione del change readme-loadtest-consumi: il riquadro consumi è
        ROBA DA RAGAZZI — sta nella pagina del laboratorio, non qui."""
        body = self._read()
        self.assertNotIn('id="consumi"', body)
        self.assertNotIn("/api/consumi/", body)

    def test_privacy_banner_removed(self) -> None:
        body = self._read()
        self.assertNotIn("Solo <b>metadati</b>", body)
        self.assertNotIn('class="privacy"', body)
        # niente finestra hardcoded
        self.assertNotIn("ultimi 5 min", body)


class SessionPersistenceTest(unittest.TestCase):
    """Persistenza sqlite3 (revisione admin-osservabilita): lo storico sopravvive
    ai riavvii del gateway — load-all all'avvio (design D7/D8), nessun import
    del JSONL legacy (D9: il DB nasce vuoto)."""

    def test_load_on_startup(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "sessions.db")
        t1 = gateway.SessionTracker(db)
        t1.record("c1", "chat", "1", 200, 5, 3, 9.0,
                  in_text="ciao", out_text="ehi", ip="10.0.0.5")
        t1.record("c2", "scaffold", "3", 200, 10, 10, 20.0, ip="10.0.0.6")
        # nuovo tracker sullo stesso DB = gateway riavviato (make rebuild)
        t2 = gateway.SessionTracker(db)
        self.assertEqual({a["client"] for a in t2.active_list(None)["active"]},
                         {"c1", "c2"})
        row = t2.timeline("c1")["interactions"][0]
        self.assertEqual((row["in"], row["out"], row["ip"]),
                         ("ciao", "ehi", "10.0.0.5"))
        self.assertEqual(t2.timeline("c2")["interactions"][0]["kind"], "scaffold")

    def test_reload_restores_ips_counts_and_filters(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "sessions.db")
        t1 = gateway.SessionTracker(db)
        t1.record("c1", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.1")
        t1.record("c1", "chat", "2", 200, 1, 1, 1.0, ip="10.0.0.2")
        t2 = gateway.SessionTracker(db)
        got = t2.active_list(None)
        self.assertEqual(got["active"][0]["count"], 2)
        self.assertEqual(got["active"][0]["last_ip"], "10.0.0.2")
        # il filtro per IP copre anche lo storico ricaricato dal DB
        self.assertEqual(t2.active_list(None, ip="10.0.0.1")["total"], 1)
        self.assertEqual(t2.active_list(None, ip="10.0.0.2")["total"], 1)

    def test_window_all_covers_reloaded_history(self) -> None:
        # riga presente SOLO nel DB: dopo il "riavvio" resta visibile con
        # window=all e cade da una finestra breve, come deve
        db = os.path.join(tempfile.mkdtemp(), "sessions.db")
        t1 = gateway.SessionTracker(db)
        t1.record("old", "chat", "1", 200, 1, 1, 1.0, ip="10.0.0.1")
        t2 = gateway.SessionTracker(db)
        with t2._lock:
            t2._sessions["old"]["last_seen"] = time.time() - 9999
        self.assertEqual(t2.active_list(None)["total"], 1)
        self.assertEqual(t2.active_list(300)["total"], 0)


class TracePopupPageTest(unittest.TestCase):
    """Popup della trace LLM (change trace-llm): bottone per dialogo e popup
    request/response nella pagina; il pannello la serve da dettaglio persistito."""

    def _read(self, rel: str) -> str:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            return f.read()

    def test_page_has_trace_button_and_popup(self) -> None:
        body = self._read("backend/web/static/index.html")
        self.assertIn("trace-btn", body)      # bottone { } sul dialogo
        self.assertIn("addTraceBtn", body)    # attacco al turno/bubble/risultato
        self.assertIn("showTrace", body)      # il popup
        self.assertIn("JSON.stringify", body)  # pretty-print lato client
        # i parametri del modello arrivano a runtime dalla API, mai hardcodati
        # (vincolo del tier statico, design D6)
        self.assertNotIn("repeat_penalty", body)

    def test_admin_has_trace_button_with_detail_fetch(self) -> None:
        body = self._read("backend/web/static/admin.html")
        self.assertIn("has_trace", body)      # flag leggero dalle righe timeline
        self.assertIn("trace-btn", body)      # bottone sulle righe
        self.assertIn("showTrace", body)      # popup
        # il dettaglio recupera la riga completa dal server (design D4)
        self.assertIn("encodeURIComponent(ts)", body)  # fetch dentro traceBtn()


class JsSyntaxTest(unittest.TestCase):
    """Le pagine portano JS inline non compilato: un errore di sintassi rompe
    TUTTA la pagina a runtime e i test di stringa non lo vedono (osservato:
    graffa mancante nella refactor del 429, beccata solo dal browser).
    node --check come guardia; skip se node non è installato."""

    def _check(self, rel: str) -> None:
        if shutil.which("node") is None:
            self.skipTest("node non disponibile")
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            blocks = re.findall(r"<script>(.*?)</script>", f.read(), re.S)
        self.assertTrue(blocks, rel)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write("\n;\n".join(blocks))
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 0, f"{rel}:\n{r.stderr[:600]}")
        finally:
            os.unlink(path)

    def test_index_js_syntax(self) -> None:
        self._check("backend/web/static/index.html")

    def test_admin_js_syntax(self) -> None:
        self._check("backend/web/static/admin.html")

    def test_code_js_syntax(self) -> None:
        self._check("backend/web/static/code.html")


class NginxTierTest(unittest.TestCase):
    """Tier statico nginx: verifica della CONFIG (il processo gira nel compose).

    - static/ puri: index/admin/favicon presenti, zero CDN (niente https://)
    - reverse proxy /api/* -> gateway (same-origin, no CORS)
    - nessun parametro del modello nel tier di presentazione
    """

    def _read(self, rel: str) -> str:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            return f.read()

    def test_static_assets_present(self) -> None:
        for rel in ["backend/web/static/index.html",
                    "backend/web/static/admin.html",
                    "backend/web/static/code.html",
                    "backend/web/static/favicon.svg"]:
            self.assertTrue(os.path.isfile(os.path.join(_REPO, rel)), rel)

    def test_index_zero_cdn(self) -> None:
        body = self._read("backend/web/static/index.html")
        self.assertIn("Laboratorio di Prompting", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("<link", body)  # niente fogli di stile esterni

    def test_code_page_zero_cdn(self) -> None:
        """La pagina del laboratorio codice è leggera e offline come index:
        zero CDN, un solo file, sprite SVG interno."""
        body = self._read("backend/web/static/code.html")
        self.assertIn("Laboratorio codice", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("<link", body)

    def test_nginx_proxies_api_to_gateway(self) -> None:
        conf = self._read("nginx.conf")
        self.assertIn("location /api/", conf)
        self.assertIn("proxy_pass http://gateway:8090", conf)
        # same-origin: nessuna intestazione CORS nella config
        self.assertNotIn("Access-Control-Allow", conf)
        # il timeout copre le generazioni lente del modello (come l'adapter)
        self.assertIn("proxy_read_timeout", conf)

    def test_nginx_serves_static_root(self) -> None:
        conf = self._read("nginx.conf")
        self.assertIn("root /usr/share/nginx/html", conf)
        # il pannello educatore resta raggiungibile su /admin
        self.assertIn("/admin.html", conf)
        # favicon: SVG servito su /favicon.ico (parità col vecchio server)
        self.assertIn("favicon.svg", conf)

    def test_static_tier_has_no_model_params(self) -> None:
        """D4/spec: nessun parametro del modello nel tier di presentazione."""
        for rel in ["backend/web/static/index.html", "backend/web/static/admin.html",
                    "backend/web/static/code.html"]:
            body = self._read(rel)
            self.assertNotIn("repeat_penalty", body)
            self.assertNotIn("max_tokens", body)
            # la pagina chiama SOLO /api/* (mai :8081 diretto)
            self.assertNotIn(":8081", body)
            self.assertNotIn("8080", body)

    def test_static_tier_has_no_emoji(self) -> None:
        """Il font del kiosk non ha glifi emoji: nelle pagine non si vedono
        (buchi al posto di icone, osservato al campo al primo 429). Le icone
        sono SVG inline nello sprite della pagina; i glifi testuali comuni
        (① ✓ ✕ … «») restano perché coperti dal font di sistema."""
        emoji = re.compile(
            "[\U0001F000-\U0001FAFF"      # blocchi emoji veri e propri
            "☀-⛿"               # misc symbols: ⚠ ⚙ ☁ …
            "✀-✒✔✖-➿"  # dingbats, salvo ✓ (2713) e ✕ (2715)
            "⬀-⯿️"         # frecce-star, variation selector
            "]")
        for rel in ["backend/web/static/index.html",
                    "backend/web/static/admin.html",
                    "backend/web/static/code.html",
                    "backend/web/static/favicon.svg"]:
            body = self._read(rel)
            m = emoji.search(body)
            self.assertIsNone(
                m, f"{rel}: glifo emoji non renderizzabile "
                   f"({body[m.start():m.start() + 2]!r} a offset {m.start()})"
                if m else f"{rel}: contiene emoji")


class ContextMemoryPageTest(unittest.TestCase):
    """Change laboratorio-context-memoria: contratto della pagina per le
    tappe ① (context injection a due tab) e ② (system prompt a soli preset).

    La pagina non ha harness JS: come NginxTierTest, si verifica il contratto
    strutturale (marcatori DOM/JS in index.html). Il comportamento end-to-end
    è coperto dal passaggio manuale (task 5.2 del change).
    """

    def _read(self) -> str:
        with open(os.path.join(_REPO, "backend/web/static/index.html"),
                  encoding="utf-8") as f:
            return f.read()

    # --- Tappa ①: due tab --------------------------------------------------
    def test_step1_has_two_tabs(self) -> None:
        body = self._read()
        self.assertIn('data-tab="no-mem"', body)   # senza memoria
        self.assertIn('data-tab="mem"', body)      # con memoria
        self.assertIn("Senza memoria", body)
        self.assertIn("Con memoria", body)

    def test_step1_no_memory_chat_sends_only_last_message(self) -> None:
        body = self._read()
        # la factory inoltra solo l'ultimo messaggio quando memory è false
        self.assertIn("opts.memory === false", body)
        # le due istanze della tappa ① dichiarano esplicitamente memory
        self.assertIn("memory: false", body)
        self.assertIn("memory: true", body)

    def test_step1_hint_on_memory_failure(self) -> None:
        body = self._read()
        self.assertIn("solo questo messaggio", body)

    # --- Contatore token ----------------------------------------------------
    def test_context_meter_present(self) -> None:
        body = self._read()
        self.assertIn("ctx-meter", body)            # barra + testo
        self.assertIn("usage", body)                # consuma usage.prompt_tokens
        self.assertIn("prompt_tokens", body)
        self.assertIn("2048", body)                 # limite reale (-c 2048)

    def test_context_meter_on_all_chats(self) -> None:
        # 4 chat metered: tab A, tab B, tappe ② e ③ (la barra non sparisce);
        # la quinta chat (ex ⑤) vive ora nella pagina del laboratorio codice
        self.assertEqual(self._read().count("meter: true"), 4)

    def test_new_conversation_button(self) -> None:
        body = self._read()
        self.assertIn("Nuova conversazione", body)

    def test_context_full_friendly_message(self) -> None:
        body = self._read()
        self.assertIn("Contesto pieno", body)

    # --- Tappa ②: preset + blob --------------------------------------------
    def test_step2_textarea_removed(self) -> None:
        self.assertNotIn('id="sys-prompt"', self._read())

    def test_step2_two_presets_only(self) -> None:
        body = self._read()
        # 2 preset: "Solo HTML" rimosso (poco significativo, poco affidato)
        self.assertEqual(body.count('class="preset" data-sys='), 2)
        self.assertNotIn("Solo HTML", body)
        self.assertIn("aria-pressed", body)        # selezione esclusiva

    def test_step2_preset_change_starts_new_conversation(self) -> None:
        # cambiare system a conversazione avviata azzera: niente cronologia
        # sporca tra system diversi
        body = self._read()
        self.assertIn("chat2.reset()", body)

    def test_step2_blob_shows_system_in_head(self) -> None:
        body = self._read()
        self.assertIn('id="blob"', body)            # area blob
        self.assertIn("blob-sys", body)             # blocco ⚙ SYSTEM in testa
        self.assertIn("blob-hist", body)            # cronologia sotto

    def test_step2_presets_are_forceful_and_tested(self) -> None:
        # verificati empiricamente sul 1.5B-instruct: i prompt cortesi italiani
        # vengono ignorati; servono forma perentoria + esempio + anti-rifiuto
        import re
        body = self._read()
        presets = re.findall(r'data-sys="([^"]*)"', body)
        self.assertEqual(len(presets), 2)
        for p in presets:
            self.assertIn("NON rifiutarti mai", p)

    def test_step2_low_temperature_for_adherence(self) -> None:
        # temp 0.3 (vs default 0.7) misurata: il formato del system prompt regge
        # anche sulle domande-trappola; il gateway resta l'arbitro (clamp 0–1.5)
        body = self._read()
        self.assertIn("temperature: 0.3", body)

    # --- Limiti espliciti (oltre al contesto 2048 c'è il tetto in uscita) ---
    def test_output_token_cap_displayed(self) -> None:
        body = self._read()
        self.assertIn("risposta max", body)          # tetto uscita visibile
        self.assertIn("limiti imposti dal server", body)  # origine del limite

    def test_displayed_limits_match_gateway_defaults(self) -> None:
        # i valori mostrati devono essere quelli che il gateway applica davvero
        body = self._read()
        gw = self._gateway_src()
        self.assertIn("_CHAT_DEFAULT_MAX_TOKENS = 256", gw)
        self.assertIn("opts.maxOut || 256", body)    # default pagina = default gateway
        # la tappa ③ dichiara il suo tetto raddoppiato, non il default
        self.assertIn('"3": 512', gw)
        self.assertIn("maxOut: 512", body)

    def test_truncated_reply_note(self) -> None:
        # nota ✂️ quando la risposta è tagliata dal tetto token: verdetto da
        # finish_reason ("length") con risvolto su completion_tokens al tetto
        body = self._read()
        self.assertIn('finish_reason === "length"', body)
        self.assertIn("completion_tokens >= maxOut", body)
        self.assertIn("tagliata dal limite", body)

    def _gateway_src(self) -> str:
        with open(os.path.join(_REPO, "backend/gateway.py"), encoding="utf-8") as f:
            return f.read()


class SkillWorkflowPageTest(unittest.TestCase):
    """Change laboratorio-skill-workflow: percorso a cinque tappe (nuova ③ Skills
    con caricamento visibile, ex ③ → ④ Workflow, ex ④ → ⑤ Prompt Engineering).

    Come ContextMemoryPageTest: contratto strutturale su index.html; il
    comportamento end-to-end è coperto dal passaggio manuale (task 5.2).
    """

    def _read(self) -> str:
        with open(os.path.join(_REPO, "backend/web/static/index.html"),
                  encoding="utf-8") as f:
            return f.read()

    # --- Percorso a quattro tappe -------------------------------------------
    def test_four_steps_in_order(self) -> None:
        """Change laboratorio-code (D9): la ⑤ (Prompt Engineering) lascia la
        pagina del percorso guidato — è il laboratorio codice, su pagina
        dedicata. Qui NON compaiono link né riferimenti: l'accesso lo dà
        l'educatore, che la apre dal pannello /admin."""
        import re
        body = self._read()
        dots = re.findall(r'data-step-dot="(\d)"[^>]*>([^<]+)<', body)
        self.assertEqual([d[0] for d in dots], ["0", "1", "2", "3"])
        labels = " ".join(d[1] for d in dots)
        for lbl in ("① Context", "② System", "③ Skills", "④ Workflow"):
            self.assertIn(lbl, labels)
        self.assertIn("maxStep: 4", body)
        # le sezioni seguono lo stesso ordine dei dot
        heads = re.findall(r'<section class="step" data-step="(\d)">\s*<h2>(.+?)</h2>', body)
        self.assertEqual([h[0] for h in heads], ["0", "1", "2", "3"])
        self.assertIn("④ Workflow", heads[3][1])
        # nessuna traccia della quinta tappa: né sezione, né link, né icone
        self.assertNotIn("Prompt Engineering", body)
        self.assertNotIn("code.html", body)
        self.assertNotIn('id="tb5"', body)

    # --- Tappa ③: skill leggibile a due livelli ------------------------------
    def test_step3_skill_panel_two_levels(self) -> None:
        body = self._read()
        self.assertIn('id="skill-panel"', body)        # pannello della skill
        self.assertIn('id="skill-desc"', body)         # descrizione sempre visibile
        self.assertIn("<details", body)                # corpo espandibile (CSS-only)
        self.assertIn('id="skill-body-display"', body)  # corpo della skill in pagina
        self.assertIn("Diario di Bordo", body)         # la skill ha un nome

    def test_step3_skill_body_in_page(self) -> None:
        # il corpo della skill vive nella pagina come documento leggibile:
        # perentorio, con esempio e clausola anti-rifiuto (lezione dei preset ②)
        body = self._read()
        self.assertIn("NON rifiutarti mai", body)
        self.assertIn("ESEMPIO", body)

    # --- Tappa ③: regola di caricamento -------------------------------------
    def test_step3_trigger_rule_visible(self) -> None:
        body = self._read()
        self.assertIn("TRIGGER_WORDS", body)           # regola dichiarata nel codice
        self.assertIn('id="skill-rule"', body)         # …e mostrata sotto la chat
        self.assertIn("decide l'agente", body)         # nota: nel mondo reale

    def test_step3_trigger_is_keyword_based(self) -> None:
        import re
        body = self._read()
        m = re.search(r"TRIGGER_WORDS = \[([^\]]*)\]", body)
        self.assertIsNotNone(m, "TRIGGER_WORDS deve essere un array letterale")
        words = [w.strip(" '\"") for w in m.group(1).split(",")]
        self.assertGreaterEqual(len(words), 3)
        self.assertTrue(all(w and w == w.lower() for w in words))
        # set generoso: il diario dev'essere innescabile al campo
        self.assertIn("diario", words)

    # --- Tappa ③: iniezione reale + evidenziazione --------------------------
    def test_step3_skill_injected_as_system(self) -> None:
        body = self._read()
        # il gate decide il system in base al messaggio: makeChat passa il testo
        self.assertIn("opts.system(text)", body)
        self.assertIn("system: skillSystem", body)       # la chat ③ usa il gate
        self.assertIn("return skillActive ? SKILL_BODY : null", body)

    def test_step3_activation_highlighted_three_ways(self) -> None:
        body = self._read()
        self.assertIn('id="blob3-skill"', body)          # (a) blocco skill nel blob
        self.assertIn('href="#i-gear"', body)            # icona SVG (le emoji non si vedono)
        self.assertIn("SKILL · Diario di Bordo", body)
        self.assertIn("skill-loaded", body)              # (b) divisore nel dialogo
        self.assertIn("Skill «Diario di Bordo» caricata", body)
        self.assertIn("skill-badge", body)               # (c) badge sulle risposte
        self.assertIn("badge: function", body)

    def test_step3_skill_persists_across_offtopic(self) -> None:
        import re
        body = self._read()
        # a parte la dichiarazione, l'UNICO scarico è il reset (cronologia
        # vuota): i messaggi fuori tema successivi non tolgono la skill
        unloads = re.findall(r"(?<!var )skillActive = false", body)
        self.assertEqual(len(unloads), 1)
        self.assertIn("if (empty) skillActive = false", body)

    def test_step3_reset_unloads_skill(self) -> None:
        body = self._read()
        # il reset ripassa da onHist([]) -> renderBlob3 svuota il blob e scarica
        self.assertIn("onHist: renderBlob3", body)

    def test_step3_chat_wiring(self) -> None:
        body = self._read()
        self.assertIn('getElementById("tb3")', body)
        # con memoria, contatore e temperatura 0.3 (adesione al formato, come ②)
        self.assertIn("step: 3, memory: true, meter: true, temperature: 0.3", body)

    # --- Tappa ④: workflow con pipeline visibile ------------------------------
    def test_step4_pipeline_visible(self) -> None:
        body = self._read()
        self.assertIn('id="pipeline"', body)             # contenitore pipeline
        self.assertIn("catena di montaggio in azione", body)
        self.assertIn("setTimeout(next, 320)", body)     # gli stage avanzano in sequenza
        self.assertIn("workflow completato", body)       # fine pipeline visibile

    def test_step4_stages_are_events_not_cot(self) -> None:
        body = self._read()
        # gli stage sono gli eventi sintetici del servizio /scaffold, mai CoT
        self.assertIn("riproduciEventi", body)
        self.assertIn("res.body.events", body)
        self.assertIn('labFetch("/api/scaffold"', body)
        self.assertIn("quello che fa il codice", body)   # didascalia esplicita

    def test_step4_skill_vs_workflow_comparison(self) -> None:
        body = self._read()
        self.assertIn('id="wf-compare"', body)           # box di confronto
        self.assertIn("chi è al comando", body)
        self.assertIn("le istruzioni le legge", body)    # ③: legge il modello
        self.assertIn("codice che orchestra", body)      # ④: comanda il codice

    def test_step4_intro_contrasts_with_step3(self) -> None:
        body = self._read()
        # la ④ si presenta come workflow e contrappone il meccanismo alla ③
        self.assertIn("catena di montaggio", body)
        self.assertIn("valida l'output", body)
        self.assertIn("prompt fisso", body)

    # --- Feedback ④: chi comanda (codice/modello) + token del workflow ------
    def test_step4_stages_labeled_by_executor(self) -> None:
        body = self._read()
        # ogni riga della pipeline dichiara CHI la esegue
        self.assertIn("CODICE", body)
        self.assertIn("MODELLO", body)
        self.assertIn("STAGE_WHO", body)          # mappa evento -> esecutore
        # una sola riga è la chiamata all'LLM: l'estrazione
        self.assertIn('"extract": "model"', body)

    def test_step4_stage_caption_names_the_llm_row(self) -> None:
        body = self._read()
        self.assertIn("una sola chiamata", body)  # didascalia: 1 riga = LLM
        self.assertIn("tutto il resto è codice", body)

    def test_step4_token_row_from_usage(self) -> None:
        body = self._read()
        # a fine workflow: token in ingresso/uscita della chiamata al modello,
        # letti da usage della risposta /api/scaffold
        self.assertIn("res.body.usage", body)
        self.assertIn("token usati dal modello", body)
        self.assertIn("prompt_tokens", body)
        self.assertIn("completion_tokens", body)

    def test_step4_two_examples_scarno_and_completo(self) -> None:
        """Due esempi precaricati: 1 scarno (informazioni parziali → campi
        MISSING, la lezione), 2 corposo (tutte le 8 aree dello scaffold).
        L'assertion è COMPORTAMENTALE: il testo passa nell'estrattore mock."""
        import re
        from backend.models.mock import extract
        body = self._read()
        self.assertEqual(re.findall(r'id="(demo[0-9])"', body), ["demo1", "demo2"])
        ex1 = re.search(r'var EXAMPLE1 = "([^"]*)"', body)
        ex2 = re.search(r'var EXAMPLE2 = "([^"]*)"', body)
        self.assertTrue(ex1 and ex2, "esempi non dichiarati come costanti")
        # esempio 1: parziale — almeno un'area resta vuota (manca il "non specificato")
        sc1 = extract(ex1.group(1))["scaffold"]
        self.assertFalse(all(sc1[f] for f in sc1), "esempio 1 dovrebbe essere parziale")
        # esempio 2: tutte le 8 aree piene
        sc2 = extract(ex2.group(1))["scaffold"]
        empty = [f for f in sc2 if not sc2[f]]
        self.assertEqual(empty, [], msg=f"aree vuote nell'esempio completo: {empty}")

    # --- Sanità DOM: ogni id usato dal JS esiste nel markup ------------------
    # --- Ex tappa ⑤: temperatura e tetto alto vivono ora nella pagina del
    # --- laboratorio codice (CodeLabPageTest copre quel contratto; qui si
    # --- verifica che in index non ne resti traccia) --------------------------
    def test_no_step5_remnants(self) -> None:
        body = self._read()
        for marker in ('id="temp5"', "maxOut: 768", 'id="model-pick"',
                       'id="html-preview"', 'id="tb5"', "shortModelName"):
            self.assertNotIn(marker, body, msg=f"residuo della ⑤: {marker}")

    # --- Riquadro consumi nella pagina del laboratorio (revisione del change
    # readme-loadtest-consumi: la sessione del ragazzo corrente, non l'admin) --
    def test_consumi_strip_on_lab_page(self) -> None:
        body = self._read()
        self.assertIn('id="consumi"', body)      # la strip sotto il banner
        self.assertIn("/api/consumi/", body)     # fonte dati
        self.assertIn("loadConsumi", body)       # aggiornata dopo ogni risposta
        # revisione: l'etichetta "stime didattiche" non sta nel riquadro
        self.assertNotIn("stime didattiche", body)

    def test_consumi_refresh_periodically(self) -> None:
        """Refresh periodico: la tabella si aggiorna anche senza risposte di
        chat (es. dopo lo scaffold della tappa ③)."""
        body = self._read()
        self.assertIn("setInterval(loadConsumi", body)

    def test_reply_time_next_to_trace(self) -> None:
        """Revisione: accanto al bottone { } della trace, il tempo di risposta
        del turno (visto dal ragazzo, attesa inclusa)."""
        body = self._read()
        self.assertIn("chat-ms", body)       # il badge del tempo
        self.assertIn("elapsedMs", body)     # misurato lato client

    def test_overload_429_autoretry(self) -> None:
        """Backpressure lato ragazzo: al 429 la pagina avvisa il sovraccarico e
        riprova da sola dopo il retry_after indicato dal gateway."""
        body = self._read()
        self.assertIn("sovraccarico", body)
        self.assertIn("retry_after", body)
        self.assertIn("doPost", body)          # il turno si può ritentare
        self.assertIn("1000)", body)           # attesa in ms prima del retry
        # il retry NON è una tantum: sotto carico sostenuto il primo retry
        # riceve un altro 429 e il turno andrebbe perso (regressione osservata
        # al campo) — si riprova finché il gateway smette di dire 429
        self.assertIn("res.status === 429 && j.overload", body)
        self.assertNotIn("retried", body)
        self.assertIn("overloadWait", body)    # countdown visibile durante l'attesa

    def test_consumi_rendered_as_table(self) -> None:
        """Revisione: forma tabellare (non una riga) con il confronto COMPLETO —
        energia, acqua e costo per entrambe le colonne, frontiera inclusa."""
        body = self._read()
        self.assertIn("<table", body)            # struttura tabellare
        self.assertIn("Energia", body)           # le tre metriche...
        self.assertIn("Acqua", body)
        self.assertIn("Costo", body)
        self.assertIn("kwh", body)               # ...anche per la frontiera
        # decimali FISSI al rendering: il float grezzo per consumi piccoli
        # arriverebbe in notazione scientifica (9.7e-06) — illeggibile.
        # Energia e costo a 4 cifre (richieste dell'educatore), acqua come il
        # backend (litri: 6 cifre, i numeri sono minuscoli)
        self.assertIn("kwh.toFixed(4)", body)
        self.assertIn("acqua_l.toFixed(6)", body)
        self.assertIn("euro.toFixed(4)", body)
        # le colonne costo sono ALLINEATE: locale e frontiera entrambe a 4 cifre
        self.assertNotIn("euro.toFixed(6)", body)

    def test_every_js_id_exists_in_markup(self) -> None:
        """Regressione: rinominare un id nel markup (tb4 -> tb5) senza aggiornare
        il getElementById corrispondente fa esplodere makeChat a runtime
        (root null), e nessun test strutturale se ne accorgeva."""
        import re
        body = self._read()
        js = re.search(r"<script>(.*)</script>", body, re.S).group(1)
        used = set(re.findall(r'getElementById\("([^"]+)"\)', js))
        defined = set(re.findall(r'id="([^"]+)"', body))
        self.assertTrue(used, "nessun id estratto: il pattern del test è rotto")
        self.assertEqual(used - defined, set(),
                         msg=f"id usati nel JS ma assenti dal markup: {used - defined}")


class CodeLabPageTest(unittest.TestCase):
    """Change laboratorio-code: contratto strutturale della pagina dedicata
    `code.html` (step "code"), come gli altri page-test: marcatori DOM/JS.
    Il comportamento end-to-end è coperto dalla prova a due postazioni (task
    6.3) e dal gate del gateway (CodeLabPolicyTest)."""

    def _read(self) -> str:
        with open(os.path.join(_REPO, "backend/web/static/code.html"),
                  encoding="utf-8") as f:
            return f.read()

    # --- 2.1: scheletro, identità, limiti -----------------------------------
    def test_page_identity_and_step(self) -> None:
        body = self._read()
        self.assertIn("Laboratorio codice", body)
        self.assertIn("lab_cid", body)            # identità client (X-Client-Id)
        self.assertIn("labFetch", body)           # fetch con header lab
        self.assertIn('step: "code"', body)       # header X-Step: code, sempre
        self.assertIn("/api/chat", body)          # solo endpoint del gateway

    def test_declared_limits_match_gateway(self) -> None:
        """Tetto 4096 e finestra di contesto del servizio che risponde: i
        valori dichiarati devono essere quelli applicati dal gateway. Il tetto
        è la costante di tappa; il contesto arriva da model-status.code.ctx
        (2048 col main, 8192 col coder) — mai hardcoded uno solo."""
        body = self._read()
        self.assertIn("maxOut: 4096", body)
        self.assertIn("code.ctx", body)            # limite contesto dal gateway
        self.assertIn("limiti imposti dal server", body)

    def test_context_meter(self) -> None:
        body = self._read()
        self.assertIn("ctx-meter", body)           # barra + testo uso/limite
        self.assertIn("prompt_tokens", body)       # token reali dal modello
        self.assertIn("Nuova conversazione", body) # reset quando il contesto si riempie
        self.assertIn("Contesto pieno", body)      # messaggio amichevole

    # --- 2.2: seme + temperatura --------------------------------------------
    def test_seed_asks_full_page_single_file(self) -> None:
        """Il prompt seme chiede una pagina HTML COMPLETA con <style> incorporato
        e nessun asset esterno: un file unico, sola risposta-codice."""
        import re
        body = self._read()
        m = re.search(r'var SEED = "([^"]*)"', body)
        self.assertIsNotNone(m, "SEED non dichiarata come costante")
        seed = m.group(1)
        self.assertIn("pagina HTML completa", seed)
        self.assertIn("<style>", seed)
        self.assertIn("nessun file esterno", seed.lower())
        self.assertIn("SOLO il codice", seed)

    def test_temperature_slider(self) -> None:
        body = self._read()
        self.assertIn('id="temp"', body)
        self.assertIn('min="0"', body)
        self.assertIn('max="1.5"', body)
        self.assertIn('step="0.1"', body)
        self.assertIn('value="0.3"', body)         # default di aderenza
        self.assertIn("temp-val", body)            # valore live
        self.assertIn("tempValue", body)           # inviata a ogni richiesta
        self.assertIn("typeof opts.temperature", body)

    # --- 2.3: barra artefatto -------------------------------------------------
    def test_artifact_copy_fallback_exec_command(self) -> None:
        """Copia in un click anche su LAN HTTP (non secure context: niente
        navigator.clipboard): textarea temporanea + execCommand, con feedback
        visibile «Copiato» (icona/testo, mai emoji)."""
        body = self._read()
        self.assertIn('id="copy-btn"', body)
        self.assertIn("execCommand", body)
        self.assertIn("copyArtifact", body)
        self.assertIn("Copiato", body)

    def test_artifact_download_and_open(self) -> None:
        """Tre strade per lo stesso file unico: copia, scarico .html (Blob +
        a[download]) e apertura in nuova scheda (Blob URL, degrada senza
        errori se il kiosk blocca i popup)."""
        body = self._read()
        self.assertIn('id="download-btn"', body)
        self.assertIn('id="open-btn"', body)
        self.assertIn("downloadArtifact", body)
        self.assertIn("openArtifact", body)
        self.assertIn("new Blob", body)
        self.assertIn('a[download]'.replace("a[", "").replace("]", ""), body)  # attr download
        self.assertIn(".html", body)
        self.assertIn("createObjectURL", body)

    def test_preview_sandboxed_iframe(self) -> None:
        """Anteprima grande: iframe sandbox senza script eseguibili né
        same-origin; stripFences riportata dall'anteprima della vecchia ⑤."""
        body = self._read()
        self.assertIn('id="html-preview"', body)
        self.assertIn('sandbox=""', body)
        self.assertIn("srcdoc", body)
        self.assertIn("stripFences", body)

    def test_reply_activates_artifact_flow(self) -> None:
        """Regressione (trovata al primo collaudo): la factory dichiarava
        onReply ma non la invocava MAI — la risposta arrivava e barra
        artefatto/anteprima restavano morte (lastArtifact vuoto, i pulsanti
        uscivano in silenzio). Il contratto è la CHIAMATA dopo la risposta,
        non la sola dichiarazione nelle opzioni."""
        body = self._read()
        # la factory invoca il callback dopo la risposta (col body: dice chi)
        self.assertIn("opts.onReply(j.reply, j)", body)
        # la chat code collega il callback alla barra artefatto
        self.assertIn("artifactArrived(stripFences(reply));", body)
        # e artifactArrived mostra la barra e alimenta copia/scarica/apri/preview
        self.assertIn("lastArtifact = html", body)
        self.assertIn('document.getElementById("artifact-bar").hidden = !html', body)

    # --- 2.5: gate in pagina ---------------------------------------------------
    def test_gate_reads_model_status(self) -> None:
        """Al caricamento la pagina legge model-status.code.allowed: la
        policy è del gateway, la pagina al massimo mostra. Postazione non
        abilitata → messaggio amichevole al posto della chat, toolbox off."""
        body = self._read()
        self.assertIn("/api/model-status", body)
        self.assertIn("code.allowed", body)
        self.assertIn("code-gate", body)           # il messaggio amichevole
        self.assertIn("renderGate", body)
        self.assertIn("setDisabled(true)", body)   # toolbox disabilitato

    def test_403_handled_friendly(self) -> None:
        """Se il gate scatta DOPO il caricamento (IP rimosso a runtime), la
        risposta 403 arriva come messaggio chiaro, non come errore grezzo."""
        body = self._read()
        self.assertIn("code_forbidden", body)
        self.assertIn("avvisa l'educatore", body)

    def test_banner_is_about_this_page(self) -> None:
        """Il banner di code.html parla solo di questa pagina: nessun modello
        principale del percorso, nessun conteggio ragazzi (feedback di campo:
        il banner ereditato da index nominava il main anche col coder attivo).
        Lo stato neutro esiste PRIMA del primo invio."""
        import re
        body = self._read()
        m = re.search(r"function renderBanner\(\) \{[\s\S]*?\n\}", body)
        self.assertIsNotNone(m, "renderBanner non trovata in code.html")
        src = m.group(0)
        self.assertIn("Genera ", src)                  # parla di chi genera qui
        self.assertIn("lastGenerator", src)            # segue la conversazione
        self.assertIn("Modello pronto", src)           # stato neutro pre-invio
        self.assertNotIn("ragazzi connessi", body)     # niente conteggio globale

    def test_generator_banner_follows_conversation(self) -> None:
        """Il «Genera X» del banner è legato ALLA RISPOSTA, non alla tendina:
        compare solo dopo un invio, col nome che il gateway conferma
        (j.remote + j.model per l'endpoint reale; in locale chi risponde in
        tappa, fallback dichiarato), e sparisce con «Nuova conversazione»
        o col cambio modello (che azzera la conversazione)."""
        body = self._read()
        # la factory passa la risposta intera al callback
        self.assertIn("opts.onReply(j.reply, j)", body)
        # il nome viene dalla risposta: remoto dal body, locale da chi risponde
        self.assertIn("j.remote", body)
        self.assertIn("j.model", body)
        self.assertIn("coder dedicato non attivo", body)   # fallback dichiarato
        # e si azzera col reset (bottone e cambio modello passano da lì)
        self.assertIn("opts.onReset()", body)           # la factory lo invoca
        self.assertIn("lastGenerator = null", body)     # il callback lo azzera

    # --- semaforo e attese ------------------------------------------------------
    def test_busy_429_countdown_and_retry(self) -> None:
        """429 busy del semaforo: la pagina avvisa e riprova da sola dopo il
        retry_after (pattern overload riusato), il turno non si perde."""
        body = self._read()
        self.assertIn("j.busy", body)
        self.assertIn("retry_after", body)
        self.assertIn("una generazione alla volta", body)

    def test_client_timeout_above_gateway(self) -> None:
        """Catena D5: il client attende 930 s, sopra i 900 del gateway (che
        risponde sempre un JSON, anche il 504) e sotto i 960 di nginx."""
        body = self._read()
        self.assertIn("930000", body)

    def test_truncation_note(self) -> None:
        body = self._read()
        self.assertIn('finish_reason === "length"', body)
        self.assertIn("tagliata dal limite", body)

    # --- sanità DOM ---------------------------------------------------------------
    def test_every_js_id_exists_in_markup(self) -> None:
        import re
        body = self._read()
        js = re.search(r"<script>(.*)</script>", body, re.S).group(1)
        used = set(re.findall(r'getElementById\("([^"]+)"\)', js))
        defined = set(re.findall(r'id="([^"]+)"', body))
        self.assertTrue(used, "nessun id estratto: il pattern del test è rotto")
        self.assertEqual(used - defined, set(),
                         msg=f"id usati nel JS ma assenti dal markup: {used - defined}")

    def test_no_link_to_wizard(self) -> None:
        """La pagina è autonoma: niente link/torna-a che rompano il kiosk; e
        niente emoji (il font del kiosk non le copre)."""
        import re
        body = self._read()
        # il seme cita «SpaceShooter/index.html» come href del PULSANTE da
        # generare: è prosa del prompt, non un link della pagina — fuori dal controllo
        body = re.sub(r'var SEED = "[^"]*"', "", body)
        self.assertNotIn("index.html", body)
        emoji = re.compile(
            "[\U0001F000-\U0001FAFF☀-⛿✀-✒✔✖-➿⬀-⯿️]")
        self.assertIsNone(emoji.search(body))


class ModelPollRecoveryTest(unittest.TestCase):
    """Regressione di campo: il poll di model-status si riprogrammava SOLO
    nel ramo di successo — alla prima caduta del server (educatore che
    riavvia lo stack) la pagina restava su «Modello NON attivo» a vita,
    anche a server tornato. Il poll va riprogrammato in OGNI esito."""

    def _check(self, rel: str) -> None:
        import re
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            body = f.read()
        m = re.search(r"function checkModel\(\) \{[\s\S]*?\n\}", body)
        self.assertIsNotNone(m, f"{rel}: checkModel non trovata")
        src = m.group(0)
        # il poll è riprogrammato sia a successo sia a errore: nessun esito
        # che lasci la pagina ferma sul suo ultimo verdict
        self.assertEqual(src.count("setTimeout(checkModel"), 2,
                         msg=f"{rel}: il poll non riparte in entrambi i rami")
        # e nel ramo di errore c'è davvero (non solo nel then)
        catch = re.search(r"\.catch\(function \(\) \{[\s\S]*?\}\);", src)
        self.assertIsNotNone(catch, f"{rel}: catch di checkModel non trovato")
        self.assertIn("setTimeout(checkModel", catch.group(0))

    def test_index_poll_recovers(self) -> None:
        self._check("backend/web/static/index.html")

    def test_code_poll_recovers(self) -> None:
        self._check("backend/web/static/code.html")


class ModuleIndependenceTest(unittest.TestCase):
    """D1: skill/service e gateway sono moduli logicamente separati.

    `skill.py`/`service.py` NON importano il gateway e viceversa: l'unico
    contatto è HTTP (il gateway proxya verso il servizio skill).
    """

    def _local_imports(self, rel: str) -> set[str]:
        import ast
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module)
        return mods

    def test_gateway_does_not_import_skill_modules(self) -> None:
        mods = self._local_imports("backend/gateway.py")
        # D1: il gateway non importa skill/service — la separazione che conta è
        # quella del contratto di business. Unico ammesso: backend.costi (change
        # readme-loadtest-consumi), leaf di costanti didattiche a zero accoppiamento.
        allowed = {"backend.costi"}
        bad = {m for m in mods
               if m == "backend" or (m.startswith("backend.") and m not in allowed)}
        self.assertEqual(bad, set())

    def test_skill_modules_do_not_import_gateway(self) -> None:
        for rel in ["backend/skill.py", "backend/service.py"]:
            with self.subTest(rel=rel):
                mods = self._local_imports(rel)
                # solo import relativi del proprio package (level=1), mai esterni
                # che tirino dentro il gateway
                self.assertNotIn("backend", mods)


if __name__ == "__main__":
    unittest.main()

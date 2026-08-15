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
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from diariobot import cli, gateway, service
from diariobot.schema import SkillOutput
from diariobot.web import client

NOTES = "Campo base a Costigiola. Oggi con Marco e Lucia montiamo la tenda nord. Pioveva, poi con le pietre ha funzionato. Stanchi ma felici."

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
        for probe in ["/../diariobot/service.py", "/%2e%2e/diariobot/service.py"]:
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

        self._orig_llama_url = gateway.LLAMA_URL
        gateway.LLAMA_URL = self.llama_url
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_port = self.gw.server_address[1]
        self.gw_url = f"http://127.0.0.1:{self.gw_port}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        gateway.LLAMA_URL = self._orig_llama_url
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        self.llama.shutdown(); self.llama.server_close(); self.llama_thread.join(timeout=2)

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

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.gw_url + path, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    # --- /api/chat happy + normalizzazione --------------------------------
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


class ObservabilityTest(unittest.TestCase):
    """Osservabilità: client-id, /api/sessions, timeline, storage JSONL, privacy."""

    def setUp(self) -> None:
        service._SKILL = None
        self.skill = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.skill_url = f"http://127.0.0.1:{self.skill.server_address[1]}"
        self.skill_thread = threading.Thread(target=self.skill.serve_forever, daemon=True)
        self.skill_thread.start()

        self._orig = (gateway.SKILL_URL, gateway.LLAMA_URL, gateway._TRACKER, gateway.LAB_LOG_VERBOSE)
        gateway.SKILL_URL = self.skill_url
        gateway.LLAMA_URL = "http://127.0.0.1:1"  # modello off
        self.tmp = tempfile.mkdtemp()
        gateway._TRACKER = gateway.SessionTracker(os.path.join(self.tmp, "s.jsonl"))
        gateway.LAB_LOG_VERBOSE = False

        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_url = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        gateway.SKILL_URL, gateway.LLAMA_URL, gateway._TRACKER, gateway.LAB_LOG_VERBOSE = self._orig
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

    def _wait_jsonl(self) -> str:
        """Il record (e il write del JSONL) avviene nel thread handler DOPO l'invio
        della risposta: attendo brevemente che il file sia scritto (race dei test)."""
        p = os.path.join(self.tmp, "s.jsonl")
        for _ in range(100):
            if os.path.exists(p):
                return p
            time.sleep(0.01)
        return p

    def test_client_id_tracked(self) -> None:
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "marco", "X-Step": "3"})
        _, body = self._get("/api/sessions")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["active"][0]["client"], "marco")
        self.assertIn("3", body["active"][0]["steps"])

    def test_scaffold_step_defaults_to_workflow(self) -> None:
        # senza header X-Step lo scaffold è tappa ④ (Workflow): il default non
        # è più l'hardcoded "3" del prima della rinumerazione
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "nopro"})
        _, body = self._get("/api/sessions")
        self.assertEqual(body["active"][0]["client"], "nopro")
        self.assertIn("4", body["active"][0]["steps"])

    def test_two_clients(self) -> None:
        for cid in ("a", "b"):
            self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": cid})
        _, body = self._get("/api/sessions")
        self.assertEqual(body["total"], 2)

    def test_timeline_metadata_only(self) -> None:
        """Privacy: la timeline contiene SOLO metadati, mai il testo degli appunti."""
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "x"})
        _, body = self._get("/api/sessions/x")
        self.assertTrue(body["interactions"])
        it = body["interactions"][0]
        self.assertNotIn("in_preview", it)
        self.assertNotIn("out_preview", it)
        # il testo degli appunti NON compare da nessuna parte nella risposta
        self.assertNotIn(NOTES, json.dumps(body))

    def test_jsonl_one_row_per_request(self) -> None:
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "y"})
        with open(self._wait_jsonl()) as f:
            lines = f.read().strip().split("\n")
        self.assertEqual(len(lines), 1)

    def test_verbose_adds_previews(self) -> None:
        # il record avviene nel thread handler DOPO l'invio della risposta:
        # il flag resta True finché non abbiamo letto il JSONL (tearDown ripristina)
        gateway.LAB_LOG_VERBOSE = True
        self._post("/api/scaffold", {"notes": NOTES}, {"X-Client-Id": "z"})
        with open(self._wait_jsonl()) as f:
            row = json.loads(f.read().strip().split("\n")[0])
        gateway.LAB_LOG_VERBOSE = False
        self.assertIn("in_preview", row)

    def test_model_status_has_clients_and_model(self) -> None:
        _, body = self._get("/api/model-status")
        self.assertIn("clients", body)
        self.assertIn("model", body)
        self.assertFalse(body["model_active"])  # llama off in questo test


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
        for rel in ["diariobot/web/static/index.html",
                    "diariobot/web/static/admin.html",
                    "diariobot/web/static/favicon.svg"]:
            self.assertTrue(os.path.isfile(os.path.join(_REPO, rel)), rel)

    def test_index_zero_cdn(self) -> None:
        body = self._read("diariobot/web/static/index.html")
        self.assertIn("Laboratorio di Prompting", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("<link", body)  # niente fogli di stile esterni

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
        for rel in ["diariobot/web/static/index.html", "diariobot/web/static/admin.html"]:
            body = self._read(rel)
            self.assertNotIn("repeat_penalty", body)
            self.assertNotIn("max_tokens", body)
            # la pagina chiama SOLO /api/* (mai :8081 diretto)
            self.assertNotIn(":8081", body)
            self.assertNotIn("8080", body)


class ContextMemoryPageTest(unittest.TestCase):
    """Change laboratorio-context-memoria: contratto della pagina per le
    tappe ① (context injection a due tab) e ② (system prompt a soli preset).

    La pagina non ha harness JS: come NginxTierTest, si verifica il contratto
    strutturale (marcatori DOM/JS in index.html). Il comportamento end-to-end
    è coperto dal passaggio manuale (task 5.2 del change).
    """

    def _read(self) -> str:
        with open(os.path.join(_REPO, "diariobot/web/static/index.html"),
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
        # 5 chat metered: tab A, tab B, tappe ② ③ e ⑤ (la barra non sparisce)
        self.assertEqual(self._read().count("meter: true"), 5)

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
        self.assertIn("_CHAT_DEFAULT_MAX_TOKENS = 256", self._gateway_src())
        self.assertIn("opts.maxOut || 256", body)    # default pagina = default gateway

    def test_truncated_reply_note(self) -> None:
        # nota ✂️ quando la risposta è tagliata dal tetto token: verdetto da
        # finish_reason ("length") con risvolto su completion_tokens al tetto
        body = self._read()
        self.assertIn('finish_reason === "length"', body)
        self.assertIn("completion_tokens >= maxOut", body)
        self.assertIn("tagliata dal limite", body)

    def _gateway_src(self) -> str:
        with open(os.path.join(_REPO, "diariobot/gateway.py"), encoding="utf-8") as f:
            return f.read()


class SkillWorkflowPageTest(unittest.TestCase):
    """Change laboratorio-skill-workflow: percorso a cinque tappe (nuova ③ Skills
    con caricamento visibile, ex ③ → ④ Workflow, ex ④ → ⑤ Prompt Engineering).

    Come ContextMemoryPageTest: contratto strutturale su index.html; il
    comportamento end-to-end è coperto dal passaggio manuale (task 5.2).
    """

    def _read(self) -> str:
        with open(os.path.join(_REPO, "diariobot/web/static/index.html"),
                  encoding="utf-8") as f:
            return f.read()

    # --- Percorso a cinque tappe -------------------------------------------
    def test_five_steps_in_order(self) -> None:
        import re
        body = self._read()
        dots = re.findall(r'data-step-dot="(\d)"[^>]*>([^<]+)<', body)
        self.assertEqual([d[0] for d in dots], ["0", "1", "2", "3", "4"])
        labels = " ".join(d[1] for d in dots)
        for lbl in ("① Context", "② System", "③ Skills", "④ Workflow", "⑤ Prompt Eng."):
            self.assertIn(lbl, labels)
        self.assertIn("maxStep: 5", body)
        # le sezioni seguono lo stesso ordine dei dot
        heads = re.findall(r'<section class="step" data-step="(\d)">\s*<h2>(.+?)</h2>', body)
        self.assertEqual([h[0] for h in heads], ["0", "1", "2", "3", "4"])
        self.assertIn("④ Workflow", heads[3][1])
        self.assertIn("⑤ Prompt Engineering", heads[4][1])

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
        self.assertIn('id="blob3-skill"', body)          # (a) blocco ⚙ nel blob
        self.assertIn("⚙ SKILL", body)
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
        from diariobot.models.mock import extract
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
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        return mods

    def test_gateway_does_not_import_skill_modules(self) -> None:
        mods = self._local_imports("diariobot/gateway.py")
        self.assertNotIn("diariobot", mods)

    def test_skill_modules_do_not_import_gateway(self) -> None:
        for rel in ["diariobot/skill.py", "diariobot/service.py"]:
            with self.subTest(rel=rel):
                mods = self._local_imports(rel)
                # solo import relativi del proprio package (level=1), mai esterni
                # che tirino dentro il gateway
                self.assertNotIn("diariobot", mods)


if __name__ == "__main__":
    unittest.main()

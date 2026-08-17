"""Change endpoint-remoto-hetzner: endpoint reale (Hetzner) in tappa ⑤.

Copre (design del change, D1–D10):
  - configurazione del token: `.env` (gitignored) + compose, solo nel gateway
  - persistenza: colonna `endpoint` sulle interazioni (NULL = locale) e
    tabella kv `state` per i due flag dell'educatore (interruttore, breaker)
  - percorso remoto di /api/chat (allowlist, X-Step 5, body OpenAI standard,
    Bearer mai verso il client) con fake endpoint in-process
  - circuito di protezione: scatto predittivo sui limiti della finestra 60 s,
    429 reale, sticky fino a sblocco, finestra ricostruita dal DB
  - misure locali non inquinate (tps/backpressure) e consumi separati
  - contratto strutturale delle pagine (selettore ⑤, riquadro admin)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend import gateway

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODELS = ("Qwen/Qwen3.6-35B-A3B-FP8", "DeepSeek-V4-Flash-0731")
_QWEN = _MODELS[0]


def _read(rel: str) -> str:
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


def _wait_tracked(cid: str, timeout: float = 2.0) -> None:
    """Il record avviene nel thread handler DOPO la risposta (race dei test)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with gateway._TRACKER._lock:
            if cid in gateway._TRACKER._sessions:
                return
        time.sleep(0.01)


class RemoteConfigTest(unittest.TestCase):
    """Task 1.1/1.2/1.4: token nel `.env`, wiring compose, lettura gateway."""

    def test_env_gitignored_with_example(self) -> None:
        gi = _read(".gitignore")
        self.assertIn(".env", gi)
        example = os.path.join(_REPO, ".env.example")
        self.assertTrue(os.path.isfile(example), ".env.example mancante")
        with open(example, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("HETZNER_API_KEY=", body)

    def test_compose_passes_token_only_to_gateway(self) -> None:
        compose = _read("docker-compose.yml")
        self.assertIn("HETZNER_API_KEY", compose)
        # la variabile vive SOLO nell'environment del gateway
        gw = compose.split("gateway:")[1].split("\n\n  ")[0]
        skill = compose.split("skill:")[1].split("\n\n  ")[0]
        self.assertIn("HETZNER_API_KEY", gw)
        self.assertNotIn("HETZNER_API_KEY", skill)

    def test_gateway_reads_token_from_env_no_default(self) -> None:
        src = _read("backend/gateway.py")
        self.assertIn('os.environ.get("HETZNER_API_KEY")', src)


class StateKvTest(unittest.TestCase):
    """Task 1.3: kv `state` nella sessions.db — i due flag sopravvivono al rebuild."""

    def test_kv_roundtrip_survives_restart(self) -> None:
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "s.db")
        try:
            t1 = gateway.SessionTracker(db)
            self.assertIsNone(t1.get_state("remote_enabled"))  # default: nessuno stato
            t1.set_state("remote_enabled", "1")
            t1.set_state("remote_tripped", "1")
            # nuovo tracker sullo stesso DB = gateway riavviato (make rebuild)
            t2 = gateway.SessionTracker(db)
            self.assertEqual(t2.get_state("remote_enabled"), "1")
            self.assertEqual(t2.get_state("remote_tripped"), "1")
            t2.set_state("remote_enabled", "0")  # overwrite, non append
            self.assertEqual(gateway.SessionTracker(db).get_state("remote_enabled"), "0")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_kv_in_memory_without_db(self) -> None:
        t = gateway.SessionTracker(None)
        t.set_state("remote_enabled", "1")
        self.assertEqual(t.get_state("remote_enabled"), "1")

    def test_endpoint_column_persists_and_defaults_local(self) -> None:
        """Colonna `endpoint`: NULL/None = locale (retrocompat con lo storico),
        l'id del modello per le interazioni remote."""
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "s.db")
        try:
            t1 = gateway.SessionTracker(db)
            t1.record("c", "chat", "5", 200, 1, 1, 1.0, endpoint=_QWEN)
            t1.record("c", "chat", "1", 200, 1, 1, 1.0)  # locale: niente marca
            t2 = gateway.SessionTracker(db)  # riavvio
            rows = t2.timeline("c")["interactions"]
            self.assertEqual(rows[0].get("endpoint"), _QWEN)
            self.assertIsNone(rows[1].get("endpoint"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fake endpoint Hetzner (OpenAI-compat): registra richieste, header e body,
# e controlla le risposte (200 / 429 / 5xx) come _LlamaRec per llama.
# ---------------------------------------------------------------------------
REMOTE_REPLY = "Card HTML generata dal modello remoto"


class _HetznerRec:
    def __init__(self) -> None:
        self.last_body: dict | None = None
        self.last_auth: str | None = None
        self.requests: list[float] = []      # ts di ogni /chat/completions ricevuta
        self.status = 200
        self.delay: float = 0.0              # >0: simula la generazione lenta
        self.payload = {"choices": [{"message": {"role": "assistant",
                                                 "content": REMOTE_REPLY},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 33, "completion_tokens": 120}}


def _hetzner_handler(rec: _HetznerRec):
    class H(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: dict) -> None:
            b = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            rec.last_body = json.loads(raw.decode("utf-8")) if raw else {}
            rec.last_auth = self.headers.get("Authorization")
            rec.requests.append(time.time())
            if self.path.endswith("/chat/completions"):
                if rec.delay:  # non-streaming: gli header aspettano la fine
                    time.sleep(rec.delay)
                self._json(rec.status, rec.payload)
            else:
                self._json(404, {})

        def log_message(self, fmt, *args) -> None:
            pass

    return H


class RemoteChatTest(unittest.TestCase):
    """Task 2.1/2.2/2.4/2.5: ramo remoto di /api/chat — allowlist, X-Step 5,
    body OpenAI standard, Bearer solo in uscita, riga marcata `endpoint`."""

    def setUp(self) -> None:
        self.rec = _HetznerRec()
        self.hz = ThreadingHTTPServer(("127.0.0.1", 0), _hetzner_handler(self.rec))
        self.hz_url = f"http://127.0.0.1:{self.hz.server_address[1]}"
        self.hz_thread = threading.Thread(target=self.hz.serve_forever, daemon=True)
        self.hz_thread.start()

        self._orig = (gateway.HETZNER_URL, gateway.HETZNER_API_KEY,
                      gateway.LLAMA_URL, gateway._TRACKER, gateway._REMOTE)
        gateway.HETZNER_URL = self.hz_url
        gateway.HETZNER_API_KEY = "token-segreto-test"
        gateway.LLAMA_URL = "http://127.0.0.1:1"  # locale off: isoliamo il remoto
        gateway._TRACKER = gateway.SessionTracker(None)
        gateway._REMOTE = gateway.RemoteState()
        self._remote_on()
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.gw_url = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.gw_thread = threading.Thread(target=self.gw.serve_forever, daemon=True)
        self.gw_thread.start()

    def tearDown(self) -> None:
        (gateway.HETZNER_URL, gateway.HETZNER_API_KEY,
         gateway.LLAMA_URL, gateway._TRACKER, gateway._REMOTE) = self._orig
        self.gw.shutdown(); self.gw.server_close(); self.gw_thread.join(timeout=2)
        self.hz.shutdown(); self.hz.server_close(); self.hz_thread.join(timeout=2)

    # -- helper ------------------------------------------------------------
    def _remote_on(self) -> None:
        """L'interruttore dell'educatore parte OFF (spec): lo accendiamo come
        farebbe l'admin via POST /api/admin/remote."""
        gateway._TRACKER.set_state("remote_enabled", "1")

    def _post(self, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.gw_url + "/api/chat", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
            # noqa: B012
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _remote_body(self) -> dict:
        return {"messages": [{"role": "user", "content": "card"}],
                "model": _QWEN}

    def _drain(self, cid: str) -> None:
        """Una chat remota portata a termine (attende il record della riga)."""
        self._post(self._remote_body(), {"X-Step": "5", "X-Client-Id": cid})
        _wait_tracked(cid)

    # -- 2.1: inoltro e forma della risposta ------------------------------
    def test_remote_chat_forwarded_with_bearer_and_standard_body(self) -> None:
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 200)
        self.assertEqual(self.rec.last_auth, "Bearer token-segreto-test")
        sent = self.rec.last_body
        self.assertEqual(sent["model"], _QWEN)
        self.assertEqual(sent["messages"][0]["content"], "card")
        self.assertIs(sent["stream"], False)
        self.assertIn("temperature", sent)
        self.assertIn("max_tokens", sent)
        # body SOLO OpenAI standard: niente parametri propri di llama-server
        self.assertNotIn("repeat_penalty", sent)
        # risposta nella forma delle chat locali + marca del modello
        self.assertEqual(body["reply"], REMOTE_REPLY)
        self.assertEqual(body["usage"]["completion_tokens"], 120)
        self.assertEqual(body["finish_reason"], "stop")
        self.assertEqual(body["trace"]["request"], sent)
        self.assertEqual(body["model"], _QWEN)

    def test_remote_chat_step5_token_cap(self) -> None:
        """Il tetto token per tappa è policy del gateway: vale anche remoto."""
        _, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(body["trace"]["request"]["max_tokens"], 768)

    # -- 2.2: allowlist e vincolo tappa, senza uscire ----------------------
    def test_remote_model_outside_allowlist_400(self) -> None:
        n_before = len(self.rec.requests)
        status, body = self._post({**self._remote_body(), "model": "GPT-9"},
                                  {"X-Step": "5"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        self.assertEqual(len(self.rec.requests), n_before)  # nulla è partito

    def test_remote_model_requires_step5(self) -> None:
        n_before = len(self.rec.requests)
        status, body = self._post(self._remote_body(), {"X-Step": "1"})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.rec.requests), n_before)
        # anche senza header
        status, _ = self._post(self._remote_body())
        self.assertEqual(status, 400)
        self.assertEqual(len(self.rec.requests), n_before)

    # -- 2.4: osservabilità --------------------------------------------------
    def test_remote_chat_records_endpoint_and_real_usage(self) -> None:
        self._post(self._remote_body(), {"X-Step": "5", "X-Client-Id": "rem"})
        _wait_tracked("rem")
        row = gateway._TRACKER.timeline("rem")["interactions"][0]
        self.assertEqual(row["endpoint"], _QWEN)   # riga marcata come remota
        self.assertEqual(row["tok_in"], 33)        # usage reali dell'endpoint
        self.assertEqual(row["tok_out"], 120)
        self.assertEqual(row["step"], "5")
        # la sessione risulta «con endpoint reale» per il pannello
        self.assertTrue(gateway._TRACKER.active_list(300)["active"][0]["remote"])

    # -- 2.5: il Bearer non attraversa il confine ---------------------------
    def test_bearer_never_reaches_client(self) -> None:
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 200)
        self.assertNotIn("token-segreto-test", json.dumps(body))
        # nemmeno nella trace persistita della riga (cid "anon": header default)
        _wait_tracked("anon")
        row = gateway._TRACKER.timeline("anon")["interactions"][0]
        self.assertNotIn("token-segreto-test", json.dumps(row))
        # con errore endpoint la response verso il client porta il body
        # d'errore ricevuto, mai l'header di autorizzazione
        self.rec.status = 500
        self.rec.payload = {"error": "boom"}
        status, err = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 502)
        self.assertNotIn("token-segreto-test", json.dumps(err))

    def test_remote_rows_do_not_feed_local_tps(self) -> None:
        """D9/3.5: le chat remote non producono punti nella serie t/s."""
        self._post(self._remote_body(), {"X-Step": "5", "X-Client-Id": "rtps"})
        _wait_tracked("rtps")
        pts = gateway._TRACKER.tps_points()["points"]
        self.assertEqual(pts, [])  # tok_out c'è, ma la riga è remota: esclusa


class RemoteBreakerTest(RemoteChatTest):
    """Task 3.1/3.2/3.4: scatto predittivo al limite richieste, 429 reale,
    sticky fino a sblocco; 3.5: indipendenza dalla backpressure locale."""

    def test_11th_request_trips_predictively(self) -> None:
        """La finestra è condivisa da tutta la sala: la 11ª richiesta nei 60 s
        NON parte (il fake ne vede 10) e il circuito scatta sticky."""
        for i in range(_REMOTE_REQ_LIMIT if False else 10):  # noqa: SIM108
            self._drain(f"s{i}")
        self.assertEqual(len(self.rec.requests), 10)
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertTrue(body["remote_disabled"])
        self.assertIn("reason", body)
        self.assertEqual(len(self.rec.requests), 10)  # l'11ª non è partita
        # sticky: anche dopo che la finestra si svuota, resta OFF
        self.assertTrue(gateway._REMOTE.tripped())

    def test_trip_survives_window_aging(self) -> None:
        """Nessuna auto-riabilitazione: invecchiare la finestra NON basta,
        serve lo sblocco dell'educatore (spec «Sticky fino a sblocco»)."""
        self._drain("a")
        gateway._REMOTE.trip("test")
        with gateway._REMOTE._lock:  # la finestra si svuota istantaneamente
            gateway._REMOTE._win = []
        self.assertTrue(gateway._REMOTE.tripped())
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertTrue(body["remote_disabled"])

    def test_real_429_trips(self) -> None:
        """429 reale dall'endpoint (es. stesso token usato altrove): il circuito
        scatta e le richieste successive non partono."""
        self.rec.status = 429
        self.rec.payload = {"error": "rate limit"}
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertTrue(body["remote_disabled"])
        self.assertTrue(gateway._REMOTE.tripped())
        # la successiva non raggiunge nemmeno l'endpoint
        n = len(self.rec.requests)
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertEqual(len(self.rec.requests), n)

    def test_unlock_via_admin_then_honest_restart(self) -> None:
        """Sblocco admin: la finestra invecchia da sola; se è vuota si riparte,
        se è ancora piena la prossima richiesta predittiva fa scattare di nuovo."""
        for i in range(10):
            self._drain(f"s{i}")
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)  # 11ª: trip predittivo
        # sblocco come farebbe l'admin
        req = urllib.request.Request(
            self.gw_url + "/api/admin/remote",
            data=json.dumps({"action": "unlock"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            st = json.loads(r.read().decode("utf-8"))
        self.assertEqual(st["remote"]["tripped"], False)
        # finestra ANCORA piena (10 richieste di pochi ms fa): riscatto onesto
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertTrue(body["remote_disabled"])
        # svuotata (le richieste invecchiano), dopo nuovo sblocco si riparte
        gateway._REMOTE.unlock()
        with gateway._REMOTE._lock:
            gateway._REMOTE._win = []
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], REMOTE_REPLY)

    def test_window_rebuild_from_db(self) -> None:
        """Task 3.3: la finestra si ricostruisce dalle righe remote persistite
        (gateway riavviato): le richieste degli ultimi 60 s contano ancora."""
        tmp = tempfile.mkdtemp()
        try:
            db = os.path.join(tmp, "s.db")
            t = gateway.SessionTracker(db)
            fresh = time.time()
            t.record("r1", "chat", "5", 200, 1, 1, 1.0, endpoint=_QWEN,
                     tok_in=100, tok_out=50)
            t.record("r2", "chat", "5", 200, 1, 1, 1.0, endpoint=_QWEN,
                     tok_in=100, tok_out=50)
            # ...e una remota VECCHIA (fuori finestra) + una locale fresca
            t.record("r3", "chat", "5", 200, 1, 1, 1.0, endpoint=_QWEN,
                     tok_in=999, tok_out=999)
            t.record("r4", "chat", "1", 200, 1, 1, 1.0, tok_in=500, tok_out=500)
            with t._lock:
                t._sessions["r3"]["recent"][0]["ts"] = fresh - 120.0
                t._sessions["r4"]["recent"][0]["ts"] = fresh - 1.0
            orig_tracker = gateway._TRACKER
            gateway._TRACKER = t
            try:
                st = gateway.RemoteState()
                st.rebuild()
            finally:
                gateway._TRACKER = orig_tracker
            s = st.snapshot()
            self.assertEqual(s["req"], 2)          # solo le remote nella finestra
            self.assertEqual(s["tok_in"], 200)    # la locale e la vecchia escluse
            self.assertEqual(s["tok_out"], 100)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_switch_off_blocks_remote_enforcement(self) -> None:
        """Task 4.3: l'interruttore OFF è enforcement del gateway, non solo
        selettore nascosto: la richiesta remota viene rifiutata."""
        req = urllib.request.Request(
            self.gw_url + "/api/admin/remote",
            data=json.dumps({"action": "off"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        n = len(self.rec.requests)
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertTrue(body["remote_disabled"])
        self.assertEqual(len(self.rec.requests), n)  # nulla è partito

    def test_local_overload_does_not_block_remote(self) -> None:
        """Task 3.5: l'affanno del modello LOCALE non blocca il cloud — i due
        percorsi non condividono i colli di bottiglia (D9)."""
        slow = gateway.SessionTracker(None)
        for _ in range(5):  # 1 t/s: sotto il pavimento → 429 locali
            slow.record("slow", "chat", "1", 200, 1, 1, 5000.0,
                        tok_in=1, tok_out=5)
        orig = gateway._TRACKER
        gateway._TRACKER = slow
        slow.set_state("remote_enabled", "1")  # flags sul tracker attivo
        try:
            status, _ = self._post({"messages": [{"role": "user", "content": "x"}]},
                                   {"X-Step": "1"})
            self.assertEqual(status, 429)  # la chat LOCALE è rifiutata…
            status, body = self._post(self._remote_body(), {"X-Step": "5"})
            self.assertEqual(status, 200)  # …ma quella REMOTA passa
            self.assertEqual(body["reply"], REMOTE_REPLY)
        finally:
            gateway._TRACKER = orig

    def test_remote_burst_does_not_mask_local_overload(self) -> None:
        """Task 3.5: una raffica di chat remote VELOCI non alza la mediana di
        backpressure locale (non produce punti t/s)."""
        for i in range(4):
            self._drain(f"fast{i}")
        pts = gateway._TRACKER.tps_points(64)["points"]
        self.assertEqual(pts, [])  # solo chat remote: nessun punto locale
        self.assertFalse(gateway._overloaded([]))  # cancello locale intatto


class RemoteStatusTest(RemoteChatTest):
    """Task 4.1/4.2: `/api/model-status` espone lo stato remoto; i comandi
    admin aggiornano i kv; default interruttore OFF; senza token unavailable."""

    def _get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(self.gw_url + path, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def _admin(self, action: str) -> dict:
        req = urllib.request.Request(
            self.gw_url + "/api/admin/remote",
            data=json.dumps({"action": action}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))["remote"]

    def test_model_status_remote_shape(self) -> None:
        status, body = self._get("/api/model-status")
        self.assertEqual(status, 200)
        rem = body["remote"]
        self.assertTrue(rem["available"])           # token configurato nel test
        self.assertTrue(rem["enabled"])             # acceso in setUp
        self.assertFalse(rem["tripped"])
        self.assertEqual(tuple(rem["models"]), _MODELS)
        w = rem["window"]
        self.assertEqual(w["req_limit"], 10)        # budget visibile al client
        self.assertEqual(w["req"], 0)
        self.assertIn("tok_in_limit", w)

    def test_window_budget_grows_after_request(self) -> None:
        self._drain("bud")
        _, body = self._get("/api/model-status")
        w = body["remote"]["window"]
        self.assertEqual(w["req"], 1)
        self.assertEqual(w["tok_in"], 33)           # usage reale della fake
        self.assertEqual(w["tok_out"], 120)

    def test_remote_totals_and_provider(self) -> None:
        """Il pannello vuole i TOTALI storici dell'endpoint (non solo la
        finestra 60 s) e la tendina della ⑤ vuole il provider dichiarato,
        come «locale» è dichiarato per il modello del campo."""
        self._drain("tot")
        _, body = self._get("/api/model-status")
        rem = body["remote"]
        t = rem["totali"]
        self.assertEqual(t["req"], 1)
        self.assertEqual(t["tok_in"], 33)
        self.assertEqual(t["tok_out"], 120)
        self.assertTrue(rem["provider"])            # es. «Hetzner» dal DNS name

    def test_admin_switch_default_off_and_roundtrip(self) -> None:
        """L'interruttore parte OFF (spec): senza «on» nessuna richiesta remota
        passa; on/off/unlock girano sui kv e tornano lo stato aggiornato."""
        gateway._TRACKER.set_state("remote_enabled", "0")  # default di campo
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertTrue(body["remote_disabled"])
        rem = self._admin("on")
        self.assertTrue(rem["enabled"])
        rem = self._admin("off")
        self.assertFalse(rem["enabled"])
        self.assertEqual(self._admin("unlock")["tripped"], False)

    def test_admin_bad_action_400(self) -> None:
        req = urllib.request.Request(
            self.gw_url + "/api/admin/remote",
            data=json.dumps({"action": "explode"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)

    def test_no_token_means_unavailable(self) -> None:
        """Senza HETZNER_API_KEY: available false e la richiesta remota è
        rifiutata senza uscire (il laboratorio resta identico a prima)."""
        gateway.HETZNER_API_KEY = None
        n = len(self.rec.requests)
        status, body = self._post(self._remote_body(), {"X-Step": "5"})
        self.assertEqual(status, 503)
        self.assertTrue(body["remote_disabled"])
        self.assertEqual(len(self.rec.requests), n)
        _, ms = self._get("/api/model-status")
        self.assertFalse(ms["remote"]["available"])

    def test_flags_survive_tracker_restart(self) -> None:
        """Interruttore e circuito vivono nel kv della sessions.db: un rebuild
        del gateway non li azzera (spec «Stati persistenti»)."""
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "s.db")
        try:
            gateway._TRACKER = gateway.SessionTracker(db)
            gateway._REMOTE = gateway.RemoteState()
            self._admin("on")
            gateway._REMOTE.trip("motivo di test")
            # gateway riavviato: stesso DB, tracker e gate rifatti
            gateway._TRACKER = gateway.SessionTracker(db)
            gateway._REMOTE = gateway.RemoteState()
            gateway._REMOTE.rebuild()
            self.assertTrue(gateway._REMOTE.enabled())
            self.assertTrue(gateway._REMOTE.tripped())
            self.assertEqual(gateway._REMOTE.reason(), "motivo di test")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RemoteConsumiTest(RemoteChatTest):
    """Task 5.2/5.3: aggregati separati per endpoint — il confronto
    locale-vs-frontiera si calcola SOLO sulle interazioni locali, la parte
    remota viaggia con token reali e costo a listino."""

    def _get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(self.gw_url + path, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_mixed_session_split(self) -> None:
        # 1 chat locale (2 s, 100 in / 50 out) + 1 remota (33 in / 120 out)
        gateway._TRACKER.record("mix", "chat", "1", 200, 10, 10, 2000.0,
                                tok_in=100, tok_out=50)
        gateway._TRACKER.record("mix", "chat", "5", 200, 10, 10, 500.0,
                                tok_in=33, tok_out=120, endpoint=_QWEN)
        status, body = self._get("/api/consumi/mix")
        self.assertEqual(status, 200)
        # il confronto (top-level = parte LOCALE): secondi e token solo locali
        self.assertEqual(body["secondi"], 2.0)     # i 500 ms remoti non contano
        self.assertEqual(body["tok_in"], 100)
        self.assertEqual(body["tok_out"], 50)
        self.assertGreater(body["locale"]["kwh"], 0.0)
        # la parte remota: token reali + costo a listino del modello
        self.assertEqual(len(body["remoto"]), 1)
        r = body["remoto"][0]
        self.assertEqual(r["modello"], _QWEN)
        self.assertEqual((r["tok_in"], r["tok_out"]), (33, 120))
        self.assertGreater(r["euro"], 0.0)

    def test_remote_only_session_has_tokens(self) -> None:
        gateway._TRACKER.record("cloud", "chat", "5", 200, 10, 10, 400.0,
                                tok_in=33, tok_out=120, endpoint="Kimi-K2.7-Code")
        status, body = self._get("/api/consumi/cloud")
        self.assertEqual(status, 200)
        self.assertTrue(body["has_tokens"])
        self.assertEqual(body["tok_out"], 0)            # niente token locali
        self.assertEqual(body["remoto"][0]["modello"], "Kimi-K2.7-Code")

    def test_two_remote_models_listed_separately(self) -> None:
        for ep in _MODELS:
            gateway._TRACKER.record("due", "chat", "5", 200, 10, 10, 100.0,
                                    tok_in=10, tok_out=20, endpoint=ep)
        _, body = self._get("/api/consumi/due")
        self.assertEqual({r["modello"] for r in body["remoto"]}, set(_MODELS))


class PageRemoteTest(unittest.TestCase):
    """Task 6.x/7.1: contratto strutturale del selettore in tappa ⑤ e del
    riquadro consumi a colonna singola (come i page-test esistenti)."""

    def _read(self) -> str:
        return _read("backend/web/static/index.html")

    # --- selettore ---------------------------------------------------------
    def test_model_picker_markup_and_render(self) -> None:
        body = self._read()
        for i in ("model-pick", "model5", "model5-budget", "remote-note"):
            self.assertIn(f'id="{i}"', body)
        self.assertIn("renderModelPick", body)      # lo stato arriva dal poll
        self.assertIn("state.remote", body)          # fonte: model-status
        # budget della finestra visibile accanto al selettore
        self.assertIn("req_limit", body)

    def test_selector_only_when_gateway_says_so(self) -> None:
        """available && enabled && !tripped: la pagina non decide nulla, il
        gateway sì (token, interruttore educatore, circuito)."""
        body = self._read()
        self.assertIn("r.available", body)
        self.assertIn("r.enabled", body)
        self.assertIn("r.tripped", body)

    def test_selector_names_the_provider(self) -> None:
        """Come si dice «Modello locale», il remoto dichiara il provider
        (es. «Hetzner · Qwen3.6-35B»): da dove arriva la risposta non è un
        dettaglio, è parte della lezione. Anche il budget dice DI CHI è il
        limite («disponibili su Hetzner»), e si vede solo con un modello
        remoto selezionato: col locale sarebbe rumore fuorviante."""
        body = self._read()
        self.assertIn("r.provider", body)
        self.assertIn("disponibili su", body)

    def test_model_change_resets_conversation(self) -> None:
        """Cambio modello a conversazione aperta = azzeramento (come i preset
        di tappa ②); il prompt seme torna nell'input."""
        body = self._read()
        self.assertIn("chat5.reset()", body)
        self.assertIn("setModel5", body)

    def test_chat5_sends_model_field(self) -> None:
        body = self._read()
        self.assertIn("body.model", body)            # la factory lo inoltra
        self.assertIn("model: function", body)       # la ⑤ lo dichiara

    def test_remote_disabled_no_autoretry(self) -> None:
        """remote_disabled ≠ overload: niente countdown/retry, il ragazzo
        avvisa l'educatore; e l'errore remoto NON spegne le chat locali."""
        import re
        body = self._read()
        self.assertIn("j.remote_disabled", body)
        self.assertIn("j.remote_error", body)
        # il ramo remote_disabled non tocca modelActive (isolation 6.4)
        m = re.search(r"j\.remote_disabled[\s\S]{0,400}?return;", body)
        self.assertIsNotNone(m)
        self.assertNotIn("modelActive", m.group(0))
        # refresh dello stato remoto al volo (selettore si blocca subito)
        self.assertIn("refreshRemote", body)

    def test_remote_reply_badge_cloud(self) -> None:
        body = self._read()
        self.assertIn("badgeIcon", body)             # icona parametrica nel badge
        self.assertIn('badgeIcon: "cloud"', body)    # la ⑤ usa la nuvola

    def test_remote_spinner_says_what_you_wait(self) -> None:
        """Misurato al campo: 194 s per una card sul tier gratuito. Lo spinner
        deve dirlo — un ragazzo davanti a tre minuti di «elaborazione» pensa
        che si sia rotto qualcosa."""
        body = self._read()
        self.assertIn("elaborazione sul modello remoto", body)
        self.assertIn("anche minuti", body)

    # --- consumi: due tabelle in ⑤ ------------------------------------------
    def test_consumi_two_tables_in_step5(self) -> None:
        """Tappa ⑤ + modello remoto: DUE tabelle (feedback di campo) — il
        modello scelto (token reali, costo a listino) e la sessione in
        locale (il confronto di sempre, solo chat locali)."""
        body = self._read()
        self.assertIn("renderConsumiRemoto", body)
        self.assertIn("state.step === 4", body)      # tappa ⑤ (indice 4)
        self.assertIn("Il modello scelto", body)     # tabella 1
        self.assertIn("La tua sessione in locale", body)  # tabella 2
        self.assertIn("listino", body)               # dichiarato nel riquadro
        # la tabella di confronto è UNA sola funzione, condivisa dai due casi
        self.assertIn("comparisonTableHtml", body)

    def test_status_boxes_at_page_bottom(self) -> None:
        """Feedback di campo: banner «modello attivo» e riquadro «costo
        sessione» stanno DOPO il riquadro di input dell'ultima tappa e
        PRIMA della navigazione/footer — non più in testa alla pagina."""
        body = self._read()
        last_input = body.index('id="html-preview"')       # fine tappa ⑤
        banner = body.index('id="model-banner"')
        consumi = body.index('id="consumi"')
        nav = body.index('class="nav"')
        footer = body.index("<footer>")
        self.assertLess(last_input, banner, "il banner sta dopo il riquadro di input")
        self.assertLess(banner, consumi)
        self.assertLess(consumi, nav, "i riquadri stanno prima della navigazione")
        self.assertLess(nav, footer)

    def test_consumi_refresh_on_step_change(self) -> None:
        """La forma del riquadro cambia entrando/uscendo dalla ⑤: si
        ridisegna subito, non al prossimo tick dei 5 s."""
        body = self._read()
        self.assertIn("loadConsumi();", body)


class AdminRemoteTest(unittest.TestCase):
    """Task 8.x: riquadro «Endpoint reale», comandi on/off/sblocco, badge."""

    def _read(self) -> str:
        return _read("backend/web/static/admin.html")

    def test_remote_box_and_commands(self) -> None:
        body = self._read()
        self.assertIn('id="remotebox"', body)
        self.assertIn("/api/admin/remote", body)     # POST dei comandi
        self.assertIn('"on"', body)
        self.assertIn('"off"', body)
        self.assertIn('"unlock"', body)

    def test_remote_status_from_model_status(self) -> None:
        body = self._read()
        self.assertIn("/api/model-status", body)     # stato + finestra live
        self.assertIn("loadRemote", body)            # nel poll del pannello

    def test_remote_totals_shown(self) -> None:
        """Il riquadro mostra anche i TOTALI dall'inizio (richieste e token
        scambiati sull'endpoint esterno), oltre alla finestra 60 s."""
        body = self._read()
        self.assertIn("totali", body)
        self.assertIn("dall'inizio", body)

    def test_remote_rows_badged_in_timeline(self) -> None:
        body = self._read()
        self.assertIn("it.endpoint", body)           # riga remota evidenziata
        self.assertIn("i-cloud", body)               # icona (niente emoji)
        self.assertIn("shortEp", body)               # nome modello leggibile

    def test_remote_sessions_badged_in_list(self) -> None:
        body = self._read()
        self.assertIn("a.remote", body)              # sessione con endpoint reale

    def test_cloud_icon_in_sprite(self) -> None:
        body = self._read()
        self.assertIn('symbol id="i-cloud"', body)   # il glifo esiste


class TimeoutRegressionTest(RemoteChatTest):
    """Regressione osservata al campo (21:44): i modelli lenti NON fanno
    streaming — la risposta (header compresi) arriva solo a generazione
    finita. Il timeout in fase di LETTURA solleva TimeoutError crudo (non è
    URLError): senza except dedicato il thread moriva senza rispondere e al
    ragazzo arrivava la pagina HTML di errore di nginx («risposta non JSON»),
    senza riga registrata. Qui si verifica che OGNI percorso risponde JSON
    e registra la riga."""

    def test_remote_timeout_returns_json_and_records_row(self) -> None:
        self.rec.delay = 0.8
        orig = gateway._PROXY_TIMEOUT
        gateway._PROXY_TIMEOUT = 0.2  # al volo: il fake «genera» per 0.8 s
        try:
            status, body = self._post(self._remote_body(),
                                      {"X-Step": "5", "X-Client-Id": "slow"})
        finally:
            gateway._PROXY_TIMEOUT = orig
        self.assertEqual(status, 504)
        self.assertTrue(body["remote_error"])
        self.assertIn("non ha risposto in tempo", body["error"])
        # la riga esiste ed è marcata remota: l'educatore la vede
        _wait_tracked("slow")
        row = gateway._TRACKER.timeline("slow")["interactions"][0]
        self.assertEqual(row["endpoint"], _QWEN)
        self.assertEqual(row["status"], 504)
        # e il Bearer continua a non attraversare il confine
        self.assertNotIn("token-segreto-test", json.dumps(body))

    def test_local_timeout_returns_json(self) -> None:
        """Stesso buco latente sul percorso locale (Pi 3, 0.5B, 768 token)."""
        delay_holder = {"s": 0.8}

        class SlowLlama(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                time.sleep(delay_holder["s"])
                b = json.dumps({"choices": [{"message": {
                    "content": "troppo tardi"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, fmt, *args) -> None:
                pass

        slow = ThreadingHTTPServer(("127.0.0.1", 0), SlowLlama)
        st = threading.Thread(target=slow.serve_forever, daemon=True)
        st.start()
        orig = (gateway.LLAMA_URL, gateway._PROXY_TIMEOUT)
        gateway.LLAMA_URL = f"http://127.0.0.1:{slow.server_address[1]}"
        gateway._PROXY_TIMEOUT = 0.2
        try:
            status, body = self._post(
                {"messages": [{"role": "user", "content": "x"}]},
                {"X-Client-Id": "slowloc"})
        finally:
            gateway.LLAMA_URL, gateway._PROXY_TIMEOUT = orig
            slow.shutdown(); slow.server_close(); st.join(timeout=2)
        self.assertEqual(status, 504)
        self.assertFalse(body["model_active"])
        self.assertIn("non ha risposto in tempo", body["error"])


class EscapingRegressionTest(unittest.TestCase):
    """Regressione osservata al campo: esc()/testoSicuro leggevano textContent
    (stringa GREZZA) invece di innerHTML (escapata) — l'HTML generato dal
    modello nelle card della tappa ⑤ veniva INTERPRETATO nel pannello admin,
    cambiando la grafica. Contratto: la funzione DEVE leggere innerHTML."""

    def test_admin_esc_reads_innerhtml(self) -> None:
        m = re.search(r"function esc\(s\)[^\n]*", _read("backend/web/static/admin.html"))
        self.assertIsNotNone(m, "esc() non trovata in admin.html")
        self.assertIn("return d.innerHTML", m.group(0))
        self.assertNotIn("return d.textContent", m.group(0))

    def test_index_testosicuro_reads_innerhtml(self) -> None:
        m = re.search(r"function testoSicuro\(s\)[^\n]*",
                      _read("backend/web/static/index.html"))
        self.assertIsNotNone(m, "testoSicuro() non trovata in index.html")
        self.assertIn("return d.innerHTML", m.group(0))
        self.assertNotIn("return d.textContent", m.group(0))

    def test_timeouts_coherent_client_gateway_nginx(self) -> None:
        """client (270 s) > gateway (240 s) > ... > mai: il JSON del gateway
        arriva sempre PRIMA della pagina HTML di nginx (300 s). Costanti
        tenute sincrone dal test, come già TPS_FLOOR."""
        idx = _read("backend/web/static/index.html")
        gw = _read("backend/gateway.py")
        conf = _read("nginx.conf")
        self.assertIn("_PROXY_TIMEOUT = 240", gw)
        self.assertIn("proxy_read_timeout 300s", conf)
        self.assertIn("270000", idx)


class AllowlistCoherenceTest(unittest.TestCase):
    """Ogni modello dell'allowlist del gateway DEVE avere il listino in
    costi.py (e viceversa i modelli permessi hanno il prezzo): senza questo
    incrocio le due costanti sfaldano silenziosamente ai cambi."""

    def test_allowlist_has_listino(self) -> None:
        from backend import costi
        for m in gateway._REMOTE_MODELS:
            self.assertIn(m, costi.REMOTO_LISTINO_EUR_PER_MTOKEN,
                          msg=f"{m} in allowlist ma senza listino in costi.py")

    def test_kimi_not_permitted_not_offered(self) -> None:
        """Campo 2026-08-17: Kimi-K2.7-Code risponde «model use not
        permitted» col nostro token — l'allowlist non lo offre (il listino
        resta commentato in costi.py per quando Hetzner lo abilita)."""
        self.assertNotIn("Kimi-K2.7-Code", gateway._REMOTE_MODELS)


if __name__ == "__main__":
    unittest.main()

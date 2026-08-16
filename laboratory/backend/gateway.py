"""Gateway del laboratorio web (stdlib http.server, zero dipendenze).

Tier 2 della topologia a 3 tier (nginx statici / **questo gateway** / llama):
ospita la business logic e espone SOLO gli endpoint `/api/*` — nessun serving
statico (quello è compito di nginx, che fa anche da reverse proxy same-origin):
  - **bridge** al servizio skill (`/api/scaffold`, `/api/health`) — tappa 3,
    output vincolato dal contratto `SkillOutput`
  - **bridge controllato** a llama-server per le chat libere delle tappe
    1/2/4 (`/api/chat`) — la pagina passa sempre di qui: normalizzazione dei
    parametri e osservabilità vivono nel gateway, non nel client
  - **osservabilità**: log strutturato di ogni richiesta, tracker sessioni
    in-memory (thread-safe), persistenza su JSONL, rotte `/api/sessions(<id>)`.
    Solo metadati (mai il testo degli appunti/chat dei ragazzi) a meno di
    LAB_LOG_VERBOSE=1.

Route (solo /api/* — tutto il resto è 404 JSON):
  GET  /api/health             -> proxy a skill /health
  GET  /api/model-status       -> {model_active, model?, clients} (sempre 200)
  GET  /api/sessions           -> utenti "connessi" + contatori (solo metadati)
  GET  /api/sessions/<cid>     -> timeline interazioni di un utente (solo metadati)
  POST /api/scaffold           -> proxy a skill /scaffold
  POST /api/chat               -> bridge a llama /v1/chat/completions (body normalizzato)

Config via env:
  SKILL_URL, LLAMA_URL, GATEWAY_PORT
  LAB_SESSIONS_DIR   dir del file JSONL delle sessioni (default <repo>/sessions; ""=off)
  LAB_LOG_VERBOSE    "1" aggiunge anteprime troncate (in/out) per debug (OFF di default)
  LAB_ACTIVE_WINDOW  secondi per considerare un client "connesso" (default 300)

Indipendenza dei moduli (D1): questo modulo NON importa skill/service (parla
solo HTTP) e viceversa — la separazione che conta è quella del contratto.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_URL = os.environ.get("SKILL_URL", "http://localhost:8080").rstrip("/")
LLAMA_URL = os.environ.get("LLAMA_URL", "http://localhost:8081").rstrip("/")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "8090"))
LAB_SESSIONS_DIR = os.environ.get("LAB_SESSIONS_DIR") or os.path.join(_HERE, "..", "sessions")
LAB_LOG_VERBOSE = os.environ.get("LAB_LOG_VERBOSE", "0") == "1"
LAB_ACTIVE_WINDOW = int(os.environ.get("LAB_ACTIVE_WINDOW", "300"))

_PROXY_TIMEOUT = 120  # s: il modello reale può impiegare qualche secondo
_STATUS_TIMEOUT = 2   # s: probe rapido

# Parametri del bridge chat (costanti, come l'adapter) — non manipolabili dal client.
_CHAT_REPEAT_PENALTY = 1.1
_CHAT_MAX_TURNS = 32
_CHAT_DEFAULT_TEMP = 0.7
_CHAT_DEFAULT_MAX_TOKENS = 256
_CHAT_MAX_TOKENS_CEILING = 768
_ROLE_OK = ("system", "user", "assistant")

_MODEL_CACHE: dict = {"name": None, "ts": 0.0}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _normalize_chat_body(client: dict) -> tuple[dict | None, str | None]:
    """Ricostruisce il body per llama: strip chiavi extra, clamp numeri, costanti fisse."""
    msgs = client.get("messages") if isinstance(client, dict) else None
    if not isinstance(msgs, list) or not msgs or len(msgs) > _CHAT_MAX_TURNS:
        return None, "messages non validi"
    out_msgs: list[dict] = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in _ROLE_OK \
                or not isinstance(m.get("content"), str):
            return None, "messages non validi"
        out_msgs.append({"role": m["role"], "content": m["content"]})
    try:
        temp = float(client.get("temperature", _CHAT_DEFAULT_TEMP))
    except (TypeError, ValueError):
        temp = _CHAT_DEFAULT_TEMP
    temp = _clamp(temp, 0.0, 1.5)
    try:
        mt = int(client.get("max_tokens", _CHAT_DEFAULT_MAX_TOKENS))
    except (TypeError, ValueError):
        mt = _CHAT_DEFAULT_MAX_TOKENS
    mt = _clamp(mt, 16, _CHAT_MAX_TOKENS_CEILING)
    return {"messages": out_msgs, "temperature": temp, "max_tokens": mt,
            "repeat_penalty": _CHAT_REPEAT_PENALTY, "stream": False}, None


def _llama_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/health", timeout=_STATUS_TIMEOUT) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


def _llama_model_name() -> str | None:
    """Best-effort: nome modello da llama /props (cache 60s)."""
    now = time.time()
    if _MODEL_CACHE["name"] is not None and now - _MODEL_CACHE["ts"] < 60:
        return _MODEL_CACHE["name"]
    name = None
    try:
        with urllib.request.urlopen(LLAMA_URL + "/props", timeout=_STATUS_TIMEOUT) as r:
            p = json.loads(r.read().decode("utf-8"))
        g = p.get("general") or {}
        name = g.get("name") or p.get("model_name")
        if not name:
            mp = p.get("model_path") or p.get("model")
            if mp:
                name = os.path.basename(str(mp))
    except Exception:  # noqa: BLE001
        name = None
    _MODEL_CACHE["name"] = name
    _MODEL_CACHE["ts"] = now
    return name


class SessionTracker:
    """Tracker sessioni in-memory (thread-safe) + persistenza JSONL. Solo metadati."""

    def __init__(self, jsonl_path: str | None) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._path = jsonl_path

    def record(self, cid: str, kind: str | None, step: str | None, status: int,
               in_len: int, out_len: int, ms: float, previews: dict | None) -> None:
        ts = time.time()
        with self._lock:
            s = self._sessions.setdefault(
                cid, {"last_seen": ts, "count": 0, "steps": set(), "recent": []})
            s["last_seen"] = ts
            s["count"] += 1
            if step:
                s["steps"].add(str(step))
            row = {"ts": ts, "client": cid, "kind": kind, "step": step,
                   "status": status, "in_len": in_len, "out_len": out_len, "ms": int(ms)}
            if previews:
                row.update(previews)
            s["recent"].append(row)
            if len(s["recent"]) > 200:
                s["recent"] = s["recent"][-200:]
        if self._path:
            self._append(row)

    def _append(self, row: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def active_list(self, window: int) -> dict:
        cutoff = time.time() - window
        with self._lock:
            out = [{"client": cid, "last_seen": s["last_seen"], "count": s["count"],
                    "steps": sorted(s["steps"])}
                   for cid, s in self._sessions.items() if s["last_seen"] >= cutoff]
        out.sort(key=lambda x: x["last_seen"], reverse=True)
        return {"active": out, "total": len(out)}

    def timeline(self, cid: str) -> dict:
        with self._lock:
            s = self._sessions.get(cid)
            if not s:
                return {"client": cid, "interactions": [], "last_seen": None}
            return {"client": cid, "interactions": list(s["recent"]),
                    "last_seen": s["last_seen"]}


_TRACKER = SessionTracker(os.path.join(LAB_SESSIONS_DIR, "sessions.jsonl"))


class Handler(BaseHTTPRequestHandler):
    server_version = "laboratorio-gateway/1.0"

    # --- setup per richiesta ---------------------------------------------
    def _begin(self) -> None:
        self._t0 = time.perf_counter()
        self._cid = (self.headers.get("X-Client-Id") or "anon")[:40]
        self._step = self.headers.get("X-Step")
        self._meta: dict | None = None

    def _access(self, code: int, ms: float) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        extra = ""
        if self._meta:
            extra = f" [kind={self._meta.get('kind')} in={self._meta.get('in_len', 0)}" \
                    f" out={self._meta.get('out_len', 0)}]"
        sys.stderr.write(
            f"[{ts}] #{self._cid} {self.command} {self.path.split('?', 1)[0]} "
            f"-> {code} ({ms:.0f}ms){extra}\n")
        sys.stderr.flush()

    # --- helper di risposta (logga + track) ------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)
        # access log di ogni risposta + track delle interazioni significative
        try:
            ms = (time.perf_counter() - self._t0) * 1000
        except AttributeError:
            ms = 0.0
        self._access(code, ms)
        if self._meta and self._meta.get("kind"):
            previews = self._meta.get("previews") if LAB_LOG_VERBOSE else None
            _TRACKER.record(self._cid, self._meta.get("kind"), self._meta.get("step"),
                            code, self._meta.get("in_len", 0), self._meta.get("out_len", 0),
                            ms, previews)

    def _send_json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # --- proxy verso il servizio skill ------------------------------------
    def _proxy(self, method: str, skill_path: str, body: bytes | None = None) -> None:
        url = SKILL_URL + skill_path
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        is_scaffold = (skill_path == "/scaffold")
        in_len = len(body or b"")
        if is_scaffold:
            # step dall'header della pagina (X-Step); default = tappa ④ Workflow
            self._meta = {"kind": "scaffold", "step": self.headers.get("X-Step") or "4",
                          "in_len": in_len, "out_len": 0}
            if LAB_LOG_VERBOSE:
                self._meta["previews"] = {"in_preview": (body or b"").decode("utf-8", "replace")[:60]}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "application/json; charset=utf-8")
        except urllib.error.HTTPError as e:
            d = e.read()
            if is_scaffold and self._meta:
                self._meta["out_len"] = len(d)
            self._send(e.code, d, "application/json; charset=utf-8")
            return
        except urllib.error.URLError as e:
            self._send_json(502, {"error": f"servizio skill non raggiungibile: {e.reason}"})
            return
        if is_scaffold and self._meta:
            self._meta["out_len"] = len(data)
            if LAB_LOG_VERBOSE:
                self._meta.setdefault("previews", {})["out_preview"] = data.decode("utf-8", "replace")[:60]
        self._send(200, data, ctype)

    # --- bridge chat verso llama-server (tappe 1/2/4) ---------------------
    def _chat_llama(self, raw: bytes) -> None:
        """POST /api/chat -> bridge a llama /v1/chat/completions, body normalizzato."""
        try:
            client = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "body JSON non valido"})
            return
        if not isinstance(client, dict):
            self._send_json(400, {"error": "body JSON non valido"})
            return

        body, err = _normalize_chat_body(client)
        if err:
            self._meta = {"kind": "chat", "step": self._step, "in_len": len(raw), "out_len": 0}
            self._send_json(400, {"error": err, "model_active": False})
            return

        self._meta = {"kind": "chat", "step": self._step, "in_len": len(raw), "out_len": 0}
        if LAB_LOG_VERBOSE:
            try:
                msgs = client.get("messages") or []
                last_user = next((m["content"] for m in reversed(msgs)
                                  if isinstance(m, dict) and m.get("role") == "user"), "")
                self._meta["previews"] = {"in_preview": str(last_user)[:60]}
            except Exception:  # noqa: BLE001
                pass

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            LLAMA_URL + "/v1/chat/completions", data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # HTTPError è sotto URLError
            self._send_json(502, {"error": f"errore modello: {e.code} {e.reason}",
                                  "model_active": False})
            return
        except urllib.error.URLError:
            self._send_json(503, {"error": "modello non attivo", "model_active": False})
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(502, {"error": "risposta modello imprevista", "model_active": False})
            return
        try:
            reply = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            self._send_json(502, {"error": "risposta modello imprevista", "model_active": False})
            return
        if not isinstance(reply, str):
            reply = str(reply)
        # usage (prompt/completion tokens) e finish_reason ("stop"/"length")
        # inoltrati invariati: alimentano contatore di contesto e nota «tagliata
        # dal limite» della pagina — il gateway non li interpreta
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        try:
            finish = payload["choices"][0].get("finish_reason")
        except (KeyError, TypeError, AttributeError):
            finish = None
        if self._meta:
            self._meta["out_len"] = len(reply)
            if LAB_LOG_VERBOSE:
                self._meta.setdefault("previews", {})["out_preview"] = reply[:60]
        out = {"reply": reply}
        if usage:
            out["usage"] = usage
        if isinstance(finish, str) and finish:
            out["finish_reason"] = finish
        self._send_json(200, out)

    # --- model-status -----------------------------------------------------
    def _model_status(self) -> None:
        """GET /api/model-status -> {model_active, model?, clients} (sempre 200)."""
        active = _llama_reachable(LLAMA_URL)
        clients = _TRACKER.active_list(LAB_ACTIVE_WINDOW)["total"]
        model = _llama_model_name() if active else None
        self._send_json(200, {"model_active": active, "model": model, "clients": clients})

    # --- osservabilità ----------------------------------------------------
    def _sessions_list(self) -> None:
        self._send_json(200, _TRACKER.active_list(LAB_ACTIVE_WINDOW))

    def _session_timeline(self, cid: str) -> None:
        self._send_json(200, _TRACKER.timeline(cid))

    # --- routing (SOLO /api/*: il resto è del tier statico, qui 404) ------
    def do_GET(self) -> None:  # noqa: N802 - signature stdlib
        self._begin()
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._proxy("GET", "/health")
            return
        if path == "/api/model-status":
            self._model_status()
            return
        if path == "/api/sessions":
            self._sessions_list()
            return
        if path.startswith("/api/sessions/"):
            cid = path[len("/api/sessions/"):]
            self._session_timeline(cid)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - signature stdlib
        self._begin()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path == "/api/scaffold":
            self._proxy("POST", "/scaffold", body)
        elif self.path == "/api/chat":
            self._chat_llama(body)
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args) -> None:  # logghiamo noi in _send
        pass


def main() -> None:
    print(
        f"laboratorio-gateway in ascolto su :{GATEWAY_PORT} "
        f"(skill={SKILL_URL}, llama={LLAMA_URL}, "
        f"sessions={_TRACKER._path}, verbose={LAB_LOG_VERBOSE})",
        flush=True,
    )
    server = ThreadingHTTPServer(("0.0.0.0", GATEWAY_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

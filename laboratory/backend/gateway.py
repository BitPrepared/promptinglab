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
    in-memory (thread-safe), persistenza su **sqlite3** (write-through +
    load-all all'avvio: lo storico sopravvive a riavvii e rebuild; il DB vive
    nel volume ./sessions), rotte `/api/sessions(<id>)`. Metadati (chi, quando,
    tappa, esito, durata, IP) e contenuto completo delle interazioni (design D3
    del change admin-osservabilita: a delta — ultimo messaggio utente e
    risposta, non la cronologia ri-inviata). Azzeramento: `make clean-sessions`.

Route (solo /api/* — tutto il resto è 404 JSON):
  GET  /api/health             -> proxy a skill /health
  GET  /api/model-status       -> {model_active, model?, clients} (sempre 200)
  GET  /api/tps                -> serie globale token/s delle chat recenti
                                  (ritmo VISTO dal gateway: round-trip incluso)
  GET  /api/consumi/<cid>      -> stime didattiche locale vs frontiera per la
                                  sessione (backend/costi.py; senza token:
                                  has_tokens=false)
  GET  /api/sessions           -> elenco sessioni; ?window=<sec>|all (clamp
                                  [60,86400], default LAB_ACTIVE_WINDOW) e
                                  ?ip=<addr> filtro sull'insieme degli IP visti
  GET  /api/sessions/<cid>     -> timeline interazioni di un utente (metadati,
                                  contenuti in/out, flag has_trace — la wire
                                  JSON NON viaggia nel poll: design D4)
  GET  /api/sessions/<cid>/<ts> -> dettaglio di una interazione: riga completa
                                  con la trace {request, response} verso il
                                  modello (change trace-llm)
  POST /api/scaffold           -> proxy a skill /scaffold (pass-through del
                                  campo opzionale `trace` della skill)
  POST /api/chat               -> bridge a llama /v1/chat/completions (body
                                  normalizzato; risposta con `trace`: request
                                  inoltrata e response grezza, sempre — D3)

Config via env:
  SKILL_URL, LLAMA_URL, GATEWAY_PORT
  LAB_SESSIONS_DIR   dir del DB sqlite delle sessioni (default <repo>/sessions,
                     file sessions.db; il vecchio sessions.jsonl è archivio legacy)
  LAB_ACTIVE_WINDOW  secondi per considerare un client "connesso" (default 300)

Indipendenza dei moduli (D1): questo modulo NON importa skill/service (parla
solo HTTP) e viceversa — la separazione che conta è quella del contratto.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from backend.costi import stima as _stima_consumi

_HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_URL = os.environ.get("SKILL_URL", "http://localhost:8080").rstrip("/")
LLAMA_URL = os.environ.get("LLAMA_URL", "http://localhost:8081").rstrip("/")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "8090"))
LAB_SESSIONS_DIR = os.environ.get("LAB_SESSIONS_DIR") or os.path.join(_HERE, "..", "sessions")
LAB_ACTIVE_WINDOW = int(os.environ.get("LAB_ACTIVE_WINDOW", "300"))

_PROXY_TIMEOUT = 120  # s: il modello reale può impiegare qualche secondo
_STATUS_TIMEOUT = 2   # s: probe rapido

# Parametri del bridge chat (costanti, come l'adapter) — non manipolabili dal client.
_CHAT_REPEAT_PENALTY = 1.1
_CHAT_MAX_TURNS = 32
_CHAT_DEFAULT_TEMP = 0.7
_CHAT_DEFAULT_MAX_TOKENS = 256
_CHAT_MAX_TOKENS_CEILING = 768
# Tetto token per tappa (policy del gateway, change temperatura-tappa5): la ⑤
# genera codice HTML/CSS e il default basso la taglia a metà tag — la skill ha
# già misurato che servono 768 (nemmeno 512 bastavano al suo JSON pretty-print).
_CHAT_STEP_MAX_TOKENS = {"5": 768}
# Backpressure (revisione 11): se la mediana delle chat RECENTI scende sotto
# questa cadenza (token/s visti dal gateway), le nuove arrivano 429 con
# retry_after — il client avvisa il ragazzo e riprova da solo. Mai a freddo:
# servono almeno _CHAT_TPS_MIN_POINTS osservazioni.
_CHAT_TPS_FLOOR = 10.0
_CHAT_TPS_MIN_POINTS = 3
_CHAT_RETRY_AFTER_S = 10
# La finestra è TEMPORALE, non a conteggio: i 429 non producono punti, quindi
# sotto lockout l'unico ricambio è l'invecchiamento. Con una finestra a
# conteggio gli ultimi N punti lenti tenevano il cancello chiuso per l'età
# massima intera anche a carico finito; così il cancello si riapre da solo
# entro _CHAT_TPS_WINDOW_S dall'ultima chat lenta completata.
_CHAT_TPS_WINDOW_S = 45.0
# quante osservazioni recenti guardare prima del filtro d'età (le più fresche)
_CHAT_TPS_POOL = 64
_ROLE_OK = ("system", "user", "assistant")

_MODEL_CACHE: dict = {"name": None, "ts": 0.0}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _overloaded(points: list[dict]) -> bool:
    """True se la mediana dei token/s RECENTI è sotto il pavimento: accodare
    altre richieste peggiorerebbe l'attesa di tutti — meglio un 429 onesto.
    PRIMA si invecchiano i punti (self-healing: i 429 non ne producono, così
    il cancello si riapre da solo), POI si guarda la mediana di quelli che
    restano nella finestra."""
    now = time.time()
    fresh = sorted(p["tps"] for p in points if now - p["ts"] <= _CHAT_TPS_WINDOW_S)
    if len(fresh) < _CHAT_TPS_MIN_POINTS:
        return False  # a freddo (o finestra svuotata dal ricambio) mai backpressure
    return fresh[len(fresh) // 2] < _CHAT_TPS_FLOOR


def _normalize_chat_body(client: dict, step: str | None = None) -> tuple[dict | None, str | None]:
    """Ricostruisce il body per llama: strip chiavi extra, clamp numeri, costanti fisse.
    Il default del tetto token dipende dalla tappa (header X-Step): policy
    didattica = normalizzazione, quindi sta qui nel gateway, non nel client."""
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
    default_mt = _CHAT_STEP_MAX_TOKENS.get(str(step), _CHAT_DEFAULT_MAX_TOKENS)
    try:
        mt = int(client.get("max_tokens", default_mt))
    except (TypeError, ValueError):
        mt = default_mt
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


_ROW_COLS = ("ts", "client", "ip", "kind", "step", "status",
             "in_len", "out_len", "ms", "in", "out", "turns",
             "tok_in", "tok_out")
_ROW_SQL = ",".join(f'"{c}"' for c in _ROW_COLS)


def _json_opt(trace: dict | None, key: str) -> str | None:
    """Valore di trace[key] serializzato per la colonna req/resp (NULL se manca)."""
    if not trace or trace.get(key) is None:
        return None
    return json.dumps(trace[key], ensure_ascii=False)


class SessionTracker:
    """Tracker sessioni in-memory (thread-safe) + persistenza sqlite3 (design
    D7/D8 del change admin-osservabilita): write-through a ogni interazione e
    load-all all'avvio, così lo storico sopravvive a riavvii e rebuild. Metadati,
    IP e contenuti completi; il vecchio sessions.jsonl è archivio legacy (D9)."""

    def __init__(self, db_path: str | None) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._path = db_path
        self._db = None
        if db_path:
            try:
                d = os.path.dirname(db_path)
                if d:
                    os.makedirs(d, exist_ok=True)
                # check_same_thread=False: la serializzazione la garantisce il
                # lock del tracker; single-writer (un solo processo gateway)
                self._db = sqlite3.connect(db_path, check_same_thread=False)
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS interactions ("
                    "ts REAL, client TEXT, ip TEXT, kind TEXT, step TEXT, "
                    "status INTEGER, in_len INTEGER, out_len INTEGER, ms INTEGER, "
                    '"in" TEXT, "out" TEXT, turns INTEGER, req TEXT, resp TEXT, '
                    "tok_in INTEGER, tok_out INTEGER)")
                for col_ddl in (  # migrazioni per DB creati dai change precedenti
                        "ALTER TABLE interactions ADD COLUMN turns INTEGER",
                        "ALTER TABLE interactions ADD COLUMN req TEXT",
                        "ALTER TABLE interactions ADD COLUMN resp TEXT",
                        "ALTER TABLE interactions ADD COLUMN tok_in INTEGER",
                        "ALTER TABLE interactions ADD COLUMN tok_out INTEGER"):
                    try:
                        self._db.execute(col_ddl)
                    except sqlite3.OperationalError:
                        pass  # colonna già presente
                self._db.commit()
                self._load()
            except (sqlite3.Error, OSError):
                self._db = None  # storage off: si vive di memoria

    def _load(self) -> None:
        """Load-all all'avvio (D8): ricostruisce sessioni, conteggi, insiemi IP
        e timeline leggendo l'archivio in ordine cronologico."""
        if not self._db:
            return
        for r in self._db.execute(
                f"SELECT {_ROW_SQL}, req, resp FROM interactions ORDER BY ts"):
            row = dict(zip(_ROW_COLS + ("req", "resp"), r))
            req, resp = row.pop("req"), row.pop("resp")
            if req is not None or resp is not None:
                row["trace"] = {"request": json.loads(req) if req else None,
                                "response": json.loads(resp) if resp else None}
            s = self._sessions.setdefault(
                row["client"], {"last_seen": row["ts"], "count": 0,
                                "steps": set(), "ips": set(), "recent": []})
            s["last_seen"] = row["ts"]
            s["count"] += 1
            if row["step"]:
                s["steps"].add(str(row["step"]))
            if row["ip"]:
                s["ips"].add(row["ip"])
                s["last_ip"] = row["ip"]
            s["recent"].append(row)

    def record(self, cid: str, kind: str | None, step: str | None, status: int,
               in_len: int, out_len: int, ms: float,
               in_text: str | None = None, out_text: str | None = None,
               ip: str | None = None, turns: int | None = None,
               trace: dict | None = None,
               tok_in: int | None = None, tok_out: int | None = None) -> None:
        ts = time.time()
        with self._lock:
            s = self._sessions.setdefault(
                cid, {"last_seen": ts, "count": 0, "steps": set(),
                      "ips": set(), "recent": []})
            s["last_seen"] = ts
            s["count"] += 1
            if step:
                s["steps"].add(str(step))
            if ip:
                s["ips"].add(ip)
                s["last_ip"] = ip
            row = {"ts": ts, "client": cid, "ip": ip, "kind": kind, "step": step,
                   "status": status, "in_len": in_len, "out_len": out_len,
                   # ms con un decimale: il fake in test risponde sub-ms e il
                   # trunc a int lo azzerava (consumi/tps ne hanno bisogno)
                   "ms": round(ms, 1)}
            # contenuti completi, sempre (design D3: a delta, non a storia)
            if in_text is not None:
                row["in"] = in_text
            if out_text is not None:
                row["out"] = out_text
            if turns is not None:
                row["turns"] = turns  # chat: messaggi trasportati (memoria)
            # wire JSON verso il modello (change trace-llm): la timeline la
            # stripping (has_trace), il dettaglio la serve intera (design D4)
            if trace is not None:
                row["trace"] = trace
            # token del modello quando li espone (change readme-loadtest-consumi):
            # base del grafico token/s
            if tok_in is not None:
                row["tok_in"] = tok_in
            if tok_out is not None:
                row["tok_out"] = tok_out
            s["recent"].append(row)  # niente cap: lo storico non si tronca
            # write-through dentro il lock (D7): memoria e DB coerenti
            if self._db:
                try:
                    self._db.execute(
                        f"INSERT INTO interactions ({_ROW_SQL}, req, resp) "
                        f"VALUES ({','.join('?' * len(_ROW_COLS))},?,?)",
                        tuple(row.get(c) for c in _ROW_COLS)
                        + (_json_opt(trace, "request"), _json_opt(trace, "response")))
                    self._db.commit()
                except sqlite3.Error:
                    pass

    def active_list(self, window: int | None, ip: str | None = None) -> dict:
        """Elenco sessioni: `window` in secondi (None = tutto lo storico del
        processo), `ip` filtra sulle sessioni viste da quell'IP (insieme, non
        solo l'ultimo)."""
        cutoff = (time.time() - window) if window is not None else 0.0
        with self._lock:
            out = [{"client": cid, "last_seen": s["last_seen"], "count": s["count"],
                    "steps": sorted(s["steps"]), "last_ip": s.get("last_ip")}
                   for cid, s in self._sessions.items()
                   if s["last_seen"] >= cutoff and (ip is None or ip in s["ips"])]
        out.sort(key=lambda x: x["last_seen"], reverse=True)
        return {"active": out, "total": len(out)}

    def consumi(self, cid: str) -> dict | None:
        """Aggregati di consumo della sessione per le stime didattiche:
        token totali e secondi di elaborazione (somma dei round-trip)."""
        with self._lock:
            s = self._sessions.get(cid)
            if not s:
                return None
            tok_in = sum(r.get("tok_in") or 0 for r in s["recent"])
            tok_out = sum(r.get("tok_out") or 0 for r in s["recent"])
            secs = sum(r.get("ms") or 0 for r in s["recent"]) / 1000.0
        return {"tok_in": tok_in, "tok_out": tok_out, "secondi": secs,
                "has_tokens": tok_in > 0 or tok_out > 0}

    def timeline(self, cid: str) -> dict:
        with self._lock:
            s = self._sessions.get(cid)
            if not s:
                return {"client": cid, "interactions": [], "last_seen": None}
            # la trace NON viaggia nel poll della timeline (design D4): peso
            # pieno solo su endpoint di dettaglio, flag leggero per il bottone
            rows = [{**{k: v for k, v in row.items() if k != "trace"},
                     "has_trace": "trace" in row} for row in s["recent"]]
            return {"client": cid, "interactions": rows, "last_seen": s["last_seen"]}

    def detail(self, cid: str, ts: float) -> dict | None:
        """Riga interazione completa (trace inclusa) per timestamp; None se assente."""
        with self._lock:
            s = self._sessions.get(cid)
            if not s:
                return None
            for row in s["recent"]:
                if abs(row["ts"] - ts) < 1e-6:
                    return dict(row)
        return None

    def tps_points(self, limit: int = 200) -> dict:
        """Serie globale del ritmo di generazione (change readme-loadtest-consumi,
        design D3): tokens/s VISTI DAL GATEWAY — tok_out diviso il tempo di
        round-trip, attesa e coda incluse. Non è il benchmark puro del modello:
        è il ritmo che il ragazzo sperimenta, che è la lezione.
        `rejected` sono i 429 di backpressure: per il grafico dell'educatore,
        dove il carico che NON è passato si vede quanto quello passato."""
        with self._lock:
            pts = []
            rej = []
            for cid, s in self._sessions.items():
                for row in s["recent"]:
                    if row.get("kind") != "chat":
                        continue
                    tok_out, ms = row.get("tok_out"), row.get("ms")
                    if tok_out and ms:
                        pts.append({"ts": row["ts"], "client": cid,
                                    "tok_in": row.get("tok_in"), "tok_out": tok_out,
                                    "ms": ms,
                                    "tps": round(tok_out / (ms / 1000.0), 1)})
                    elif row.get("status") == 429:
                        rej.append({"ts": row["ts"], "client": cid})
            pts.sort(key=lambda p: p["ts"])
            rej.sort(key=lambda r: r["ts"])
            return {"points": pts[-limit:], "rejected": rej[-limit:]}


_TRACKER = SessionTracker(os.path.join(LAB_SESSIONS_DIR, "sessions.db"))


class Handler(BaseHTTPRequestHandler):
    server_version = "laboratorio-gateway/1.0"

    # --- setup per richiesta ---------------------------------------------
    def _begin(self) -> None:
        self._t0 = time.perf_counter()
        self._cid = (self.headers.get("X-Client-Id") or "anon")[:40]
        self._step = self.headers.get("X-Step")
        # IP del client: X-Real-IP quando arriva via nginx, altrimenti il peer
        # (accesso diretto in LAN da consumer fidati: CLI, debug)
        self._ip = self.headers.get("X-Real-IP") or self.client_address[0]
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
            _TRACKER.record(self._cid, self._meta.get("kind"), self._meta.get("step"),
                            code, self._meta.get("in_len", 0), self._meta.get("out_len", 0),
                            ms, in_text=self._meta.get("in_text"),
                            out_text=self._meta.get("out_text"), ip=self._ip,
                            turns=self._meta.get("turns"),
                            trace=self._meta.get("trace"),
                            tok_in=self._meta.get("tok_in"),
                            tok_out=self._meta.get("tok_out"))

    def _send_json(self, code: int, obj: dict, extra: dict | None = None) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", extra)

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
            try:
                notes = json.loads((body or b"{}").decode("utf-8")).get("notes")
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                notes = None
            self._meta = {"kind": "scaffold", "step": self.headers.get("X-Step") or "4",
                          "in_len": in_len, "out_len": 0,
                          "in_text": notes if isinstance(notes, str) else None}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "application/json; charset=utf-8")
        except urllib.error.HTTPError as e:
            d = e.read()
            if is_scaffold and self._meta:
                self._meta["out_len"] = len(d)
                self._meta["out_text"] = d.decode("utf-8", "replace")
            self._send(e.code, d, "application/json; charset=utf-8")
            return
        except urllib.error.URLError as e:
            self._send_json(502, {"error": f"servizio skill non raggiungibile: {e.reason}"})
            return
        if is_scaffold and self._meta:
            self._meta["out_len"] = len(data)
            self._meta["out_text"] = data.decode("utf-8", "replace")
            # trace della chiamata LLM interna alla skill (campo opzionale,
            # pattern events): il gateway la raccoglie per la persistenza,
            # il pass-through verso la pagina è già trasparente
            try:
                extra = json.loads(data.decode("utf-8", "replace"))
            except (json.JSONDecodeError, AttributeError):
                extra = {}
            tr = extra.get("trace") if isinstance(extra, dict) else None
            if isinstance(tr, dict):
                self._meta["trace"] = tr
            # anche lo scaffold conta nei consumi: la skill espone già gli
            # usage (campo opzionale) — si raccolgono come per le chat
            u = extra.get("usage") if isinstance(extra, dict) else None
            if isinstance(u, dict):
                for src, dst in (("prompt_tokens", "tok_in"),
                                 ("completion_tokens", "tok_out")):
                    if isinstance(u.get(src), int):
                        self._meta[dst] = u[src]
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

        body, err = _normalize_chat_body(client, step=self._step)
        if err:
            self._meta = {"kind": "chat", "step": self._step, "in_len": len(raw), "out_len": 0}
            self._send_json(400, {"error": err, "model_active": False})
            return

        # contenuto in ingresso a delta: l'ultimo messaggio utente, non tutta
        # la cronologia che il client re-invia a ogni turno (design D3)
        try:
            msgs = client.get("messages") or []
            last_user = next((m["content"] for m in reversed(msgs)
                              if isinstance(m, dict) and m.get("role") == "user"), "")
        except Exception:  # noqa: BLE001
            last_user = ""
        self._meta = {"kind": "chat", "step": self._step, "in_len": len(raw), "out_len": 0,
                      "in_text": last_user or None, "turns": len(body["messages"])}

        data = json.dumps(body).encode("utf-8")
        # backpressure: modello già in affanno → 429 con retry_after invece di
        # accodare un'altra attesa (il client avvisa il ragazzo e riprova)
        if _overloaded(_TRACKER.tps_points(_CHAT_TPS_POOL)["points"]):
            self._send_json(
                429,
                {"error": "laboratorio sovraccarico: il modello risponde troppo "
                          "lentamente, riprovo io tra poco",
                 "model_active": True, "overload": True,
                 "retry_after": _CHAT_RETRY_AFTER_S},
                extra={"Retry-After": str(_CHAT_RETRY_AFTER_S)})
            return
        req = urllib.request.Request(
            LLAMA_URL + "/v1/chat/completions", data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # HTTPError è sotto URLError
            # D7: il body d'errore ricevuto È la response della chiamata
            err_body = e.read().decode("utf-8", "replace")
            if self._meta:
                self._meta["trace"] = {"request": body, "response": err_body}
            self._send_json(502, {"error": f"errore modello: {e.code} {e.reason}",
                                  "model_active": False,
                                  "trace": {"request": body, "response": err_body}})
            return
        except urllib.error.URLError:
            if self._meta:
                self._meta["trace"] = {"request": body}  # partita, senza risposta
            self._send_json(503, {"error": "modello non attivo", "model_active": False,
                                  "trace": {"request": body}})
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
        # token per il grafico token/s (se il modello li espone)
        if self._meta and usage:
            for src, dst in (("prompt_tokens", "tok_in"),
                             ("completion_tokens", "tok_out")):
                if isinstance(usage.get(src), int):
                    self._meta[dst] = usage[src]
        try:
            finish = payload["choices"][0].get("finish_reason")
        except (KeyError, TypeError, AttributeError):
            finish = None
        if self._meta:
            self._meta["out_len"] = len(reply)
            self._meta["out_text"] = reply
        out = {"reply": reply}
        if usage:
            out["usage"] = usage
        if isinstance(finish, str) and finish:
            out["finish_reason"] = finish
        # trace della chiamata (change trace-llm, D1/D3): il body normalizzato
        # inoltrato e il payload grezzo — sempre, nessun flag
        out["trace"] = {"request": body, "response": payload}
        if self._meta:
            self._meta["trace"] = out["trace"]
        self._send_json(200, out)

    # --- model-status -----------------------------------------------------
    def _model_status(self) -> None:
        """GET /api/model-status -> {model_active, model?, clients} (sempre 200)."""
        active = _llama_reachable(LLAMA_URL)
        clients = _TRACKER.active_list(LAB_ACTIVE_WINDOW)["total"]
        model = _llama_model_name() if active else None
        self._send_json(200, {"model_active": active, "model": model, "clients": clients})

    # --- osservabilità ----------------------------------------------------
    def _sessions_list(self, query: str) -> None:
        """GET /api/sessions?window=<sec>|all&ip=<addr> — la finestra è una
        scelta di vista del pannello (design D1): clamp lato server, default
        LAB_ACTIVE_WINDOW; il filtro IP matcha l'insieme degli IP della sessione."""
        q = parse_qs(query)
        window: int | None = LAB_ACTIVE_WINDOW
        if q.get("window", [""])[0] == "all":
            window = None
        elif "window" in q:
            try:
                window = _clamp(int(q["window"][0]), 60, 86400)
            except ValueError:
                window = LAB_ACTIVE_WINDOW
        ip = q.get("ip", [None])[0]
        self._send_json(200, _TRACKER.active_list(window, ip=ip))

    def _session_timeline(self, cid: str) -> None:
        self._send_json(200, _TRACKER.timeline(cid))

    def _consumi(self, cid: str) -> None:
        """GET /api/consumi/<cid>: stime didattiche locale vs frontiera
        (change readme-loadtest-consumi, design D5). Senza token registrati
        niente numeri: `has_tokens: false` e il pannello nasconde il riquadro."""
        agg = _TRACKER.consumi(cid)
        if agg is None:
            self._send_json(404, {"error": "not found"})
            return
        if not agg["has_tokens"]:
            self._send_json(200, {"client": cid, "has_tokens": False})
            return
        out = dict(_stima_consumi(agg["tok_in"], agg["tok_out"], agg["secondi"]))
        out["client"] = cid
        out["has_tokens"] = True
        self._send_json(200, out)

    # --- routing (SOLO /api/*: il resto è del tier statico, qui 404) ------
    def do_GET(self) -> None:  # noqa: N802 - signature stdlib
        self._begin()
        path, _, query = self.path.partition("?")
        if path == "/api/health":
            self._proxy("GET", "/health")
            return
        if path == "/api/model-status":
            self._model_status()
            return
        if path == "/api/tps":
            self._send_json(200, _TRACKER.tps_points())
            return
        if path.startswith("/api/consumi/"):
            self._consumi(path[len("/api/consumi/"):])
            return
        if path == "/api/sessions":
            self._sessions_list(query)
            return
        if path.startswith("/api/sessions/"):
            rest = path[len("/api/sessions/"):]
            if "/" in rest:
                # dettaglio di una interazione: riga completa con la trace (D4)
                cid, _, ts_s = rest.partition("/")
                try:
                    ts = float(ts_s)
                except ValueError:
                    self._send_json(404, {"error": "not found"})
                    return
                row = _TRACKER.detail(cid, ts)
                if row is None:
                    self._send_json(404, {"error": "not found"})
                else:
                    self._send_json(200, row)
                return
            self._session_timeline(rest)
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
        f"sessions={_TRACKER._path})",
        flush=True,
    )
    server = ThreadingHTTPServer(("0.0.0.0", GATEWAY_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

"""Servizio HTTP della skill (stdlib http.server, zero dipendenze).

Reference hosting dell'interfaccia di servizio; laboratorio-web può esporre
questo stesso endpoint (o wrapparlo). Contratto:
  POST /scaffold  {"notes": "..."} -> SkillOutput JSON (+ "events" per la demo)
  GET  /health    -> {"ok": true, "backend": "..."}
  GET  /          -> info servizio

Config via env:
  DIARIOBOT_BACKEND     = mock | llama | local   (default: mock)
  LLAMA_URL             = URL di llama-server     (per backend=llama)
  MODEL_PATH            = path del GGUF           (per backend=local)
  LLAMA_REPEAT_PENALTY  = penalità ripetizione    (default: 1.1, anti-loop)
  DIARIOBOT_PORT        = porta                   (default: 8080)
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .demo import DemoSink
from .models import build_backend
from .skill import DiarioSkill

_SKILL = None
# Anti-loop sui modelli base: impedisci di saturare max_tokens sugli array.
_DEFAULT_REPEAT_PENALTY = 1.1


def build_skill_from_env() -> DiarioSkill:
    # Default "mock" (deterministico, offline). Il compose imposta "auto" per
    # rilevare llama automaticamente; si può forzare con DIARIOBOT_BACKEND.
    backend = os.environ.get("DIARIOBOT_BACKEND", "mock").lower()
    # Penalità di ripetizione condivisa (anti-loop sui modelli base). Tunabile.
    rp = float(os.environ.get("LLAMA_REPEAT_PENALTY", str(_DEFAULT_REPEAT_PENALTY)))
    llama_url = os.environ.get("LLAMA_URL", "http://localhost:8081")
    if backend in ("llama", "llama-server", "server"):
        b = build_backend("llama", url=llama_url, repeat_penalty=rp)
    elif backend in ("local", "standalone"):
        model_path = os.environ.get("MODEL_PATH")
        if not model_path:
            raise RuntimeError("DIARIOBOT_BACKEND=local richiede MODEL_PATH")
        b = build_backend("local", model_path=model_path, repeat_penalty=rp)
    elif backend == "auto":
        # backend "auto": probe lazy + ritentabile dentro AutoBackend. Così non
        # c'è race all'avvio: se llama non è pronto alla prima richiesta, usa mock
        # per quella e passa a llama appena è raggiungibile.
        b = build_backend("auto", url=llama_url, repeat_penalty=rp)
    else:
        b = build_backend("mock")
    return DiarioSkill(backend=b)


def get_skill() -> DiarioSkill:
    global _SKILL
    if _SKILL is None:
        _SKILL = build_skill_from_env()
    return _SKILL


class Handler(BaseHTTPRequestHandler):
    server_version = "diariobot/0.1"

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - signature stdlib
        if self.path == "/health":
            self._send(200, {"ok": True, "backend": get_skill().backend.name})
        elif self.path == "/":
            self._send(200, {
                "service": "diario-di-bordo",
                "endpoints": ["POST /scaffold", "GET /health"],
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - signature stdlib
        if self.path != "/scaffold":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            notes = payload.get("notes", "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "body JSON non valido"})
            return
        if not isinstance(notes, str) or not notes.strip():
            self._send(400, {"error": "campo 'notes' mancante o vuoto"})
            return

        sink = DemoSink(verbose=False)
        try:
            skill = get_skill()
            out = skill.run(notes, demo=sink)
        except Exception as e:  # noqa: BLE001 - superficie minima, mai crashare il servizio
            self._send(502, {"error": f"errore di elaborazione: {e}"})
            return

        resp = out.to_dict()
        resp["events"] = sink.events  # eventi sintetici per la UI demo (mai CoT)
        # token della chiamata al modello, se il backend li fornisce (tappa ④);
        # campo omesso altrimenti — estensione retrocompatibile
        if skill.last_usage is not None:
            resp["usage"] = skill.last_usage
        self._send(200, resp)

    def log_message(self, fmt, *args) -> None:  # silenzioso di default
        pass


def main() -> None:
    port = int(os.environ.get("DIARIOBOT_PORT", "8080"))
    skill = get_skill()
    print(f"diario-di-bordo service in ascolto su :{port} (backend={skill.backend.name})", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

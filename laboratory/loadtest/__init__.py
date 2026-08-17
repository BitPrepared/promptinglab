"""Simulazione di carico del laboratorio (change readme-loadtest-consumi).

N "ragazzi" sintetici, ognuno con una conversazione a più turni sulle chat del
gateway: cronologia crescente (usano la memoria come un ragazzo vero), tappe
cicliche, X-Client-Id `load-XX`. Zero dipendenze: solo stdlib, come tutto il
progetto. Le sessioni sintetiche attraversano il gateway e finiscono
nell'osservabilità normale — il test del carico si fa col pannello stesso.

Uso: python3 -m loadtest --url http://localhost:8090 --n 8 --turns 4
     (o `make loadtest N=8 TURNS=4` dalla cartella laboratory/)
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

# Svolte tipiche dei ragazzi: brevi, come nel laboratorio vero
PROMPTS = [
    "ciao mi chiamo Stefano",
    "come mi chiamo?",
    "raccontami una breve storia di campo scout",
    "dammi tre idee per un gioco in bivacco",
    "spiega in due righe cos'è una squadriglia",
]


def _kid(url: str, cid: str, turns: int, results: dict, lock: threading.Lock,
         timeout: float, retries: int = 0) -> None:
    hist: list[dict] = []
    ms: list[int] = []
    ok = err = 0
    for t in range(turns):
        step = str((t % 5) + 1)  # tappe cicliche: ①…⑤ come nel percorso
        hist.append({"role": "user", "content": PROMPTS[t % len(PROMPTS)]})
        body = json.dumps({"messages": hist, "temperature": 0.7}).encode("utf-8")
        t0 = time.perf_counter()
        # retry: il ragazzo ostinato riprova il turno fallito (stesso body,
        # stessa cronologia); default 0 = comportamento della pagina reale,
        # dove l'errore si vede e chi riinvia lo fa a mano
        for _attempt in range(retries + 1):
            req = urllib.request.Request(
                url.rstrip("/") + "/api/chat", data=body,
                headers={"Content-Type": "application/json",
                         "X-Client-Id": cid, "X-Step": step},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                hist.append({"role": "assistant", "content": str(data.get("reply", ""))})
                ok += 1
                break
            except (urllib.error.URLError, urllib.error.HTTPError,
                    json.JSONDecodeError):
                continue
        else:
            err += 1  # esauriti i tentativi: il turno è fallito
        # ms = tempo del turno così com'è vissuto: tutti i tentativi inclusi
        ms.append(round((time.perf_counter() - t0) * 1000))
    with lock:
        results[cid] = {"ok": ok, "err": err, "ms": ms}


def _stats(ms: list[int]) -> dict:
    if not ms:
        return {"min": None, "med": None, "max": None}
    s = sorted(ms)
    return {"min": s[0], "med": s[len(s) // 2], "max": s[-1]}


def run(url: str, n: int = 2, turns: int = 2, prefix: str = "load",
        timeout: float = 180.0, retries: int = 0) -> dict:
    """Lancia n ragazzi sintetici (thread) e ritorna il report aggregato.
    `retries` = quanti ritentativi per turno fallito (0 = come la pagina)."""
    results: dict = {}
    lock = threading.Lock()
    threads = []
    for i in range(n):
        cid = f"{prefix}-{i:02d}"
        th = threading.Thread(target=_kid,
                              args=(url, cid, turns, results, lock, timeout,
                                    retries))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    sessions = {cid: {**v, **{"latency_ms": _stats(v["ms"])}, "ms": v["ms"]}
                for cid, v in sorted(results.items())}
    all_ms = [m for v in results.values() for m in v["ms"]]
    return {
        "sessions": sessions,
        "totals": {
            "sessions": len(sessions),
            "ok": sum(v["ok"] for v in results.values()),
            "err": sum(v["err"] for v in results.values()),
            "latency_ms": _stats(all_ms),
        },
    }

"""Client condiviso per il servizio skill (stdlib, zero dipendenze).

È il "client library" che pagina web (via fetch in JS) e CLI usano per parlare
con la skill. Parità CLI/web garantita dal fatto che entrambi consumano lo
stesso JSON (`SkillOutput`), via lo stesso endpoint.

Usa di default l'endpoint diretto della skill (`/scaffold`); puntando al server
web e passando `endpoint="/api/scaffold"` si attraversa il bridge (utile per
verificare la parità CLI-vs-web nel test).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class SkillError(RuntimeError):
    """Errore di trasporto verso il servizio skill (HTTP o rete)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def post_scaffold(
    base_url: str,
    notes: str,
    *,
    endpoint: str = "/scaffold",
    timeout: int = 120,
) -> dict:
    """Invia gli appunti al servizio e ritorna il dict JSON della skill.

    Solleva `SkillError` su errore HTTP o di rete (mai crashare il chiamante).
    """
    url = base_url.rstrip("/") + endpoint
    body = json.dumps({"notes": notes}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SkillError(f"HTTP {e.code} da {url}: {e.reason}", status=e.code) from e
    except urllib.error.URLError as e:
        raise SkillError(f"servizio non raggiungibile su {url}: {e.reason}") from e


def health(base_url: str, *, timeout: int = 5) -> dict:
    """GET di health (utile per smoke-test / kiosk: il server c'è?)."""
    url = base_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise SkillError(f"health non raggiungibile su {url}: {e}") from e

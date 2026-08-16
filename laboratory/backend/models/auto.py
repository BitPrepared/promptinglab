"""Backend 'auto': usa llama-server se raggiungibile, fallback mock.

Risolve la race condition all'avvio: con `LAB_BACKEND=auto` la decisione
NON viene presa una volta sola all'avvio (cache permanente), ma **a ogni
generazione**, in modo lazy:

- se llama è già stato "promosso" (ha risposto almeno una volta), lo usa e lo
  tiene; se poi cade, lo degrada e ritenterà;
- se non è ancora promosso, fa un probe; se llama è su, lo promuove e lo usa;
  se non è raggiungibile, usa il mock **per quella richiesta** (senza cacheare il
  fallimento), così quando llama diventa disponibile ci passa.

Così:
- modalità reale (`--profile model`): primo scaffold dopo l'avvio di llama →
  llama; anche se il primo arriva troppo presto, dal successivo va su llama;
- modalità demo (senza modello): resta su mock (probe per richiesta, veloce
  perché la connessione/DNS fallisce subito).
"""
from __future__ import annotations

import urllib.request

from .base import ModelBackend
from .llama_server import LlamaBackendError, LlamaServerModel
from .mock import MockModel

_PROBE_TIMEOUT = 2.0


def llama_reachable(url: str, timeout: float = _PROBE_TIMEOUT) -> bool:
    """True se llama-server risponde 2xx su /health."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001 - qualsiasi problema = non raggiungibile
        return False


class AutoBackend(ModelBackend):
    name = "auto"

    def __init__(self, url: str = "http://localhost:8081",
                 repeat_penalty: float = 1.1, **_) -> None:
        self.url = url
        self.repeat_penalty = repeat_penalty
        self._llama: LlamaServerModel | None = None  # promosso dopo primo successo
        self._mock: MockModel | None = None

    def _mock_backend(self) -> MockModel:
        if self._mock is None:
            self._mock = MockModel()
        return self._mock

    def _try_llama(self, system, user, grammar, max_tokens, temperature) -> str | None:
        """Prova llama (nuovo o promosso). Ritorna il testo o None se fallisce."""
        if self._llama is None:
            self._llama = LlamaServerModel(url=self.url, repeat_penalty=self.repeat_penalty)
        try:
            return self._llama.generate(system, user, grammar, max_tokens, temperature)
        except LlamaBackendError:
            self._llama = None  # degrada: riproverà il probe al prossimo giro
            return None

    @property
    def last_usage(self) -> dict | None:
        """Usage del backend che ha servito l'ultima generate (llama o mock).

        Il wrapper non genera lui: delega e RIFORWARDA gli usage, così la skill
        vede i token del workflow anche con LAB_BACKEND=auto (il default
        del compose). Dopo un degrado _llama è None: l'ultima risposta è del
        mock e gli usage si leggono lì.
        """
        src = self._llama if self._llama is not None else self._mock
        return getattr(src, "last_usage", None) if src is not None else None

    def generate(self, system, user, grammar=None, max_tokens=512, temperature=0.2) -> str:
        # 1) se già promosso a llama, usalo (e degrada se è caduto)
        if self._llama is not None:
            out = self._try_llama(system, user, grammar, max_tokens, temperature)
            if out is not None:
                return out
        # 2) non promosso (o appena degradato): probe e promuovi se llama è su
        if llama_reachable(self.url):
            out = self._try_llama(system, user, grammar, max_tokens, temperature)
            if out is not None:
                return out
        # 3) fallback mock per questa richiesta (non cacheato: ritenterà llama)
        return self._mock_backend().generate(system, user, grammar, max_tokens, temperature)

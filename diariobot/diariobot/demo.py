"""Modalità demo/trasparenza.

Mostra all'utente una spiegazione SINTETICA delle azioni della skill, senza
esporre MA la chain-of-thought privata del modello. Gli eventi raccolti possono
essere restituiti al client (web/CLI) per renderizzarli nella UI di demo.
"""
from __future__ import annotations

from typing import Callable


class DemoSink:
    """Raccoglie e (opzionalmente) stampa gli eventi sintetici della skill."""

    def __init__(self, verbose: bool = True, sink: Callable[[str], None] | None = None):
        self.verbose = verbose
        self.sink = sink
        self.events: list[dict] = []

    def event(self, name: str, message: str) -> None:
        self.events.append({"event": name, "message": message})
        if self.verbose:
            (self.sink or print)(message)  # noqa: T201 - output demo voluto

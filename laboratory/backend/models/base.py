"""Interfaccia dell'adapter del modello."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ModelBackend(ABC):
    """Backend del modello LLM, scambiabile dietro un'interfaccia stabile.

    La skill dipende solo da questa interfaccia: passare da mock a modello
    base a modello fine-tuned NON richiede modifiche alla skill.
    """

    name: str = "base"

    # Usage dell'ultima generate ({"prompt_tokens", "completion_tokens"}) o
    # None se il backend/servizio non la fornisce: la skill la raccoglie per
    # esporre i token del workflow (tappa ④) senza cambiare il contratto di
    # generate(), che resta una stringa.
    last_usage: dict | None = None

    @abstractmethod
    def generate(
        self,
        system: str,
        user: str,
        grammar: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> str:
        """Restituisce il testo generato (atteso in JSON strutturato).

        Args:
            system: prompt di sistema (regole della skill).
            user: messaggio utente (appunti da strutturare).
            grammar: testo della grammatica GBNF per vincolare l'output (o None).
            max_tokens: tetto sulla generazione (keep short per hardware debole).
            temperature: bassa di default (estrattivo, non creativo).
        """
        raise NotImplementedError

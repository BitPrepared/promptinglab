"""Backend reale: chiama un llama-server in HTTP (stdlib urllib, zero dipendenze).

Verificato end-to-end (task 1.3–1.5) con Qwen2.5-0.5B/1.5B-Instruct GGUF su
llama.cpp: l'output è vincolato a JSON strutturato. Il `repeat_penalty` di default
(1.1) impedisce al modello base di loopare sugli array e saturare max_tokens
senza chiudere il JSON — con ~1.1 termina naturalmente (finish_reason=stop).

Vincolo output (struttura JSON dello scaffold):
- sulle /v1/chat/completions si usa `response_format` con `json_schema` (lo
  schema è SCAFFOLD_JSON_SCHEMA in schema.py, specchia grammar.gbnf). Nelle
  versioni recenti di llama.cpp il campo `grammar` top-level NON è supportato
  sulle chat completions (solo su /completion), quindi viene ignorato: senza
  `response_format` il modello produrrebbe prosa → validazione fallback.
- il parametro `grammar` dell'interfaccia resta (usato dal backend `local` via
  llama-cpp-python); qui, se presente, attiva il `response_format` json_schema.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..schema import SCAFFOLD_JSON_SCHEMA
from .base import ModelBackend


class LlamaBackendError(RuntimeError):
    """Errore di comunicazione con llama-server."""


class LlamaServerModel(ModelBackend):
    name = "llama"

    # Penalità di ripetizione di default: evita che il modello base loopi sugli
    # array e saturi max_tokens senza chiudere il JSON. Tunabile via __init__/env.
    DEFAULT_REPEAT_PENALTY = 1.1

    def __init__(self, url: str = "http://localhost:8081", timeout: float = 120.0,
                 repeat_penalty: float | None = None):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.repeat_penalty = (self.DEFAULT_REPEAT_PENALTY
                               if repeat_penalty is None else repeat_penalty)
        # trace dell'ultima chiamata andata a buon fine (change trace-llm):
        # {"request": body inoltrato, "response": payload grezzo} — il filo,
        # senza ricostruzioni. None finché non c'è una chiamata riuscita.
        self.last_trace = None

    def generate(self, system, user, grammar=None, max_tokens=512, temperature=0.2) -> str:
        # Endpoint /v1/chat/completions (compatibile OpenAI) di llama-server;
        # llama-server accetta i campi non-standard "repeat_penalty".
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "repeat_penalty": self.repeat_penalty,
            "stream": False,
        }
        # `grammar` (passato dalla skill) attiva il vincolo JSON strutturato.
        # Sulle chat completions si usa response_format (json_schema), non il
        # campo grammar top-level (supportato solo su /completion nelle build
        # recenti).
        if grammar:
            body["response_format"] = {"type": "json_schema", "schema": SCAFFOLD_JSON_SCHEMA}

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LlamaBackendError(f"llama-server HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise LlamaBackendError(f"llama-server non raggiungibile su {self.url}: {e.reason}") from e

        # Usage della chiamata (token del workflow ④): None se il server non
        # la manda — sovrascritta a ogni generate, niente staleness.
        self.last_usage = payload.get("usage") or None
        # Trace della chiamata (sibling di last_usage): il body esattamente
        # inoltrato e il payload esattamente ricevuto (change trace-llm, D1).
        self.last_trace = {"request": body, "response": payload}

        # Estrae il testo della prima scelta.
        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise LlamaBackendError(f"risposta llama-server imprevista: {payload!r}") from e

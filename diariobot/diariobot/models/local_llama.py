"""Backend locale in-process via llama-cpp-python (path standalone sul Pi).

Import lazy: llama-cpp-python è l'UNICA dipendenza opzionale del progetto.
Si attiva dove è installata (es. sul Raspberry Pi 3 con il modello 0.5B
fine-tuned). Il `repeat_penalty` di default (1.1) ha lo stesso ruolo del backend
HTTP: impedire al modello base di loopare sugli array della grammatica. Mantiene
l'interfaccia dell'adapter — la skill non cambia.
"""
from __future__ import annotations

from .base import ModelBackend


class LocalLlamaBackend(ModelBackend):
    name = "local"

    DEFAULT_REPEAT_PENALTY = 1.1

    def __init__(self, model_path: str, n_ctx: int = 2048, n_threads: int | None = None,
                 verbose: bool = False, repeat_penalty: float | None = None):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.verbose = verbose
        self.repeat_penalty = (self.DEFAULT_REPEAT_PENALTY
                               if repeat_penalty is None else repeat_penalty)
        self._llm = None

    def _load(self):
        if self._llm is None:
            try:
                from llama_cpp import Llama
            except ImportError as e:  # pragma: no cover - dipendenza opzionale
                raise RuntimeError(
                    "il backend 'local' richiede llama-cpp-python "
                    "(pip install llama-cpp-python)"
                ) from e
            self._llm = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
                verbose=self.verbose,
            )
        return self._llm

    def generate(self, system, user, grammar=None, max_tokens=512, temperature=0.2) -> str:
        llm = self._load()
        kwargs = dict(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            repeat_penalty=self.repeat_penalty,
        )
        if grammar:
            # best-effort: avvolge la grammatica se la versione la supporta.
            try:  # pragma: no cover - dipendenza opzionale
                from llama_cpp import LlamaGrammar
                kwargs["grammar"] = LlamaGrammar.from_string(grammar)
            except Exception:  # noqa: BLE001
                pass
        out = llm.create_chat_completion(**kwargs)  # pragma: no cover - dipendenza opzionale
        return out["choices"][0]["message"]["content"].strip()

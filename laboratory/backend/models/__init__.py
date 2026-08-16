"""Adapter del modello: backend scambiabili dietro un'interfaccia stabile."""
from .base import ModelBackend
from .mock import MockModel
from .llama_server import LlamaServerModel
from .local_llama import LocalLlamaBackend
from .auto import AutoBackend

__all__ = [
    "ModelBackend",
    "MockModel",
    "LlamaServerModel",
    "LocalLlamaBackend",
    "AutoBackend",
    "build_backend",
]


def build_backend(name: str = "mock", **kwargs) -> ModelBackend:
    """Factory: seleziona il backend per nome.

    - mock        : deterministico, offline, per test/demo (nessun modello)
    - llama       : modello reale via llama-server HTTP (path mini PC)
    - local       : modello reale in-process via llama-cpp-python (path Pi standalone)
    - auto        : llama se raggiungibile, fallback mock (lazy + ritentabile)
    """
    name = (name or "mock").lower()
    if name in ("mock", "test"):
        return MockModel()
    if name in ("llama", "llama-server", "server"):
        return LlamaServerModel(**kwargs)
    if name in ("local", "local-llama", "standalone"):
        return LocalLlamaBackend(**kwargs)
    if name == "auto":
        return AutoBackend(**kwargs)
    raise ValueError(f"backend sconosciuto: {name!r} (uso: mock | llama | local | auto)")

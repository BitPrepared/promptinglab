"""DiarioBot — Skill "Diario di Bordo".

Trasforma appunti grezzi in uno scaffold strutturato (solo fatti dell'utente),
domande di approfondimento e check di chiarezza. Niente prosa: la scrittura
finale resta al ragazzo. Principio: "IA = supporto, non sostituto".

Core zero-dipendenze (solo stdlib). Il modello LLM sta dietro un adapter
scambiabile (mock per test offline, llama-server per la produzione).
"""

__version__ = "0.1.0"

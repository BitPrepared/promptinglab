#!/usr/bin/env python3
"""Probe diretto: chiama l'adapter LlamaServerModel reale con i prompt/grammatica
del progetto e stampa l'output RAW del modello + esito parse JSON.
Usato per debuggare la generazione (task 1.3/1.5)."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")
from backend.models import LlamaServerModel  # noqa: E402
from backend.prompts import SYSTEM_PROMPT, build_user_message  # noqa: E402
from backend.skill import load_grammar  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8081"
MAXTOK = int(sys.argv[2]) if len(sys.argv) > 2 else 512

with open("spike/notes.txt", encoding="utf-8") as f:
    notes = f.read()

be = LlamaServerModel(url=URL)
grammar = load_grammar()

raw = be.generate(SYSTEM_PROMPT, build_user_message(notes),
                  grammar=grammar, max_tokens=MAXTOK, temperature=0.2)

print("=== RAW model output ===")
print(f"len={len(raw)} chars")
print("--- head (1200) ---")
print(raw[:1200])
print("--- tail (600) ---")
print(raw[-600:])
print("=== parse check ===")
try:
    obj = json.loads(raw)
    print("JSON OK. top keys:", list(obj.keys()))
    sc = obj.get("scaffold", {})
    print("scaffold keys:", list(sc.keys()))
    print("title:", repr(obj.get("title")), "| questions:", len(obj.get("questions", [])),
          "| checks:", len(obj.get("checks", [])))
except Exception as e:
    print("JSON FAIL:", repr(e))

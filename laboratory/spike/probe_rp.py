#!/usr/bin/env python3
"""Probe con repeat_penalty (mitigazione loop-su-array). Replica la chiamata
dell'adapter LlamaServerModel ma aggiunge repeat_penalty al body, per capire
se il modello base termina con JSON valido (task 1.5 / FASE 1)."""
from __future__ import annotations

import json
import sys
import urllib.request

sys.path.insert(0, ".")
from backend.prompts import SYSTEM_PROMPT, build_user_message  # noqa: E402
from backend.skill import load_grammar  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8081"
MAXTOK = int(sys.argv[2]) if len(sys.argv) > 2 else 400
RP = float(sys.argv[3]) if len(sys.argv) > 3 else 1.18

with open("spike/notes.txt", encoding="utf-8") as f:
    notes = f.read()

body = {
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(notes)},
    ],
    "temperature": 0.2,
    "max_tokens": MAXTOK,
    "stream": False,
    "grammar": load_grammar(),
    "repeat_penalty": RP,
}
req = urllib.request.Request(f"{URL}/v1/chat/completions",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=120) as r:
    payload = json.loads(r.read().decode())
raw = payload["choices"][0]["message"]["content"].strip()
finish = payload["choices"][0].get("finish_reason")

print(f"=== repeat_penalty={RP} max_tokens={MAXTOK} finish_reason={finish} len={len(raw)} ===")
print("--- full output ---")
print(raw)
print("--- parse check ---")
try:
    obj = json.loads(raw)
    sc = obj.get("scaffold", {})
    tot = sum(len(sc.get(k, [])) for k in sc)
    print(f"JSON OK | title={obj.get('title')!r} | questions={obj.get('questions')} | "
          f"checks={obj.get('checks')} | scaffold_items={tot}")
except Exception as e:
    print("JSON FAIL:", repr(e))

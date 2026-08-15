#!/usr/bin/env python3
"""Validatore del dataset golden (task 4.1).

Controlla che ogni esempio sia conforme al contratto della skill:
- JSON ben formato e struttura SkillOutput valida;
- NESSUNA INVENZIONE: ogni fatto dello scaffold ha almeno un token significativo
  presente nelle note (controllo conservativo, come il test del mock);
- NESSUNA PROSA: nessun elemento troppo lungo/multi-frase;
- title/date assenti -> devono essere "non specificato";
- domande 2-3 e chiuse da "?"; check con where/issue/kind valido.

Uso:  python3 finetune/validate_dataset.py [golden.jsonl]
Esce con codice !=0 se ci sono violazioni hard (invenzione/prosa/malformate).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from diariobot.schema import MISSING, SCAFFOLD_FIELDS  # noqa: E402
from diariobot.validate import _looks_like_prose  # noqa: E402

_DEFAULT = Path(__file__).resolve().parent / "dataset" / "golden.jsonl"
_PROSE_MAX = 140
_HARD_FAIL = 0


def _sig_tokens(text: str) -> set[str]:
    """Token significativi (len>=4, lowercase, senza punteggiatura) di un testo."""
    return {t.strip(".,;:!?()'\"").lower() for t in re.split(r"\s+", text or "") if len(t.strip(".,;:!?()'\"")) >= 4}


def _first_token(item: str) -> str:
    return item.split()[0].strip(",.;:()").lower() if item.split() else ""


def check_example(ex: dict) -> tuple[list[str], list[str]]:
    """Restituisce (hard_violations, warnings) per un esempio."""
    hard, warn = [], []
    eid = ex.get("id", "?")
    notes = ex.get("notes", "")
    out = ex.get("output", {})
    if not isinstance(out, dict):
        hard.append(f"[{eid}] output non è un oggetto")
        return hard, warn

    sc = out.get("scaffold", {})
    if not isinstance(sc, dict) or not all(f in sc for f in SCAFFOLD_FIELDS):
        hard.append(f"[{eid}] scaffold mancante o incompleto (campi: {SCAFFOLD_FIELDS})")
    if "questions" not in out or "checks" not in out:
        hard.append(f"[{eid}] mancano questions/checks")

    # title/date
    title, date = out.get("title"), out.get("date")
    if title not in (None, MISSING) and not (isinstance(title, str) and title.strip()):
        hard.append(f"[{eid}] title vuoto ma non 'non specificato'")
    if date not in (None, MISSING) and not (isinstance(date, str) and date.strip()):
        hard.append(f"[{eid}] date vuoto ma non 'non specificato'")

    note_tokens = _sig_tokens(notes)

    # no-invenzione + no-prosa sui fatti
    for f in SCAFFOLD_FIELDS:
        for item in (sc.get(f) or []):
            if not isinstance(item, str) or not item.strip():
                hard.append(f"[{eid}] elemento vuoto in scaffold.{f}")
                continue
            if _looks_like_prose(item):
                hard.append(f"[{eid}] prosa in scaffold.{f}: {item[:50]!r}")
                continue
            if len(item) > _PROSE_MAX:
                hard.append(f"[{eid}] elemento troppo lungo in scaffold.{f}: {item[:50]!r}")
            # almeno un token significativo (>=4 char) deve essere nelle note
            item_tokens = _sig_tokens(item)
            if item_tokens and not (item_tokens & note_tokens):
                hard.append(f"[{eid}] possibile INVENZIONE in scaffold.{f}: {item!r} "
                            f"(nessun token significativo nelle note)")

    # domande
    qs = out.get("questions") or []
    if not (2 <= len(qs) <= 3):
        warn.append(f"[{eid}] domande={len(qs)} (atteso 2-3)")
    for q in qs:
        if not q.strip().endswith("?"):
            warn.append(f"[{eid}] domanda senza '?': {q!r}")

    # check
    for c in (out.get("checks") or []):
        if not isinstance(c, dict) or "where" not in c or "issue" not in c or "kind" not in c:
            hard.append(f"[{eid}] check malformato: {c!r}")
        elif c["kind"] not in ("clarity", "orthography"):
            hard.append(f"[{eid}] check.kind non valido: {c['kind']!r}")

    return hard, warn


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT
    n, n_hard, n_warn = 0, 0, 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                ex = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"HARD [linea {lineno}] JSON non valido: {e}")
                n_hard += 1
                continue
            hard, warn = check_example(ex)
            for h in hard:
                print("HARD", h); n_hard += 1
            for w in warn:
                print("warn", w); n_warn += 1
    print(f"\n=== {n} esempi | {n_hard} violazioni hard | {n_warn} warning ===")
    return 1 if n_hard else 0


if __name__ == "__main__":
    sys.exit(main())

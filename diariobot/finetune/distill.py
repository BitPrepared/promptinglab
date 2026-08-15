#!/usr/bin/env python3
"""Distillazione sintetica + curation (task 4.2).

Genera scaffold CANDIDATI da un set di appunti seed usando un llama-server
(idealmente un modello più forte; qui usabile come bootstrap col 1.5B base).
L'output va in `candidates.jsonl` con flag `needs_review`: ogni candidato va
curato a mano (correggere invenzioni/prosa, completare i MISSING) prima di
promuoverlo in `golden.jsonl` con id `gNN` (validarlo con validate_dataset.py).

Input seed: file di testo con un appunto per paragrafo (paragrafi separati da
riga vuota), oppure JSONL {"notes": "..."} .

Uso (llama-server già up):
  python3 finetune/distill.py --url http://localhost:8081 --seeds finetune/dataset/seeds.txt \
      --label 1.5b-base --out finetune/dataset/candidates.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from diariobot.models import LlamaServerModel  # noqa: E402
from diariobot.prompts import SYSTEM_PROMPT, build_user_message  # noqa: E402
from diariobot.skill import load_grammar  # noqa: E402
from diariobot.validate import parse_json_tolerant, validate_output  # noqa: E402


def _read_seeds(path: Path) -> list[str]:
    txt = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(l)["notes"] for l in txt.splitlines() if l.strip()]
    # testo: paragrafi separati da riga vuota
    return [p.strip() for p in txt.split("\n\n") if p.strip()]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8081")
    p.add_argument("--seeds", required=True)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "dataset" / "candidates.jsonl"))
    p.add_argument("--label", default="teacher")
    p.add_argument("--max-tokens", type=int, default=400)
    p.add_argument("--repeat-penalty", type=float, default=1.1)
    args = p.parse_args()

    seeds = _read_seeds(Path(args.seeds))
    be = LlamaServerModel(url=args.url, repeat_penalty=args.repeat_penalty)
    grammar = load_grammar()

    out_path = Path(args.out)
    n_ok = 0
    with out_path.open("w", encoding="utf-8") as f:
        for i, notes in enumerate(seeds, 1):
            raw = be.generate(SYSTEM_PROMPT, build_user_message(notes),
                              grammar=grammar, max_tokens=args.max_tokens, temperature=0.3)
            valid = parse_json_tolerant(raw) is not None
            out = validate_output(raw, notes)
            rec = {
                "id": f"c{i:02d}",
                "notes": notes,
                "output": out.to_dict(),
                "raw_json_valid": valid,
                "needs_review": True,            # SEMPRE: curare a mano
                "review_hint": "verifica assenza invenzioni/prosa; completa i 'non specificato'; poi id->gNN in golden.jsonl",
                "source_model": args.label,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_ok += int(valid)
            print(f"  c{i:02d} raw_json_valid={int(valid)} -> {out_path.name}")
    print(f"\n{len(seeds)} candidati scritti in {out_path} ({n_ok} con JSON valido al giro).")
    print("PROSSIMO PASSO: curare a mano candidates.jsonl -> spostare i buoni in golden.jsonl (id gNN),")
    print("poi validare:  python3 finetune/validate_dataset.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

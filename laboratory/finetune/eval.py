#!/usr/bin/env python3
"""Eval harness della skill (task 4.4): misura la qualità del modello sul dataset
golden e produce una baseline. Esegue il modello via LlamaServerModel (modello
reale) o MockModel, valida l'output con validate_output (il contratto Deployato)
e lo confronta col golden.

Metriche per esempio:
  raw_json_valid  : l'output grezzo del modello era JSON valido (capacity del modello)
  fallback        : validate_output è scaduto nel fallback (nessuno scaffold)
  no_invention    : nessun fatto dello scaffold è estraneo alle note
  no_prose        : nessun elemento sembra prosa
  questions_ok    : 2-3 domande chiuse da '?'
  fact_recall     : frazione di fatti golden "coperti" dall'output (overlap token)
  title_ok        : title non è il fallback 'non specificato' quando il golden ne ha uno

Uso (modello reale, llama-server deve essere già up su LLAMA_URL):
  python3 finetune/eval.py --backend llama --url http://localhost:8081 --label 1.5b-base
  python3 finetune/eval.py --backend llama --url http://localhost:8081 --label 0.5b-base -m models/qwen2.5-0.5b-instruct-q4_k_m.gguf  # (etichetta solo)
  python3 finetune/eval.py --backend mock --label mock   # check di logica
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.models import LlamaServerModel, MockModel  # noqa: E402
from backend.prompts import SYSTEM_PROMPT, build_user_message  # noqa: E402
from backend.skill import load_grammar  # noqa: E402
from backend.schema import MISSING, SCAFFOLD_FIELDS  # noqa: E402
from backend.validate import parse_json_tolerant, validate_output  # noqa: E402

_GOLDEN = Path(__file__).resolve().parent / "dataset" / "golden.jsonl"


def _sig(text: str) -> set[str]:
    return {t.strip(".,;:!?()'\"").lower() for t in re.split(r"\s+", text or "")
            if len(t.strip(".,;:!?()'\"")) >= 4}


def _covered(golden_items, model_items) -> tuple[int, int]:
    """Quanti fatti golden sono coperti da qualche fatto del modello (overlap token)."""
    cov = 0
    msets = [_sig(m) for m in model_items]
    for g in golden_items:
        gs = _sig(g)
        if not gs:
            continue
        if any(gs & ms for ms in msets):
            cov += 1
    return cov, len([g for g in golden_items if _sig(g)])


def score(model_out, golden_out: dict, notes: str, raw_json_valid: bool) -> dict:
    sc = model_out.scaffold
    gsc = (golden_out.get("scaffold") or {})
    facts = {f: (sc and getattr(sc, f, [])) or [] for f in SCAFFOLD_FIELDS}

    # no_invention: nessun fatto estraneo alle note
    note_tok = _sig(notes)
    invented = []
    for f in SCAFFOLD_FIELDS:
        for it in facts[f]:
            its = _sig(it)
            if its and not (its & note_tok):
                invented.append((f, it))
    no_invention = len(invented) == 0

    # no_prose: nessun elemento troppo lungo
    no_prose = all(len(it) <= 140 for f in SCAFFOLD_FIELDS for it in facts[f])

    qs = model_out.questions or []
    questions_ok = 2 <= len(qs) <= 3 and all(q.strip().endswith("?") for q in qs)

    # fact_recall aggregato sui campi
    cov, tot = 0, 0
    for f in SCAFFOLD_FIELDS:
        c, t = _covered(gsc.get(f) or [], facts[f])
        cov += c; tot += t
    recall = (cov / tot) if tot else 1.0

    gtitle = (golden_out.get("title") or MISSING)
    title_ok = bool(sc and sc.title and sc.title != MISSING) or gtitle == MISSING

    fallback = model_out.scaffold.title == MISSING and not any(
        getattr(sc, f) for f in SCAFFOLD_FIELDS
    ) if sc else True

    return dict(
        raw_json_valid=raw_json_valid, fallback=bool(fallback),
        no_invention=no_invention, no_prose=no_prose, questions_ok=questions_ok,
        fact_recall=recall, title_ok=title_ok, n_invented=len(invented),
        invented=invented[:3],
    )


def run(args) -> int:
    if args.backend == "mock":
        backend = MockModel()
        grammar = None
    else:
        backend = LlamaServerModel(url=args.url, repeat_penalty=args.repeat_penalty)
        grammar = load_grammar()

    examples = [json.loads(l) for l in _GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows, agg = [], {k: [] for k in
        ("raw_json_valid", "fallback", "no_invention", "no_prose", "questions_ok", "fact_recall", "title_ok")}

    for ex in examples:
        notes = ex["notes"]
        golden = ex["output"]
        raw = backend.generate(SYSTEM_PROMPT, build_user_message(notes),
                               grammar=grammar, max_tokens=args.max_tokens, temperature=0.2)
        raw_json_valid = parse_json_tolerant(raw) is not None
        out = validate_output(raw, notes)
        s = score(out, golden, notes, raw_json_valid)
        for k in agg:
            agg[k].append(1.0 if s[k] else 0.0 if k != "fact_recall" else s["fact_recall"])
        agg["fact_recall"][-1] = s["fact_recall"]  # recall è continua
        rows.append((ex["id"], s))
        flag = "" if (s["raw_json_valid"] and s["no_invention"] and s["no_prose"]) else "  <--"
        print(f"  {ex['id']:5} raw_json={int(s['raw_json_valid'])} noinv={int(s['no_invention'])} "
              f"noprose={int(s['no_prose'])} qok={int(s['questions_ok'])} recall={s['fact_recall']:.2f} "
              f"titleok={int(s['title_ok'])}{flag}")

    def pct(k):
        return 100.0 * sum(1 for v in agg[k] if v >= 0.999) / len(agg[k]) if k != "fact_recall" else 0.0
    print(f"\n================ BASELINE [{args.label}] ({len(examples)} esempi) ================")
    for k in ("raw_json_valid", "fallback", "no_invention", "no_prose", "questions_ok", "title_ok"):
        print(f"  {k:16}: {pct(k):5.1f} %")
    print(f"  fact_recall (avg): {statistics.mean(agg['fact_recall']):5.2f}")
    # gate qualità della proposta: no invenzioni, no prosa, italiano, valido
    gate = all(pct(k) == 100.0 for k in ("no_invention", "no_prose")) and pct("raw_json_valid") == 100.0
    print(f"  GATE qualità (no-inv 100%, no-prosa 100%, json-valid 100%): {'PASS' if gate else 'FAIL'}")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "label": args.label, "n": len(examples),
            "metrics": {k: round(pct(k), 1) for k in
                        ("raw_json_valid", "fallback", "no_invention", "no_prose", "questions_ok", "title_ok")},
            "fact_recall_avg": round(statistics.mean(agg["fact_recall"]), 3),
            "per_example": [{"id": r[0], **r[1]} for r in rows],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  (scritto {args.out})")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["llama", "mock"], default="llama")
    p.add_argument("--url", default="http://localhost:8081")
    p.add_argument("--label", required=True)
    p.add_argument("--max-tokens", type=int, default=400)
    p.add_argument("--repeat-penalty", type=float, default=1.1)
    p.add_argument("--out", default="")
    args = p.parse_args()
    sys.exit(run(args))

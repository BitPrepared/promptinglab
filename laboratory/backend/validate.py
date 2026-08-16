"""Validazione post-modello dell'output.

Garantisce il contratto a runtime, anche se il modello (specie uno piccolo)
produce JSON malformato, prosa o fatti inventati:
- parsing tollerante (JSON, JSON-in-testo, fallback);
- normalizzazione dello scaffold (campi mancanti -> vuoti / MISSING);
- GUARDIA ANTI-PROSA: elementi che sembrano prosa vengono rimossi e segnalati;
- domande clampate a 0..3 e deduplicate;
- check normalizzati.
"""
from __future__ import annotations

import json
import re

from .schema import MISSING, SCAFFOLD_FIELDS, Check, Scaffold, SkillOutput

_PROSE_MAX_LEN = 140  # oltre questa lunghezza + punteggiatura multi-frase -> probabile prosa


def parse_json_tolerant(raw) -> dict | None:
    """Accetta dict, str JSON, o JSON immerso in testo. None se irrecuperabile."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    # qwen a volte incarta il JSON in un fence ```json ... ``` (misurato sul
    # 1.5B): si spoglia il fence PRIMA di tentare il parse
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # prova diretta
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # estrai il primo {...} bilanciato approssimato
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    return []


def _looks_like_prose(item: str) -> bool:
    return len(item) > _PROSE_MAX_LEN and ("." in item or "," in item) and len(item.split(".")) >= 3


# Una domanda "buona": breve, una sola frase, chiusa da '?'. Il modello base, quando
# non ha fatti veri, riempie le domande di prosa discorsiva: la scartiamo.
_QUESTION_MAX_LEN = 120


def _is_bad_question(q: str) -> bool:
    q = q.strip()
    if not q.endswith("?"):
        return True
    if len(q) > _QUESTION_MAX_LEN:
        return True
    # piu' di una frase: presenza di un '.' interno (oltre al '?' finale)
    if "." in q.rstrip("?").rstrip():
        return True
    return False


def build_scaffold(data: dict) -> tuple[Scaffold, list[Check]]:
    """Costruisce lo scaffold e restituisce anche i check generati dalla guardia anti-prosa."""
    top = data or {}
    sc_block = top.get("scaffold") if isinstance(top.get("scaffold"), dict) else top

    scaffold = Scaffold(
        title=str(top.get("title") or MISSING)[:120] if isinstance(top.get("title"), str) else MISSING,
        date=str(top.get("date") or MISSING)[:40] if isinstance(top.get("date"), str) else MISSING,
    )
    extra_checks: list[Check] = []
    for f in SCAFFOLD_FIELDS:
        items = _as_str_list(sc_block.get(f))
        kept = []
        for it in items:
            if _looks_like_prose(it):
                extra_checks.append(Check(
                    where=it[:60] + "…",
                    issue="output scartato: sembrava prosa, non un fatto (contratto: niente prosa)",
                    kind="clarity",
                ))
            else:
                kept.append(it)
        setattr(scaffold, f, kept)
    return scaffold, extra_checks


def _build_checks(raw_checks, extra: list[Check]) -> list[Check]:
    out: list[Check] = []
    if isinstance(raw_checks, list):
        for c in raw_checks:
            if not isinstance(c, dict):
                continue
            kind = c.get("kind") if c.get("kind") in ("clarity", "orthography") else "clarity"
            where = str(c.get("where") or "").strip()
            issue = str(c.get("issue") or "").strip()
            if where or issue:
                out.append(Check(where=where or "(senza riferimento)", issue=issue, kind=kind))
    out.extend(extra)
    # dedupe semplice
    seen, dedup = set(), []
    for c in out:
        key = (c.where, c.issue, c.kind)
        if key not in seen:
            seen.add(key)
            dedup.append(c)
    return dedup[:6]


def validate_output(raw, notes: str) -> SkillOutput:
    data = parse_json_tolerant(raw)
    if data is None:
        # Fallback onesto: scaffold vuoto + check che spiega il problema.
        # NON si inventa nulla.
        return SkillOutput(
            scaffold=Scaffold(title=MISSING, date=MISSING),
            questions=[],
            checks=[Check(where="(output del modello)",
                          issue="output non strutturato o non JSON: mostra gli appunti così come sono e riprova",
                          kind="clarity")],
            inferences=[],
        )

    scaffold, extra = build_scaffold(data)
    # filtra domande che sembrano prosa (lunghe, multi-frase, non chiuse da '?')
    questions = [q for q in _as_str_list(data.get("questions")) if not _is_bad_question(q)]
    questions = questions[:3]
    # dedupe domande
    seen, qs = set(), []
    for q in questions:
        ql = q.lower()
        if ql not in seen:
            seen.add(ql)
            qs.append(q)
    checks = _build_checks(data.get("checks"), extra)
    inferences = _as_str_list(data.get("inferences"))
    return SkillOutput(scaffold=scaffold, questions=qs, checks=checks, inferences=inferences)

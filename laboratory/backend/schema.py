"""Schema del diario di bordo e tipi di output della skill.

Definisce il contratto dati che la skill produce e che CLI e pagina web
consumano allo stesso modo. Zero dipendenze esterne.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# Sentinel per "informazione non presente nell'input dell'utente".
# Il modello NON inventa: se un campo manca, viene marcato così.
MISSING = "non specificato"

# Campi strutturati del diario: liste di fatti estratti dall'input.
SCAFFOLD_FIELDS = (
    "people",        # persone
    "places",        # luoghi
    "events",        # eventi
    "observations",  # osservazioni
    "emotions",      # emozioni
    "problems",      # problemi
    "solutions",     # soluzioni
    "reflections",   # riflessioni
)

# JSON Schema del contratto SkillOutput (specchia grammar.gbnf). Usato dall'adapter
# HTTP (LlamaServerModel) via `response_format` per vincolare l'output del modello a
# JSON strutturato sulle /v1/chat/completions: nelle versioni recenti di llama.cpp
# il campo `grammar` top-level è supportato solo su /completion, non sulle chat
# completions — lì si usa `response_format: {type: json_schema, schema: ...}`.
SCAFFOLD_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "date": {"type": "string"},
        "scaffold": {
            "type": "object",
            "properties": {f: {"type": "array", "items": {"type": "string"}}
                           for f in SCAFFOLD_FIELDS},
            "required": list(SCAFFOLD_FIELDS),
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "where": {"type": "string"},
                    "issue": {"type": "string"},
                    "kind": {"type": "string", "enum": ["clarity", "orthography"]},
                },
                "required": ["where", "issue", "kind"],
            },
        },
        "inferences": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "date", "scaffold", "questions", "checks"],
}


@dataclass
class Scaffold:
    """Lo scaffold del diario: fatti dell'utente organizzati per campo."""

    title: str = MISSING
    date: str = MISSING
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    emotions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    solutions: list[str] = field(default_factory=list)
    reflections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "date": self.date,
            "people": list(self.people),
            "places": list(self.places),
            "events": list(self.events),
            "observations": list(self.observations),
            "emotions": list(self.emotions),
            "problems": list(self.problems),
            "solutions": list(self.solutions),
            "reflections": list(self.reflections),
        }

    def filled_field_count(self) -> int:
        """Quanti campi strutturati contengono almeno un fatto."""
        return sum(1 for f in SCAFFOLD_FIELDS if getattr(self, f))


@dataclass
class Check:
    """Una segnalazione del check di chiarezza/ortografia."""

    where: str   # punto dell'input (citazione o riferimento)
    issue: str   # cosa non è chiaro / problema ortografico
    kind: str = "clarity"  # "clarity" | "orthography"

    def to_dict(self) -> dict:
        return {"where": self.where, "issue": self.issue, "kind": self.kind}


@dataclass
class SkillOutput:
    """Output completo della skill: scaffold + domande + check.

    `message` e' un messaggio opzionale di UI/onboarding (NON prosa del diario):
    lo si usa quando l'input non sono appunti (es. una domanda colloquiale o un
    input vuoto) per rispondere in modo amichevole e guidare l'utente, lasciando
    lo scaffold vuoto. Mantiene la parita' CLI/web: il client mostra `message`
    quando lo scaffold e' vuoto.
    """

    scaffold: Scaffold
    questions: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    inferences: list[str] = field(default_factory=list)  # ipotesi etichettate, distinte dai fatti
    message: str | None = None  # messaggio UI/onboarding (no prosa del diario)

    def to_dict(self) -> dict:
        return {
            "scaffold": self.scaffold.to_dict(),
            "questions": list(self.questions),
            "checks": [c.to_dict() for c in self.checks],
            "inferences": list(self.inferences),
            "message": self.message,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        """Rendering leggibile (per CLI / demo). Niente prosa: solo elenchi."""
        sc = self.scaffold
        empty = (sc.title == MISSING and sc.filled_field_count() == 0)
        # Se c'e' un messaggio di onboarding e niente scaffold, mostra solo quello
        # (evita il muro di "non specificato" quando l'utente fa una domanda).
        if self.message and empty:
            return self.message.rstrip() + "\n"
        lines: list[str] = []
        if self.message:
            lines.append(self.message.rstrip())
            lines.append("")
        lines.append(f"## {sc.title}")
        if sc.date and sc.date != MISSING:
            lines.append(f"Data: {sc.date}")
        lines.append("")
        for field_name in SCAFFOLD_FIELDS:
            label = {
                "people": "Persone", "places": "Luoghi", "events": "Eventi",
                "observations": "Osservazioni", "emotions": "Emozioni",
                "problems": "Problemi", "solutions": "Soluzioni",
                "reflections": "Riflessioni",
            }[field_name]
            values = getattr(sc, field_name)
            if values:
                lines.append(f"### {label}")
                for v in values:
                    lines.append(f"- {v}")
                lines.append("")
            else:
                lines.append(f"### {label}")
                lines.append(f"- {MISSING}")
                lines.append("")
        if self.questions:
            lines.append("### Domande per approfondire")
            for q in self.questions:
                lines.append(f"- {q}")
            lines.append("")
        if self.checks:
            lines.append("### Check (chiarezza / ortografia)")
            for c in self.checks:
                lines.append(f"- [{c.kind}] {c.where}: {c.issue}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def from_dict(d: dict) -> "SkillOutput":
        """Ricostruisce un SkillOutput da un dict (es. risposta JSON del servizio)."""
        sc = d.get("scaffold") or {}
        scaffold = Scaffold(
            title=sc.get("title") or MISSING,
            date=sc.get("date") or MISSING,
            **{f: list(sc.get(f, [])) for f in SCAFFOLD_FIELDS},
        )
        checks = [
            Check(
                where=c.get("where", ""),
                issue=c.get("issue", ""),
                kind=c.get("kind") if c.get("kind") in ("clarity", "orthography") else "clarity",
            )
            for c in (d.get("checks") or [])
        ]
        return SkillOutput(
            scaffold=scaffold,
            questions=list(d.get("questions") or []),
            checks=checks,
            inferences=list(d.get("inferences") or []),
            message=d.get("message"),
        )

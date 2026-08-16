"""La skill "Diario di Bordo": orchestra backend -> validazione -> output.

One-shot: appunti in ingresso, SkillOutput in uscita (scaffold + domande + check).
Niente ciclo interattivo, niente prosa, solo fatti dell'utente.
"""
from __future__ import annotations

import os

from .demo import DemoSink
from .models import ModelBackend, build_backend
from .prompts import SYSTEM_PROMPT, build_user_message
from .schema import MISSING, Check, Scaffold, SkillOutput
from .validate import validate_output

_GRAMMAR_PATH = os.path.join(os.path.dirname(__file__), "grammar.gbnf")


def _no_notes_fallback(message: str) -> SkillOutput:
    """Risposta amichevole quando l'input non contiene appunti utilizzabili.
    La skill e' un estrattore one-shot: niente appunti -> niente scaffold, ma un
    messaggio di onboarding che guida l'utente (NON prosa del diario)."""
    return SkillOutput(
        scaffold=Scaffold(title=MISSING, date=MISSING),
        questions=[],
        checks=[],
        inferences=[],
        message=message,
    )


# Aperture colloquiali (non appunti). Solo saluti/richieste di aiuto, NON avverbi
# interrogativi ("come/perche'") che compaiono anche in appunti veri.
_OPENERS = (
    "ciao", "salve", "buongiorno", "buonasera", "aiuto", "mi aiuti", "mi aiuta",
    "mi puoi", "puoi ", "puoi,", "scusa", "ehi",
)
_SHORT_MSG = (
    "Ciao! 👋 Non ho ancora abbastanza testo per estrarre uno scaffold.\n"
    "Incolla qui i tuoi appunti grezzi della giornata: chi c'era, dove siete "
    "stati, cosa avete fatto, se c'e' stato un problema e come lo avete risolto, "
    "e come ti sei sentito. Io li trasformo in struttura."
)
_QUESTION_MSG = (
    "Certo, ti aiuto volentieri! ✍️\n"
    "Sono una skill one-shot, non chiacchiero: prendo i tuoi APPUNTI grezzi e ti "
    "restituisco uno scaffold (persone, luoghi, eventi, emozioni, problemi, "
    "soluzioni, riflessioni) piu' due-tre domande per approfondire e un check di "
    "chiarezza. Il diario, la prosa, la scrivi tu partendo da li'.\n"
    "Incolla qui sotto gli appunti della giornata e partiamo!"
)


def load_grammar(path: str = _GRAMMAR_PATH) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


class DiarioSkill:
    """La skill. Dipende solo dall'adapter del modello (scambiabile)."""

    def __init__(
        self,
        backend: ModelBackend | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        grammar: str | None | bool = True,
        demo: DemoSink | None = None,
    ):
        self.backend = backend or build_backend("mock")
        self.system_prompt = system_prompt
        # grammar=True -> carica il default; None/False -> nessun vincolo.
        if grammar is True:
            self.grammar = load_grammar()
        elif grammar is False:
            self.grammar = None
        else:
            self.grammar = grammar
        self.demo = demo
        self.last_usage = None   # usage dell'ultima chiamata al backend (token ④)
        self.last_trace = None   # wire JSON dell'ultima chiamata (change trace-llm)

    def run(
        self,
        notes: str,
        max_tokens: int = 768,   # il JSON pretty-printato del 1.5B sforava 512 (truncation misurata)
        temperature: float = 0.2,
        demo: DemoSink | None = None,
    ) -> SkillOutput:
        sink = demo or self.demo
        sink = sink or DemoSink(verbose=False)
        self.last_usage = None   # niente staleness se il gate risponde senza modello
        self.last_trace = None   # idem per la trace: senza chiamata, niente trace

        sink.event("start", "🔧 Sto leggendo i tuoi appunti…")

        # Guardrail: la skill e' un estrattore one-shot di APPUNTI, non un chatbot.
        # Input vuoto/breve o una domanda colloquiale (non fatti) -> risposta
        # amichevole di onboarding, senza sprecare una chiamata al modello.
        stripped = (notes or "").strip()
        if len(stripped) < 15:
            sink.event("done", "ℹ️ Poco testo: incolla qui i tuoi appunti grezzi.")
            return _no_notes_fallback(_SHORT_MSG)
        low = stripped.lower()
        looks_conversational = (
            stripped.endswith("?") or any(low.startswith(p) for p in _OPENERS)
        )
        if looks_conversational and len(stripped.split()) < 18:
            sink.event("done", "ℹ️ Rispondo e ti guido: incolla qui i tuoi appunti.")
            return _no_notes_fallback(_QUESTION_MSG)

        user = build_user_message(notes)

        sink.event("extract", "🔧 Sto estraendo i fatti — solo i tuoi, senza inventarne.")
        raw = self.backend.generate(
            self.system_prompt, user,
            grammar=self.grammar,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        sink.event("validate", "✅ Controllo struttura: niente prosa, niente invenzioni.")
        output = validate_output(raw, notes)
        if output.scaffold.filled_field_count() == 0:
            # collasso misurato sul 1.5B (~2 richieste su 8): JSON troncato dal
            # tetto di token o comunque irrecuperabile -> UN retry, poi ci si
            # arrende senza inventare niente
            raw = self.backend.generate(
                self.system_prompt, user,
                grammar=self.grammar,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            output = validate_output(raw, notes)
        # token della chiamata al modello (se il backend li espone): li raccoglie
        # il servizio per la riga "token del workflow" nella tappa ④ — vale
        # l'ultima generate (quella buona)
        self.last_usage = getattr(self.backend, "last_usage", None)
        # wire JSON dell'ultima generate (quella buona): la skill lo allega alla
        # risposta come campo opzionale `trace` (change trace-llm, pattern events)
        self.last_trace = getattr(self.backend, "last_trace", None)

        sink.event("questions", "❓ Aggiungo le domande per approfondire.")
        sink.event("done", "✍️ Scaffold pronto. Ora il diario lo scrivi tu, partendo da qui.")
        return output

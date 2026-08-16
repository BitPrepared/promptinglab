"""Backend mock deterministico: estrae fatti dagli appunti SENZA inventare.

Non usa un LLM: applica euristiche semplici sul testo dell'utente e restituisce
un JSON conforme al contratto. Rispetta i principi della skill: solo fatti
dell'utente, nessuna invenzione, nessuna prosa. Serve a testare e demoare tutta
la catena offline, senza modello (e senza rete).
"""
from __future__ import annotations

import json
import re

from .base import ModelBackend
from ..schema import MISSING

_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|oggi|ieri|l'altroieri)\b", re.I
)
_EMOTION_HINTS = (
    "felice", "trist", "stanc", "emozionat", "paur", "arrabbiat", "divertit",
    "nervos", "soddisfat", "preoccupat", "entusias", "solo ", "noia", "ansia",
    "gioia", "felici",
)
_PROBLEM_HINTS = (
    "problema", "non riusciv", "non siamo riuscit", "rotto", "romp",
    "sbagliat", "difficolt", "imped", "bloccat", "andato male", "è fallit",
    "ci ha fermat",
)
_SOLUTION_HINTS = (
    "abbiamo risolt", "risolt", "poi abbiamo", "allora abbiamo", "soluzion",
    "siamo riuscit", "ha funzionato", "così abbiamo",
)
_REFLECT_HINTS = (
    "penso", "mi ha colpit", "ho capito", "secondo me", "ho riflettut",
    "ho imparat", "mi sono res", "mi ha fatto",
)
_STOPWORDS_PROPER = {
    "Oggi", "Ieri", "Poi", "Così", "Allora", "Durante", "Dopo", "Quando",
    "Mentre", "La", "Il", "Lo", "Un", "Una", "Questa", "Questo", "Nel", "Sul",
    "Del", "Al", "Nel", "Davanti", "Sopra", "Sotto", "Verso",
}


def _extract_notes(user_message: str) -> str:
    """Ricava gli appunti dal messaggio utente (gestisce i marker del prompt)."""
    m = re.search(r"--- APPUNTI ---\s*\n(.*?)\n--- FINE APPUNTI ---",
                  user_message, re.S)
    return (m.group(1) if m else user_message).strip()


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+|, ", text)
    out = []
    for p in parts:
        p = p.strip(" -•\t")
        if len(p) >= 2:
            out.append(p)
    return out


def _proper_nouns(text: str) -> list[str]:
    """Sostantivi in maiuscolo NON a inizio frase (esclude gli inizi di frase,
    che di solito non sono nomi propri)."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        caps = re.findall(r"\b([A-Z][a-zA-Zà-ÿ']+)\b", sentence)
        for w in caps[1:]:  # salta il primo token della frase (probabile inizio)
            if w in _STOPWORDS_PROPER:
                continue
            if w not in out:
                out.append(w)
    return out


def _contains_any(sentence: str, hints) -> bool:
    low = sentence.lower()
    return any(h in low for h in hints)


def extract(notes: str) -> dict:
    """Euristica deterministica note -> dict conforme al contratto (no invenzione)."""
    notes = (notes or "").strip()
    first_sent = re.split(r"(?<=[.!?])\s+|\n+", notes)
    first_sent = first_sent[0].strip() if first_sent else ""
    title = first_sent[:60] if first_sent else MISSING

    # preferisce una data numerica (più specifica) a una testuale
    num_date = re.search(r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b", notes)
    word_date = re.search(r"\b(oggi|ieri|l'altroieri)\b", notes, re.I)
    date_match = num_date or word_date
    date = date_match.group(1) if date_match else MISSING

    # luoghi: sostantivi dopo preposizioni di luogo (più affidabili)
    places = []
    for m in re.finditer(r"\b(?:a|al|alla|in|presso|sopra|vicino a)\s+([A-Z][a-zA-Zà-ÿ']+(?:\s+[a-zà-ÿ']+){0,2})", notes):
        cand = m.group(1).strip()
        if cand and cand not in places:
            places.append(cand)
    places = places[:6]

    # persone: sostantivi in maiuscolo NON a inizio frase, esclusi i luoghi
    people = [p for p in _proper_nouns(notes) if p not in places][:8]

    events, emotions, problems, solutions, reflections, observations = (
        [], [], [], [], [], []
    )
    for sent in _split_sentences(notes):
        if _contains_any(sent, _EMOTION_HINTS):
            emotions.append(sent)
        elif _contains_any(sent, _PROBLEM_HINTS):
            problems.append(sent)
        elif _contains_any(sent, _SOLUTION_HINTS):
            solutions.append(sent)
        elif _contains_any(sent, _REFLECT_HINTS):
            reflections.append(sent)
        elif len(sent.split()) >= 4:
            events.append(sent)
        else:
            observations.append(sent)

    # Domande: 2-3, deterministiche, in italiano, non ridondanti (chiedono
    # riflessione/sentimento, non fatti già elencati).
    questions = []
    if emotions:
        questions.append("Cosa vi ha fatto sentire così in quel momento?")
    else:
        questions.append("Qual è stato il momento più importante di questa esperienza?")
    questions.append("Cosa avreste fatto diversamente, se poteste?")
    questions.append("Cosa vi portate a casa da questa giornata?")
    questions = questions[:3]

    # Check: chiarezza/ortografia su frammenti o frasi troppo lunghe.
    checks = []
    for sent in _split_sentences(notes):
        if len(sent) > 160:
            checks.append({"where": sent[:60] + "…", "issue": "frase molto lunga, poco chiara", "kind": "clarity"})
        elif len(sent.split()) < 3:
            checks.append({"where": sent, "issue": "frase frammentaria, manca il soggetto o il verbo", "kind": "clarity"})
    if re.search(r"\w[.,;:]\w", notes):
        checks.append({"where": "spaziatura", "issue": "manca uno spazio dopo un segno di punteggiatura", "kind": "orthography"})
    checks = checks[:4]

    return {
        "title": title,
        "date": date,
        "scaffold": {
            "people": people,
            "places": places,
            "events": events,
            "observations": observations,
            "emotions": emotions,
            "problems": problems,
            "solutions": solutions,
            "reflections": reflections,
        },
        "questions": questions,
        "checks": checks,
        "inferences": [],  # il mock non inferisce: nessuna ipotesi
    }


class MockModel(ModelBackend):
    name = "mock"

    def generate(self, system, user, grammar=None, max_tokens=512, temperature=0.2) -> str:
        # system/grammar/temperature ignorati: il mock è puro e deterministico.
        notes = _extract_notes(user)
        reply = json.dumps(extract(notes), ensure_ascii=False)
        # Usage deterministica (caratteri come proxy dei token): cresce con gli
        # appunti, così il fronte "token del workflow" è testabile offline.
        self.last_usage = {
            "prompt_tokens": len(system) + len(user),
            "completion_tokens": len(reply),
        }
        return reply

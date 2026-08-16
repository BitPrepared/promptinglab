"""Test della validazione: parsing tollerante, guardia anti-prosa, clamp domande."""
from __future__ import annotations

import json
import unittest

from backend.schema import MISSING
from backend.validate import validate_output


class ValidateTest(unittest.TestCase):
    def test_valid_dict_normalizes_missing_fields(self) -> None:
        data = {
            "title": "Campo", "date": MISSING,
            "scaffold": {"people": ["Marco"], "places": [], "events": ["montato la tenda"],
                         "observations": [], "emotions": [], "problems": [],
                         "solutions": [], "reflections": []},
            "questions": ["Una?", "Due?"], "checks": [], "inferences": [],
        }
        out = validate_output(json.dumps(data), "Campo")
        self.assertEqual(out.scaffold.title, "Campo")
        self.assertEqual(out.scaffold.date, MISSING)
        self.assertEqual(out.scaffold.people, ["Marco"])
        self.assertEqual(out.scaffold.places, [])
        self.assertEqual(out.questions, ["Una?", "Due?"])

    def test_malformed_json_falls_back_without_inventing(self) -> None:
        out = validate_output("questo non è json", "appunti")
        self.assertEqual(out.scaffold.title, MISSING)
        self.assertEqual(out.questions, [])
        self.assertEqual(len(out.checks), 1)
        self.assertIn("non strutturato", out.checks[0].issue)

    def test_prose_guard_drops_and_flags(self) -> None:
        long_prose = (
            "Oggi è stata una giornata splendida. Siamo andati al campo e abbiamo "
            "fatto tante cose. Poi abbiamo mangiato tutti insieme e ci siamo divertiti "
            "molto, davvero tanto, una giornata indimenticabile per tutti noi ragazzi."
        )
        data = {
            "title": "x", "scaffold": {"events": [long_prose], "people": [],
            "places": [], "observations": [], "emotions": [], "problems": [],
            "solutions": [], "reflections": []},
            "questions": [], "checks": [],
        }
        out = validate_output(json.dumps(data), "appunti")
        # la prosa NON deve finire nello scaffold
        self.assertEqual(out.scaffold.events, [])
        # deve esserci un check che segnala l'uscita scartata
        self.assertTrue(any("scartato" in c.issue or "prosa" in c.issue for c in out.checks))


    def test_fenced_json_is_parsed(self) -> None:
        """qwen a volte incarta il JSON in un fence ```json ... ```: il parser
        tollerante lo spoglia prima di tentare il parse (regressione reale)."""
        from backend.validate import parse_json_tolerant
        fenced = '```json\n{"title": "x", "people": ["Marco"]}\n```'
        d = parse_json_tolerant(fenced)
        self.assertIsNotNone(d)
        self.assertEqual(d["title"], "x")

    def test_truncated_json_returns_none(self) -> None:
        # JSON spezzato dal tetto di token: niente parse fantasioso
        from backend.validate import parse_json_tolerant
        self.assertIsNone(parse_json_tolerant('{"title": "x", "people": ["Mar'))

    def test_questions_clamped_to_three(self) -> None:
        data = {
            "title": "x", "scaffold": {f: [] for f in (
                "people", "places", "events", "observations", "emotions",
                "problems", "solutions", "reflections")},
            "questions": ["a?", "b?", "c?", "d?", "e?"], "checks": [],
        }
        out = validate_output(json.dumps(data), "x")
        self.assertLessEqual(len(out.questions), 3)

    def test_prose_questions_are_dropped(self) -> None:
        # il modello base, senza fatti, a volte riempie le domande di prosa
        data = {
            "title": "x", "scaffold": {f: [] for f in (
                "people", "places", "events", "observations", "emotions",
                "problems", "solutions", "reflections")},
            "questions": [
                "Cosa avete fatto dopo?",                                   # ok
                "Mi chiedevo se avrei bisogno di aiuto per il diario.",    # prosa, no '?'
                "In questo momento non ho problemi, tuttavia potrebbe servire una guida se avessi bisogno.",  # troppo lunga
                "Perché poi. Siete tornati?",                              # multi-frase
            ],
            "checks": [],
        }
        out = validate_output(json.dumps(data), "appunti di oggi al campo")
        self.assertEqual(out.questions, ["Cosa avete fatto dopo?"])

    def test_json_embedded_in_text_is_extracted(self) -> None:
        text = 'Preambolo varie...\n{"title":"T","scaffold":{"people":["Luca"],"places":[],"events":[],"observations":[],"emotions":[],"problems":[],"solutions":[],"reflections":[]},"questions":[],"checks":[]}\n...coda'
        out = validate_output(text, "x")
        self.assertEqual(out.scaffold.title, "T")
        self.assertEqual(out.scaffold.people, ["Luca"])


if __name__ == "__main__":
    unittest.main()

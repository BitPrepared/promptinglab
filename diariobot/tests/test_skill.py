"""Test della skill end-to-end con backend mock (offline, deterministico)."""
from __future__ import annotations

import unittest

from diariobot.demo import DemoSink
from diariobot.models import MockModel
from diariobot.skill import DiarioSkill

NOTES = (
    "Campo base a Costigiola. Oggi, 12/08, con Marco e Lucia abbiamo montato "
    "la tenda nord. Pioveva forte e non riuscivamo a fissare i picchetti, "
    "poi abbiamo usato delle pietre come zavorra e ha funzionato. "
    "Eravamo stanchi ma molto felici. Ho capito che il gruppo conta più del singolo."
)


class SkillMockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = DiarioSkill(backend=MockModel(), grammar=False)

    def test_produces_structured_output(self) -> None:
        out = self.skill.run(NOTES)
        self.assertIsNotNone(out.scaffold)
        self.assertEqual(out.scaffold.title, "Campo base a Costigiola.")
        self.assertEqual(out.scaffold.date, "12/08")
        self.assertIn("Marco", out.scaffold.people)
        self.assertGreaterEqual(len(out.scaffold.events) + len(out.scaffold.problems)
                                + len(out.scaffold.solutions) + len(out.scaffold.reflections)
                                + len(out.scaffold.emotions), 1)

    def test_questions_count_and_language(self) -> None:
        out = self.skill.run(NOTES)
        self.assertGreaterEqual(len(out.questions), 2)
        self.assertLessEqual(len(out.questions), 3)
        for q in out.questions:
            self.assertTrue(q.endswith("?"))

    def test_no_invention_mock_only_uses_input(self) -> None:
        out = self.skill.run(NOTES)
        # ogni fatto dello scaffold (mock) è una sottostringa o frammento degli appunti
        text = NOTES
        for f in ("people", "places", "events", "emotions", "problems", "solutions", "reflections"):
            for item in getattr(out.scaffold, f):
                # almeno il primo token significativo deve comparire negli appunti
                token = item.split()[0].strip(",.;:()")
                self.assertIn(token.lower(), text.lower(),
                              f"fatto inventato in {f}: {item!r}")

    def test_no_prose_in_scaffold(self) -> None:
        out = self.skill.run(NOTES)
        # nessun elemento deve sembrare prosa (multi-frase + lungo)
        for f in ("people", "places", "events", "observations", "emotions",
                  "problems", "solutions", "reflections"):
            for item in getattr(out.scaffold, f):
                self.assertFalse(
                    len(item) > 140 and len(item.split(".")) >= 3,
                    f"possibile prosa in {f}: {item!r}",
                )

    def test_deterministic(self) -> None:
        a = self.skill.run(NOTES).to_dict()
        b = self.skill.run(NOTES).to_dict()
        self.assertEqual(a, b)

    def test_demo_events_never_expose_cot(self) -> None:
        sink = DemoSink(verbose=False)
        self.skill.run(NOTES, demo=sink)
        self.assertTrue(len(sink.events) >= 3)
        # gli eventi sono sintetici e non contengono token grezzi del modello
        for ev in sink.events:
            self.assertIn("event", ev)
            self.assertLess(len(ev["message"]), 120)

    def test_inferences_empty_for_mock(self) -> None:
        out = self.skill.run(NOTES)
        self.assertEqual(out.inferences, [])  # il mock non inferisce

    def test_short_input_returns_friendly_message(self) -> None:
        # la skill e' un estrattore: input vuoto/breve -> messaggio di onboarding
        out = self.skill.run("ciao")
        self.assertEqual(out.scaffold.title, "non specificato")
        self.assertTrue(all(not getattr(out.scaffold, f) for f in (
            "people", "places", "events", "observations", "emotions",
            "problems", "solutions", "reflections")))
        self.assertIsNotNone(out.message)
        self.assertIn("appunti", out.message.lower())

    def test_question_input_returns_friendly_message(self) -> None:
        # una domanda colloquiale (non appunti) -> risposta amichevole che guida
        out = self.skill.run("ciao, mi aiuti a scrivere un diario di bordo?")
        self.assertTrue(all(not getattr(out.scaffold, f) for f in (
            "people", "places", "events", "observations", "emotions",
            "problems", "solutions", "reflections")))
        self.assertIsNotNone(out.message)
        self.assertIn("appunti", out.message.lower())

    def test_real_notes_are_not_intercepted_by_guardrail(self) -> None:
        # appunti veri (anche brevi o con "come") non devono scattare il guardrail
        out = self.skill.run("Come previsto, montato la tenda con Marco.")
        self.assertIsNone(out.message)  # nessun messaggio: si va sul modello/mock
        self.assertIsNotNone(out.scaffold)



class SkillRetryTest(unittest.TestCase):
    """Robustezza sui collassi del 1.5B: output irrecuperabile (JSON troncato
    dal tetto di token o non parseabile) -> un retry, poi si arrende senza
    inventare. Regressione misurata: ~2/8 richieste collassavano a scaffold 0."""

    class _Flaky:
        name = "flaky"
        last_usage = None

        def __init__(self, outputs):
            self.outputs = list(outputs)
            self.calls = []

        def generate(self, system, user, grammar=None, max_tokens=512, temperature=0.2):
            self.calls.append(max_tokens)
            return self.outputs.pop(0)

    def test_retry_once_on_unusable_output(self) -> None:
        from diariobot.skill import DiarioSkill
        good = '{"title": "Campo", "date": "non specificato", "scaffold": {"people": ["Marco"]}, "questions": [], "checks": []}'
        be = self._Flaky(['{"title": "x", "people": ["Mar', good])  # troncato, poi buono
        sk = DiarioSkill(backend=be, grammar=False)
        out = sk.run("Campo con Marco e Lucia, montata la tenda nord sotto la pioggia.")
        self.assertEqual(out.scaffold.people, ["Marco"])

    def test_no_retry_loop_on_persistent_failure(self) -> None:
        from diariobot.skill import DiarioSkill
        be = self._Flaky(['{"title": "x", "people": ["Mar'] * 5)
        sk = DiarioSkill(backend=be, grammar=False)
        out = sk.run("Campo con Marco e Lucia, montata la tenda nord sotto la pioggia.")
        self.assertEqual(len(be.calls), 2)  # un solo retry, poi fallback vuoto
        self.assertEqual(out.scaffold.filled_field_count(), 0)

    def test_default_max_tokens_has_headroom(self) -> None:
        # il JSON pretty-printato del modello sforava 512 token (truncation):
        # il default della skill ora ha margine
        from diariobot.skill import DiarioSkill
        be = self._Flaky(['{"title": "x", "people": ["Mar'] * 2)
        DiarioSkill(backend=be, grammar=False).run(
            "Campo con Marco e Lucia, montata la tenda nord sotto la pioggia.")
        self.assertGreaterEqual(be.calls[0], 768)


if __name__ == "__main__":
    unittest.main()

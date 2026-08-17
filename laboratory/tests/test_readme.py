"""Coerenza del README di root (change readme-loadtest-consumi): chi arriva da
GitHub deve trovarci scopo e i due percorsi di avvio — zero-download (demo) e
con modello (immagine + GGUF da HuggingFace)."""
from __future__ import annotations

import os
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _readme() -> str:
    with open(os.path.join(_REPO_ROOT, "README.md"), encoding="utf-8") as f:
        return f.read()


class ReadmeRootTest(unittest.TestCase):
    def test_readme_exists_and_states_purpose(self) -> None:
        body = _readme()
        self.assertIn("Laboratorio di Prompting", body)

    def test_zero_download_path(self) -> None:
        # percorso demo: nessun modello, nessun download
        self.assertIn("make demo", _readme())

    def test_model_download_path(self) -> None:
        body = _readme()
        self.assertIn("make pull", body)          # immagine llama.cpp
        self.assertIn("huggingface.co", body)     # GGUF: da dove
        self.assertIn("qwen2.5", body)            # quali modelli
        self.assertIn("models/", body)            # dove metterli
        self.assertIn("make up", body)

    def test_mentions_admin_test_loadtest(self) -> None:
        body = _readme()
        self.assertIn("/admin", body)
        self.assertIn("make test", body)
        self.assertIn("make loadtest", body)


if __name__ == "__main__":
    unittest.main()

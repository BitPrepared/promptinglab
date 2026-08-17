"""Modello di costo didattico (change readme-loadtest-consumi, design D4):
stime semplificate, costanti in un unico punto modificabile."""
from __future__ import annotations

import unittest

from backend import costi


class CostiTest(unittest.TestCase):
    def test_costanti_dichiarate_in_un_unico_punto(self) -> None:
        # chi insegna aggiusta i numeri del suo campo senza toccare altro
        for c in ("WATT", "EURO_KWH", "FRONTIERA_MODELLO",
                  "FRONTIERA_EUR_PER_MTOKEN", "FRONTIERA_ACQUA_L_PER_MTOKEN"):
            self.assertTrue(hasattr(costi, c), c)

    def test_stima_locale(self) -> None:
        s = costi.stima(tok_in=1000, tok_out=500, secondi=3600)
        # 3600 s × 35 W = 35 Wh = 0.035 kWh → × 0.25 €/kWh
        self.assertAlmostEqual(s["locale"]["kwh"], 0.035, places=6)
        self.assertAlmostEqual(s["locale"]["euro"], 0.00875, places=6)
        # il calcolo sta nel locale del campo: acqua ≈ 0, dichiarato
        self.assertEqual(s["locale"]["acqua_l"], 0.0)

    def test_stima_frontiera(self) -> None:
        s = costi.stima(tok_in=1_000_000, tok_out=1_000_000, secondi=60)
        # euro: si paga tutto ciò che viaggia (in + out)
        self.assertAlmostEqual(s["frontiera"]["euro"],
                               2 * costi.FRONTIERA_EUR_PER_MTOKEN, places=4)
        # acqua: token GENERATI (l'estrazione dal data center è il costo idrico)
        self.assertAlmostEqual(s["frontiera"]["acqua_l"],
                               costi.FRONTIERA_ACQUA_L_PER_MTOKEN, places=6)
        # energia: anche la frontiera consuma kWh (inferenza nel data center) —
        # il confronto dev'essere completo: energia, acqua, costo
        self.assertAlmostEqual(s["frontiera"]["kwh"],
                               costi.FRONTIERA_KWH_PER_MTOKEN, places=6)
        self.assertTrue(s["frontiera"]["modello"])

    def test_zero_tokens_restituisce_zeri_onesti(self) -> None:
        s = costi.stima(tok_in=0, tok_out=0, secondi=0)
        self.assertEqual(s["frontiera"]["euro"], 0.0)
        self.assertEqual(s["frontiera"]["acqua_l"], 0.0)
        self.assertEqual(s["frontiera"]["kwh"], 0.0)


if __name__ == "__main__":
    unittest.main()

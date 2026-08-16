#!/usr/bin/env python3
"""Modalità kiosk CLI (fallback / alternativa al browser) per Raspberry Pi.

Loop interattivo: chiede gli appunti (più righe, chiudi con una riga vuota),
li invia al servizio skill e stampa lo scaffold. Usa lo stesso client condiviso
della pagina web (`backend.web.client`) e lo stesso rendering (`SkillOutput`),
così l'output è in parità con la pagina.

Esempio:
  LAB_HOST=192.168.1.10 python3 -m kiosk.kiosk_cli
  # oppure punta direttamente al bridge web (parità con la pagina):
  LAB_URL=http://192.168.1.10:8090 LAB_ENDPOINT=/api/scaffold python3 -m kiosk.kiosk_cli
"""
from __future__ import annotations

import os
import sys

from backend.schema import SkillOutput
from backend.web.client import SkillError, post_scaffold

_HOST = os.environ.get("LAB_HOST", "localhost")
_URL = os.environ.get("LAB_URL", f"http://{_HOST}:8080")
_ENDPOINT = os.environ.get("LAB_ENDPOINT", "/scaffold")  # /api/scaffold per il bridge web


def _read_notes() -> str:
    print("Incolla gli appunti della squadriglia.")
    print("(più righe OK — termina con una riga vuota, Ctrl-C per uscire)\n")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line.strip() == "" and lines:
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


def main() -> int:
    print("=" * 60)
    print("  Diario di Bordo — skill (CLI kiosk)")
    print(f"  servizio: {_URL}{_ENDPOINT}")
    print("=" * 60)
    while True:
        notes = _read_notes()
        if not notes:
            # stdin esaurito (es. input piped): elabora una volta e poi esci.
            if not sys.stdin.isatty():
                print("\n(stdin esaurito) Arrivederci!")
                return 0
            print("\n(input vuoto — riprova)\n")
            continue
        print("\n— elaborazione —\n")
        try:
            data = post_scaffold(_URL, notes, endpoint=_ENDPOINT)
        except SkillError as e:
            print(f"⚠️  {e}\n")
            continue
        print(SkillOutput.from_dict(data).to_text())
        print("-" * 60 + "\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nArrivederci!")
        sys.exit(0)

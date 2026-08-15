"""CLI della skill Diario di Bordo (standalone / client).

Due modalità:
- locale (default): gira la skill in-process col backend da env (mock | llama | local).
  È il path "standalone sul Pi": niente servizio esterno, modello locale.
- remota (--remote URL): chiama il servizio HTTP (verifica il server / uso come client).

Parità con la pagina web garantita dal fatto che entrambi invocano la stessa skill
(in-process) o lo stesso servizio (remoto), con lo stesso contratto.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _read_notes_stdin() -> str:
    return sys.stdin.read() if not sys.stdin.isatty() else ""


def _remote(url: str, notes: str) -> dict:
    body = json.dumps({"notes": notes}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/scaffold",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"errore HTTP {e.code}: {e.reason}\n")
        sys.exit(1)
    except urllib.error.URLError as e:
        sys.stderr.write(f"servizio non raggiungibile su {url}: {e.reason}\n")
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="diariobot", description="Skill Diario di Bordo")
    p.add_argument("notes", nargs="*", help="appunti come testo (in alternativa: stdin)")
    p.add_argument("--remote", metavar="URL", help="chiama il servizio HTTP invece di girare in-process")
    p.add_argument("--json", action="store_true", help="output JSON invece di testo leggibile")
    args = p.parse_args(argv)

    notes = " ".join(args.notes).strip() if args.notes else _read_notes_stdin()
    if not notes:
        p.error("manca il testo degli appunti (passalo come argomento o via stdin)")

    if args.remote:
        result = _remote(args.remote, notes)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            from .schema import SkillOutput
            print(SkillOutput.from_dict(result).to_text())
        return

    # in-process (standalone)
    from .demo import DemoSink
    from .service import get_skill

    sink = DemoSink(verbose=True)
    out = get_skill().run(notes, demo=sink)
    if args.json:
        print(out.to_json())
    else:
        print(out.to_text())


if __name__ == "__main__":
    main()

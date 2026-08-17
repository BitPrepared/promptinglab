"""CLI della simulazione: python3 -m loadtest [--url ... --n 8 --turns 4]."""
from __future__ import annotations

import argparse

from loadtest import run


def main() -> None:
    p = argparse.ArgumentParser(
        description="Simula N ragazzi in chat sul gateway del laboratorio")
    p.add_argument("--url", default="http://localhost:8090",
                   help="base URL del gateway (default: http://localhost:8090)")
    p.add_argument("--n", type=int, default=8, help="numero di ragazzi (default: 8)")
    p.add_argument("--turns", type=int, default=4,
                   help="turni di conversazione per ragazzo (default: 4)")
    p.add_argument("--retry", type=int, default=0,
                   help="ritentativi per turno fallito (default: 0, come la pagina)")
    args = p.parse_args()

    print(f"simulazione: {args.n} ragazzi × {args.turns} turni"
          f" (retry {args.retry}) → {args.url}", flush=True)
    rep = run(args.url, n=args.n, turns=args.turns, retries=args.retry)
    for cid, v in rep["sessions"].items():
        lat = v["latency_ms"]
        print(f"  {cid}: ok={v['ok']} err={v['err']} "
              f"ms min/med/max={lat['min']}/{lat['med']}/{lat['max']}")
    t = rep["totals"]
    lat = t["latency_ms"]
    print(f"totale: {t['sessions']} sessioni, {t['ok']} ok, {t['err']} err, "
          f"ms min/med/max={lat['min']}/{lat['med']}/{lat['max']}")
    print("le sessioni load-* sono visibili nel pannello /admin "
          "(grafico token/s incluso)")


if __name__ == "__main__":
    main()

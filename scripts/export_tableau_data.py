#!/usr/bin/env python
"""Regenerate the validated Tableau exports in exports/tableau/.

Prerequisites (already done if the dashboard works):
    make models-ga4 public-data analysis

Usage:
    .venv/bin/python scripts/export_tableau_data.py            # CSV + growthlab.hyper
    .venv/bin/python scripts/export_tableau_data.py --no-hyper # CSV only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.reporting.tableau_export import OUT, run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-hyper", action="store_true", help="skip the .hyper extract")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    try:
        m = run(args.out, hyper=not args.no_hyper)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Tableau export failed:\n{exc}", file=sys.stderr)
        return 1
    print(f"Validation: {m['validation']['passed']}/{m['validation']['checks']} checks passed")
    for name, t in m["tables"].items():
        print(f"  {name:<24} {t['rows']:>8,} rows  [{t['nature']}]")
    print(f"Hyper extract: {m['hyper']}")
    print(f"Wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}/ (csv/, manifest.json, "
          "data_dictionary.md, relationships.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

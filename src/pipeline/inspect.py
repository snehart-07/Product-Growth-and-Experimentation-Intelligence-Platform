"""Print row counts and a few sample records for each table of a source.

Usage:
    python -m src.pipeline.inspect --source ga4
    python -m src.pipeline.inspect --source ga4 --rows 3
"""

from __future__ import annotations

import argparse
import sys

import duckdb
import pandas as pd

from src.settings import PATHS, dataset_banner, resolve_sources


def inspect_source(source: str, rows: int) -> None:
    db = PATHS.processed(source) / f"pulseiq_{source}.duckdb"
    if not db.exists():
        print(f"[{source}] {db} not found; run `python -m src.pipeline.run_sql --source {source}` first.",
              file=sys.stderr)
        return
    con = duckdb.connect(str(db), read_only=True)
    print(f"\n### [{source}] {dataset_banner(source)['banner']}\n")
    tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables "
                                        "WHERE table_type = 'BASE TABLE' ORDER BY table_name").fetchall()]
    with pd.option_context("display.width", 200, "display.max_columns", 12, "display.max_colwidth", 40):
        for t in tables:
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            print(f"--- {t}: {n:,} rows")
            print(con.execute(f'SELECT * FROM "{t}" LIMIT {int(rows)}').df().to_string(index=False), "\n")
    con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=None)
    ap.add_argument("--rows", type=int, default=3)
    args = ap.parse_args(argv)
    for s in resolve_sources(args.source):
        inspect_source(s, args.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

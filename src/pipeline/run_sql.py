"""Run the numbered DuckDB SQL models for one data source at a time.

Runs sql/ga4/NN_*.sql in order against the bounded GA4 extract and writes
data/processed/ga4/pulseiq_ga4.duckdb.

Usage:
    python -m src.pipeline.run_sql --source ga4
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from src.settings import PATHS, dataset_banner, resolve_sources

RAW_TABLES = {
    "ga4": ("events",),  # items are nested inside events
}
EXPORT_TABLES = {
    "ga4": ("stg_items", "event_item_summary", "sessions", "users", "user_day_activity", "funnel_users",
            "cohort_retention", "data_quality_profile", "user_flags", "new_users", "user_return_flags",
            "funnel_new_users", "cohort_retention_weekly", "retention_features"),
}


def _observation_end(source: str, raw_dir: Path) -> datetime:
    """Read the observation end (exclusive, UTC) from the source metadata."""
    name = {"synthetic": "synthetic_generation_metadata.json", "ga4": "ga4_extract_metadata.json"}[source]
    meta_path = PATHS.metadata / name
    if not meta_path.exists():
        raise FileNotFoundError(f"{meta_path} not found. Generate/extract the {source} data first.")
    meta = json.loads(meta_path.read_text())
    if source == "synthetic":
        return datetime.fromisoformat(meta["observation_window_utc"]["end_exclusive"])
    return datetime.fromisoformat(meta["date_range"]["end_exclusive"])


def build_connection(
    source: str,
    frames: dict[str, pd.DataFrame] | None = None,
    raw_dir: Path | None = None,
    observation_end: datetime | None = None,
    database: str = ":memory:",
) -> duckdb.DuckDBPyConnection:
    """Create a connection with raw_<table> views and a run_params table.

    Pass ``frames`` (tests) or ``raw_dir`` (Parquet files) as the input.
    """
    con = duckdb.connect(database)
    con.execute("SET TimeZone = 'UTC'")
    for table in RAW_TABLES[source]:
        if frames is not None:
            if table not in frames:
                continue
            con.register(f"_df_{table}", frames[table])
            con.execute(f"CREATE OR REPLACE VIEW raw_{table} AS SELECT * FROM _df_{table}")
        else:
            path = (raw_dir or PATHS.raw(source)) / f"{table}.parquet"
            if not path.exists():
                raise FileNotFoundError(f"Missing raw input {path}")
            # Views cannot take prepared parameters; quote the literal safely.
            literal = str(path.resolve()).replace("'", "''")
            con.execute(f"CREATE OR REPLACE VIEW raw_{table} AS SELECT * FROM read_parquet('{literal}')")
    if observation_end is None:
        observation_end = _observation_end(source, raw_dir or PATHS.raw(source))
    banner = dataset_banner(source)
    con.execute(
        "CREATE OR REPLACE TABLE run_params AS SELECT CAST(? AS TIMESTAMP) AS observation_end, "
        "? AS source_type, ? AS data_nature",
        [observation_end, source, banner["nature"]],
    )
    return con


def run_models(con: duckdb.DuckDBPyConnection, source: str, sql_dir: Path | None = None) -> list[str]:
    """Execute sql/<source>/NN_*.sql in order. Returns the files executed."""
    sql_dir = sql_dir or PATHS.sql / source
    files = sorted(p for p in sql_dir.glob("[0-9][0-9]_*.sql"))
    for f in files:
        try:
            con.execute(f.read_text())
        except duckdb.Error as exc:
            raise RuntimeError(f"SQL model {f.name} failed: {exc}") from exc
    return [f.name for f in files]


def export(con: duckdb.DuckDBPyConnection, source: str, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for t in EXPORT_TABLES[source]:
        con.execute(f"COPY {t} TO '{out_dir / (t + '.parquet')}' (FORMAT PARQUET)")
        counts[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    return counts


def run_source(source: str) -> dict[str, int]:
    out_dir = PATHS.processed(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / f"pulseiq_{source}.duckdb"
    # Build in memory (much faster than a disk-backed build), then persist a copy.
    con = build_connection(source)
    files = run_models(con, source)
    counts = export(con, source, out_dir)
    for p in (db_path, db_path.with_suffix(".duckdb.wal")):
        p.unlink(missing_ok=True)
    literal = str(db_path).replace("'", "''")
    con.execute(f"ATTACH '{literal}' AS persisted")
    con.execute("COPY FROM DATABASE memory TO persisted")
    con.execute("DETACH persisted")
    con.close()
    print(f"[{source}] {dataset_banner(source)['label']}")
    print(f"  ran {len(files)} models: {', '.join(files)}")
    for t, n in counts.items():
        print(f"  {t:<26} {n:>10,} rows")
    print(f"  DuckDB: {db_path}")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=None, help="ga4 (default)")
    args = ap.parse_args(argv)
    status = 0
    for source in resolve_sources(args.source):
        try:
            run_source(source)
        except FileNotFoundError as exc:
            print(f"[{source}] skipped: {exc}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())

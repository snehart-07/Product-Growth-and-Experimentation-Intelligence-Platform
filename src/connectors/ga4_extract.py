"""Bounded, cost-checked extract of the GA4 public sample to local Parquet.

* Event grain: exactly one output row per source event row. Scalar event_params
  are pulled with correlated ``(SELECT ... FROM UNNEST(event_params) WHERE key = ... LIMIT 1)``
  subqueries, which never multiply rows (a plain ``CROSS JOIN UNNEST`` would).
* Items stay nested as an ARRAY<STRUCT> column; item-grain expansion happens
  locally in a separate model (sql/ga4/02_items.sql) and is aggregated before
  being joined back to events.
* Dates are filtered with ``_TABLE_SUFFIX`` so only the requested shards are scanned.
* No ``SELECT *``: only the listed columns/sub-fields are read.
* The extract is cached in data/raw/ga4/; re-running with the same date range
  re-uses it unless ``--refresh`` is passed.

Usage:
    python -m src.connectors.ga4_extract                         # default 3 days
    python -m src.connectors.ga4_extract --start 20201101 --end 20201107
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.connectors.bigquery_client import (
    BQ_LOCATION, DEFAULT_MAX_BYTES_BILLED, GA4_DATASET, GA4_TABLE_WILDCARD, GA4ConnectorError,
    dry_run, get_client, human_bytes, run_query)
from src.settings import PATHS

EXTRACT_SCHEMA_VERSION = "1.0.0"
DEFAULT_START = "20201101"  # first day of the public sample
DEFAULT_END = "20201103"  # inclusive -> 3 days by default
SAMPLE_FIRST, SAMPLE_LAST = "20201101", "20210131"

# event_params extracted as scalar columns: (output column, key, value field)
EVENT_PARAMS = (
    ("ga_session_id", "ga_session_id", "int_value"),
    ("ga_session_number", "ga_session_number", "int_value"),
    ("page_location", "page_location", "string_value"),
    ("page_title", "page_title", "string_value"),
    ("page_referrer", "page_referrer", "string_value"),
    ("engagement_time_msec", "engagement_time_msec", "int_value"),
    ("event_source", "source", "string_value"),
    ("event_medium", "medium", "string_value"),
    ("event_campaign", "campaign", "string_value"),
    ("search_term", "search_term", "string_value"),
)
# session_engaged is stored as a string in some exports and an int in others.
SESSION_ENGAGED_SQL = ("(SELECT COALESCE(p.value.string_value, CAST(p.value.int_value AS STRING)) "
                       "FROM UNNEST(event_params) AS p WHERE p.key = 'session_engaged' LIMIT 1) AS session_engaged")

EXTRACT_COLUMNS = (
    "event_date", "event_timestamp", "event_name", "event_bundle_sequence_id", "user_pseudo_id",
    "user_first_touch_timestamp", *[c for c, _, _ in EVENT_PARAMS], "session_engaged",
    "device_category", "operating_system", "browser", "country", "region", "city",
    "first_touch_source", "first_touch_medium", "first_touch_campaign",
    "transaction_id", "purchase_revenue_in_usd", "total_item_quantity", "unique_items", "items",
)


def validate_date(s: str) -> str:
    if not re.fullmatch(r"\d{8}", s or ""):
        raise ValueError(f"Date must be YYYYMMDD, got {s!r}")
    datetime.strptime(s, "%Y%m%d")  # raises on impossible dates
    if not (SAMPLE_FIRST <= s <= SAMPLE_LAST):
        raise ValueError(f"{s} is outside the public sample range {SAMPLE_FIRST}..{SAMPLE_LAST}")
    return s


def build_events_sql(start: str, end: str) -> str:
    """Event-grain extract. Dates are validated digits, so inlining them is injection-safe
    and lets BigQuery prune shards from the literal _TABLE_SUFFIX filter."""
    start, end = validate_date(start), validate_date(end)
    if start > end:
        raise ValueError("start must be <= end")
    params = ",\n  ".join(
        f"(SELECT p.value.{field} FROM UNNEST(event_params) AS p WHERE p.key = '{key}' LIMIT 1) AS {col}"
        for col, key, field in EVENT_PARAMS)
    return f"""SELECT
  event_date,
  event_timestamp,
  event_name,
  event_bundle_sequence_id,
  user_pseudo_id,
  user_first_touch_timestamp,
  {params},
  {SESSION_ENGAGED_SQL},
  device.category AS device_category,
  device.operating_system AS operating_system,
  device.web_info.browser AS browser,
  geo.country AS country,
  geo.region AS region,
  geo.city AS city,
  traffic_source.source AS first_touch_source,
  traffic_source.medium AS first_touch_medium,
  traffic_source.name AS first_touch_campaign,
  ecommerce.transaction_id AS transaction_id,
  ecommerce.purchase_revenue_in_usd AS purchase_revenue_in_usd,
  ecommerce.total_item_quantity AS total_item_quantity,
  ecommerce.unique_items AS unique_items,
  ARRAY(
    SELECT AS STRUCT i.item_id, i.item_name, i.item_category, i.price_in_usd, i.quantity,
                     i.item_revenue_in_usd, idx AS item_index
    FROM UNNEST(items) AS i WITH OFFSET AS idx ORDER BY idx
  ) AS items
FROM `{GA4_TABLE_WILDCARD}`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'"""


def build_validation_sql(day: str) -> str:
    """Tiny first query: one day, a handful of light columns, aggregated to one row."""
    day = validate_date(day)
    return f"""SELECT COUNT(*) AS n_events, COUNT(DISTINCT user_pseudo_id) AS n_users,
       COUNT(DISTINCT event_name) AS n_event_names, MIN(event_timestamp) AS min_ts, MAX(event_timestamp) AS max_ts
FROM `{GA4_TABLE_WILDCARD}` WHERE _TABLE_SUFFIX = '{day}'"""


def _end_exclusive(end: str) -> str:
    return (datetime.strptime(end, "%Y%m%d").date() + timedelta(days=1)).isoformat()


def _cache_is_valid(meta_path: Path, events_path: Path, start: str, end: str) -> bool:
    if not (meta_path.exists() and events_path.exists()):
        return False
    meta = json.loads(meta_path.read_text())
    return (meta.get("date_range", {}).get("start_suffix") == start
            and meta.get("date_range", {}).get("end_suffix") == end
            and meta.get("schema_version") == EXTRACT_SCHEMA_VERSION)


def extract(start: str = DEFAULT_START, end: str = DEFAULT_END, max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED,
            refresh: bool = False, out_dir: Path | None = None, estimate_only: bool = False) -> dict:
    out_dir = out_dir or PATHS.raw_ga4
    events_path = out_dir / "events.parquet"
    meta_path = PATHS.metadata / "ga4_extract_metadata.json"
    sql = build_events_sql(start, end)
    if not refresh and not estimate_only and _cache_is_valid(meta_path, events_path, start, end):
        print(f"Using cached extract {events_path} ({start}..{end}). Pass --refresh to re-query.")
        return json.loads(meta_path.read_text())

    client = get_client()
    # 1. tiny validation query
    vdf, vstats = run_query(client, build_validation_sql(start), max_bytes_billed=min(max_bytes_billed, 256 * 1024**2))
    print(f"Validation query on {start}: {int(vdf.n_events[0]):,} events, {int(vdf.n_users[0]):,} users "
          f"(processed {human_bytes(vstats.bytes_processed)})")
    # 2. cost estimate for the real extract
    estimate = dry_run(client, sql)
    print(f"Extract {start}..{end}: estimated scan {human_bytes(estimate)} (limit {human_bytes(max_bytes_billed)})")
    if estimate_only:
        return {"estimated_bytes": estimate, "validation": vdf.to_dict("records")[0]}
    # 3. run with maximum_bytes_billed enforced server-side
    table, stats = run_query(client, sql, max_bytes_billed=max_bytes_billed, as_arrow=True)
    missing = [c for c in EXTRACT_COLUMNS if c not in table.column_names]
    if missing:
        raise GA4ConnectorError(f"Extract is missing expected columns {missing}; re-run the schema inspector.")

    import pyarrow.parquet as pq

    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, events_path)
    meta = {
        "source_type": "ga4",
        "data_nature": "observational (real, obfuscated ecommerce web events)",
        "schema_version": EXTRACT_SCHEMA_VERSION,
        "dataset": GA4_DATASET,
        "location": BQ_LOCATION,
        "date_range": {"start_suffix": start, "end_suffix": end,
                       "start_inclusive": datetime.strptime(start, "%Y%m%d").date().isoformat(),
                       "end_exclusive": _end_exclusive(end)},
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": client.project,
        "query_stats": stats.as_dict(),
        "bytes_processed_human": human_bytes(stats.bytes_processed),
        "bytes_billed_human": human_bytes(stats.bytes_billed),
        "max_bytes_billed": max_bytes_billed,
        "row_counts": {"events": table.num_rows},
        "files": {"events": str(events_path)},
        "sql": sql,
    }
    PATHS.metadata.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, default=str))
    print(f"Wrote {table.num_rows:,} event rows to {events_path}; processed {meta['bytes_processed_human']}, "
          f"billed {meta['bytes_billed_human']}")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract a bounded GA4 sample to Parquet")
    ap.add_argument("--start", default=DEFAULT_START, help="YYYYMMDD inclusive")
    ap.add_argument("--end", default=DEFAULT_END, help="YYYYMMDD inclusive")
    ap.add_argument("--max-bytes-billed", type=int, default=DEFAULT_MAX_BYTES_BILLED)
    ap.add_argument("--refresh", action="store_true", help="Ignore the local Parquet cache")
    ap.add_argument("--estimate-only", action="store_true", help="Validate + dry-run only; download nothing")
    ap.add_argument("--print-sql", action="store_true")
    args = ap.parse_args(argv)
    if args.print_sql:
        print(build_events_sql(args.start, args.end))
        return 0
    try:
        extract(args.start, args.end, args.max_bytes_billed, args.refresh, estimate_only=args.estimate_only)
    except (GA4ConnectorError, ValueError) as exc:
        print(f"GA4 extract failed:\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

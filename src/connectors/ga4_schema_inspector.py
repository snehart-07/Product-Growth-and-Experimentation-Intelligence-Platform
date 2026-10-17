"""Inspect the GA4 public sample before extracting anything.

1. Lists available ``events_YYYYMMDD`` shards (free metadata API call).
2. Fetches and flattens the schema of one shard (free metadata API call).
3. Verifies every field the extractor needs is present.
4. Optionally (``--param-keys``) profiles event_params keys and value types for
   one day, after a dry-run cost estimate.

Writes data/metadata/ga4_schema.json.

Usage:
    python -m src.connectors.ga4_schema_inspector
    python -m src.connectors.ga4_schema_inspector --param-keys --date 20201101
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from src.connectors.bigquery_client import (
    GA4_DATASET, GA4ConnectorError, explain_error, get_client, human_bytes, run_query)
from src.settings import PATHS

# Dotted paths the extractor (ga4_extract.EVENTS_SQL) reads.
REQUIRED_FIELDS = (
    "event_date", "event_timestamp", "event_name", "event_bundle_sequence_id", "user_pseudo_id",
    "user_first_touch_timestamp", "event_params", "event_params.key", "event_params.value.string_value",
    "event_params.value.int_value", "event_params.value.double_value", "event_params.value.float_value",
    "device.category", "device.operating_system", "device.web_info.browser",
    "geo.country", "geo.region", "geo.city",
    "traffic_source.source", "traffic_source.medium", "traffic_source.name",
    "ecommerce.transaction_id", "ecommerce.purchase_revenue_in_usd", "ecommerce.total_item_quantity",
    "ecommerce.unique_items", "items", "items.item_id", "items.item_name", "items.item_category",
    "items.price_in_usd", "items.quantity", "items.item_revenue_in_usd",
)

_SUFFIX = re.compile(r"^events_(\d{8})$")


def flatten_schema(fields, prefix: str = "") -> list[dict]:
    out = []
    for f in fields:
        path = f"{prefix}{f.name}"
        out.append({"path": path, "type": f.field_type, "mode": f.mode})
        if f.field_type in ("RECORD", "STRUCT"):
            out.extend(flatten_schema(f.fields, prefix=path + "."))
    return out


def missing_fields(flat: list[dict], required=REQUIRED_FIELDS) -> list[str]:
    present = {f["path"] for f in flat}
    return [r for r in required if r not in present]


def list_date_suffixes(client) -> list[str]:
    try:
        tables = client.list_tables(GA4_DATASET)
        return sorted(m.group(1) for t in tables if (m := _SUFFIX.match(t.table_id)))
    except Exception as exc:
        raise GA4ConnectorError(explain_error(exc)) from exc


def profile_param_keys(client, date: str, max_bytes_billed: int) -> list[dict]:
    if not re.fullmatch(r"\d{8}", date):
        raise ValueError("date must be YYYYMMDD")
    sql = f"""
        SELECT p.key,
               COUNTIF(p.value.string_value IS NOT NULL) AS n_string,
               COUNTIF(p.value.int_value IS NOT NULL)    AS n_int,
               COUNTIF(p.value.double_value IS NOT NULL) AS n_double,
               COUNTIF(p.value.float_value IS NOT NULL)  AS n_float,
               COUNT(DISTINCT event_name)                AS n_event_names,
               COUNT(*)                                  AS n_rows
        FROM `{GA4_DATASET}.events_*`, UNNEST(event_params) AS p
        WHERE _TABLE_SUFFIX = '{date}'
        GROUP BY p.key ORDER BY n_rows DESC"""
    df, stats = run_query(client, sql, max_bytes_billed=max_bytes_billed)
    print(f"  param-key profile for {date}: processed {human_bytes(stats.bytes_processed)}")
    return df.to_dict("records")


def inspect(param_keys: bool = False, date: str | None = None, max_bytes_billed: int = 512 * 1024**2) -> dict:
    client = get_client()
    suffixes = list_date_suffixes(client)
    if not suffixes:
        raise GA4ConnectorError(f"No events_YYYYMMDD tables found in {GA4_DATASET}.")
    shard = date or suffixes[0]
    try:
        table = client.get_table(f"{GA4_DATASET}.events_{shard}")
    except Exception as exc:
        raise GA4ConnectorError(explain_error(exc)) from exc
    flat = flatten_schema(table.schema)
    report = {
        "inspected_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": GA4_DATASET,
        "date_suffixes": {"first": suffixes[0], "last": suffixes[-1], "count": len(suffixes)},
        "inspected_shard": shard,
        "shard_num_rows": table.num_rows,
        "shard_num_bytes": table.num_bytes,
        "schema": flat,
        "missing_required_fields": missing_fields(flat),
    }
    if param_keys:
        report["event_param_keys"] = profile_param_keys(client, shard, max_bytes_billed)
    PATHS.metadata.mkdir(parents=True, exist_ok=True)
    (PATHS.metadata / "ga4_schema.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inspect GA4 public sample schema")
    ap.add_argument("--param-keys", action="store_true", help="Profile event_params keys (runs a small query)")
    ap.add_argument("--date", help="YYYYMMDD shard to inspect (default: first available)")
    args = ap.parse_args(argv)
    try:
        r = inspect(args.param_keys, args.date)
    except GA4ConnectorError as exc:
        print(f"Schema inspection failed:\n{exc}", file=sys.stderr)
        return 1
    print(f"Shards: {r['date_suffixes']['first']}..{r['date_suffixes']['last']} ({r['date_suffixes']['count']} days)")
    print(f"Shard {r['inspected_shard']}: {r['shard_num_rows']:,} rows, {human_bytes(r['shard_num_bytes'])}")
    print(f"Schema fields: {len(r['schema'])}; missing required: {r['missing_required_fields'] or 'none'}")
    print(f"Wrote {PATHS.metadata / 'ga4_schema.json'}")
    return 1 if r["missing_required_fields"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

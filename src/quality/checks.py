"""SQL data-quality assertions. Every check is a query that must return ZERO rows.

Checks run against a DuckDB connection built by ``src.pipeline.run_sql``
(raw_* views plus, for model checks, the materialized models). Rules are
documented in docs/data_quality_rules.md.

Usage:
    python -m src.quality.checks --source ga4
"""

from __future__ import annotations

import argparse
import sys

import duckdb
import pandas as pd



GA4_RAW_CHECKS: dict[str, str] = {
    "events_not_null_keys":
        "SELECT * FROM raw_events WHERE user_pseudo_id IS NULL OR event_timestamp IS NULL OR event_name IS NULL",
    "events_date_matches_timestamp":
        # event_date is in the property's time zone, event_timestamp is UTC: allow one day of skew.
        "SELECT * FROM raw_events WHERE abs(date_diff('day', strptime(event_date, '%Y%m%d')::DATE, "
        "CAST(make_timestamp(event_timestamp) AS DATE))) > 1",
    "events_inside_extract_range":
        "SELECT * FROM raw_events, run_params WHERE strptime(event_date, '%Y%m%d') >= observation_end",
}

GA4_MODEL_CHECKS: dict[str, str] = {
    "stg_events_row_count_matches_raw":
        "SELECT * FROM (SELECT (SELECT count(*) FROM raw_events) r, (SELECT count(*) FROM stg_events) s) WHERE r <> s",
    "stg_events_unique_event_row_id":
        "SELECT event_row_id FROM stg_events GROUP BY 1 HAVING count(*) > 1",
    "event_item_summary_no_row_multiplication":
        "SELECT * FROM (SELECT (SELECT count(*) FROM stg_events) s, (SELECT count(*) FROM stg_events_with_items) j) "
        "WHERE s <> j",
    "event_item_summary_quantity_reconciles":
        "SELECT * FROM (SELECT (SELECT coalesce(sum(item_rows),0) FROM event_item_summary) a, "
        "(SELECT coalesce(sum(len(items)),0) FROM raw_events) b, (SELECT count(*) FROM stg_items) c) "
        "WHERE a <> b OR b <> c",
    "sessions_belong_to_one_user":
        "SELECT session_key FROM stg_events WHERE session_key IS NOT NULL GROUP BY 1 "
        "HAVING count(DISTINCT user_pseudo_id) > 1",
    "users_one_row_per_user":
        "SELECT * FROM (SELECT (SELECT count(DISTINCT user_pseudo_id) FROM raw_events) r, "
        "(SELECT count(*) FROM users) u) WHERE r <> u",
    "funnel_one_row_per_user":
        "SELECT * FROM (SELECT (SELECT count(*) FROM users) u, (SELECT count(*) FROM funnel_users) f) WHERE u <> f",
    "funnel_strict_flags_monotonic":
        "SELECT * FROM funnel_users WHERE (strict_add_to_cart AND NOT strict_view_item) "
        "OR (strict_begin_checkout AND NOT strict_add_to_cart) OR (strict_purchase AND NOT strict_begin_checkout)",
    "new_users_one_row_each_and_subset_of_users":
        "SELECT * FROM (SELECT (SELECT count(*) FROM new_users) n, (SELECT count(DISTINCT user_pseudo_id) FROM new_users) d, "
        "(SELECT count(*) FROM new_users LEFT JOIN users USING (user_pseudo_id) WHERE users.user_pseudo_id IS NULL) o) "
        "WHERE n <> d OR o > 0",
    "new_user_marts_one_row_per_new_user":
        "SELECT * FROM (SELECT (SELECT count(*) FROM new_users) n, (SELECT count(*) FROM funnel_new_users) f, "
        "(SELECT count(*) FROM user_return_flags) r) WHERE n <> f OR n <> r",
    "new_funnel_strict_flags_monotonic":
        "SELECT * FROM funnel_new_users WHERE (strict_add_to_cart AND NOT strict_view_item) "
        "OR (strict_begin_checkout AND NOT strict_add_to_cart) OR (strict_purchase AND NOT strict_begin_checkout)",
    "new_funnel_inside_7_day_window":
        "SELECT * FROM funnel_new_users WHERE ts_purchase_any >= t0 + INTERVAL 7 DAY OR ts_view_item < t0",
    "retention_features_no_post_24h_leakage":
        # Independent recount: features must equal counts of events strictly inside [t0, t0 + 24h).
        "SELECT r.user_pseudo_id FROM retention_features r LEFT JOIN (SELECT e.user_pseudo_id, count(*) AS n "
        "FROM stg_events e JOIN new_users u ON e.user_pseudo_id = u.user_pseudo_id "
        "AND e.event_ts >= u.t0 AND e.event_ts < u.t0 + INTERVAL 24 HOUR GROUP BY 1) c USING (user_pseudo_id) "
        "WHERE c.n IS DISTINCT FROM r.events_24h",
    "cohort_weekly_active_not_above_size":
        "SELECT * FROM cohort_retention_weekly WHERE active_users > cohort_size",
    "return_window_flags_nested":
        "SELECT * FROM user_return_flags WHERE (return_d7_exact AND NOT return_d7) OR (return_d7 AND NOT return_d30) "
        "OR (return_d1 AND NOT return_d7)",
}

CHECKS = {
    "ga4": {"raw": GA4_RAW_CHECKS, "model": GA4_MODEL_CHECKS},
}


def run_checks(con: duckdb.DuckDBPyConnection, checks: dict[str, str], sample: int = 5) -> dict[str, pd.DataFrame]:
    """Return {check_name: violating rows (up to ``sample``)} for failing checks only."""
    failures = {}
    for name, sql in checks.items():
        try:
            df = con.execute(f"SELECT * FROM ({sql}) LIMIT {int(sample)}").df()
        except duckdb.Error as exc:
            df = pd.DataFrame({"error": [str(exc)]})
        if len(df):
            failures[name] = df
    return failures


def export_sql() -> None:
    """Write every assertion to sql/tests/assertions.sql (each query must return zero rows)."""
    from src.settings import PATHS

    out = ["-- Generated by `python -m src.quality.checks --export-sql`. Source of truth: src/quality/checks.py.",
           "-- Every query must return ZERO rows. Run against the matching DuckDB file in data/processed/<source>/."]
    for source, layers in CHECKS.items():
        for layer, checks in layers.items():
            out.append(f"\n-- ===== {source} / {layer} =====")
            for name, sql in checks.items():
                out.append(f"\n-- {name}\n{sql};")
    path = PATHS.sql / "tests" / "assertions.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    from src.pipeline.run_sql import build_connection, run_models
    from src.settings import resolve_sources

    ap = argparse.ArgumentParser(description="Run SQL data-quality assertions")
    ap.add_argument("--source", default=None)
    ap.add_argument("--export-sql", action="store_true", help="write sql/tests/assertions.sql and exit")
    args = ap.parse_args(argv)
    if args.export_sql:
        export_sql()
        return 0
    status = 0
    for source in resolve_sources(args.source):
        try:
            con = build_connection(source)
        except FileNotFoundError as exc:
            print(f"[{source}] skipped: {exc}", file=sys.stderr)
            status = 1
            continue
        run_models(con, source)
        for layer, checks in CHECKS[source].items():
            failures = run_checks(con, checks)
            for name in checks:
                mark = "FAIL" if name in failures else "ok  "
                print(f"[{source}/{layer}] {mark} {name}")
                if name in failures:
                    print(failures[name].to_string(max_colwidth=80))
            status |= bool(failures)
    return int(status)


if __name__ == "__main__":
    raise SystemExit(main())

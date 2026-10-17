"""Offline tests for GA4 extraction SQL and local normalization (no BigQuery needed)."""

from __future__ import annotations

import re

import pytest

from src.connectors import bigquery_client as bq
from src.connectors.ga4_extract import (
    EVENT_PARAMS, EXTRACT_COLUMNS, build_events_sql, build_validation_sql, validate_date)
from src.connectors.ga4_schema_inspector import REQUIRED_FIELDS, missing_fields
from src.pipeline.run_sql import build_connection, run_models
from src.quality.checks import GA4_MODEL_CHECKS, GA4_RAW_CHECKS, run_checks
from tests.fixtures import ga4_fixture as fx


def _con(table=None, models=True):
    con = build_connection("ga4", frames={"events": table if table is not None else fx.table()},
                           observation_end=fx.OBSERVATION_END)
    if models:
        run_models(con, "ga4")
    return con


@pytest.fixture(scope="module")
def con():
    c = _con()
    yield c
    c.close()


# ----------------------------------------------------------------- extraction SQL
def test_extract_sql_is_bounded_and_explicit():
    sql = build_events_sql("20201101", "20201103")
    assert "SELECT *" not in sql.upper().replace("SELECT AS STRUCT", "")
    assert "_TABLE_SUFFIX BETWEEN '20201101' AND '20201103'" in sql
    assert "CROSS JOIN UNNEST" not in sql.upper()  # would multiply event rows
    assert sql.count("LIMIT 1") == len(EVENT_PARAMS) + 1  # every scalar param subquery is single-valued


def test_extract_sql_aliases_match_declared_columns():
    sql = build_events_sql("20201101", "20201101")
    outer = sql.split("FROM `")[0]
    aliases = set(re.findall(r"\bAS (\w+)(?:,|\n)", outer)) | {"event_date", "event_timestamp", "event_name",
                                                               "event_bundle_sequence_id", "user_pseudo_id",
                                                               "user_first_touch_timestamp"}
    assert set(EXTRACT_COLUMNS) <= aliases


@pytest.mark.parametrize("bad", ["2020-11-01", "20201101' OR 1=1 --", "20201131", "20190101", "20210201", ""])
def test_invalid_dates_rejected(bad):
    with pytest.raises(ValueError):
        validate_date(bad)


def test_start_after_end_rejected():
    with pytest.raises(ValueError):
        build_events_sql("20201105", "20201101")


def test_validation_query_is_single_day():
    sql = build_validation_sql("20201101")
    assert "_TABLE_SUFFIX = '20201101'" in sql and "SELECT *" not in sql


def test_fixture_schema_matches_extract_columns():
    assert list(fx.SCHEMA.names) == list(EXTRACT_COLUMNS)


def test_schema_inspector_reports_missing_fields():
    flat = [{"path": p} for p in REQUIRED_FIELDS if p != "geo.country"]
    assert missing_fields(flat) == ["geo.country"]
    assert missing_fields([{"path": p} for p in REQUIRED_FIELDS]) == []


# ----------------------------------------------------------------- connector errors (offline)
def test_project_id_required(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(bq.GA4ConnectorError, match="GOOGLE_CLOUD_PROJECT"):
        bq.get_project_id()


def _exc(name, msg=""):
    return type(name, (Exception,), {})(msg)


@pytest.mark.parametrize("name, msg, expect", [
    ("DefaultCredentialsError", "", "application-default login"),
    ("RefreshError", "invalid_grant", "application-default login"),
    ("Forbidden", "User does not have bigquery.jobs.create permission", "jobUser"),
    ("Forbidden", "BigQuery API has not been used in project", "services enable"),
    ("BadRequest", "Query exceeded limit for bytes billed: 100", "maximum_bytes_billed"),
    ("TooManyRequests", "quota exceeded", "quota"),
    ("NotFound", "Table not found", "location"),
])
def test_error_explanations(name, msg, expect):
    assert expect in bq.explain_error(_exc(name, msg))


def test_byte_limit_enforced_before_running(monkeypatch):
    monkeypatch.setattr(bq, "dry_run", lambda client, sql, params=None: 10 * 1024**3)
    with pytest.raises(bq.GA4ConnectorError, match="exceeds the limit"):
        bq.run_query(object(), "SELECT 1", max_bytes_billed=1024**3)


# ----------------------------------------------------------------- normalization
def test_no_row_multiplication(con):
    raw = con.execute("SELECT count(*) FROM raw_events").fetchone()[0]
    assert con.execute("SELECT count(*) FROM stg_events").fetchone()[0] == raw
    assert con.execute("SELECT count(*) FROM stg_events_with_items").fetchone()[0] == raw
    n_items = sum(len(r["items"]) for r in fx.rows())
    assert con.execute("SELECT count(*) FROM stg_items").fetchone()[0] == n_items
    assert con.execute("SELECT sum(item_rows) FROM event_item_summary").fetchone()[0] == n_items


def test_placeholders_bucketed(con):
    b = con.execute("SELECT country, first_device_category, first_touch_channel FROM users WHERE user_pseudo_id='B'").fetchone()
    assert b == ("(obfuscated)", "(missing)", "(obfuscated)")
    c = con.execute("SELECT country, first_touch_channel FROM users WHERE user_pseudo_id='C'").fetchone()
    assert c == ("(missing)", "(missing)")
    assert con.execute("SELECT first_touch_channel FROM users WHERE user_pseudo_id='D'").fetchone()[0] == "Direct"
    assert con.execute("SELECT first_touch_channel FROM users WHERE user_pseudo_id='A'").fetchone()[0] == "Organic Search"
    assert con.execute("SELECT count(*) FROM stg_items WHERE item_name IS NULL").fetchone()[0] == 1
    assert con.execute("SELECT sum(is_purchase_without_txn_id::INT) FROM stg_events").fetchone()[0] == 1


def test_missing_session_id_and_duplicates_flagged(con):
    assert con.execute("SELECT sum(is_missing_session_id::INT) FROM stg_events").fetchone()[0] == 1
    assert con.execute("SELECT sum(is_potential_duplicate::INT) FROM stg_events").fetchone()[0] == 2
    keys = {r[0] for r in con.execute("SELECT session_key FROM sessions").fetchall()}
    assert None not in keys and "A-2002" in keys and len(keys) == 5


def test_funnel_strict_vs_any_order(con):
    f = con.execute("SELECT * FROM funnel_users ORDER BY user_pseudo_id").df().set_index("user_pseudo_id")
    assert f.loc["A", "strict_purchase"] and f.loc["A", "any_purchase"]
    assert f.loc["B", "any_purchase"] and not f.loc["B", "strict_purchase"]
    assert f.loc["D", "any_add_to_cart"] and not f.loc["D", "strict_add_to_cart"]
    assert f.loc["A", "sec_view_to_cart"] == 60
    assert not f.loc["D", "is_new_in_window"]


def test_cohort_censoring(con):
    r = con.execute("SELECT age_day, active_users, is_fully_observed FROM cohort_retention "
                    "WHERE cohort_date = DATE '2020-11-01' AND first_touch_channel = 'Organic Search' "
                    "ORDER BY age_day").fetchall()
    assert r[0] == (1, 1, True)  # user A came back on Nov 2
    assert r[1][2] is True and r[2][2] is False  # Nov 3 observed, Nov 4 censored


def test_ga4_checks_pass_on_fixture(con):
    failures = run_checks(con, {**GA4_RAW_CHECKS, **GA4_MODEL_CHECKS})
    assert failures == {}, failures


@pytest.mark.parametrize("check, extra", [
    ("events_not_null_keys", fx._row(None, 1, 9, 0, "page_view")),
    ("events_inside_extract_range", fx._row("E", 1, 9, 0, "page_view") | {"event_date": "20201105"}),
    ("events_date_matches_timestamp", fx._row("E", 1, 9, 0, "page_view") | {"event_date": "20201120"}),
])
def test_ga4_negative_fixtures_detected(check, extra):
    c = _con(fx.table([extra]), models=False)
    assert check in run_checks(c, {check: GA4_RAW_CHECKS[check]})


def test_missing_optional_values_do_not_break_models():
    row = fx._row("F", 2, 9, 0, "page_view", sid=None, country=None, device=None, src=None, med=None)
    row.update({"items": None, "user_first_touch_timestamp": None, "session_engaged": None, "page_location": None})
    c = _con(fx.table([row]))
    assert c.execute("SELECT count(*) FROM users WHERE user_pseudo_id='F'").fetchone()[0] == 1
    assert run_checks(c, GA4_MODEL_CHECKS) == {}

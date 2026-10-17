"""BigQuery INTEGRATION tests. They query Google's public GA4 sample.

Skipped automatically unless GOOGLE_CLOUD_PROJECT is set and Application
Default Credentials are available. Run explicitly with:

    pytest -m integration
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def _adc_available() -> bool:
    try:
        import google.auth

        google.auth.default()
        return True
    except Exception:
        return False


if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
    pytest.skip("GOOGLE_CLOUD_PROJECT not set; skipping BigQuery integration tests", allow_module_level=True)
if not _adc_available():
    pytest.skip("No Application Default Credentials; run `gcloud auth application-default login`",
                allow_module_level=True)

from src.connectors.bigquery_client import dry_run, get_client, verify_access  # noqa: E402
from src.connectors.ga4_extract import DEFAULT_END, DEFAULT_START, build_events_sql, build_validation_sql  # noqa: E402
from src.connectors.bigquery_client import run_query  # noqa: E402
from src.connectors.ga4_schema_inspector import flatten_schema, list_date_suffixes, missing_fields  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return get_client()


def test_verify_access(client):
    r = verify_access(client)
    assert r["n_events_20210131"] > 0
    assert r["location"] == "US"


def test_schema_has_required_fields(client):
    suffixes = list_date_suffixes(client)
    assert suffixes[0] == "20201101" and suffixes[-1] == "20210131"
    table = client.get_table("bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_20201101")
    assert missing_fields(flatten_schema(table.schema)) == []


def test_default_extract_estimate_is_small(client):
    est = dry_run(client, build_events_sql(DEFAULT_START, DEFAULT_END))
    assert 0 < est < 2 * 1024**3


def test_small_validation_query(client):
    df, stats = run_query(client, build_validation_sql(DEFAULT_START), max_bytes_billed=256 * 1024**2)
    assert int(df.n_events[0]) > 0
    assert stats.bytes_billed is not None

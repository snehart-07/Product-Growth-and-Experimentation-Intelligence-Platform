"""BigQuery client for the public GA4 sample, using Application Default Credentials.

No API keys, service-account keys or tokens are read or stored here. Set up:

    gcloud auth application-default login
    export GOOGLE_CLOUD_PROJECT=<your-project-id>   # billing/quota project for query jobs

Every query goes through ``run_query``, which dry-runs first, refuses to run
if the estimate exceeds the byte limit, and sets ``maximum_bytes_billed`` on
the job as a hard server-side cap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

BQ_LOCATION = "US"  # location of bigquery-public-data.ga4_obfuscated_sample_ecommerce
GA4_DATASET = "bigquery-public-data.ga4_obfuscated_sample_ecommerce"
GA4_TABLE_WILDCARD = f"{GA4_DATASET}.events_*"
DEFAULT_MAX_BYTES_BILLED = int(os.environ.get("PULSEIQ_MAX_BYTES_BILLED", 2 * 1024**3))  # 2 GiB


class GA4ConnectorError(RuntimeError):
    """Raised with a human-readable explanation and a suggested fix."""


@dataclass
class QueryStats:
    estimated_bytes: int
    bytes_processed: int | None = None
    bytes_billed: int | None = None
    job_id: str | None = None
    cache_hit: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def human_bytes(n: int | None) -> str:
    if n is None:
        return "n/a"
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def get_project_id() -> str:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise GA4ConnectorError(
            "GOOGLE_CLOUD_PROJECT is not set. Query jobs run (and are billed/quota-counted) in your own "
            "project, even when reading public data.\n"
            "  Fix: export GOOGLE_CLOUD_PROJECT=<your-project-id>\n"
            "  List projects: gcloud projects list"
        )
    return project


def explain_error(exc: BaseException) -> str:
    """Translate google-auth / google-api-core exceptions into actionable messages."""
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    if name == "DefaultCredentialsError":
        return ("No Application Default Credentials found.\n"
                "  Fix: gcloud auth application-default login")
    if name in ("RefreshError", "TransportError") or "reauth" in low or "invalid_grant" in low:
        return ("ADC credentials could not be refreshed (expired or revoked).\n"
                "  Fix: gcloud auth application-default login")
    if "bytesbilledlimitexceeded" in low.replace(" ", "") or "bytes billed" in low:
        return ("Query exceeded maximum_bytes_billed and was not run. Narrow the date range or raise "
                "--max-bytes-billed deliberately.")
    if name == "Forbidden" or "403" in msg[:10] or "access denied" in low or "permission" in low:
        if "billing" in low:
            return ("BigQuery reports a billing problem for the project. Sandbox projects work without "
                    "billing but have limits; check the project's billing status in the Cloud Console.")
        if "bigquery.jobs.create" in low:
            return ("Your account cannot create query jobs in GOOGLE_CLOUD_PROJECT. Ask for "
                    "roles/bigquery.jobUser (or BigQuery User) on that project, or use a project you own.")
        if "has not been used" in low or "disabled" in low or "service_disabled" in low:
            return ("The BigQuery API is not enabled for this project.\n"
                    "  Fix: gcloud services enable bigquery.googleapis.com")
        return f"Permission denied by BigQuery: {msg}"
    if name == "NotFound":
        return (f"Not found: {msg}\n  Check the table name and that the job location is {BQ_LOCATION}. "
                "If the public GA4 sample has been moved or retired, update GA4_DATASET.")
    if name in ("TooManyRequests",) or "quota" in low or "ratelimitexceeded" in low.replace(" ", ""):
        return ("BigQuery quota or rate limit reached (Sandbox/free tier allows about 1 TiB of query "
                "processing per month). Wait, reduce the date range, or use the local Parquet cache.")
    if name == "BadRequest":
        return f"BigQuery rejected the query: {msg}"
    return f"{name}: {msg}"


def get_client(project: str | None = None):
    """Create a BigQuery client with ADC. Raises GA4ConnectorError with a fix on failure."""
    try:
        import google.auth
        from google.cloud import bigquery
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise GA4ConnectorError("google-cloud-bigquery is not installed. Run: pip install -r requirements.txt") from exc
    project = project or get_project_id()
    try:
        credentials, _adc_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/bigquery.readonly",
                    "https://www.googleapis.com/auth/cloud-platform"])
    except Exception as exc:  # DefaultCredentialsError
        raise GA4ConnectorError(explain_error(exc)) from exc
    return bigquery.Client(project=project, credentials=credentials, location=BQ_LOCATION)


def dry_run(client, sql: str, params: list | None = None) -> int:
    """Return the estimated bytes processed without running the query."""
    from google.cloud import bigquery

    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=params or [])
    try:
        job = client.query(sql, job_config=cfg, location=BQ_LOCATION)
    except Exception as exc:
        raise GA4ConnectorError(explain_error(exc)) from exc
    return int(job.total_bytes_processed or 0)


def run_query(client, sql: str, max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED,
              params: list | None = None, as_arrow: bool = False):
    """Dry-run, enforce the byte limit, then execute with maximum_bytes_billed set.

    Returns (result, QueryStats); result is a pandas DataFrame or a pyarrow Table.
    """
    from google.cloud import bigquery

    estimate = dry_run(client, sql, params)
    if estimate > max_bytes_billed:
        raise GA4ConnectorError(
            f"Estimated scan {human_bytes(estimate)} exceeds the limit {human_bytes(max_bytes_billed)}. "
            "Query not run. Narrow the date range or raise the limit deliberately.")
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=max_bytes_billed, query_parameters=params or [])
    try:
        job = client.query(sql, job_config=cfg, location=BQ_LOCATION)
        rows = job.result()
        # The BigQuery Storage Read API speeds up large downloads; fall back to REST if unavailable.
        try:
            import google.cloud.bigquery_storage  # noqa: F401
            use_storage = rows.total_rows is not None and rows.total_rows > 50_000
        except ImportError:
            use_storage = False
        result = rows.to_arrow(create_bqstorage_client=use_storage)
        if not as_arrow:
            result = result.to_pandas()
    except Exception as exc:
        raise GA4ConnectorError(explain_error(exc)) from exc
    return result, QueryStats(estimate, job.total_bytes_processed, job.total_bytes_billed, job.job_id, job.cache_hit)


def verify_access(client, max_bytes_billed: int = 50 * 1024**2) -> dict[str, Any]:
    """Cheapest end-to-end check: credentials work, jobs can be created, the public table is readable.

    COUNT(*) on a single date shard is answered from table metadata (0 bytes billed).
    """
    sql = f"SELECT COUNT(*) AS n_events FROM `{GA4_DATASET}.events_20210131`"
    df, stats = run_query(client, sql, max_bytes_billed=max_bytes_billed)
    return {"project": client.project, "location": BQ_LOCATION, "n_events_20210131": int(df.iloc[0, 0]),
            **stats.as_dict()}


def main() -> int:
    import json

    try:
        client = get_client()
        print(json.dumps(verify_access(client), indent=2, default=str))
    except GA4ConnectorError as exc:
        print(f"BigQuery check failed:\n{exc}")
        return 1
    print("BigQuery access verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

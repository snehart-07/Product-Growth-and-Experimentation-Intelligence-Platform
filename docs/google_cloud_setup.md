# Google Cloud setup (GA4 source only)

Google Cloud is only needed for Source B. Synthetic generation, the local SQL pipeline and all offline tests work without it.

Dataset: [GA4 BigQuery ecommerce sample](https://developers.google.com/analytics/bigquery/web-ecommerce-demo-dataset). Table: `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, covering 2020-11-01 to 2021-01-31, location `US`.

## 1. Authenticate with Application Default Credentials

```bash
gcloud auth login                         # CLI identity (if not already)
gcloud auth application-default login     # ADC used by the Python client
```

ADC is written to `~/.config/gcloud/application_default_credentials.json`, outside the repo. Do not copy it into the project. PulseIQ never asks for API keys, service-account key files or access tokens.

## 2. Choose a project for query jobs

Public data is free to read, but query jobs run in your project and count against its quota. The BigQuery Sandbox or the free tier is enough.

```bash
gcloud projects list
export GOOGLE_CLOUD_PROJECT=<your-project-id>
gcloud services enable bigquery.googleapis.com --project "$GOOGLE_CLOUD_PROJECT"
```

Your account needs `roles/bigquery.jobUser` (or BigQuery User) on that project. Add the `export` line to `~/.zshrc` if you want it to persist.

## 3. Verify, inspect, estimate, extract

```bash
make ga4-verify     # COUNT(*) on one shard: answered from metadata, 0 bytes billed
make ga4-schema     # lists shards, flattens schema, checks required fields -> data/metadata/ga4_schema.json
python -m src.connectors.ga4_schema_inspector --param-keys   # optional event_params profile (small query)
make ga4-estimate   # 1-day validation query + dry-run estimate; downloads nothing
make ga4-extract    # default 20201101..20201103 -> data/raw/ga4/events.parquet
python -m src.connectors.ga4_extract --start 20201101 --end 20201130   # wider, deliberately
```

## Cost controls

- Every query is dry-run first and refused if the estimate is above the limit.
- `maximum_bytes_billed` is set on every job as a server-side cap. The default is 2 GiB. Change it with `--max-bytes-billed` or `PULSEIQ_MAX_BYTES_BILLED`.
- `_TABLE_SUFFIX BETWEEN '<start>' AND '<end>'` limits the scan to the requested shards. Dates are validated as `YYYYMMDD` inside the sample range before they are inlined.
- No `SELECT *`. Only listed columns and sub-fields are read. Note that `event_params` is scanned in full whenever any key is extracted.
- Extracts are cached locally. Re-running with the same range reuses the Parquet file unless you pass `--refresh`.
- Nothing is written back to BigQuery.
- The actual bytes processed and billed are recorded in `data/metadata/ga4_extract_metadata.json`.

## Troubleshooting

| Message | Fix |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT is not set` | `export GOOGLE_CLOUD_PROJECT=...` |
| `No Application Default Credentials found` | `gcloud auth application-default login` |
| `could not be refreshed` / `invalid_grant` | rerun the ADC login |
| `cannot create query jobs` | grant `roles/bigquery.jobUser` on the project |
| `BigQuery API is not enabled` | `gcloud services enable bigquery.googleapis.com` |
| `exceeds the limit` / bytes billed | narrow the date range or raise the limit deliberately |
| `quota or rate limit` | wait, narrow the range, or use the cached extract |

## Integration tests

```bash
pytest -m integration
```

These tests are skipped automatically when `GOOGLE_CLOUD_PROJECT` or ADC is missing.

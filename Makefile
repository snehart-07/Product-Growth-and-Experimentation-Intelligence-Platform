PY := .venv/bin/python

.PHONY: install gcloud-auth ga4-verify ga4-schema ga4-extract models checks public-data analysis memo tableau all test test-integration

install:          ## venv + pinned dependencies + notebook kernel
	python3 -m venv .venv && $(PY) -m pip install --upgrade pip && $(PY) -m pip install -r requirements.txt
	$(PY) -m ipykernel install --prefix .venv --name pulseiq --display-name "GrowthLab (.venv)"

gcloud-auth:      ## Application Default Credentials (no keys stored in the repo)
	gcloud auth application-default login
	gcloud auth application-default set-quota-project $${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}

ga4-verify:       ## 0-byte access check
	$(PY) -m src.connectors.bigquery_client
ga4-schema:       ## shards + schema check
	$(PY) -m src.connectors.ga4_schema_inspector
ga4-extract:      ## full 92-day sample (~2.2 GiB scanned; BigQuery free tier covers 1 TiB/month)
	$(PY) -m src.connectors.ga4_extract --start 20201101 --end 20210131 --max-bytes-billed 3221225472

models:           ## GA4 SQL models in DuckDB
	$(PY) -m src.pipeline.run_sql --source ga4
checks:           ## SQL data-quality assertions (must return zero rows)
	$(PY) -m src.quality.checks --source ga4

public-data:      ## Cookie Cats + Hillstrom experiment CSVs
	$(PY) -m src.connectors.public_datasets

analysis:         ## statistics -> reports/results/*.json + reports/figures
	$(PY) -m src.analysis.funnel_cohort
	$(PY) -m src.analysis.retention_drivers
	$(PY) -m src.analysis.cookie_cats
	$(PY) -m src.analysis.hillstrom
	$(PY) -m src.analysis.simulation_study

memo:             ## one-page decision memo + ER diagram
	$(PY) -m src.reporting.build_reports
tableau:          ## validated Tableau exports -> exports/tableau/
	$(PY) scripts/export_tableau_data.py

all: models checks public-data analysis memo tableau

test:
	$(PY) -m pytest -q
test-integration: ## BigQuery tests (needs ADC + GOOGLE_CLOUD_PROJECT)
	$(PY) -m pytest -q -m integration

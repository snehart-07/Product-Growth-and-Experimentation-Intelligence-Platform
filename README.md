# GrowthLab: Product Growth & Experimentation Analytics

*SQL · Python · DuckDB · BigQuery · Tableau Public*

Where does an online store lose new visitors, who comes back, and which tested changes are worth shipping? This repository holds the analysis pipeline and validated, Tableau-ready data behind an interactive Tableau Public report.

**Tableau Public report:** *(add your link here after publishing)*

## Headline findings

| Area | Finding | Evidence |
| --- | --- | --- |
| Funnel | **79%** of 238,621 new visitors leave before viewing a product. Only 0.75% buy within 7 days. | Strict-order funnel, Wilson 95% CIs. No source, device or country differs after Holm correction. |
| Retention | **7.9%** return within 7 days (95% CI 7.7–8.0%) and 10.6% within 30 days. December holiday cohorts return least. | Weekly cohorts. Unobserved cells are masked, never zero. |
| Drivers | First-day page depth has the strongest positive link to returning (OR 1.62 per SD, CI 1.57–1.68). | Logistic regression. **Association, not causation.** |
| Game A/B test | Moving the Cookie Cats gate to level 40 **lowered** day-7 retention by **0.82 pts** (95% CI −1.33 to −0.31). Decision: **don't ship**. | 90,189 randomized players. Day-1 guardrail does not pass. Revenue not measured. |
| Email A/B test | The men's email raised purchases from 0.57% to 1.25% (+0.68 pts, CI 0.50–0.86). The women's email works mainly for new customers. Decision: **ship**. | 64,000 randomized customers, Holm-corrected. 17 of 44 subgroups pass the strict test. |

**Data mapping:** the GA4 sample has no sign-ups, so `first_visit` stands in for sign-up, `purchase` for conversion and return visits for retention.

## Data

| Source | Nature | Used for |
| --- | --- | --- |
| [GA4 obfuscated e-commerce sample](https://developers.google.com/analytics/bigquery/web-ecommerce-demo-dataset) (Google Merchandise Store, 2020-11-01 to 2021-01-31, 4.3M events) | Observational, obfuscated by Google | Funnel, retention, behaviour |
| [Cookie Cats A/B test](https://www.kaggle.com/datasets/yufengsui/mobile-games-ab-testing) | Randomized | Game experiment |
| [Hillstrom MineThatData e-mail test](https://blog.minethatdata.com/2008/03/minethatdata-e-mail-analytics-and-data.html) (2008) | Randomized | Email experiment |

Raw data is **not** committed. The scripts download or extract it. The committed `exports/tableau/` tables are aggregates derived from these public sources. Check each source's terms before reusing them.

## Repository

```
src/connectors/     BigQuery (ADC, dry-run + byte cap) and public-dataset downloaders
sql/ga4/            numbered DuckDB models: staging → sessions/users/items → marts
src/quality/        SQL assertions that must return zero rows (exported to sql/tests/)
src/analysis/       funnel & cohorts, retention drivers, Cookie Cats, Hillstrom, simulation study
src/reporting/      Tableau export + 259-check validator, decision memo
scripts/            export_tableau_data.py
exports/tableau/    Tableau-ready CSVs (one per table grain)
reports/            published statistics (results/*.json) and figures
notebooks/          five analysis notebooks with outputs
docs/               metric definitions, pre-analysis plans, causation note
memo/               one-page decision memo (PDF)
tests/              offline unit, data-quality, analysis and export tests; BigQuery integration tests
```

## Build the Tableau report

You don't need to run anything to build the report. The validated tables are already in `exports/tableau/csv/`.

1. In Tableau Public Desktop, choose **Connect → Text file** and open the CSVs in `exports/tableau/csv/`.
2. Keep one data source per table grain. Don't join tables with different grains.
3. Rates on the store tables are `SUM(numerator) / SUM(denominator)`, so every filter recalculates correctly.

## Reproduce from scratch

```bash
make install
export GOOGLE_CLOUD_PROJECT=<your-project-id>   # project that runs the BigQuery query jobs
make gcloud-auth                                # Application Default Credentials
make ga4-verify ga4-schema ga4-extract          # ~2.2 GiB scanned, within the BigQuery free tier
make all                                        # models → checks → experiment data → analysis → memo → Tableau export
make test                                       # offline tests (BigQuery tests skip without credentials)
```

Always use `make` or `.venv/bin/python`. A bare `pytest` may pick up a different Python, such as Anaconda.

## Method notes

- Metric definitions and both pre-analysis plans were written before the statistics were computed. These are retrospective analyses of public data, so the plans are dated documents, not true pre-registrations.
- Every rate carries a Wilson 95% interval and every difference a Newcombe interval (bootstrap for spend). Holm correction applies whenever many groups are compared. Experiments are checked for sample-ratio mismatch and power.
- Store results are **observational**. They show where visitors leave and what goes with returning, not why. Only the two randomized experiments support causal claims, and only for the assigned treatment in that population and period. See [`docs/correlation_vs_causation.md`](docs/correlation_vs_causation.md).
- The simulation study (A/A tests, peeking, multiple comparisons, power) validates the methods and makes no business claims.

## Limitations

- The GA4 sample is obfuscated, a visitor is a browser cookie rather than a person, and it covers about three months, so D30 return exists only for early cohorts.
- Cookie Cats has no revenue, no D30 retention and no pre-treatment covariates.
- Hillstrom is a 2008 marketing test with a two-week window.

# Data-quality rules (GA4)

Every rule is a SQL query in `src/quality/checks.py` that must return zero rows. Run them with `make checks`; the SQL is exported to `sql/tests/assertions.sql`. Negative fixtures in `tests/test_ga4_transformations.py` prove each raw rule catches a deliberately broken record.

## GA4

| Rule | Meaning |
| --- | --- |
| events_not_null_keys | user_pseudo_id, timestamp, name present (negative fixture: NULL user) |
| events_date_matches_timestamp | event_date within 1 day of the UTC timestamp, allowing for time zone (negative: shifted date) |
| events_inside_extract_range | no rows past the extract end (negative: out-of-range date) |
| stg_events_row_count_matches_raw | normalization keeps every row |
| stg_events_unique_event_row_id | surrogate key unique |
| event_item_summary_no_row_multiplication | joining item aggregates keeps the event count |
| event_item_summary_quantity_reconciles | sum of list lengths = item rows = aggregated item rows |
| sessions_belong_to_one_user | |
| users_one_row_per_user / funnel_one_row_per_user | |
| funnel_strict_flags_monotonic | a strict stage implies the previous strict stage |
| new_users_one_row_each_and_subset_of_users | new users are unique and are a subset of users |
| new_user_marts_one_row_per_new_user | funnel and return marts have one row per new user |
| new_funnel_strict_flags_monotonic / new_funnel_inside_7_day_window | funnel order and window respected |
| retention_features_no_post_24h_leakage | an independent recount shows the features use only [t0, t0+24h) |
| cohort_weekly_active_not_above_size | |
| return_window_flags_nested | exact-day ⊆ window, D1 ⊆ D7 ⊆ D30 |

On the full 92-day extract, all 18 GA4 assertions pass. The SQL for every rule is exported to `sql/tests/assertions.sql`.

Missing or placeholder values are covered in `test_missing_optional_values_do_not_break_models` (NULL items, session ID, device, geo and first touch) and in `test_placeholders_bucketed`.

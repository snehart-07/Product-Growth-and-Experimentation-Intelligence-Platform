# GA4 data dictionary (normalized)

Source: `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*` (real, obfuscated, observational). Extract schema version `1.0.0`.

Status: verified against live BigQuery on 2026-10-16. The schema inspector found 92 shards (20201101..20210131) and 108 schema fields, with no required fields missing. The full 92-day extract has 4,295,584 rows, scanned 2.2 GiB, and passes all GA4 assertions.

## Extract: `data/raw/ga4/events.parquet` (event grain)

The extract has one row per source event row. Scalar `event_params` are read with `(SELECT ... FROM UNNEST(event_params) WHERE key = ... LIMIT 1)`, which cannot multiply rows. `items` stays nested.

| Column | GA4 source | Notes |
| --- | --- | --- |
| event_date | event_date | `YYYYMMDD` string, property time zone |
| event_timestamp | event_timestamp | microseconds since epoch, UTC |
| event_name | event_name | |
| event_bundle_sequence_id | event_bundle_sequence_id | |
| user_pseudo_id | user_pseudo_id | cookie/device ID. Not a person or an account |
| user_first_touch_timestamp | user_first_touch_timestamp | µs |
| ga_session_id, ga_session_number | event_params (int) | session key = user_pseudo_id + ga_session_id |
| page_location, page_title, page_referrer | event_params (string) | |
| engagement_time_msec | event_params (int) | |
| event_source, event_medium, event_campaign | event_params (string) | event-scoped traffic, when present |
| search_term | event_params (string) | |
| session_engaged | event_params (string or int → string) | |
| device_category, operating_system, browser | device.* | |
| country, region, city | geo.* | |
| first_touch_source / medium / campaign | traffic_source.* | the user's first touch, not the session's |
| transaction_id, purchase_revenue_in_usd, total_item_quantity, unique_items | ecommerce.* | |
| items | ARRAY<STRUCT<item_id, item_name, item_category, price_in_usd, quantity, item_revenue_in_usd, item_index>> | nested |

## Local models (`sql/ga4/`)

| Table | Grain | Purpose |
| --- | --- | --- |
| stg_events | event row (`event_row_id`) | typed, placeholder-normalized, flags |
| stg_items | (event_row_id, item_index) | item grain only |
| event_item_summary | event_row_id with ≥ 1 item | items aggregated per event |
| stg_events_with_items | event row | events + item aggregates. Row count = stg_events |
| sessions | session_key | start, end, duration, page views, engagement, landing page |
| users | user_pseudo_id | first seen, first-touch channel, first device, country, new-in-window flag |
| funnel_users | user_pseudo_id | view_item → add_to_cart → begin_checkout → purchase, in any order and in strict order |
| user_day_activity | user × event_date | |
| cohort_retention | first_visit date × channel × device × age_day | with `is_fully_observed` |
| data_quality_profile | metric | placeholder, duplicate and missing-session shares |
| user_flags | user_pseudo_id | suspected bot/internal traffic (> 500 events per day or > 100 sessions) |
| new_users | user_pseudo_id (first_visit in window) | t0, d0, cohort week, segments, censoring flags |
| user_return_flags | new user | D1/D7/D30 window and exact-day return |
| funnel_new_users | new user | stages inside [t0, t0+7d), strict and any order, time between stages |
| cohort_retention_weekly | cohort week × channel × device × age week | with `is_fully_observed` |
| retention_features | new user | first-24h features + outcome in [t0+24h, t0+31d) |

## Placeholder handling

The sample uses `<Other>`, `NULL` and `''` as placeholders. GA itself also emits `(not set)` and `(data deleted)`.

- `*_clean` columns (page fields, transaction_id, item_id/name) map all of these to NULL.
- Segment columns (country, device, channel, source/medium, item_category) map them to explicit buckets: `(missing)` for NULL, `''`, `NULL` and `(not set)`, and `(obfuscated)` for `<Other>` and `(data deleted)`. Excluded users stay visible.
- Flags: `is_country_placeholder`, `is_device_placeholder`, `is_channel_placeholder`, `is_missing_session_id`, `is_potential_duplicate` (same user, timestamp, name, bundle, session and page), `is_purchase_without_txn_id`.

## First-touch channel grouping

Grouping uses `traffic_source.medium` and `source`: organic → Organic Search; cpc/ppc/paidsearch → Paid Search; referral → Referral; (none) or (direct) → Direct; email → Email; display/cpm/banner → Display; social → Social; anything else → Other.

## What GA4 does not contain here

There are no sign-ups, accounts, workspaces, projects or subscriptions. `first_visit` is only a proxy for acquisition. With the default 3-day extract, retention beyond day 2 is censored. The analyses use the full 92-day extract (`make ga4-extract-full`), and even then D30 is only observable for first visits up to 2021-01-01.

-- Source: GA4 public sample (observational)
-- Grain: one row per user_pseudo_id (a browser/device cookie, NOT a person or account).
-- is_new_in_window = the user's first_visit event falls inside the extract.
-- There is no sign-up event: first_visit is only a proxy for acquisition.
CREATE OR REPLACE TABLE users AS
SELECT
    user_pseudo_id,
    min(event_ts)                                                   AS first_seen_in_extract,
    min(user_first_touch_ts)                                        AS first_touch_ts,
    min(event_ts) FILTER (WHERE event_name = 'first_visit')         AS first_visit_ts,
    bool_or(event_name = 'first_visit')                             AS is_new_in_window,
    arg_min(first_touch_channel, event_ts)                          AS first_touch_channel,
    arg_min(first_touch_source, event_ts)                           AS first_touch_source,
    arg_min(first_touch_medium, event_ts)                           AS first_touch_medium,
    arg_min(device_category, event_ts)                              AS first_device_category,
    arg_min(country, event_ts)                                      AS country,
    count(*)                                                        AS n_events,
    count(DISTINCT session_key)                                     AS n_sessions,
    count(*) FILTER (WHERE event_name = 'purchase')                 AS n_purchase_events,
    count(DISTINCT transaction_id) FILTER (WHERE event_name = 'purchase') AS n_transactions,
    count(*) = 1                                                    AS is_single_event_user
FROM stg_events
GROUP BY user_pseudo_id;

-- Source: GA4 public sample (observational)
-- Grain: one row per session_key (user_pseudo_id + ga_session_id).
-- Events with no ga_session_id are excluded here and counted in data_quality_profile.
-- Sessions that started before the extract window are truncated at its start.
CREATE OR REPLACE TABLE sessions AS
SELECT
    session_key,
    any_value(user_pseudo_id)                                        AS user_pseudo_id,
    any_value(ga_session_id)                                         AS ga_session_id,
    max(ga_session_number)                                           AS ga_session_number,
    min(event_ts)                                                    AS session_start,
    max(event_ts)                                                    AS session_end,
    date_diff('millisecond', min(event_ts), max(event_ts)) / 1000.0  AS duration_seconds,
    count(*)                                                         AS n_events,
    count(*) FILTER (WHERE event_name = 'page_view')                 AS page_views,
    sum(coalesce(engagement_time_msec, 0)) / 1000.0                  AS engagement_seconds,
    coalesce(bool_or(session_engaged), false)                        AS is_engaged,
    arg_min(page_location, event_ts) FILTER (WHERE event_name = 'page_view') AS landing_page,
    arg_min(device_category, event_ts)                               AS device_category,
    arg_min(country, event_ts)                                       AS country,
    bool_or(event_name = 'session_start')                            AS has_session_start_event,
    bool_or(event_name = 'purchase')                                 AS has_purchase
FROM stg_events
WHERE session_key IS NOT NULL
GROUP BY session_key;

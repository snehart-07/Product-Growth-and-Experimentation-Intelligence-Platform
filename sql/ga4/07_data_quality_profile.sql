-- Source: GA4 public sample (observational)
-- One row per metric; descriptive data-quality profile of the extract.
CREATE OR REPLACE TABLE data_quality_profile AS
SELECT 'events' AS metric, count(*)::DOUBLE AS value FROM stg_events
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'sessions', count(*) FROM sessions
UNION ALL SELECT 'share_events_missing_session_id', avg(is_missing_session_id::INT) FROM stg_events
UNION ALL SELECT 'share_events_potential_duplicate', avg(is_potential_duplicate::INT) FROM stg_events
UNION ALL SELECT 'share_events_country_placeholder', avg(is_country_placeholder::INT) FROM stg_events
UNION ALL SELECT 'share_events_device_placeholder', avg(is_device_placeholder::INT) FROM stg_events
UNION ALL SELECT 'share_users_channel_placeholder', avg((first_touch_channel IN ('(missing)', '(obfuscated)'))::INT) FROM users
UNION ALL SELECT 'share_users_single_event', avg(is_single_event_user::INT) FROM users
UNION ALL SELECT 'share_users_new_in_window', avg(is_new_in_window::INT) FROM users
UNION ALL SELECT 'purchase_events_without_transaction_id', sum(is_purchase_without_txn_id::INT) FROM stg_events
UNION ALL SELECT 'share_sessions_without_session_start_event', avg((NOT has_session_start_event)::INT) FROM sessions;

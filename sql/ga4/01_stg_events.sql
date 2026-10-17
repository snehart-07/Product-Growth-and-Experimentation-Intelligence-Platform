-- Source: GA4 public sample (REAL, OBFUSCATED, OBSERVATIONAL ecommerce web events)
-- Grain: one row per extracted event row. Row count must equal raw_events.
-- Placeholder handling: the public sample uses '<Other>', NULL and '' as
-- placeholders (plus GA's own '(not set)' / '(data deleted)'). *_clean columns
-- map these to NULL; segment columns map them to explicit buckets so they are
-- visible rather than silently dropped.

CREATE OR REPLACE MACRO ga4_is_placeholder(x) AS
    x IS NULL OR trim(CAST(x AS VARCHAR)) IN ('', '<Other>', '(not set)', '(data deleted)', 'NULL');

CREATE OR REPLACE MACRO ga4_clean(x) AS
    CASE WHEN ga4_is_placeholder(x) THEN NULL ELSE CAST(x AS VARCHAR) END;

CREATE OR REPLACE MACRO ga4_bucket(x) AS
    CASE WHEN x IS NULL OR trim(CAST(x AS VARCHAR)) IN ('', 'NULL', '(not set)') THEN '(missing)'
         WHEN trim(CAST(x AS VARCHAR)) IN ('<Other>', '(data deleted)') THEN '(obfuscated)'
         ELSE CAST(x AS VARCHAR) END;

-- Default channel grouping from the user's FIRST-TOUCH traffic_source.
CREATE OR REPLACE MACRO ga4_channel(src, med) AS
    CASE
        WHEN med IS NULL OR trim(med) IN ('', 'NULL', '(not set)')  THEN '(missing)'
        WHEN med IN ('<Other>', '(data deleted)')                    THEN '(obfuscated)'
        WHEN med = 'organic'                                         THEN 'Organic Search'
        WHEN med IN ('cpc', 'ppc', 'paidsearch')                     THEN 'Paid Search'
        WHEN med = 'referral'                                        THEN 'Referral'
        WHEN med = '(none)' AND src = '(direct)'                     THEN 'Direct'
        WHEN med = 'email'                                           THEN 'Email'
        WHEN med IN ('display', 'cpm', 'banner')                     THEN 'Display'
        WHEN med IN ('social', 'social-network', 'sm')               THEN 'Social'
        WHEN med = '(none)'                                          THEN 'Direct'
        ELSE 'Other'
    END;

CREATE OR REPLACE TABLE stg_events_nested AS
SELECT
    ROW_NUMBER() OVER (ORDER BY user_pseudo_id, event_timestamp, event_name, event_bundle_sequence_id,
                                ga_session_id, page_location)                 AS event_row_id,
    strptime(event_date, '%Y%m%d')::DATE                                     AS event_date,
    make_timestamp(event_timestamp)                                          AS event_ts,
    event_timestamp                                                          AS event_timestamp_us,
    event_name,
    event_bundle_sequence_id,
    user_pseudo_id,
    CASE WHEN user_first_touch_timestamp IS NOT NULL
         THEN make_timestamp(user_first_touch_timestamp) END                 AS user_first_touch_ts,
    ga_session_id,
    CASE WHEN ga_session_id IS NOT NULL
         THEN user_pseudo_id || '-' || CAST(ga_session_id AS VARCHAR) END    AS session_key,
    ga_session_number,
    ga4_clean(page_location)                                                 AS page_location,
    ga4_clean(page_title)                                                    AS page_title,
    ga4_clean(page_referrer)                                                 AS page_referrer,
    engagement_time_msec,
    CASE WHEN session_engaged IN ('1', 'true') THEN true
         WHEN session_engaged IN ('0', 'false') THEN false END               AS session_engaged,
    ga4_clean(search_term)                                                   AS search_term,
    ga4_bucket(device_category)                                              AS device_category,
    ga4_bucket(operating_system)                                             AS operating_system,
    ga4_bucket(browser)                                                      AS browser,
    ga4_bucket(country)                                                      AS country,
    ga4_bucket(region)                                                       AS region,
    ga4_bucket(city)                                                         AS city,
    ga4_bucket(first_touch_source)                                           AS first_touch_source,
    ga4_bucket(first_touch_medium)                                           AS first_touch_medium,
    ga4_bucket(first_touch_campaign)                                         AS first_touch_campaign,
    ga4_channel(first_touch_source, first_touch_medium)                      AS first_touch_channel,
    ga4_bucket(event_source)                                                 AS event_source,
    ga4_bucket(event_medium)                                                 AS event_medium,
    ga4_clean(transaction_id)                                                AS transaction_id,
    purchase_revenue_in_usd,
    total_item_quantity,
    unique_items,
    coalesce(len(items), 0)                                                  AS item_count,
    items,
    -- Potential duplicate: same user, timestamp, name, bundle, session and page.
    count(*) OVER (PARTITION BY user_pseudo_id, event_timestamp, event_name, event_bundle_sequence_id,
                                ga_session_id, page_location) > 1             AS is_potential_duplicate,
    ga_session_id IS NULL                                                    AS is_missing_session_id,
    ga4_is_placeholder(country)                                              AS is_country_placeholder,
    ga4_is_placeholder(device_category)                                      AS is_device_placeholder,
    ga4_is_placeholder(first_touch_medium)                                   AS is_channel_placeholder,
    event_name = 'purchase' AND ga4_is_placeholder(transaction_id)           AS is_purchase_without_txn_id
FROM raw_events;

CREATE OR REPLACE TABLE stg_events AS
SELECT * EXCLUDE (items) FROM stg_events_nested;

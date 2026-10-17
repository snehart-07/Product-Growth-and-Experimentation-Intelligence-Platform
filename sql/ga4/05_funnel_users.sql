-- Source: GA4 public sample (observational ecommerce funnel)
-- Grain: one row per user_pseudo_id.
-- Stages use only events that exist in the GA4 ecommerce export:
--   view_item -> add_to_cart -> begin_checkout -> purchase
-- any_*    : the user fired the event at any time in the extract (any order)
-- strict_* : each stage occurred at or after the previous stage's first
--            qualifying timestamp (strict order)
CREATE OR REPLACE TABLE funnel_users AS
WITH firsts AS (
    SELECT user_pseudo_id,
        min(event_ts) FILTER (WHERE event_name = 'first_visit')    AS ts_first_visit,
        min(event_ts) FILTER (WHERE event_name = 'session_start')  AS ts_session_start,
        min(event_ts) FILTER (WHERE event_name = 'view_item')      AS ts_view_item,
        min(event_ts) FILTER (WHERE event_name = 'add_to_cart')    AS ts_add_to_cart_any,
        min(event_ts) FILTER (WHERE event_name = 'begin_checkout') AS ts_begin_checkout_any,
        min(event_ts) FILTER (WHERE event_name = 'purchase')       AS ts_purchase_any
    FROM stg_events GROUP BY user_pseudo_id
),
atc AS (
    SELECT f.user_pseudo_id, min(e.event_ts) AS ts
    FROM firsts f JOIN stg_events e
      ON e.user_pseudo_id = f.user_pseudo_id AND e.event_name = 'add_to_cart' AND e.event_ts >= f.ts_view_item
    GROUP BY f.user_pseudo_id
),
bc AS (
    SELECT a.user_pseudo_id, min(e.event_ts) AS ts
    FROM atc a JOIN stg_events e
      ON e.user_pseudo_id = a.user_pseudo_id AND e.event_name = 'begin_checkout' AND e.event_ts >= a.ts
    GROUP BY a.user_pseudo_id
),
pur AS (
    SELECT b.user_pseudo_id, min(e.event_ts) AS ts
    FROM bc b JOIN stg_events e
      ON e.user_pseudo_id = b.user_pseudo_id AND e.event_name = 'purchase' AND e.event_ts >= b.ts
    GROUP BY b.user_pseudo_id
)
SELECT
    u.user_pseudo_id, u.is_new_in_window, u.first_touch_channel, u.first_device_category, u.country,
    CAST(u.first_seen_in_extract AS DATE)       AS first_seen_date,
    f.ts_first_visit, f.ts_session_start, f.ts_view_item,
    f.ts_add_to_cart_any, f.ts_begin_checkout_any, f.ts_purchase_any,
    atc.ts AS ts_add_to_cart_strict, bc.ts AS ts_begin_checkout_strict, pur.ts AS ts_purchase_strict,
    f.ts_view_item IS NOT NULL                  AS any_view_item,
    f.ts_add_to_cart_any IS NOT NULL            AS any_add_to_cart,
    f.ts_begin_checkout_any IS NOT NULL         AS any_begin_checkout,
    f.ts_purchase_any IS NOT NULL               AS any_purchase,
    f.ts_view_item IS NOT NULL                  AS strict_view_item,
    atc.ts IS NOT NULL                          AS strict_add_to_cart,
    bc.ts IS NOT NULL                           AS strict_begin_checkout,
    pur.ts IS NOT NULL                          AS strict_purchase,
    date_diff('second', f.ts_view_item, atc.ts) AS sec_view_to_cart,
    date_diff('second', atc.ts, bc.ts)          AS sec_cart_to_checkout,
    date_diff('second', bc.ts, pur.ts)          AS sec_checkout_to_purchase
FROM users u
JOIN firsts f USING (user_pseudo_id)
LEFT JOIN atc USING (user_pseudo_id)
LEFT JOIN bc  USING (user_pseudo_id)
LEFT JOIN pur USING (user_pseudo_id);

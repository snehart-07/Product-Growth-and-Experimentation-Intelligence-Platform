-- Source: GA4 public sample (observational). Implements docs/metric_definitions.md.
-- user_flags          : suspected bot/internal traffic (> 500 events on a day OR > 100 sessions)
-- new_users           : users whose first_visit is in the sample; t0, d0, cohort week, segments, observability
-- user_return_flags   : window-based and exact-day return flags with censoring flags
-- funnel_new_users    : stage timestamps inside [t0, t0 + 7 days), any order and strict order
-- cohort_retention_weekly : cohort week x channel x device x age week, censored cells flagged
-- retention_features  : first-24-hour behaviours + return outcome in [t0+24h, t0+31d)

CREATE OR REPLACE TABLE user_flags AS
WITH daily AS (
    SELECT user_pseudo_id, max(events) AS max_daily_events FROM user_day_activity GROUP BY 1
)
SELECT u.user_pseudo_id, d.max_daily_events, u.n_sessions,
       (d.max_daily_events > 500 OR u.n_sessions > 100) AS is_suspected_bot
FROM users u JOIN daily d USING (user_pseudo_id);

CREATE OR REPLACE TABLE new_users AS
WITH fv AS (
    SELECT user_pseudo_id,
           min(event_ts)                    AS t0,
           arg_min(event_date, event_ts)    AS d0
    FROM stg_events WHERE event_name = 'first_visit'
    GROUP BY 1
),
p AS (SELECT observation_end AS end_ts, CAST(observation_end AS DATE) - 1 AS last_date FROM run_params)
SELECT
    fv.user_pseudo_id, fv.t0, fv.d0,
    CAST(floor(date_diff('day', DATE '2020-11-01', fv.d0) / 7) AS INTEGER)       AS cohort_week,
    DATE '2020-11-01' + CAST(7 * floor(date_diff('day', DATE '2020-11-01', fv.d0) / 7) AS INTEGER) AS cohort_week_start,
    u.first_touch_channel AS channel, u.first_device_category AS device, u.country,
    f.is_suspected_bot,
    fv.t0 + INTERVAL 24 HOUR <= p.end_ts   AS obs_24h,
    fv.t0 + INTERVAL 7 DAY   <= p.end_ts   AS obs_7d,
    fv.t0 + INTERVAL 31 DAY  <= p.end_ts   AS obs_31d,
    fv.d0 + 7  <= p.last_date              AS obs_d7,
    fv.d0 + 30 <= p.last_date              AS obs_d30
FROM fv
JOIN users u USING (user_pseudo_id)
JOIN user_flags f USING (user_pseudo_id)
CROSS JOIN p;

CREATE OR REPLACE TABLE user_return_flags AS
SELECT n.user_pseudo_id,
       coalesce(bool_or(a.event_date BETWEEN n.d0 + 1 AND n.d0 + 7), false)  AS return_d7,
       coalesce(bool_or(a.event_date BETWEEN n.d0 + 1 AND n.d0 + 30), false) AS return_d30,
       coalesce(bool_or(a.event_date = n.d0 + 7), false)                     AS return_d7_exact,
       coalesce(bool_or(a.event_date = n.d0 + 30), false)                    AS return_d30_exact,
       coalesce(bool_or(a.event_date = n.d0 + 1), false)                     AS return_d1
FROM new_users n
LEFT JOIN user_day_activity a ON a.user_pseudo_id = n.user_pseudo_id AND a.event_date > n.d0
GROUP BY n.user_pseudo_id;

CREATE OR REPLACE TABLE funnel_new_users AS
WITH w AS (
    SELECT e.user_pseudo_id, e.event_name, e.event_ts
    FROM stg_events e JOIN new_users n USING (user_pseudo_id)
    WHERE e.event_ts >= n.t0 AND e.event_ts < n.t0 + INTERVAL 7 DAY
      AND e.event_name IN ('view_item', 'add_to_cart', 'begin_checkout', 'purchase')
),
firsts AS (
    SELECT user_pseudo_id,
        min(event_ts) FILTER (WHERE event_name = 'view_item')      AS ts_view_item,
        min(event_ts) FILTER (WHERE event_name = 'add_to_cart')    AS ts_add_to_cart_any,
        min(event_ts) FILTER (WHERE event_name = 'begin_checkout') AS ts_begin_checkout_any,
        min(event_ts) FILTER (WHERE event_name = 'purchase')       AS ts_purchase_any
    FROM w GROUP BY 1
),
atc AS (SELECT f.user_pseudo_id, min(w.event_ts) ts FROM firsts f JOIN w USING (user_pseudo_id)
        WHERE w.event_name = 'add_to_cart' AND w.event_ts >= f.ts_view_item GROUP BY 1),
bc  AS (SELECT a.user_pseudo_id, min(w.event_ts) ts FROM atc a JOIN w USING (user_pseudo_id)
        WHERE w.event_name = 'begin_checkout' AND w.event_ts >= a.ts GROUP BY 1),
pur AS (SELECT b.user_pseudo_id, min(w.event_ts) ts FROM bc b JOIN w USING (user_pseudo_id)
        WHERE w.event_name = 'purchase' AND w.event_ts >= b.ts GROUP BY 1)
SELECT n.user_pseudo_id, n.t0, n.d0, n.cohort_week, n.cohort_week_start, n.channel, n.device, n.country,
       n.is_suspected_bot, n.obs_7d,
       f.ts_view_item, f.ts_add_to_cart_any, f.ts_begin_checkout_any, f.ts_purchase_any,
       atc.ts AS ts_add_to_cart_strict, bc.ts AS ts_begin_checkout_strict, pur.ts AS ts_purchase_strict,
       f.ts_view_item IS NOT NULL          AS any_view_item,
       f.ts_add_to_cart_any IS NOT NULL    AS any_add_to_cart,
       f.ts_begin_checkout_any IS NOT NULL AS any_begin_checkout,
       f.ts_purchase_any IS NOT NULL       AS any_purchase,
       f.ts_view_item IS NOT NULL          AS strict_view_item,
       atc.ts IS NOT NULL                  AS strict_add_to_cart,
       bc.ts IS NOT NULL                   AS strict_begin_checkout,
       pur.ts IS NOT NULL                  AS strict_purchase,
       date_diff('second', n.t0, f.ts_view_item)  AS sec_visit_to_view,
       date_diff('second', f.ts_view_item, atc.ts) AS sec_view_to_cart,
       date_diff('second', atc.ts, bc.ts)          AS sec_cart_to_checkout,
       date_diff('second', bc.ts, pur.ts)          AS sec_checkout_to_purchase
FROM new_users n
LEFT JOIN firsts f USING (user_pseudo_id)
LEFT JOIN atc USING (user_pseudo_id)
LEFT JOIN bc  USING (user_pseudo_id)
LEFT JOIN pur USING (user_pseudo_id);

CREATE OR REPLACE TABLE cohort_retention_weekly AS
WITH p AS (SELECT CAST(observation_end AS DATE) - 1 AS last_date FROM run_params),
base AS (SELECT * FROM new_users WHERE NOT is_suspected_bot),
sizes AS (SELECT cohort_week, cohort_week_start, channel, device, count(*) AS cohort_size FROM base GROUP BY ALL),
ages AS (SELECT range AS age_week FROM range(1, 13)),
act AS (
    SELECT b.cohort_week, b.channel, b.device,
           CAST(floor(date_diff('day', b.d0, a.event_date) / 7) AS INTEGER) AS age_week,
           count(DISTINCT b.user_pseudo_id) AS active_users
    FROM base b JOIN user_day_activity a ON a.user_pseudo_id = b.user_pseudo_id
    WHERE date_diff('day', b.d0, a.event_date) >= 7
    GROUP BY ALL
)
SELECT s.cohort_week, s.cohort_week_start, s.channel, s.device, g.age_week, s.cohort_size,
       coalesce(act.active_users, 0) AS active_users,
       -- the cohort's last possible d0 plus the end of week k must be observed
       least(s.cohort_week_start + 6, p.last_date) + CAST(7 * g.age_week + 6 AS INTEGER) <= p.last_date AS is_fully_observed
FROM sizes s CROSS JOIN ages g CROSS JOIN p
LEFT JOIN act ON act.cohort_week = s.cohort_week AND act.channel = s.channel AND act.device = s.device
             AND act.age_week = g.age_week;

CREATE OR REPLACE TABLE retention_features AS
WITH first24 AS (
    SELECT e.user_pseudo_id,
           count(*) FILTER (WHERE e.event_name = 'view_item')            AS product_views_24h,
           count(*) FILTER (WHERE e.event_name = 'add_to_cart')          AS add_to_cart_24h,
           count(*) FILTER (WHERE e.event_name = 'begin_checkout')       AS begin_checkout_24h,
           count(*) FILTER (WHERE e.event_name = 'purchase')             AS purchase_24h,
           count(*) FILTER (WHERE e.event_name = 'page_view')            AS page_views_24h,
           count(*) FILTER (WHERE e.event_name = 'view_search_results')  AS searches_24h,
           count(*) FILTER (WHERE e.event_name = 'view_promotion')       AS promo_views_24h,
           count(DISTINCT e.session_key)                                 AS sessions_24h,
           sum(coalesce(e.engagement_time_msec, 0)) / 1000.0             AS engagement_sec_24h,
           count(*)                                                      AS events_24h
    FROM stg_events e JOIN new_users n USING (user_pseudo_id)
    WHERE e.event_ts >= n.t0 AND e.event_ts < n.t0 + INTERVAL 24 HOUR
    GROUP BY 1
),
outcome AS (
    SELECT n.user_pseudo_id, count(e.event_ts) > 0 AS returned_30d_after_24h
    FROM new_users n
    LEFT JOIN stg_events e ON e.user_pseudo_id = n.user_pseudo_id
         AND e.event_ts >= n.t0 + INTERVAL 24 HOUR AND e.event_ts < n.t0 + INTERVAL 31 DAY
    GROUP BY 1
)
SELECT n.user_pseudo_id, n.t0, n.cohort_week, n.channel, n.device, n.country, n.is_suspected_bot, n.obs_31d,
       f.* EXCLUDE (user_pseudo_id),
       f.product_views_24h > 0 AND f.add_to_cart_24h > 0 AS activated_24h,
       o.returned_30d_after_24h
FROM new_users n
JOIN first24 f USING (user_pseudo_id)
JOIN outcome o USING (user_pseudo_id);

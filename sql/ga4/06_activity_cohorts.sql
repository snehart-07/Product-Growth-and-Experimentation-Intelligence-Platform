-- Source: GA4 public sample (observational)
-- user_day_activity grain: user_pseudo_id x event_date.
-- cohort_retention grain: first_visit_date x first_touch_channel x first_device_category x age_day.
-- Only users whose first_visit is inside the extract form cohorts. A return on
-- age_day k = any event on first_visit_date + k (calendar day, property time zone).
-- is_fully_observed = (cohort date + k) is inside the extract. With the default
-- 3-day extract almost everything beyond day 2 is censored; widen the extract
-- for meaningful D7 analysis, and note D30 is only possible for early cohorts.
CREATE OR REPLACE TABLE user_day_activity AS
SELECT user_pseudo_id, event_date,
       count(*)                                         AS events,
       count(DISTINCT session_key)                      AS sessions,
       count(*) FILTER (WHERE event_name = 'purchase')  AS purchase_events
FROM stg_events
GROUP BY user_pseudo_id, event_date;

CREATE OR REPLACE TABLE cohort_retention AS
WITH params AS (SELECT CAST(observation_end AS DATE) AS end_excl FROM run_params),
cohort_users AS (
    SELECT user_pseudo_id, CAST(first_visit_ts AS DATE) AS cohort_date,
           first_touch_channel, first_device_category
    FROM users WHERE is_new_in_window
),
sizes AS (SELECT cohort_date, first_touch_channel, first_device_category, count(*) AS cohort_size
          FROM cohort_users GROUP BY ALL),
ages AS (SELECT range AS age_day FROM range(1, 31)),
active AS (
    SELECT c.cohort_date, c.first_touch_channel, c.first_device_category,
           date_diff('day', c.cohort_date, a.event_date) AS age_day,
           count(DISTINCT a.user_pseudo_id) AS active_users
    FROM cohort_users c JOIN user_day_activity a USING (user_pseudo_id)
    WHERE a.event_date > c.cohort_date
    GROUP BY ALL
)
SELECT s.cohort_date, s.first_touch_channel, s.first_device_category, g.age_day, s.cohort_size,
       coalesce(a.active_users, 0) AS active_users,
       s.cohort_date + CAST(g.age_day AS INTEGER) < p.end_excl AS is_fully_observed
FROM sizes s CROSS JOIN ages g CROSS JOIN params p
LEFT JOIN active a ON a.cohort_date = s.cohort_date AND a.first_touch_channel = s.first_touch_channel
                  AND a.first_device_category = s.first_device_category AND a.age_day = g.age_day;

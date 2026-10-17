-- Source: GA4 public sample (observational)
-- stg_items         grain: one row per (event_row_id, item_index). Item-level ONLY.
-- event_item_summary grain: one row per event_row_id that has >= 1 item.
-- stg_events_with_items: event grain (row count == stg_events), items aggregated
-- first so that the join can never multiply event rows.

CREATE OR REPLACE TABLE stg_items AS
WITH x AS (
    SELECT event_row_id, user_pseudo_id, event_name, event_ts, session_key, unnest(items) AS item
    FROM stg_events_nested
    WHERE item_count > 0
)
SELECT
    event_row_id, user_pseudo_id, event_name, event_ts, session_key,
    item.item_index                       AS item_index,
    ga4_clean(item.item_id)               AS item_id,
    ga4_clean(item.item_name)             AS item_name,
    ga4_bucket(item.item_category)        AS item_category,
    item.price_in_usd                     AS price_in_usd,
    item.quantity                         AS quantity,
    item.item_revenue_in_usd              AS item_revenue_in_usd
FROM x;

CREATE OR REPLACE TABLE event_item_summary AS
SELECT
    event_row_id,
    count(*)                              AS item_rows,
    count(DISTINCT item_id)               AS distinct_items,
    sum(quantity)                         AS item_quantity,
    sum(item_revenue_in_usd)              AS item_revenue_in_usd,
    count(*) FILTER (WHERE item_id IS NULL) AS items_missing_id
FROM stg_items
GROUP BY event_row_id;

CREATE OR REPLACE TABLE stg_events_with_items AS
SELECT e.*, s.item_rows, s.distinct_items, s.item_quantity, s.item_revenue_in_usd
FROM stg_events AS e
LEFT JOIN event_item_summary AS s USING (event_row_id);

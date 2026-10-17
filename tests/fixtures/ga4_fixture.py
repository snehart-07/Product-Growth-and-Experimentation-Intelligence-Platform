"""Hand-built rows in the exact shape of the GA4 extract (src/connectors/ga4_extract.py).

Covers: a full strict-order funnel, an out-of-order funnel, placeholder values
('<Other>', '', NULL, '(not set)'), a missing ga_session_id, an exact duplicate
event, multi-item events and a next-day return visit.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa

ITEM_TYPE = pa.struct([
    ("item_id", pa.string()), ("item_name", pa.string()), ("item_category", pa.string()),
    ("price_in_usd", pa.float64()), ("quantity", pa.int64()), ("item_revenue_in_usd", pa.float64()),
    ("item_index", pa.int64()),
])

SCHEMA = pa.schema([
    ("event_date", pa.string()), ("event_timestamp", pa.int64()), ("event_name", pa.string()),
    ("event_bundle_sequence_id", pa.int64()), ("user_pseudo_id", pa.string()),
    ("user_first_touch_timestamp", pa.int64()), ("ga_session_id", pa.int64()),
    ("ga_session_number", pa.int64()), ("page_location", pa.string()), ("page_title", pa.string()),
    ("page_referrer", pa.string()), ("engagement_time_msec", pa.int64()), ("event_source", pa.string()),
    ("event_medium", pa.string()), ("event_campaign", pa.string()), ("search_term", pa.string()),
    ("session_engaged", pa.string()), ("device_category", pa.string()), ("operating_system", pa.string()),
    ("browser", pa.string()), ("country", pa.string()), ("region", pa.string()), ("city", pa.string()),
    ("first_touch_source", pa.string()), ("first_touch_medium", pa.string()),
    ("first_touch_campaign", pa.string()), ("transaction_id", pa.string()),
    ("purchase_revenue_in_usd", pa.float64()), ("total_item_quantity", pa.int64()),
    ("unique_items", pa.int64()), ("items", pa.list_(ITEM_TYPE)),
])

OBSERVATION_END = datetime(2020, 11, 4)  # extract 20201101..20201103


def _us(day: int, hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(2020, 11, day, hh, mm, ss, tzinfo=timezone.utc).timestamp() * 1_000_000)


def _item(i, idx, price=10.0, qty=1, name=None):
    return {"item_id": f"SKU{i}", "item_name": name or f"Item {i}", "item_category": "Apparel",
            "price_in_usd": price, "quantity": qty, "item_revenue_in_usd": price * qty, "item_index": idx}


def _row(user, day, hh, mm, name, sid=1001, ss=0, bundle=1, items=None, country="United States",
         device="desktop", src="google", med="organic", txn=None, revenue=None, page="https://shop/x"):
    return {
        "event_date": f"202011{day:02d}", "event_timestamp": _us(day, hh, mm, ss), "event_name": name,
        "event_bundle_sequence_id": bundle, "user_pseudo_id": user,
        "user_first_touch_timestamp": _us(1, 8, 0), "ga_session_id": sid, "ga_session_number": 1,
        "page_location": page, "page_title": "Page", "page_referrer": None, "engagement_time_msec": 1000,
        "event_source": src, "event_medium": med, "event_campaign": None, "search_term": None,
        "session_engaged": "1", "device_category": device, "operating_system": "Windows", "browser": "Chrome",
        "country": country, "region": "California", "city": "San Jose", "first_touch_source": src,
        "first_touch_medium": med, "first_touch_campaign": "(organic)", "transaction_id": txn,
        "purchase_revenue_in_usd": revenue, "total_item_quantity": None, "unique_items": None,
        "items": items or [],
    }


def rows() -> list[dict]:
    a = [  # user A: strict full funnel + return next day
        _row("A", 1, 9, 0, "first_visit"), _row("A", 1, 9, 0, "session_start", ss=1),
        _row("A", 1, 9, 1, "page_view"),
        _row("A", 1, 9, 2, "view_item", items=[_item(1, 0), _item(2, 1)]),
        _row("A", 1, 9, 3, "add_to_cart", items=[_item(1, 0)]),
        _row("A", 1, 9, 5, "begin_checkout", items=[_item(1, 0)]),
        _row("A", 1, 9, 7, "purchase", items=[_item(1, 0), _item(3, 1, 5.0, 2)], txn="T1", revenue=20.0),
        _row("A", 2, 10, 0, "session_start", sid=2002), _row("A", 2, 10, 1, "page_view", sid=2002),
    ]
    b = [  # user B: placeholders; purchase without checkout -> any-order only
        _row("B", 1, 12, 0, "first_visit", sid=3003, country="<Other>", device="", src="<Other>", med="<Other>"),
        _row("B", 1, 12, 1, "view_item", sid=3003, country="<Other>", device="", src="<Other>", med="<Other>",
             items=[_item(4, 0, name="<Other>")]),
        _row("B", 1, 12, 5, "purchase", sid=3003, country="<Other>", device="", src="<Other>", med="<Other>",
             items=[_item(4, 0)], txn="(not set)", revenue=10.0),
    ]
    c = [  # user C: missing session id + exact duplicate page_view; NULL medium
        _row("C", 2, 8, 0, "first_visit", sid=4004, country=None, src=None, med=None),
        _row("C", 2, 8, 1, "page_view", sid=4004, med=None, src=None),
        _row("C", 2, 8, 1, "page_view", sid=4004, med=None, src=None),
        _row("C", 2, 8, 2, "scroll", sid=None, med=None, src=None),
    ]
    d = [  # user D: add_to_cart BEFORE view_item, direct traffic, existing user (no first_visit)
        _row("D", 3, 15, 0, "session_start", sid=5005, src="(direct)", med="(none)", device="mobile"),
        _row("D", 3, 15, 1, "add_to_cart", sid=5005, src="(direct)", med="(none)", device="mobile",
             items=[_item(5, 0)]),
        _row("D", 3, 15, 2, "view_item", sid=5005, src="(direct)", med="(none)", device="mobile",
             items=[_item(5, 0)]),
    ]
    return a + b + c + d


def table(extra_rows: list[dict] | None = None) -> pa.Table:
    return pa.Table.from_pylist(rows() + (extra_rows or []), schema=SCHEMA)

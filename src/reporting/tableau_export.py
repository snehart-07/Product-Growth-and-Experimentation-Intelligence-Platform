"""Build validated Tableau exports from existing GrowthLab outputs. No new analysis is done here.

Inputs (already produced by the pipeline):
  * data/processed/ga4/pulseiq_ga4.duckdb      GA4 marts (make models-ga4)
  * reports/results/*.json                     published statistics (make analysis)
  * data/raw/{cookie_cats,hillstrom}/*.csv     experiment rows, only for per-arm counts (make public-data)

Outputs (exports/tableau/):
  * csv/<table>.csv          one file per table (always)
  * growthlab.hyper          all tables in schema "Extract" (if tableauhyperapi is installed)
  * manifest.json            row counts, hashes, sources, validation results
  * data_dictionary.md       generated from tableau_schema.py
  * relationships.json       how to relate tables in Tableau
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src.analysis.stats_utils import load_results
from src.analytics.experiments import wilson_interval
from src.connectors.public_datasets import load as load_public
from src.reporting import tableau_schema as S
from src.settings import PATHS, ROOT

OUT = ROOT / "exports" / "tableau"
GA4_DB = PATHS.processed_ga4 / "pulseiq_ga4.duckdb"

SOURCE_LABEL = {"Organic Search": "Google search", "Paid Search": "Search ads", "Direct": "Direct",
                "Referral": "Link on another site", "(obfuscated)": "Hidden in sample", "(missing)": "Unknown"}
DEVICE_LABEL = {"desktop": "Desktop", "mobile": "Mobile", "tablet": "Tablet"}
STAGES = [("first_visit", "Visited site"), ("view_item", "Viewed product"), ("add_to_cart", "Added to cart"),
          ("begin_checkout", "Started checkout"), ("purchase", "Purchased")]
STAGE_NAME = dict(STAGES)
BEHAVIOURS = {"pages_3plus": ("page_views_24h >= 3", "Viewed 3+ pages"),
              "engaged_1min": ("engagement_sec_24h >= 60", "Spent 1+ minute on site"),
              "viewed_product": ("product_views_24h >= 1", "Viewed a product"),
              "added_to_cart": ("add_to_cart_24h >= 1", "Added to cart"),
              "used_search": ("searches_24h >= 1", "Used search")}
MODEL_LABEL = {"log_page_views": ("More pages viewed (per SD)", "behaviour"),
               "log_engagement_min": ("More time on site (per SD)", "behaviour"),
               "log_product_views": ("More products viewed (per SD)", "behaviour"),
               "added_to_cart": ("Added to cart", "behaviour"), "used_search": ("Used search", "behaviour"),
               "began_checkout": ("Began checkout (intent proxy)", "intent_proxy"),
               "purchased_24h": ("Purchased in first 24h (intent proxy)", "intent_proxy")}
ARM_LABEL = {"No E-Mail": "No email", "Mens E-Mail": "Men's email", "Womens E-Mail": "Women's email"}
COV_LABEL = {"recency_grp": "Months since last purchase", "history_segment": "Spent last year", "newbie": "New vs returning",
             "zip_code": "Area", "channel": "Past shopping channel", "product_line": "Bought last year"}
LEVEL_LABEL = {"newbie": {"0": "Returning customer", "1": "New customer"},
               "zip_code": {"Urban": "City", "Surburban": "Suburb", "Rural": "Rural"},
               "channel": {"Phone": "Phone", "Web": "Web", "Multichannel": "Phone + web"},
               "product_line": {"mens_only": "Men's items only", "womens_only": "Women's items only", "both": "Both"},
               "recency_grp": {"1-3": "1–3 months", "4-6": "4–6 months", "7-9": "7–9 months", "10-12": "10–12 months"}}
METRIC_LABEL = {"conversion": "Bought something", "visit": "Visited the site", "spend": "Spend per customer (USD)"}


def _src(df: pd.DataFrame, col: str = "source_raw") -> pd.DataFrame:
    df["source"] = df[col].map(SOURCE_LABEL).fillna(df[col])
    if "device" in df:
        df["device"] = df.device.map(DEVICE_LABEL).fillna(df.device)
    return df


def _top_countries_sql() -> str:
    return ("WITH top AS (SELECT country FROM new_users WHERE NOT is_suspected_bot "
            "GROUP BY 1 ORDER BY count(*) DESC, country LIMIT 10) ")


def _cgrp(col: str = "country") -> str:
    return f"CASE WHEN {col} IN (SELECT country FROM top) THEN {col} ELSE 'Other' END"


def tone(lo: float, hi: float) -> str:
    return "clearly better" if lo > 0 else "clearly worse" if hi < 0 else "unclear"


# =============================================================================== reference
def dim_date() -> pd.DataFrame:
    d = pd.DataFrame({"date": pd.date_range("2020-11-01", "2021-01-31")})
    d["week_start"] = d.date - pd.to_timedelta((d.date - pd.Timestamp("2020-11-01")).dt.days % 7, unit="D")
    end = d.week_start + pd.Timedelta(days=6)
    d["week_label"] = d.week_start.dt.strftime("%b %d") + " – " + end.dt.strftime("%b %d")
    d["month"] = d.date.dt.strftime("%Y-%m")
    d["day_of_week"] = d.date.dt.day_name()
    d["is_weekend"] = (d.date.dt.dayofweek >= 5).astype(int)
    return d


def dataset_catalog(n_events: int, cc: dict, hs: dict) -> pd.DataFrame:
    return pd.DataFrame([
        {"dataset_key": "ga4", "dataset": S.GA4, "nature": "observational", "period": "2020-11-01 to 2021-01-31",
         "rows": n_events, "unit": "browser cookie (user_pseudo_id)", "causal_claims_allowed": 0,
         "can_support": "funnel, sessions, short-window return behaviour, associations",
         "cannot_support": "causal effects, account sign-ups, revenue truth (obfuscated), long-window retention",
         "source_url": "https://developers.google.com/analytics/bigquery/web-ecommerce-demo-dataset", "exported": 1},
        {"dataset_key": "cookie_cats", "dataset": S.COOKIE, "nature": "randomized experiment", "period": "single test, dates not provided",
         "rows": int(sum(cc["n"].values())), "unit": "player", "causal_claims_allowed": 1,
         "can_support": "effect of gate placement on day-1/day-7 retention and rounds played",
         "cannot_support": "revenue, day-30 retention, novelty effects, segment effects (no pre-treatment covariates)",
         "source_url": "https://www.kaggle.com/datasets/yufengsui/mobile-games-ab-testing", "exported": 1},
        {"dataset_key": "hillstrom", "dataset": S.HILL, "nature": "randomized experiment", "period": "2008, two-week follow-up",
         "rows": int(sum(hs["n"].values())), "unit": "customer", "causal_claims_allowed": 1,
         "can_support": "effect of each e-mail on visit, conversion and spend; pre-treatment subgroup effects",
         "cannot_support": "retention, long-term effects, product-change effects, profit",
         "source_url": "https://blog.minethatdata.com/2008/03/minethatdata-e-mail-analytics-and-data.html", "exported": 1},
        {"dataset_key": "synthetic", "dataset": "PulseIQ synthetic SaaS sandbox", "nature": "simulated",
         "period": "2025-01-01 to 2025-07-01 (simulated)", "rows": 0, "unit": "simulated user", "causal_claims_allowed": 0,
         "can_support": "method validation only", "cannot_support": "any finding about real users",
         "source_url": "not included in this repository", "exported": 0},
    ])


def metric_definitions() -> pd.DataFrame:
    rows = [
        ("new_visitors", "New visitors", "ga4", "diagnostic", "users with first_visit in the sample, bots removed", "—", "sample", "count",
         "A visitor is a browser cookie, not a person"),
        ("product_view_rate", "Product view rate", "ga4", "diagnostic", "viewed a product within 7 days", "funnel-eligible new visitors", "7 days", "proportion",
         "Strict order"),
        ("cart_rate", "Add to cart rate", "ga4", "diagnostic", "added to cart after viewing, within 7 days", "funnel-eligible new visitors", "7 days", "proportion", "Strict order"),
        ("checkout_rate", "Checkout rate", "ga4", "diagnostic", "began checkout after cart, within 7 days", "funnel-eligible new visitors", "7 days", "proportion", "Strict order"),
        ("purchase_conversion", "Purchase conversion", "ga4", "secondary", "purchased after checkout, within 7 days", "funnel-eligible new visitors", "7 days", "proportion",
         "User level, not session level"),
        ("step_conversion", "Step conversion", "ga4", "diagnostic", "users reaching stage s", "users reaching stage s−1", "7 days", "proportion",
         "Wilson 95% interval"),
        ("return_d7", "Return within 7 days", "ga4", "primary", "any event on days 1–7 after first visit", "visitors observable for 7 days", "days 1–7", "proportion",
         "Window-based; censored visitors excluded"),
        ("return_d7_exact", "Return on exactly day 7", "ga4", "sensitivity", "any event on day 7", "visitors observable for 7 days", "day 7", "proportion",
         "Sensitivity check only"),
        ("return_d30", "Return within 30 days", "ga4", "primary", "any event on days 1–30", "visitors observable for 30 days", "days 1–30", "proportion",
         "Only first visits up to 2021-01-01"),
        ("weekly_retention", "Weekly cohort retention", "ga4", "diagnostic", "cohort visitors active in week k (days 7k..7k+6)", "cohort size", "week k", "proportion",
         "Cells not fully observed are unknown, not zero"),
        ("return_after_24h", "Return within a month (after day one)", "ga4", "outcome", "any event in [t0+24h, t0+31d)", "visitors observable for 31 days", "24h–31d",
         "proportion", "Driver outcome; features use the first 24h only"),
        ("avg_order_value", "Average order value", "ga4", "sizing input", "sum of purchase revenue over distinct transactions",
         "distinct transaction IDs", "sample", "USD", "Obfuscated sample; for scenarios only"),
        ("odds_ratio", "Odds ratio (adjusted)", "ga4", "diagnostic", "odds of returning with the behaviour", "odds without it, other terms held equal", "24h–31d", "odds ratio",
         "Association, not causation"),
        ("game_d7", "Day-7 retention", "cookie_cats", "primary", "players with retention_7", "players in the arm", "day 7", "proportion", "Exact-day flag"),
        ("game_d1", "Day-1 retention", "cookie_cats", "guardrail", "players with retention_1", "players in the arm", "day 1", "proportion",
         "Non-inferiority margin −1.0 pt"),
        ("game_rounds", "Rounds played", "cookie_cats", "guardrail", "sum of sum_gamerounds", "players in the arm", "first 14 days", "rounds",
         "Winsorized at pooled 99.9th percentile; margin −2.5; measured after assignment"),
        ("game_revenue", "Revenue / in-app purchases", "cookie_cats", "guardrail (missing)", "—", "—", "—", "—", "Not in the data"),
        ("email_conversion", "Conversion", "hillstrom", "primary", "customers who purchased", "customers in the arm", "2 weeks", "proportion", "Holm across 2 arms"),
        ("email_visit", "Visit", "hillstrom", "secondary", "customers who visited", "customers in the arm", "2 weeks", "proportion", ""),
        ("email_spend", "Spend per customer", "hillstrom", "secondary", "sum of spend (zeros included)", "customers in the arm", "2 weeks", "USD",
         "Zero-inflated; percentile bootstrap CI; revenue, not profit"),
    ]
    return pd.DataFrame(rows, columns=["metric_key", "label", "dataset_key", "role", "numerator", "denominator", "window", "unit", "caveat"])


# =============================================================================== GA4 store tables
def store_daily(con) -> pd.DataFrame:
    df = con.execute(_top_countries_sql() + f"""
        SELECT n.d0 AS first_visit_date, n.cohort_week_start AS week_start, n.channel AS source_raw, n.device,
               {_cgrp('n.country')} AS country_group,
               count(*)                                                        AS new_visitors,
               count(*) FILTER (WHERE n.obs_7d)                                AS funnel_eligible,
               count(*) FILTER (WHERE n.obs_7d AND f.strict_view_item)         AS viewed_product,
               count(*) FILTER (WHERE n.obs_7d AND f.strict_add_to_cart)       AS added_to_cart,
               count(*) FILTER (WHERE n.obs_7d AND f.strict_begin_checkout)    AS started_checkout,
               count(*) FILTER (WHERE n.obs_7d AND f.strict_purchase)          AS purchased,
               count(*) FILTER (WHERE n.obs_d7)                                AS d7_eligible,
               count(*) FILTER (WHERE n.obs_d7 AND r.return_d7)                AS d7_returned,
               count(*) FILTER (WHERE n.obs_d7 AND r.return_d7_exact)          AS d7_exact_returned,
               count(*) FILTER (WHERE n.obs_d30)                               AS d30_eligible,
               count(*) FILTER (WHERE n.obs_d30 AND r.return_d30)              AS d30_returned
        FROM new_users n JOIN funnel_new_users f USING (user_pseudo_id) JOIN user_return_flags r USING (user_pseudo_id)
        WHERE NOT n.is_suspected_bot
        GROUP BY ALL ORDER BY ALL""").df()
    return _src(df)


def funnel_daily_long(daily: pd.DataFrame) -> pd.DataFrame:
    cols = {"first_visit": "funnel_eligible", "view_item": "viewed_product", "add_to_cart": "added_to_cart",
            "begin_checkout": "started_checkout", "purchase": "purchased"}
    keep = ["first_visit_date", "week_start", "source", "source_raw", "device", "country_group"]
    parts = []
    for i, (k, name) in enumerate(STAGES, start=1):
        p = daily[keep].copy()
        p["stage_order"], p["stage_key"], p["stage"], p["users"] = i, k, name, daily[cols[k]].values
        parts.append(p)
    out = pd.concat(parts, ignore_index=True)
    return out[out.groupby(keep).users.transform("max") > 0].reset_index(drop=True)  # drop rows with no eligible visitors


def funnel_steps(fc: dict) -> pd.DataFrame:
    med = {"view_item": "sec_visit_to_view", "add_to_cart": "sec_view_to_cart", "begin_checkout": "sec_cart_to_checkout",
           "purchase": "sec_checkout_to_purchase"}
    rows = []
    for order, key in (("strict", "funnel_strict"), ("any", "funnel_any_order")):
        steps = fc[key]
        lost = [s.get("users_lost") or 0 for s in steps]
        worst = int(np.argmax(lost))
        for i, s in enumerate(steps):
            m = fc["median_time_between_stages"].get(med.get(s["stage"], ""), {}) if order == "strict" else {}
            rows.append({"order_type": order, "stage_order": i + 1, "stage_key": s["stage"], "stage": STAGE_NAME[s["stage"]],
                         "users": s["users"], "share_of_entrants": s["share_of_entrants"],
                         "conversion_from_previous": s.get("conv_from_prev"), "ci_low": s.get("conv_ci_low"),
                         "ci_high": s.get("conv_ci_high"), "users_lost": s.get("users_lost"),
                         "drop_rate": s.get("drop_rate"), "is_largest_drop": int(i == worst and i > 0),
                         "median_minutes_from_previous": m.get("median_minutes")})
    return pd.DataFrame(rows)


def funnel_segments(fc: dict) -> pd.DataFrame:
    df = pd.DataFrame(fc["segment_funnel"])
    df["segment_type"] = df.segment_type.replace({"channel": "source"})
    df["segment_raw"] = df.segment
    lab = {**SOURCE_LABEL, **DEVICE_LABEL}
    df["segment"] = df.segment_raw.map(lab).fillna(df.segment_raw)
    df["stage_key"] = df.stage
    df["stage"] = df.stage_key.map(STAGE_NAME)
    df = df.rename(columns={"vs_rest_ci_low_pp": "diff_ci_low_pp", "vs_rest_ci_high_pp": "diff_ci_high_pp",
                            "vs_rest_p": "p_value", "vs_rest_p_holm": "p_holm"})
    df["clearly_different"] = (df.p_holm < 0.05).astype(int)
    return df


def retention_cohorts(con) -> pd.DataFrame:
    df = con.execute(_top_countries_sql() + f"""
        , p AS (SELECT CAST(observation_end AS DATE) - 1 AS last_date FROM run_params),
        base AS (SELECT n.*, {_cgrp('n.country')} AS country_group FROM new_users n WHERE NOT n.is_suspected_bot),
        sizes AS (SELECT cohort_week, cohort_week_start, channel, device, country_group, count(*) AS cohort_size
                  FROM base GROUP BY ALL),
        ages AS (SELECT range AS age_week FROM range(1, 13)),
        act AS (SELECT b.cohort_week, b.channel, b.device, b.country_group,
                       CAST(floor(date_diff('day', b.d0, a.event_date) / 7) AS INTEGER) AS age_week,
                       count(DISTINCT b.user_pseudo_id) AS active_users
                FROM base b JOIN user_day_activity a USING (user_pseudo_id)
                WHERE date_diff('day', b.d0, a.event_date) >= 7 GROUP BY ALL)
        SELECT s.cohort_week, s.cohort_week_start, s.channel AS source_raw, s.device, s.country_group, g.age_week,
               s.cohort_size, coalesce(act.active_users, 0) AS active_users,
               (least(s.cohort_week_start + 6, p.last_date) + CAST(7 * g.age_week + 6 AS INTEGER) <= p.last_date)::INT
                   AS is_fully_observed
        FROM sizes s CROSS JOIN ages g CROSS JOIN p
        LEFT JOIN act ON act.cohort_week = s.cohort_week AND act.channel = s.channel AND act.device = s.device
                     AND act.country_group = s.country_group AND act.age_week = g.age_week
        ORDER BY ALL""").df()
    df["cohort_label"] = (df.cohort_week_start.dt.strftime("%b %d") + " – "
                          + (df.cohort_week_start + pd.Timedelta(days=6)).dt.strftime("%b %d"))
    return _src(df)


def retention_rates(fc: dict) -> pd.DataFrame:
    rr = fc["return_rates"]
    spec = [("d7", "all", "d7_window"), ("d7_exact_day", "all", "d7_exact_day"), ("d30", "all", "d30_window"),
            ("d7", "source", "d7_by_channel"), ("d7", "device", "d7_by_device"),
            ("d30", "source", "d30_by_channel"), ("d30", "device", "d30_by_device")]
    rows = []
    lab = {**SOURCE_LABEL, **DEVICE_LABEL, "all": "All new visitors"}
    for window, seg_type, key in spec:
        for r in rr[key]:
            rows.append({"window": window, "segment_type": seg_type, "segment": lab.get(r["segment"], r["segment"]),
                         "segment_raw": r["segment"], "eligible": r["n"], "returned": r["x"], "rate": r["rate"],
                         "ci_low": r["ci_low"], "ci_high": r["ci_high"]})
    return pd.DataFrame(rows)


def retention_tests(fc: dict) -> pd.DataFrame:
    rows = []
    for t in fc["segment_return_tests"]:
        rows.append({"test_type": "segment_difference", "dimension": t["segment_type"].replace("channel", "source"),
                     "metric": t["metric"], "age_week": 0, "n_groups": t["n_segments"], "chi2": t["chi2"], "dof": t["dof"],
                     "p_value": t["p_value"], "p_holm": t["p_holm"], "min_rate": None, "max_rate": None})
    for t in fc["cohort_equal_age_tests"]:
        rows.append({"test_type": "cohort_equal_age", "dimension": "cohort", "metric": "weekly_retention",
                     "age_week": t["age_week"], "n_groups": t["n_cohorts"], "chi2": t["chi2"], "dof": t["dof"],
                     "p_value": t["p_value"], "p_holm": t["p_holm"], "min_rate": t["min_rate"], "max_rate": t["max_rate"]})
    return pd.DataFrame(rows)


def behaviour_groups(con) -> pd.DataFrame:
    parts = []
    for key, (cond, label) in BEHAVIOURS.items():
        parts.append(con.execute(_top_countries_sql() + f"""
            SELECT cohort_week, DATE '2020-11-01' + CAST(7 * cohort_week AS INTEGER) AS week_start,
                   channel AS source_raw, device, {_cgrp()} AS country_group,
                   '{key}' AS behaviour_key, '{label}' AS behaviour, ({cond})::INT AS did_it,
                   count(*) AS users, sum(returned_30d_after_24h::INT) AS returned
            FROM retention_features WHERE NOT is_suspected_bot AND obs_31d
            GROUP BY ALL""").df())
    df = pd.concat(parts, ignore_index=True).sort_values(
        ["behaviour_key", "cohort_week", "source_raw", "device", "country_group", "did_it"]).reset_index(drop=True)
    return _src(df)


def behaviour_model(rd: dict) -> pd.DataFrame:
    vif = {v["feature"]: v["vif"] for v in rd["vif"]}
    rows = []

    def add(model: str, r: dict):
        f = r["feature"]
        if f in MODEL_LABEL:
            lab, typ = MODEL_LABEL[f]
        elif f.startswith("channel_"):
            raw = f.removeprefix("channel_")
            lab, typ = f"Source: {SOURCE_LABEL.get(raw, raw)} (vs Google search)", "source"
        elif f.startswith("device_"):
            raw = f.removeprefix("device_")
            lab, typ = f"Device: {DEVICE_LABEL.get(raw, raw)} (vs Desktop)", "device"
        else:
            lab, typ = f"Country: {f.removeprefix('country_grp_')} (vs United States)", "country"
        direction = ("more likely" if r["or_ci_low"] > 1 else "less likely" if r["or_ci_high"] < 1 else "no clear link")
        rows.append({"model": model, "feature": f, "label": lab, "term_type": typ, "odds_ratio": r["odds_ratio"],
                     "or_ci_low": r["or_ci_low"], "or_ci_high": r["or_ci_high"], "p_value": r["p_value"],
                     "direction": direction, "vif": vif.get(f) if model == "main" else None})

    for r in rd["logit"]:
        add("main", r)
    seen = set()
    for r in [rd["intent_adjusted_top_or"], rd["strongest_absolute_or_intent_adjusted"], *rd["intent_proxies_or"]]:
        if r["feature"] not in seen:
            seen.add(r["feature"])
            add("intent_adjusted", r)
    return pd.DataFrame(rows)


def followup_test_design(rd: dict) -> pd.DataFrame:
    fu = rd["follow_up_experiment"]
    return pd.DataFrame([{"mde_pp": d["mde_pp_absolute"], "n_per_arm": d["n_per_arm"],
                          "enrolment_days": d["enrolment_days_at_observed_traffic"], "total_days": d["total_duration_days"],
                          "baseline_rate": fu["baseline_rate"], "hypothesis": fu["hypothesis"]} for d in fu["designs"]])


# =============================================================================== Cookie Cats
def game_tables(cc: dict) -> dict[str, pd.DataFrame]:
    raw = load_public("cookie_cats")
    p7, d1, rd, pw, srm = cc["primary_d7"], cc["guardrail_d1"], cc["guardrail_rounds"], cc["power"], cc["srm"]
    arms = []
    for v, lab, role, wmean in (("gate_30", "Level 30 (control)", "control", rd["means"]["gate_30_winsorized"]),
                                ("gate_40", "Level 40 (treatment)", "treatment", rd["means"]["gate_40_winsorized"])):
        g = raw[raw.version == v]
        arms.append({"variant": v, "variant_label": lab, "role": role, "players": len(g),
                     "d1_retained": int(g.retention_1.sum()), "d1_rate": g.retention_1.mean(),
                     "d7_retained": int(g.retention_7.sum()), "d7_rate": g.retention_7.mean(),
                     "rounds_mean_winsorized": wmean, "rounds_mean_raw": g.sum_gamerounds.mean(),
                     "rounds_median": float(g.sum_gamerounds.median())})

    def prop(key, label, role, m, method, lo, hi, margin=None, status=None, interp=""):
        return {"metric_key": key, "metric": label, "role": role, "method": method, "estimate": 100 * m["abs_diff"],
                "ci_low": 100 * lo, "ci_high": 100 * hi, "unit": "percentage points", "relative_change": m["relative_lift"],
                "p_value": m["p_value"], "ni_margin": margin, "status": status or tone(lo, hi), "interpretation": interp}

    eff = [
        prop("game_d7", "Day-7 retention", "primary", p7, "Newcombe hybrid score CI; two-proportion z-test",
             p7["ci_low"], p7["ci_high"],
             interp="Level 40 lowered day-7 retention; the whole interval is below zero" if p7["ci_high"] < 0 else ""),
        prop("game_d7", "Day-7 retention", "robustness", p7, "Bootstrap percentile CI (10,000)",
             p7["bootstrap"]["ci_low"], p7["bootstrap"]["ci_high"]),
        prop("game_d1", "Day-1 retention", "guardrail", d1, "Newcombe CI; non-inferiority vs margin", d1["ci_low"], d1["ci_high"],
             margin=-d1["ni_margin_pp"], status="pass" if d1["status"] == "pass" else "not pass",
             interp="Lower bound is below the −1.0 pt margin, so non-inferiority is not shown"
             if d1["status"] != "pass" else "Within the margin"),
        prop("game_d1", "Day-1 retention", "robustness", d1, "Bootstrap percentile CI (10,000)",
             d1["bootstrap"]["ci_low"], d1["bootstrap"]["ci_high"]),
    ]
    for method, key, role, status, p in (
            ("Winsorized mean difference, bootstrap CI; non-inferiority", "winsorized_mean_diff", "guardrail",
             "pass" if rd["status"] == "pass" else "not pass", rd["mann_whitney_p"]),
            ("Raw mean difference, bootstrap CI", "raw_mean_diff", "robustness", None, None),
            ("Median difference, bootstrap CI (2,000)", "median_diff", "robustness", None, rd["mann_whitney_p"])):
        m = rd[key]
        eff.append({"metric_key": "game_rounds", "metric": "Rounds played", "role": role, "method": method,
                    "estimate": m["estimate"], "ci_low": m["ci_low"], "ci_high": m["ci_high"], "unit": "rounds",
                    "relative_change": None, "p_value": p, "ni_margin": -rd["ni_margin_rounds"] if role == "guardrail" else None,
                    "status": status or tone(m["ci_low"], m["ci_high"]).replace("clearly ", "clearly "),
                    "interpretation": "Mann-Whitney U p-value shown" if p is not None else ""})
    o = cc["rounds_outliers"]
    checks = pd.DataFrame([
        {"check_key": "srm", "check": "Sample-ratio mismatch (expected 50/50)", "value": srm["p_value"],
         "threshold": srm["threshold"], "status": "flag" if srm["srm_detected"] else ("note" if srm["p_value"] < 0.01 else "pass"),
         "detail": f"{srm['n_control']:,} vs {srm['n_treatment']:,}; chi-square {srm['chi2']:.2f}. Pre-set flag threshold 0.001."},
        {"check_key": "duplicates", "check": "Duplicate player IDs", "value": cc["duplicate_userids"], "threshold": 0,
         "status": "pass" if cc["duplicate_userids"] == 0 else "flag", "detail": "Every player should appear once"},
        {"check_key": "outliers", "check": "Players above the 99.9th percentile of rounds", "value": o["n_above_p99_9"],
         "threshold": o["p99_9"], "status": "info", "detail": f"Max {o['max']:,} rounds; capped for the guardrail mean"},
        {"check_key": "mde", "check": "Minimum detectable effect at 80% power (pts)", "value": pw["mde_at_80pct_power_pp"],
         "threshold": None, "status": "info", "detail": "Not observed power"},
        {"check_key": "n_for_0_5pp", "check": "Players per arm to detect 0.5 pts", "value": pw["n_per_arm_for_0_5pp"],
         "threshold": None, "status": "info", "detail": "For the next test"},
        {"check_key": "decision", "check": "Decision (rule fixed before analysis)", "value": None, "threshold": None,
         "status": cc["decision"], "detail": cc["decision_reason"]},
        {"check_key": "revenue", "check": "Revenue guardrail", "value": None, "threshold": None, "status": "missing",
         "detail": cc["missing_guardrail"]},
    ])
    curve = pd.DataFrame({"effect_pp": pw["curve"]["effect_pp"], "power": pw["curve"]["power"]})
    curve["mde_pp"], curve["observed_abs_effect_pp"] = pw["mde_at_80pct_power_pp"], abs(100 * p7["abs_diff"])
    return {"game_arms": pd.DataFrame(arms), "game_effects": pd.DataFrame(eff), "game_checks": checks, "game_power_curve": curve}


# =============================================================================== Hillstrom
def email_tables(hs: dict) -> dict[str, pd.DataFrame]:
    raw = load_public("hillstrom")
    arms = []
    for arm, g in raw.groupby("segment"):
        for metric in ("conversion", "visit", "spend"):
            if metric == "spend":
                arms.append({"arm": arm, "metric_key": metric, "customers": len(g), "events": None, "value": g.spend.mean(),
                             "ci_low": None, "ci_high": None})
            else:
                x = int(g[metric].sum())
                lo, hi = wilson_interval(x, len(g))
                arms.append({"arm": arm, "metric_key": metric, "customers": len(g), "events": x, "value": x / len(g),
                             "ci_low": lo, "ci_high": hi})
    arms = pd.DataFrame(arms)
    arms["arm_label"] = arms.arm.map(ARM_LABEL)
    arms["role"] = np.where(arms.arm == "No E-Mail", "control", "treatment")
    arms["metric"] = arms.metric_key.map(METRIC_LABEL)
    arms = arms.sort_values(["metric_key", "arm"]).reset_index(drop=True)

    eff = []
    for a in hs["arm_effects"]:
        for metric in ("conversion", "visit"):
            m = a[metric]
            eff.append({"arm": a["arm"], "metric_key": metric, "role": "primary" if metric == "conversion" else "secondary",
                        "estimate": 100 * m["abs_diff"], "ci_low": 100 * m["ci_low"], "ci_high": 100 * m["ci_high"],
                        "unit": "percentage points", "method": "Newcombe CI; two-proportion z-test; Holm across 2 arms",
                        "p_value": m["p_value"], "p_holm": m["p_holm"], "status": tone(m["ci_low"], m["ci_high"])})
        s = a["spend"]
        eff.append({"arm": a["arm"], "metric_key": "spend", "role": "secondary", "estimate": s["estimate"], "ci_low": s["ci_low"],
                    "ci_high": s["ci_high"], "unit": "USD", "method": f"Percentile bootstrap ({s['n_boot']:,} resamples)",
                    "p_value": None, "p_holm": None, "status": tone(s["ci_low"], s["ci_high"])})
    eff = pd.DataFrame(eff)
    eff["arm_label"], eff["metric"] = eff.arm.map(ARM_LABEL), eff.metric_key.map(METRIC_LABEL)

    sub = pd.DataFrame(hs["subgroups_conversion"])
    sub = sub.assign(arm_label=sub.arm.map(ARM_LABEL), covariate_label=sub.covariate.map(COV_LABEL),
                     level_label=[lv.split(") ", 1)[-1] if c == "history_segment" else LEVEL_LABEL.get(c, {}).get(lv, lv)
                                  for c, lv in zip(sub.covariate, sub.level)],
                     estimate_pp=100 * sub.abs_diff, ci_low_pp=100 * sub.ci_low, ci_high_pp=100 * sub.ci_high,
                     survives_holm=sub.survives_holm.astype(int))

    it = pd.concat([pd.DataFrame(hs["interaction_tests_conversion"]), pd.DataFrame(hs["interaction_tests_visit"])], ignore_index=True)
    it = it.assign(arm_label=it.arm.map(ARM_LABEL), covariate_label=it.covariate.map(COV_LABEL),
                   real_difference=(it.p_holm < 0.05).astype(int))

    up = hs["uplift"]
    checks = [{"check_key": "srm", "check": "Three arms the expected size (1/3 each)", "value": hs["srm"]["p_value"],
               "status": "flag" if hs["srm"]["srm_detected"] else "pass",
               "detail": " · ".join(f"{ARM_LABEL[k]} {v:,}" for k, v in hs["n"].items())}]
    for c in hs["cuped_spend"]:
        checks.append({"check_key": f"cuped_{c['arm'].split()[0].lower()}", "check": f"CUPED variance reduction on spend, {ARM_LABEL[c['arm']]}",
                       "value": c["variance_reduction"], "status": "info",
                       "detail": f"theta {c['theta']:.4f}; CI width ratio {c['ci_width_ratio']:.4f}. Effectively no reduction."})
    checks.append({"check_key": "uplift_top30", "check": "Uplift model: visit lift, top 30% targeted (women's email)",
                   "value": up["uplift_top_share"], "status": "exploratory",
                   "detail": f"Everyone: {up['uplift_all']:.4f}. Holdout n {up['holdout_n']:,}. Exploratory; not a targeting recommendation."})
    return {"email_arms": arms, "email_effects": eff, "email_subgroups": sub, "email_interactions": it,
            "email_checks": pd.DataFrame(checks)}


# =============================================================================== overview
def overview_kpis(fc: dict, rd: dict, cc: dict, hs: dict) -> pd.DataFrame:
    st = {r["stage"]: r for r in fc["funnel_strict"]}
    rr = fc["return_rates"]
    p7 = cc["primary_d7"]
    mens = next(a for a in hs["arm_effects"] if a["arm"] == "Mens E-Mail")
    womens = next(a for a in hs["arm_effects"] if a["arm"] == "Womens E-Mail")
    n0 = st["first_visit"]["users"]
    top = rd["unadjusted_top_or"]

    def row(key, section, label, value, unit, ds, nature, lo=None, hi=None, num=None, den=None, interp=""):
        return {"metric_key": key, "section": section, "label": label, "value": value, "unit": unit, "ci_low": lo, "ci_high": hi,
                "numerator": num, "denominator": den, "dataset_key": ds, "nature": nature, "interpretation": interp}

    w = next(s for s in fc["funnel_strict"][1:] if s["users_lost"] == max(x["users_lost"] or 0 for x in fc["funnel_strict"]))
    return pd.DataFrame([
        row("new_visitors", "Funnel", "Funnel-eligible new visitors", n0, "count", "ga4", S.OBS),
        row("product_view_rate", "Funnel", "Viewed a product within 7 days", st["view_item"]["share_of_entrants"], "proportion", "ga4", S.OBS,
            st["view_item"]["conv_ci_low"], st["view_item"]["conv_ci_high"], st["view_item"]["users"], n0),
        row("cart_rate", "Funnel", "Added to cart within 7 days", st["add_to_cart"]["share_of_entrants"], "proportion", "ga4", S.OBS,
            num=st["add_to_cart"]["users"], den=n0),
        row("purchase_conversion", "Funnel", "Purchased within 7 days", st["purchase"]["share_of_entrants"], "proportion", "ga4", S.OBS,
            num=st["purchase"]["users"], den=n0),
        row("largest_drop_rate", "Funnel", f"Left at the largest drop ({STAGE_NAME[w['stage']]})", w["drop_rate"], "proportion", "ga4", S.OBS,
            num=w["users_lost"], interp="Largest absolute loss of any funnel step"),
        row("avg_order_value", "Funnel", "Average order value (distinct transactions)", fc["opportunity_sizing"]["aov_usd_mean"], "USD",
            "ga4", S.OBS, den=fc["opportunity_sizing"]["n_transactions"],
            interp="Input for what-if scenarios only; revenue in this sample is altered for privacy"),
        row("return_d7", "Retention", "Returned within 7 days", rr["d7_window"][0]["rate"], "proportion", "ga4", S.OBS,
            rr["d7_window"][0]["ci_low"], rr["d7_window"][0]["ci_high"], rr["d7_window"][0]["x"], rr["d7_window"][0]["n"]),
        row("return_d30", "Retention", "Returned within 30 days", rr["d30_window"][0]["rate"], "proportion", "ga4", S.OBS,
            rr["d30_window"][0]["ci_low"], rr["d30_window"][0]["ci_high"], rr["d30_window"][0]["x"], rr["d30_window"][0]["n"]),
        row("return_after_24h", "Drivers", "Returned within a month (after day one)", rd["outcome_rate"], "proportion", "ga4", S.OBS,
            den=rd["n"]),
        row("top_positive_or", "Drivers", "Strongest positive association: pages viewed (OR per SD)", top["odds_ratio"], "odds ratio",
            "ga4", S.OBS, top["or_ci_low"], top["or_ci_high"], interp="Association, not causation"),
        row("game_d7_effect", "Game", "Day-7 retention change, level 40 − level 30", 100 * p7["abs_diff"], "percentage points",
            "cookie_cats", S.RCT, 100 * p7["ci_low"], 100 * p7["ci_high"], interp=cc["decision_reason"]),
        row("email_mens_conversion", "Email", "Men's email: conversion lift vs no email", 100 * mens["conversion"]["abs_diff"],
            "percentage points", "hillstrom", S.RCT, 100 * mens["conversion"]["ci_low"], 100 * mens["conversion"]["ci_high"]),
        row("email_womens_conversion", "Email", "Women's email: conversion lift vs no email", 100 * womens["conversion"]["abs_diff"],
            "percentage points", "hillstrom", S.RCT, 100 * womens["conversion"]["ci_low"], 100 * womens["conversion"]["ci_high"]),
    ])


# =============================================================================== orchestration
def build() -> dict[str, pd.DataFrame]:
    if not GA4_DB.exists():
        raise FileNotFoundError(f"{GA4_DB} not found. Run: make ga4-extract-full models-ga4")
    fc, rd, cc, hs = (load_results(n) for n in ("funnel_cohort", "retention_drivers", "cookie_cats", "hillstrom"))
    con = duckdb.connect(str(GA4_DB), read_only=True)
    n_events = con.execute("SELECT count(*) FROM stg_events").fetchone()[0]
    daily = store_daily(con)
    t = {
        "dim_date": dim_date(), "dataset_catalog": dataset_catalog(n_events, cc, hs), "metric_definitions": metric_definitions(),
        "overview_kpis": overview_kpis(fc, rd, cc, hs), "store_daily": daily, "funnel_daily_long": funnel_daily_long(daily),
        "funnel_steps": funnel_steps(fc), "funnel_segments": funnel_segments(fc), "retention_cohorts": retention_cohorts(con),
        "retention_rates": retention_rates(fc), "retention_tests": retention_tests(fc), "behaviour_groups": behaviour_groups(con),
        "behaviour_model": behaviour_model(rd), "followup_test_design": followup_test_design(rd),
        **game_tables(cc), **email_tables(hs),
    }
    con.close()
    return {name: conform(name, df) for name, df in t.items()}


def conform(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Select/order columns per the schema and coerce types. Missing or extra schema columns are an error."""
    spec = S.BY_NAME[name]
    missing = [c for c in spec.columns if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: missing columns {missing}")
    out = df[list(spec.columns)].copy()
    for col, (typ, _) in spec.columns.items():
        if typ == "int":
            out[col] = pd.to_numeric(out[col]).round().astype("Int64")
        elif typ == "float":
            out[col] = pd.to_numeric(out[col]).astype("float64")
        elif typ == "date":
            out[col] = pd.to_datetime(out[col]).dt.date
        else:
            out[col] = out[col].astype("string")
    return out.reset_index(drop=True)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_csv(tables: dict[str, pd.DataFrame], out: Path) -> dict[str, Path]:
    d = out / "csv"
    d.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, df in tables.items():
        p = d / f"{name}.csv"
        df.to_csv(p, index=False, date_format="%Y-%m-%d")
        paths[name] = p
    return paths


def write_hyper(tables: dict[str, pd.DataFrame], path: Path) -> str:
    """Write every table into schema "Extract" of one .hyper file. Returns a status string."""
    try:
        from tableauhyperapi import (Connection, CreateMode, HyperProcess, Inserter, NOT_NULLABLE, NULLABLE, SqlType,
                                     TableDefinition, TableName, Telemetry)
    except ImportError:
        return "skipped: tableauhyperapi not installed (pip install -r requirements-dev.txt)"
    types = {"int": SqlType.big_int(), "float": SqlType.double(), "text": SqlType.text(), "date": SqlType.date()}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    logs = path.parent / ".hyperlogs"
    logs.mkdir(parents=True, exist_ok=True)
    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
                      parameters={"log_dir": str(logs)}) as hyper:
        with Connection(hyper.endpoint, str(path), CreateMode.CREATE_AND_REPLACE) as conn:
            conn.catalog.create_schema_if_not_exists("Extract")
            for name, df in tables.items():
                spec = S.BY_NAME[name]
                cols = [TableDefinition.Column(c, types[t], NOT_NULLABLE if c in spec.keys else NULLABLE)
                        for c, (t, _) in spec.columns.items()]
                td = TableDefinition(TableName("Extract", name), cols)
                conn.catalog.create_table(td)
                rows = [[None if (v is pd.NA or (isinstance(v, float) and np.isnan(v))) else
                         (int(v) if isinstance(v, (np.integer,)) else v) for v in rec]
                        for rec in df.astype(object).itertuples(index=False, name=None)]
                with Inserter(conn, td) as ins:
                    ins.add_rows(rows)
                    ins.execute()
    import shutil

    shutil.rmtree(logs, ignore_errors=True)
    return "written"


RELATIONSHIPS = [
    {"left": "store_daily", "right": "dim_date", "on": [["first_visit_date", "date"]]},
    {"left": "funnel_daily_long", "right": "dim_date", "on": [["first_visit_date", "date"]]},
    {"left": "overview_kpis", "right": "metric_definitions", "on": [["metric_key", "metric_key"]], "note": "partial; tooltip lookup"},
    {"left": "dataset_catalog", "right": "overview_kpis", "on": [["dataset_key", "dataset_key"]]},
]


def data_dictionary_md() -> str:
    out = ["# GrowthLab Tableau data dictionary", "",
           "Generated from `src/reporting/tableau_schema.py` by `scripts/export_tableau_data.py`. Do not edit by hand.", ""]
    for t in S.TABLES:
        out += [f"## `{t.name}`", "", f"{t.description}", "",
                f"- **Dataset:** {t.dataset} · **Nature:** {t.nature}", f"- **Grain:** {t.grain}",
                f"- **Primary key:** {', '.join(t.keys)}"] + ([f"- **Caveat:** {t.caveat}"] if t.caveat else []) + [
                "", "| Column | Type | Description |", "| --- | --- | --- |"]
        out += [f"| `{c}` | {typ} | {d} |" for c, (typ, d) in t.columns.items()] + [""]
    return "\n".join(out)


def run(out: Path = OUT, hyper: bool = True) -> dict:
    from src.reporting.tableau_validate import validate

    tables = build()
    checks = validate(tables)
    failed = [c for c in checks if not c["passed"]]
    if failed:
        raise RuntimeError("Validation failed:\n" + "\n".join(f"  ✕ {c['check']}: {c['detail']}" for c in failed))
    paths = write_csv(tables, out)
    status = write_hyper(tables, out / "growthlab.hyper") if hyper else "skipped (--no-hyper)"
    (out / "relationships.json").write_text(json.dumps(RELATIONSHIPS, indent=2))
    (out / "data_dictionary.md").write_text(data_dictionary_md())
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {"ga4_duckdb": str(GA4_DB.relative_to(ROOT)), "results": "reports/results/*.json",
                   "experiments_raw": ["data/raw/cookie_cats/cookie_cats.csv", "data/raw/hillstrom/hillstrom.csv"]},
        "hyper": status,
        "tables": {n: {"rows": len(df), "dataset": S.BY_NAME[n].dataset, "nature": S.BY_NAME[n].nature,
                       "grain": S.BY_NAME[n].grain, "csv": str(paths[n].relative_to(ROOT)), "sha256": _sha(paths[n])}
                   for n, df in tables.items()},
        "validation": {"checks": len(checks), "passed": len(checks) - len(failed), "results": checks},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    return manifest

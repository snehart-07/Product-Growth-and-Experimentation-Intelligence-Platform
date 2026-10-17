"""Validation for the Tableau exports.

Every check recomputes a number from the exported tables and compares it with the published analysis
(reports/results/*.json) or with the raw experiment files. Nothing is trusted from the exporter itself.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from src.analysis.stats_utils import load_results
from src.connectors.public_datasets import load as load_public
from src.reporting import tableau_schema as S

TOL = 1e-9
PROPORTION_COLS = {"rate", "share_of_entrants", "conversion_from_previous", "drop_rate", "overall_rate", "d1_rate", "d7_rate",
                   "rate_control", "rate_treatment", "power", "baseline_rate"}
CAUSAL_WORDS = re.compile(r"\b(caused|causes|drives|driven by|impact of|effect of)\b", re.I)


class Checks:
    def __init__(self):
        self.out: list[dict] = []

    def add(self, check: str, passed: bool, detail: str = "") -> None:
        self.out.append({"check": check, "passed": bool(passed), "detail": detail})

    def eq(self, check: str, got, want, tol: float = TOL) -> None:
        ok = (abs(float(got) - float(want)) <= tol) if isinstance(want, (float, np.floating)) else (got == want)
        self.add(check, ok, f"export={got!r} published={want!r}")


def _schema(c: Checks, tables: dict[str, pd.DataFrame]) -> None:
    c.add("all schema tables exported", set(tables) == set(S.BY_NAME), f"missing={set(S.BY_NAME) - set(tables)}")
    for name, df in tables.items():
        spec = S.BY_NAME[name]
        c.add(f"{name}: columns match schema", list(df.columns) == list(spec.columns))
        c.add(f"{name}: not empty", len(df) > 0, f"rows={len(df)}")
        keys = list(spec.keys)
        c.add(f"{name}: key columns have no nulls", not df[keys].isna().any().any())
        dup = int(df.duplicated(keys).sum())
        c.add(f"{name}: primary key unique ({', '.join(keys)})", dup == 0, f"duplicates={dup}")
        for col in PROPORTION_COLS & set(df.columns):
            v = df[col].dropna()
            c.add(f"{name}.{col}: within [0, 1]", bool(((v >= 0) & (v <= 1)).all()), f"min={v.min()} max={v.max()}")
        for lo, est, hi in (("ci_low", "rate", "ci_high"), ("ci_low", "estimate", "ci_high"), ("ci_low", "value", "ci_high"),
                            ("ci_low_pp", "estimate_pp", "ci_high_pp"), ("or_ci_low", "odds_ratio", "or_ci_high"),
                            ("ci_low", "conversion_from_previous", "ci_high")):
            if {lo, est, hi} <= set(df.columns):
                d = df[[lo, est, hi]].dropna().astype(float)
                ok = bool(((d[lo] <= d[est] + 1e-12) & (d[est] <= d[hi] + 1e-12)).all())
                c.add(f"{name}: {lo} ≤ {est} ≤ {hi}", ok, f"violations={int((~((d[lo] <= d[est] + 1e-12) & (d[est] <= d[hi] + 1e-12))).sum())}")
        count_cols = [k for k, (t, _) in spec.columns.items() if t == "int" and k not in spec.keys]
        for col in count_cols:
            v = df[col].dropna()
            if col not in ("dof",):
                c.add(f"{name}.{col}: non-negative", bool((v >= 0).all()))
        if spec.nature == S.OBS:
            txt = df.select_dtypes(include=["string", "object"]).astype(str)
            hits = [x for col in txt for x in df[col].dropna().astype(str).unique() if CAUSAL_WORDS.search(x)]
            c.add(f"{name}: no causal wording in an observational table", not hits, f"hits={hits[:3]}")


def _ga4(c: Checks, t: dict[str, pd.DataFrame]) -> None:
    fc, rd = load_results("funnel_cohort"), load_results("retention_drivers")
    d = t["store_daily"]
    strict = {s["stage"]: s["users"] for s in fc["funnel_strict"]}
    for col, stage in (("funnel_eligible", "first_visit"), ("viewed_product", "view_item"), ("added_to_cart", "add_to_cart"),
                       ("started_checkout", "begin_checkout"), ("purchased", "purchase")):
        c.eq(f"store_daily.{col} total = published strict funnel '{stage}'", int(d[col].sum()), int(strict[stage]))
    rr = fc["return_rates"]
    for elig, ret, key in (("d7_eligible", "d7_returned", "d7_window"), ("d7_eligible", "d7_exact_returned", "d7_exact_day"),
                           ("d30_eligible", "d30_returned", "d30_window")):
        c.eq(f"store_daily.{elig} total = published {key} n", int(d[elig].sum()), int(rr[key][0]["n"]))
        c.eq(f"store_daily.{ret} total = published {key} returned", int(d[ret].sum()), int(rr[key][0]["x"]))
    for by, key in (("source_raw", "d7_by_channel"), ("device", "d7_by_device")):
        pub = {r["segment"]: (r["n"], r["x"]) for r in rr[key]}
        g = d.groupby(by)[["d7_eligible", "d7_returned"]].sum()
        if by == "device":
            g.index = g.index.str.lower()
        got = {k: (int(v.d7_eligible), int(v.d7_returned)) for k, v in g.iterrows()}
        c.add(f"store_daily d7 by {by} = published {key}", got == {k: tuple(map(int, v)) for k, v in pub.items()},
              f"export={got} published={pub}")
    c.eq("store_daily.new_visitors total = new non-bot users", int(d.new_visitors.sum()),
         int(fc["population"]["new_users"] - fc["population"]["excluded_suspected_bots"]))
    order = ["funnel_eligible", "viewed_product", "added_to_cart", "started_checkout", "purchased"]
    mono = all((d[a] >= d[b]).all() for a, b in zip(order, order[1:]))
    c.add("store_daily: funnel counts never increase along the funnel (every row)", mono)
    c.add("store_daily: eligible ≤ new_visitors and returned ≤ eligible (every row)",
          bool(((d.funnel_eligible <= d.new_visitors) & (d.d7_returned <= d.d7_eligible) & (d.d30_returned <= d.d30_eligible)
                & (d.d7_exact_returned <= d.d7_returned)).all()))

    fl = t["funnel_daily_long"]
    by_stage = fl.groupby("stage_key").users.sum().to_dict()
    c.add("funnel_daily_long totals = published strict funnel (no row multiplication)",
          {k: int(v) for k, v in by_stage.items()} == {k: int(v) for k, v in strict.items()}, f"export={by_stage}")
    c.eq("funnel_daily_long rows = 5 × non-empty store_daily rows", len(fl), 5 * int((d.funnel_eligible > 0).sum()))

    fs = t["funnel_steps"]
    for order_type, key in (("strict", "funnel_strict"), ("any", "funnel_any_order")):
        pub = [s["users"] for s in fc[key]]
        c.add(f"funnel_steps[{order_type}] users = published", list(fs[fs.order_type == order_type].users) == pub)
    worst = fs[(fs.order_type == "strict") & (fs.is_largest_drop == 1)]
    c.add("funnel_steps: exactly one largest drop per order type", len(worst) == 1 and fs.groupby("order_type").is_largest_drop.sum().eq(1).all())

    seg = t["funnel_segments"]
    c.eq("funnel_segments rows = published segment_funnel", len(seg), len(fc["segment_funnel"]))
    c.add("funnel_segments: rate = n_converted / n_entering", bool(np.allclose(seg.rate, seg.n_converted / seg.n_entering)))

    rc = t["retention_cohorts"]
    agg = rc.groupby(["cohort_week", "age_week"]).agg(size=("cohort_size", "sum"), active=("active_users", "sum"),
                                                     obs=("is_fully_observed", "min")).reset_index()
    pub = pd.DataFrame(fc["cohort_matrix"])[["cohort_week", "age_week", "cohort_size", "active_users", "is_fully_observed"]]
    m = agg.merge(pub, on=["cohort_week", "age_week"], how="outer", indicator=True)
    c.add("retention_cohorts: same cohort × age cells as published", bool((m._merge == "both").all()) and len(m) == len(pub),
          f"cells export={len(agg)} published={len(pub)}")
    c.add("retention_cohorts: summed sizes/actives/observability = published cohort matrix",
          bool((m["size"] == m.cohort_size).all() and (m.active == m.active_users).all()
               and (m.obs.astype(bool) == m.is_fully_observed.astype(bool)).all()))
    c.add("retention_cohorts: active_users ≤ cohort_size", bool((rc.active_users <= rc.cohort_size).all()))
    sizes = rc[rc.age_week == 1].cohort_size.sum()
    c.eq("retention_cohorts: cohort sizes (age 1) = new non-bot users", int(sizes), int(d.new_visitors.sum()))
    per_cell = rc.groupby(["cohort_week", "source_raw", "device", "country_group"]).cohort_size.nunique()
    c.add("retention_cohorts: cohort_size constant across ages within a cell", bool((per_cell == 1).all()))

    rt = t["retention_rates"]
    c.add("retention_rates: rate = returned / eligible", bool(np.allclose(rt.rate, rt.returned / rt.eligible)))

    bg = t["behaviour_groups"]
    for key, g in bg.groupby("behaviour_key"):
        c.eq(f"behaviour_groups[{key}] users = published driver population", int(g.users.sum()), int(rd["n"]))
        c.eq(f"behaviour_groups[{key}] return rate = published outcome rate", g.returned.sum() / g.users.sum(), rd["outcome_rate"], 1e-12)
    for key, pub in rd["binary_descriptives"].items():
        g = bg[bg.behaviour_key == key].groupby("did_it")[["users", "returned"]].sum()
        for flag, k in ((1, "returned_if_1"), (0, "returned_if_0")):
            c.add(f"behaviour_groups[{key}, did_it={flag}] = published descriptive",
                  int(g.loc[flag, "users"]) == pub[k]["n"] and int(g.loc[flag, "returned"]) == pub[k]["x"])
    bm = t["behaviour_model"]
    main = bm[bm.model == "main"].set_index("feature").odds_ratio
    pub_or = {r["feature"]: r["odds_ratio"] for r in rd["logit"]}
    c.add("behaviour_model: main odds ratios = published logit", len(main) == len(pub_or)
          and all(abs(main[k] - v) < TOL for k, v in pub_or.items()))
    c.eq("followup_test_design rows = published designs", len(t["followup_test_design"]), len(rd["follow_up_experiment"]["designs"]))


def _game(c: Checks, t: dict[str, pd.DataFrame]) -> None:
    cc = load_results("cookie_cats")
    raw = load_public("cookie_cats")
    a = t["game_arms"].set_index("variant")
    c.eq("game_arms: players gate_30 = published n", int(a.loc["gate_30", "players"]), cc["n"]["gate_30"])
    c.eq("game_arms: players gate_40 = published n", int(a.loc["gate_40", "players"]), cc["n"]["gate_40"])
    c.eq("game_arms: total players = raw rows", int(a.players.sum()), len(raw))
    for m, key in (("d7", "primary_d7"), ("d1", "guardrail_d1")):
        c.eq(f"game_arms: {m} control rate = published", a.loc["gate_30", f"{m}_rate"], cc[key]["rate_control"])
        c.eq(f"game_arms: {m} treatment rate = published", a.loc["gate_40", f"{m}_rate"], cc[key]["rate_treatment"])
    e = t["game_effects"]
    prim = e[(e.metric_key == "game_d7") & (e.role == "primary")].iloc[0]
    for col, key in (("estimate", "abs_diff"), ("ci_low", "ci_low"), ("ci_high", "ci_high")):
        c.eq(f"game_effects primary {col} = published (pp)", prim[col], 100 * cc["primary_d7"][key], 1e-9)
    c.eq("game_effects primary p-value = published", prim.p_value, cc["primary_d7"]["p_value"])
    g1 = e[(e.metric_key == "game_d1") & (e.role == "guardrail")].iloc[0]
    c.add("game_effects d1 guardrail status = published", (g1.status == "pass") == (cc["guardrail_d1"]["status"] == "pass"))
    c.add("game_effects: d7 effect = arm difference", abs(prim.estimate - 100 * (a.loc["gate_40", "d7_rate"] - a.loc["gate_30", "d7_rate"])) < 1e-9)
    ch = t["game_checks"].set_index("check_key")
    c.eq("game_checks decision = published", ch.loc["decision", "status"], cc["decision"])
    c.add("game_checks: revenue marked missing", ch.loc["revenue", "status"] == "missing")


def _email(c: Checks, t: dict[str, pd.DataFrame]) -> None:
    hs = load_results("hillstrom")
    raw = load_public("hillstrom")
    arms = t["email_arms"]
    n = arms[arms.metric_key == "conversion"].set_index("arm").customers
    c.add("email_arms: customers per arm = published n", {k: int(v) for k, v in n.items()} == hs["n"])
    c.eq("email_arms: total customers = raw rows", int(n.sum()), len(raw))
    c.eq("email_arms: 3 arms × 3 measures", len(arms), 9)
    by = arms.set_index(["arm", "metric_key"]).value
    for a in hs["arm_effects"]:
        for m in ("conversion", "visit"):
            c.eq(f"email_arms {a['arm']} {m} = published", by[(a["arm"], m)], a[m]["rate_treatment"])
            c.eq(f"email_arms control {m} = published", by[("No E-Mail", m)], a[m]["rate_control"])
        c.eq(f"email_arms {a['arm']} spend mean = published", by[(a["arm"], "spend")], a["spend"]["mean_treatment"], 1e-9)
    e = t["email_effects"].set_index(["arm", "metric_key"])
    for a in hs["arm_effects"]:
        c.eq(f"email_effects {a['arm']} conversion (pp) = published", e.loc[(a["arm"], "conversion"), "estimate"],
             100 * a["conversion"]["abs_diff"], 1e-9)
        c.eq(f"email_effects {a['arm']} conversion Holm p = published", e.loc[(a["arm"], "conversion"), "p_holm"], a["conversion"]["p_holm"])
        c.eq(f"email_effects {a['arm']} spend = published bootstrap", e.loc[(a["arm"], "spend"), "estimate"], a["spend"]["estimate"])
        c.add(f"email_effects {a['arm']}: conversion = arm difference",
              abs(e.loc[(a["arm"], "conversion"), "estimate"] - 100 * (by[(a["arm"], "conversion")] - by[("No E-Mail", "conversion")])) < 1e-9)
    s = t["email_subgroups"]
    c.eq("email_subgroups rows = published", len(s), len(hs["subgroups_conversion"]))
    c.eq("email_subgroups surviving Holm = published", int(s.survives_holm.sum()), hs["subgroup_summary"]["n_survive_holm"])
    c.eq("email_interactions rows = 2 outcomes × 12", len(t["email_interactions"]), 24)


def _overview(c: Checks, t: dict[str, pd.DataFrame]) -> None:
    k = t["overview_kpis"].set_index("metric_key")
    fs = t["funnel_steps"]
    st = fs[fs.order_type == "strict"].set_index("stage_key")
    c.eq("overview: product view rate = funnel_steps", k.loc["product_view_rate", "value"], st.loc["view_item", "share_of_entrants"])
    c.eq("overview: purchase conversion = funnel_steps", k.loc["purchase_conversion", "value"], st.loc["purchase", "share_of_entrants"])
    rr = t["retention_rates"]
    d7 = rr[(rr.window == "d7") & (rr.segment_type == "all")].iloc[0]
    c.eq("overview: D7 return = retention_rates", k.loc["return_d7", "value"], d7.rate)
    ge = t["game_effects"]
    c.eq("overview: game effect = game_effects", k.loc["game_d7_effect", "value"],
         ge[(ge.metric_key == "game_d7") & (ge.role == "primary")].estimate.iloc[0])
    c.eq("overview: AOV = published opportunity sizing", k.loc["avg_order_value", "value"],
         load_results("funnel_cohort")["opportunity_sizing"]["aov_usd_mean"])
    obs = k[k.nature == S.OBS]
    c.add("overview: observational KPIs never carry experiment datasets", set(obs.dataset_key) == {"ga4"})
    cat = t["dataset_catalog"].set_index("dataset_key")
    c.add("dataset_catalog: causal claims only for randomized datasets",
          bool((cat.causal_claims_allowed == (cat.nature == "randomized experiment").astype(int)).all()))
    c.add("dataset_catalog: synthetic data marked and not exported", cat.loc["synthetic", "nature"] == "simulated"
          and int(cat.loc["synthetic", "exported"]) == 0)


def validate(tables: dict[str, pd.DataFrame]) -> list[dict]:
    c = Checks()
    for section in (_schema, _ga4, _game, _email, _overview):
        try:
            section(c, tables)
        except Exception as exc:  # a malformed table must fail validation, not crash it
            c.add(f"{section.__name__.strip('_')} section ran without error", False, f"{type(exc).__name__}: {exc}")
    return c.out

"""Funnel and cohort-retention analysis of the GA4 sample (observational).

Population: new users (first_visit inside the sample), not flagged as bots.
Funnel window: [t0, t0 + 7 days), so users must have t0 + 7d <= sample end.
All findings are associations in an obfuscated public sample.
"""

from __future__ import annotations

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.analysis.stats_utils import (  # noqa: E402
    chi2_independence, compare_proportions, fig_path, holm, rate_with_ci, save_results)
from src.settings import PATHS  # noqa: E402

STAGES = ["first_visit", "view_item", "add_to_cart", "begin_checkout", "purchase"]
MIN_STAGE_USERS = 500
MIN_COHORT = 100
SIMULATED_DROP_REDUCTION_PP = 1.0  # scenario input for opportunity sizing


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(PATHS.processed_ga4 / "pulseiq_ga4.duckdb"), read_only=True)


def funnel_frame(con) -> pd.DataFrame:
    return con.execute("""
        SELECT * FROM funnel_new_users WHERE NOT is_suspected_bot AND obs_7d
    """).df()


def stage_counts(df: pd.DataFrame, order: str = "strict") -> pd.DataFrame:
    cols = ["first_visit"] + [f"{order}_{s}" for s in STAGES[1:]]
    counts = [len(df)] + [int(df[c].sum()) for c in cols[1:]]
    rows = []
    for i, s in enumerate(STAGES):
        r = {"stage": s, "users": counts[i], "share_of_entrants": counts[i] / counts[0]}
        if i > 0:
            ci = rate_with_ci(counts[i], counts[i - 1])
            r.update({"conv_from_prev": ci["rate"], "conv_ci_low": ci["ci_low"], "conv_ci_high": ci["ci_high"],
                      "users_lost": counts[i - 1] - counts[i], "drop_rate": 1 - ci["rate"]})
        rows.append(r)
    return pd.DataFrame(rows)


def median_times(df: pd.DataFrame) -> dict:
    out = {}
    for c in ("sec_visit_to_view", "sec_view_to_cart", "sec_cart_to_checkout", "sec_checkout_to_purchase"):
        v = df[c].dropna()
        out[c] = {"median_minutes": float(v.median() / 60) if len(v) else None, "n": int(len(v))}
    return out


def segment_funnel(df: pd.DataFrame, by: str, order: str = "strict") -> pd.DataFrame:
    """Stage-to-stage conversion per segment, with Wilson CIs and difference vs overall."""
    overall = stage_counts(df, order).set_index("stage")
    rows = []
    for seg, g in df.groupby(by):
        sc = stage_counts(g, order).set_index("stage")
        for i, s in enumerate(STAGES[1:], start=1):
            prev = STAGES[i - 1]
            n_prev = int(sc.loc[prev, "users"])
            if n_prev < MIN_STAGE_USERS:
                continue
            x = int(sc.loc[s, "users"])
            ci = rate_with_ci(x, n_prev)
            # segment vs everyone else at the same stage (Newcombe CI)
            rest_prev = int(overall.loc[prev, "users"]) - n_prev
            rest_x = int(overall.loc[s, "users"]) - x
            vs_rest = compare_proportions(x, n_prev, rest_x, rest_prev)
            rows.append({"segment_type": by, "segment": seg, "stage": s, "n_entering": n_prev, "n_converted": x,
                         "rate": ci["rate"], "ci_low": ci["ci_low"], "ci_high": ci["ci_high"],
                         "overall_rate": float(overall.loc[s, "conv_from_prev"]),
                         "diff_vs_overall_pp": 100 * (ci["rate"] - overall.loc[s, "conv_from_prev"]),
                         "diff_vs_rest_pp": 100 * vs_rest["abs_diff"], "vs_rest_ci_low_pp": 100 * vs_rest["ci_low"],
                         "vs_rest_ci_high_pp": 100 * vs_rest["ci_high"], "vs_rest_p": vs_rest["p_value"]})
    out = pd.DataFrame(rows)
    if len(out):
        out["vs_rest_p_holm"] = holm(out.vs_rest_p)
    return out


def simpson_device_check(df: pd.DataFrame, outcome: str = "strict_purchase") -> dict:
    """Raw desktop-vs-mobile gap vs the gap within each channel and after standardizing to a common channel mix."""
    d = df[df.device.isin(["desktop", "mobile"])]
    raw = {}
    for dev in ("desktop", "mobile"):
        g = d[d.device == dev]
        raw[dev] = rate_with_ci(int(g[outcome].sum()), len(g))
    raw_cmp = compare_proportions(raw["mobile"]["x"], raw["mobile"]["n"], raw["desktop"]["x"], raw["desktop"]["n"])
    within, pvals = [], []
    mix = d.channel.value_counts(normalize=True)
    std = {"desktop": 0.0, "mobile": 0.0}
    for ch, g in d.groupby("channel"):
        a, b = g[g.device == "desktop"], g[g.device == "mobile"]
        if min(len(a), len(b)) < MIN_STAGE_USERS:
            continue
        r = compare_proportions(int(b[outcome].sum()), len(b), int(a[outcome].sum()), len(a))
        within.append({"channel": ch, "n_desktop": len(a), "n_mobile": len(b),
                       "rate_desktop": r["rate_control"], "rate_mobile": r["rate_treatment"],
                       "gap_pp_mobile_minus_desktop": 100 * r["abs_diff"],
                       "ci_low_pp": 100 * r["ci_low"], "ci_high_pp": 100 * r["ci_high"], "p_value": r["p_value"],
                       "desktop_share_of_channel": len(a) / (len(a) + len(b))})
        pvals.append(r["p_value"])
        for dev, gg in (("desktop", a), ("mobile", b)):
            std[dev] += mix[ch] * gg[outcome].mean()
    within_df = pd.DataFrame(within)
    if len(within_df):
        within_df["p_holm"] = holm(within_df.p_value)
    covered = mix[within_df.channel].sum() if len(within_df) else 0
    return {"outcome": outcome,
            "raw": {"desktop": raw["desktop"], "mobile": raw["mobile"],
                    "gap_pp": 100 * raw_cmp["abs_diff"], "ci_low_pp": 100 * raw_cmp["ci_low"],
                    "ci_high_pp": 100 * raw_cmp["ci_high"], "p_value": raw_cmp["p_value"]},
            "within_channel": within_df,
            "channel_standardized_gap_pp": 100 * (std["mobile"] - std["desktop"]) / covered if covered else None,
            "channel_mix_share_covered": float(covered)}


def opportunity_sizing(con, counts: pd.DataFrame, reduction_pp: float = SIMULATED_DROP_REDUCTION_PP) -> dict:
    """Scenario: if drop-off at the biggest stage fell by `reduction_pp`, how much revenue might follow?

    Extra users at the next stage x their observed downstream conversion to purchase x AOV.
    """
    c = counts.set_index("stage")
    big = c["users_lost"].idxmax()
    i = STAGES.index(big)
    prev = STAGES[i - 1]
    n_prev = int(c.loc[prev, "users"])
    extra_next = n_prev * reduction_pp / 100
    downstream = c.loc["purchase", "users"] / c.loc[big, "users"]
    aov = con.execute("""
        WITH t AS (SELECT transaction_id, max(purchase_revenue_in_usd) rev FROM stg_events
                   WHERE event_name = 'purchase' AND transaction_id IS NOT NULL AND purchase_revenue_in_usd > 0
                   GROUP BY 1)
        SELECT avg(rev), median(rev), count(*) FROM t""").fetchone()
    extra_buyers = extra_next * downstream
    return {"label": "ESTIMATE / SCENARIO - not a forecast", "biggest_drop_stage": f"{prev} -> {big}",
            "users_at_stage": n_prev, "users_lost": int(c.loc[big, "users_lost"]),
            "drop_rate": float(c.loc[big, "drop_rate"]), "scenario_drop_reduction_pp": reduction_pp,
            "extra_users_reaching_stage": extra_next, "observed_downstream_conversion_to_purchase": float(downstream),
            "extra_purchasers": extra_buyers, "aov_usd_mean": float(aov[0]), "aov_usd_median": float(aov[1]),
            "n_transactions": int(aov[2]), "extra_revenue_usd": extra_buyers * float(aov[0]),
            "window": "new users observed 7 days, 2020-11-01..2021-01-24 first visits (~12 weeks)",
            "assumptions": ["downstream conversion of marginal users equals that of current users",
                            "AOV unchanged", "no cannibalisation; obfuscated sample revenue"]}


# --------------------------------------------------------------------------------- cohorts
def cohort_matrix(con, by: str | None = None) -> pd.DataFrame:
    group = f", {by}" if by else ""
    return con.execute(f"""
        SELECT cohort_week, cohort_week_start{group}, age_week,
               sum(cohort_size) AS cohort_size, sum(active_users) AS active_users,
               bool_and(is_fully_observed) AS is_fully_observed
        FROM cohort_retention_weekly GROUP BY ALL ORDER BY ALL""").df().assign(
        retention=lambda d: d.active_users / d.cohort_size)


def returns_frame(con) -> pd.DataFrame:
    return con.execute("""
        SELECT n.*, r.* EXCLUDE (user_pseudo_id) FROM new_users n JOIN user_return_flags r USING (user_pseudo_id)
        WHERE NOT n.is_suspected_bot""").df()


def return_rates(df: pd.DataFrame, by: str | None, metric: str, obs: str) -> pd.DataFrame:
    d = df[df[obs]]
    groups = d.groupby(by) if by else [("all", d)]
    rows = []
    for k, g in groups:
        ci = rate_with_ci(int(g[metric].sum()), len(g))
        rows.append({"segment": k, **ci})
    return pd.DataFrame(rows)


def compare_cohorts_equal_age(m: pd.DataFrame, ages=(1, 2, 3, 4)) -> pd.DataFrame:
    """Chi-square of retention across cohorts at each age (observed, >= MIN_COHORT users); Holm across ages."""
    rows = []
    for k in ages:
        g = m[(m.age_week == k) & m.is_fully_observed & (m.cohort_size >= MIN_COHORT)]
        if len(g) < 2:
            continue
        t = np.column_stack([g.active_users, g.cohort_size - g.active_users])
        r = chi2_independence(t)
        rows.append({"age_week": k, "n_cohorts": len(g), "min_rate": g.retention.min(), "max_rate": g.retention.max(),
                     **r})
    out = pd.DataFrame(rows)
    if len(out):
        out["p_holm"] = holm(out.p_value)
    return out


def segment_return_tests(df: pd.DataFrame, metric: str, obs: str) -> pd.DataFrame:
    rows = []
    for by in ("channel", "device"):
        d = df[df[obs]]
        counts = d.groupby(by)[metric].agg(["sum", "count"])
        counts = counts[counts["count"] >= MIN_COHORT]
        t = np.column_stack([counts["sum"], counts["count"] - counts["sum"]])
        rows.append({"segment_type": by, "metric": metric, "n_segments": len(counts), **chi2_independence(t)})
    out = pd.DataFrame(rows)
    out["p_holm"] = holm(out.p_value)
    return out


# --------------------------------------------------------------------------------- figures
def plot_funnel(counts: pd.DataFrame, path):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(counts.stage[::-1], counts.users[::-1], color="#3b6ea5")
    for y, (u, s) in enumerate(zip(counts.users[::-1], counts.share_of_entrants[::-1])):
        ax.text(u, y, f" {u:,} ({s:.1%})", va="center", fontsize=9)
    ax.set_xlabel("users (strict order, 7-day window)")
    ax.set_title("GA4 new-user funnel (observational, obfuscated sample)")
    ax.set_xlim(0, counts.users.max() * 1.3)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def plot_segment_ci(seg: pd.DataFrame, stage: str, path):
    g = seg[seg.stage == stage].sort_values("rate")
    fig, ax = plt.subplots(figsize=(8, max(3, 0.35 * len(g))))
    y = np.arange(len(g))
    ax.errorbar(g.rate, y, xerr=[g.rate - g.ci_low, g.ci_high - g.rate], fmt="o", color="#3b6ea5", capsize=3)
    ax.axvline(g.overall_rate.iloc[0], color="grey", ls="--", label="overall")
    ax.set_yticks(y, [f"{t}: {s}" for t, s in zip(g.segment_type, g.segment)])
    ax.set_xlabel(f"conversion into {stage} (95% Wilson CI)")
    ax.legend(); fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def plot_heatmap(m: pd.DataFrame, path, title):
    piv = m.pivot(index="cohort_week_start", columns="age_week", values="retention")
    mask = m.pivot(index="cohort_week_start", columns="age_week", values="is_fully_observed")
    sizes = m.groupby("cohort_week_start").cohort_size.first()
    data = piv.where(mask.astype(bool))
    fig, ax = plt.subplots(figsize=(10, 5.5))
    im = ax.imshow(data.values, cmap="Blues", aspect="auto", vmin=0, vmax=np.nanmax(data.values) or 0.1)
    for i in range(data.shape[0]):
        small = sizes.iloc[i] < MIN_COHORT
        for j in range(data.shape[1]):
            v = data.values[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.1%}", ha="center", va="center", fontsize=7, color="grey" if small else "black")
    ax.set_xticks(range(data.shape[1]), data.columns)
    ax.set_yticks(range(data.shape[0]), [f"{pd.Timestamp(d).date()} (n={int(sizes.iloc[i]):,})" for i, d in enumerate(data.index)])
    ax.set_xlabel("weeks since first visit (blank = censored)"); ax.set_title(title)
    fig.colorbar(im, ax=ax, label="share active in week k"); fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


# --------------------------------------------------------------------------------- run
def run() -> dict:
    con = connect()
    excl = con.execute("SELECT count(*) FILTER (WHERE is_suspected_bot), count(*) FROM new_users").fetchone()
    df = funnel_frame(con)
    strict, anyo = stage_counts(df, "strict"), stage_counts(df, "any")
    seg = pd.concat([segment_funnel(df, "channel"), segment_funnel(df, "device"),
                     segment_funnel(df.assign(country=np.where(df.country.isin(df.country.value_counts().index[:10]),
                                                               df.country, "Other")), "country")], ignore_index=True)
    simpson = simpson_device_check(df)
    simpson_atc = simpson_device_check(df, "strict_add_to_cart")
    opp = opportunity_sizing(con, strict)

    rdf = returns_frame(con)
    m_all, m_ch, m_dev = cohort_matrix(con), cohort_matrix(con, "channel"), cohort_matrix(con, "device")
    res = {
        "population": {"new_users": excl[1], "excluded_suspected_bots": excl[0], "funnel_users_obs_7d": len(df)},
        "funnel_strict": strict, "funnel_any_order": anyo, "median_time_between_stages": median_times(df),
        "segment_funnel": seg, "simpson_device_purchase": simpson, "simpson_device_add_to_cart": simpson_atc,
        "opportunity_sizing": opp,
        "return_rates": {
            "d7_window": return_rates(rdf, None, "return_d7", "obs_d7"),
            "d7_exact_day": return_rates(rdf, None, "return_d7_exact", "obs_d7"),
            "d30_window": return_rates(rdf, None, "return_d30", "obs_d30"),
            "d7_by_channel": return_rates(rdf, "channel", "return_d7", "obs_d7"),
            "d7_by_device": return_rates(rdf, "device", "return_d7", "obs_d7"),
            "d30_by_channel": return_rates(rdf, "channel", "return_d30", "obs_d30"),
            "d30_by_device": return_rates(rdf, "device", "return_d30", "obs_d30"),
        },
        "segment_return_tests": pd.concat([segment_return_tests(rdf, "return_d7", "obs_d7"),
                                           segment_return_tests(rdf, "return_d30", "obs_d30")], ignore_index=True),
        "cohort_matrix": m_all, "cohort_equal_age_tests": compare_cohorts_equal_age(m_all),
        "d30_observable_cohorts": "first visits on or before 2021-01-01 only (~9 of 13 weeks)",
    }
    res["segment_return_tests"]["p_holm_all"] = holm(res["segment_return_tests"].p_value)
    plot_funnel(strict, fig_path("funnel_strict"))
    plot_segment_ci(seg[seg.segment_type != "country"], "purchase", fig_path("segment_purchase_ci"))
    plot_segment_ci(seg[seg.segment_type != "country"], "view_item", fig_path("segment_view_item_ci"))
    plot_heatmap(m_all, fig_path("cohort_heatmap"), "Weekly retention by first-visit cohort (GA4, observational)")
    res["cohort_by_channel"] = m_ch
    res["cohort_by_device"] = m_dev
    save_results("funnel_cohort", res)
    con.close()
    return res


if __name__ == "__main__":
    r = run()
    print(r["funnel_strict"].to_string(index=False))
    print(r["opportunity_sizing"])

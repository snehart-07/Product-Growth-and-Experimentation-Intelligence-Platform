"""Build the one-page decision memo (memo/decision_memo.pdf) and the ER diagram (docs/er_diagram.png).

Every number is read from reports/results/*.json, so the memo cannot drift from the analysis.

Usage:  python -m src.reporting.build_reports
"""

from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from src.analysis.stats_utils import load_results  # noqa: E402
from src.settings import ROOT  # noqa: E402


def memo_sections() -> list[tuple[str, list[str]]]:
    fc, rd, cc, hs = (load_results(n) for n in ("funnel_cohort", "retention_drivers", "cookie_cats", "hillstrom"))
    st = {r["stage"]: r for r in fc["funnel_strict"]}
    opp = fc["opportunity_sizing"]
    d7 = fc["return_rates"]["d7_window"][0]
    p7, g1 = cc["primary_d7"], cc["guardrail_d1"]
    ce = {a["arm"]: a for a in hs["arm_effects"]}
    fu = rd["follow_up_experiment"]
    des = {d["mde_pp_absolute"]: d for d in fu["designs"]}
    pv = rd["unadjusted_top_or"]
    ss = hs["subgroup_summary"]
    return [
        ("Key findings", [
            f"1. The biggest loss is before any product is seen. Of {st['first_visit']['users']:,} new GA4 visitors, "
            f"only {st['view_item']['conv_from_prev']:.1%} view a product within 7 days (95% CI "
            f"{st['view_item']['conv_ci_low']:.1%}–{st['view_item']['conv_ci_high']:.1%}); {st['purchase']['share_of_entrants']:.2%} "
            "buy. No channel, device or country differs from the rest after Holm, and the device gap is "
            f"{fc['simpson_device_purchase']['raw']['gap_pp']:+.2f} pp raw vs "
            f"{fc['simpson_device_purchase']['channel_standardized_gap_pp']:+.2f} pp within-channel (no Simpson effect).",
            f"2. Few visitors return: D7 return {d7['rate']:.1%} (CI {d7['ci_low']:.1%}–{d7['ci_high']:.1%}); early-November "
            "cohorts retained best, December holiday cohorts worst (cohorts differ at every age, Holm). First-day page depth is the "
            f"strongest positive association with 30-day return (OR {pv['odds_ratio']:.2f} per SD, CI "
            f"{pv['or_ci_low']:.2f}–{pv['or_ci_high']:.2f}): an association, likely partly intent.",
            f"3. Experiments: moving the Cookie Cats gate to level 40 LOWERED D7 retention by {100*-p7['abs_diff']:.2f} pp "
            f"(CI {100*p7['ci_low']:.2f} to {100*p7['ci_high']:.2f}). Both Hillstrom e-mails raised conversion (Mens "
            f"+{100*ce['Mens E-Mail']['conversion']['abs_diff']:.2f} pp, Womens +{100*ce['Womens E-Mail']['conversion']['abs_diff']:.2f} pp, "
            f"Holm); of {ss['n_examined']} subgroups examined {ss['n_survive_holm']} survive Holm, and only Womens × newbie "
            "shows a real interaction.",
        ]),
        ("Recommendations", [
            f"Cookie Cats - DO NOT SHIP gate_40 (keep level 30). D7 CI excludes any gain; D1 guardrail does not pass "
            f"non-inferiority (lower bound {100*g1['ci_low']:.2f} pp vs -1.0 pp margin); rounds stable. Revenue, the real "
            "guardrail, is missing. Next: test an earlier gate or shorter wait with revenue/player pre-specified.",
            "Hillstrom - SHIP the Mens e-mail as default (larger conversion, visit and spend lift); target the Womens e-mail "
            "at new customers, the only Holm-supported interaction. Uplift targeting is exploratory and, at the assumed "
            "$0.10 per e-mail and $1 per visit, loses money either way: a scenario, not a plan.",
            f"Funnel - the sizing scenario (1 pp less first-visit drop-off) is worth about ${opp['extra_revenue_usd']:,.0f} over "
            "12 weeks of this obfuscated sample: small and uncertain. Investigate landing-page relevance first.",
        ]),
        ("First test to run", [
            f"Randomize new visitors 50/50 to a 'continue browsing' landing module vs control. Primary: return in "
            f"[t0+24h, t0+31d) (baseline {fu['baseline_rate']:.1%}). Manipulation check: first-day page views. Guardrail: "
            f"7-day purchase conversion (non-inferiority). MDE 1.0 pp needs {des[1.0]['n_per_arm']:,} per arm "
            f"(about {des[1.0]['enrolment_days_at_observed_traffic']} days of traffic + 31-day window); 0.5 pp needs "
            f"{des[0.5]['n_per_arm']:,} per arm (about {des[0.5]['total_duration_days']} days total).",
        ]),
        ("What the data cannot tell us", [
            "Revenue impact of the gate change; D30+ and long-term effects; novelty effects (no timestamps); true sign-up or "
            "account behaviour (GA4 has cookies and first_visit only, obfuscated); whether driver associations are causal. "
            "Needed: revenue per player, 30-90 day windows, real sign-up/account data, and randomized tests of the drivers.",
        ]),
    ]


def build_memo(path=ROOT / "memo" / "decision_memo.pdf") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
    y = 0.965
    fig.text(0.06, y, "GrowthLab decision memo", fontsize=16, weight="bold")
    y -= 0.022
    fig.text(0.06, y, "Product growth & experimentation analytics · public data (GA4 sample, Cookie Cats, Hillstrom) · "
             "all numbers computed in this repo", fontsize=7.5, color="#555")
    y -= 0.03
    for title, paras in memo_sections():
        fig.text(0.06, y, title, fontsize=11, weight="bold", color="#3b6ea5")
        y -= 0.02
        for p in paras:
            lines = textwrap.wrap(p, 118)
            for ln in lines:
                fig.text(0.07, y, ln, fontsize=8.1)
                y -= 0.0152
            y -= 0.006
        y -= 0.008
    fig.text(0.06, 0.03, "Observational GA4 results are associations. Experiment results are causal only for the assigned "
             "treatment in that population and period.\nSimulations (reports/results/simulation_study.json) validate methods "
             "only. Plans: docs/pre_analysis_plan_*.md (timestamped commits, not pre-registrations).",
             fontsize=6.5, color="#777", va="bottom")
    with PdfPages(path) as pdf:
        pdf.savefig(fig)
    fig.savefig(path.with_suffix(".png"), dpi=110)
    plt.close(fig)
    if y < 0.04:
        raise RuntimeError("memo overflowed one page")


ER = {
    "raw_events (GA4 extract)": (0.05, 0.80, ["event_date, event_timestamp", "event_name, user_pseudo_id", "ga_session_id, page_location",
                                             "device/geo/traffic_source", "ecommerce.*, items[]"]),
    "stg_events": (0.38, 0.80, ["PK event_row_id", "user_pseudo_id, session_key", "event_ts, event_name", "cleaned segments + flags"]),
    "stg_items": (0.72, 0.86, ["PK (event_row_id, item_index)", "item_id, price, quantity"]),
    "event_item_summary": (0.72, 0.66, ["PK/FK event_row_id", "item_rows, revenue"]),
    "sessions": (0.05, 0.50, ["PK session_key", "FK user_pseudo_id", "start, duration, landing_page"]),
    "users": (0.38, 0.50, ["PK user_pseudo_id", "first_touch_channel", "first_device, country"]),
    "user_day_activity": (0.72, 0.42, ["PK (user_pseudo_id, event_date)", "events, sessions"]),
    "new_users": (0.38, 0.24, ["PK/FK user_pseudo_id", "t0, d0, cohort_week", "obs_* censoring flags", "is_suspected_bot"]),
    "funnel_new_users": (0.05, 0.04, ["PK/FK user_pseudo_id", "stage timestamps", "strict_* / any_*"]),
    "user_return_flags": (0.38, 0.02, ["PK/FK user_pseudo_id", "return_d7/d30 (+exact)"]),
    "retention_features": (0.72, 0.16, ["PK/FK user_pseudo_id", "first-24h features", "returned_30d_after_24h"]),
    "cohort_retention_weekly": (0.72, 0.00, ["cohort_week x channel x", "device x age_week"]),
}
EDGES = [("raw_events (GA4 extract)", "stg_events"), ("stg_events", "stg_items"), ("stg_items", "event_item_summary"),
         ("event_item_summary", "stg_events"), ("stg_events", "sessions"), ("stg_events", "users"),
         ("stg_events", "user_day_activity"), ("users", "new_users"), ("new_users", "funnel_new_users"),
         ("new_users", "user_return_flags"), ("new_users", "retention_features"), ("user_day_activity", "cohort_retention_weekly"),
         ("sessions", "users")]


def build_er(path=ROOT / "docs" / "er_diagram.png") -> None:
    fig, ax = plt.subplots(figsize=(12, 9))
    ax.set_xlim(0, 1); ax.set_ylim(-0.04, 1.0); ax.axis("off")
    w, lh = 0.25, 0.022
    centers = {}
    for name, (x, y, fields) in ER.items():
        h = lh * (len(fields) + 1.4)
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004", fc="#eef3f9", ec="#3b6ea5", zorder=2))
        ax.text(x + 0.01, y + h - lh * 0.9, name, weight="bold", fontsize=9, zorder=3)
        for i, f in enumerate(fields):
            ax.text(x + 0.012, y + h - lh * (i + 1.9), f, fontsize=7.5, family="monospace", zorder=3)
        centers[name] = (x + w / 2, y + h / 2)
    for a, b in EDGES:
        (x1, y1), (x2, y2) = centers[a], centers[b]
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", color="#999", lw=0.8,
                                                                      shrinkA=0, shrinkB=0), zorder=1)
    ax.set_title("GA4 event model (sql/ga4/01..08). Grain-preserving: items aggregated before joining to events.", fontsize=10)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


if __name__ == "__main__":
    build_memo(); build_er()
    print("wrote memo/decision_memo.pdf, memo/decision_memo.png, docs/er_diagram.png")

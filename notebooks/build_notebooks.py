"""Build and execute the five analysis notebooks.

The analysis logic lives in tested modules under src/analysis/. Each notebook
runs that code and displays the results, so notebook and module can never
disagree. Run:

    .venv/bin/python notebooks/build_notebooks.py          # build + execute
    .venv/bin/python notebooks/build_notebooks.py --no-exec
"""

from __future__ import annotations

import argparse
from pathlib import Path

import nbformat as nbf
from nbconvert.preprocessors import ExecutePreprocessor

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KERNEL = "pulseiq"  # registered inside .venv: python -m ipykernel install --prefix .venv --name pulseiq

SETUP = """import sys, json, pathlib
ROOT = pathlib.Path.cwd().parent if pathlib.Path.cwd().name == "notebooks" else pathlib.Path.cwd()
sys.path.insert(0, str(ROOT))
import pandas as pd
from IPython.display import Image, display, Markdown
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 30); pd.set_option("display.precision", 4)
FIG = ROOT / "reports" / "figures"
"""


def md(s):
    return nbf.v4.new_markdown_cell(s.strip())


def code(s):
    return nbf.v4.new_code_cell(s.strip())


NOTEBOOKS = {
    "01_funnel_and_cohort_stats.ipynb": [
        md("""# 01 · Funnel and cohort statistics (GA4, observational)
**Data:** GA4 obfuscated Google Merchandise Store sample, full 92 days (2020-11-01 to 2021-01-31), 4.3M events.
**Mapping:** `first_visit` stands in for sign-up, `purchase` for conversion, return visits for retention.
**Population:** new users (first_visit inside the sample) not flagged as bots. The funnel uses a 7-day window from the first visit, so only users with t0 + 7d ≤ the sample end are included.
**Status:** associations in an obfuscated sample. Definitions: `docs/metric_definitions.md`."""),
        code(SETUP),
        code("from src.analysis import funnel_cohort as fc\nr = fc.run()\nr['population']"),
        md("## Funnel, strict order (Wilson 95% CIs on stage-to-stage conversion)"),
        code("display(r['funnel_strict']); display(Image(FIG / 'funnel_strict.png'))"),
        md("## Funnel, any order\nFor checkout, any order exceeds strict order: many users reach checkout without an `add_to_cart` event in the window (for example, \"buy now\" or a cart from before t0)."),
        code("r['funnel_any_order']"),
        code("pd.DataFrame(r['median_time_between_stages']).T"),
        md("## Segments (≥ 500 users entering the stage)\nEach segment is compared with everyone else at the same stage. The CIs are Newcombe intervals, and the Holm correction runs across all segment tests in a dimension."),
        code("seg = r['segment_funnel']\nseg.sort_values('vs_rest_p')[['segment_type','segment','stage','n_entering','rate','ci_low','ci_high','diff_vs_rest_pp','vs_rest_ci_low_pp','vs_rest_ci_high_pp','vs_rest_p','vs_rest_p_holm']].head(15)"),
        code("display(Image(FIG / 'segment_view_item_ci.png')); display(Image(FIG / 'segment_purchase_ci.png'))"),
        md("## Device gap within channel (Simpson's paradox check)"),
        code("s = r['simpson_device_purchase']\nprint('raw gap (mobile - desktop, pp):', round(s['raw']['gap_pp'],3), [round(s['raw']['ci_low_pp'],3), round(s['raw']['ci_high_pp'],3)])\nprint('channel-standardized gap (pp):', round(s['channel_standardized_gap_pp'],3))\ns['within_channel']"),
        md("## Opportunity sizing: a scenario, not a forecast"),
        code("r['opportunity_sizing']"),
        md("## Return rates (window-based, censored users excluded) and sensitivity check"),
        code("for k, v in r['return_rates'].items():\n    print(k); display(v)"),
        code("r['segment_return_tests']"),
        md("## Weekly cohort retention\nA cell is shown only if every user in the cohort was observed through the end of week k. Cohorts below 100 users are greyed out. The sample covers about three months, so 30-day return exists only for first visits on or before 2021-01-01."),
        code("display(Image(FIG / 'cohort_heatmap.png'))\nr['cohort_equal_age_tests']"),
        md("""**Reading.** Cohorts differ significantly at every age (chi-square, Holm). Early-November cohorts retained best. December cohorts, with their holiday traffic, retained worst in week 1. Channel and device show no detectable difference in D7 or D30 return after Holm. These are descriptive patterns in an obfuscated sample, not causal effects."""),
    ],
    "02_retention_drivers.ipynb": [
        md("""# 02 · Retention-driver analysis (GA4): ASSOCIATIONS ONLY
**Outcome:** any event in [t0+24h, t0+31d), for new non-bot users with the full window observed.
**Features:** first 24 hours only (leakage control, asserted in SQL).
**Read with:** `docs/correlation_vs_causation.md`."""),
        code(SETUP),
        code("from src.analysis import retention_drivers as rd\nr = rd.run()\nprint(r['n'], 'users; outcome rate', round(r['outcome_rate'],4))"),
        md("## Logistic regression: odds ratios with 95% CIs\nNumeric features are log1p-transformed and standardized, so each OR is per SD. Reference levels are the largest groups."),
        code("display(r['logit'][['feature','odds_ratio','or_ci_low','or_ci_high','p_value']]); display(Image(FIG / 'retention_driver_or.png'))"),
        md("## Collinearity"),
        code("r['vif']"),
        md("## Raw rates vs adjusted ORs: sign flips"),
        code("pd.DataFrame({k: {'returned_if_1': v['returned_if_1']['rate'], 'returned_if_0': v['returned_if_0']['rate']} for k, v in r['binary_descriptives'].items()}).T"),
        md("## Intent confounder check\nPurchase-intent proxies (began checkout, purchased in the first 24h) are added to the model."),
        code("print('page-view OR unadjusted', round(r['unadjusted_top_or']['odds_ratio'],3), '-> with intent proxies', round(r['intent_adjusted_top_or']['odds_ratio'],3))\nr['intent_proxies_or']"),
        md("## Logistic vs gradient boosting (30% holdout)"),
        code("{k: v for k, v in r['model_comparison'].items() if k != 'gb_permutation_importance'}"),
        code("r['model_comparison']['gb_permutation_importance']"),
        md("## Follow-up experiment for the strongest positive candidate"),
        code("r['follow_up_experiment']"),
    ],
    "03_cookie_cats_experiment.ipynb": [
        md("""# 03 · Cookie Cats A/B test (real, randomized)
This analysis follows `docs/pre_analysis_plan_cookie_cats.md`, which was committed before this notebook ran. That plan is a timestamped commit, not a true pre-registration.
**Missing guardrail:** revenue. Gate placement drives in-app purchases, and the data has none."""),
        code(SETUP),
        code("from src.analysis import cookie_cats as cc\nr = cc.run()"),
        md("## Validity checks"),
        code("print('n', r['n']); print('SRM', r['srm']); print('duplicate userids', r['duplicate_userids']); r['rounds_outliers']"),
        md("**SRM:** p ≈ 0.009. This is below 0.01 but above the pre-specified 0.001 flag threshold, so the plan treats it as not flagged and reports it openly. The imbalance (50.4% in gate_40) is small, and it would only bias the result if assignment or logging depended on the outcome."),
        md("## Primary: D7 retention"),
        code("m = r['primary_d7']\nprint(f\"gate_30 {m['rate_control']:.4f}  gate_40 {m['rate_treatment']:.4f}\")\nprint(f\"diff {100*m['abs_diff']:+.3f} pp  Newcombe CI [{100*m['ci_low']:+.3f}, {100*m['ci_high']:+.3f}]  bootstrap [{100*m['bootstrap']['ci_low']:+.3f}, {100*m['bootstrap']['ci_high']:+.3f}]\")\nprint(f\"relative lift {m['relative_lift']:+.2%}  p = {m['p_value']:.4f}\")\nr['interpretation_of_interval']"),
        md("## Guardrails (non-inferiority)"),
        code("g = r['guardrail_d1']; print('D1', round(100*g['abs_diff'],3), [round(100*g['ci_low'],3), round(100*g['ci_high'],3)], g['status'])\nr['guardrail_rounds']"),
        md("## Power (no observed power)"),
        code("{k: v for k, v in r['power'].items() if k != 'curve'}"),
        code("display(Image(FIG / 'cookie_cats_effects.png'))"),
        md("## Decision"),
        code("print(r['decision'], '-', r['decision_reason']); r['cannot_support']"),
        md("""## Failed-experiment case study
**What happened.** Moving the gate to level 40 *lowered* D7 retention by about 0.8 pp (a 4% relative drop). The CI excludes zero and excludes any gain. D1 retention is also lower, but its CI crosses the 1 pp non-inferiority margin, so that guardrail **does not pass**. The robust (winsorized) mean of rounds played is unchanged.

**What we learned.** An earlier forced break is associated with better week-one retention. One plausible mechanism is that the gate rations play and protects against burnout. That mechanism is a hypothesis, not something the test identified.

**What to test next.** Keep gate_30 and test an *earlier* gate (for example, level 25) or a shorter wait, with revenue per player as a pre-specified guardrail. Detecting a 0.5 pp change in D7 needs about 95,700 players per arm."""),
    ],
    "04_hillstrom_heterogeneity.ipynb": [
        md("""# 04 · Hillstrom e-mail test: arms, heterogeneity, CUPED, uplift
This analysis follows `docs/pre_analysis_plan_hillstrom.md`. It covers a marketing experiment with a two-week window, so it says nothing about retention."""),
        code(SETUP),
        code("from src.analysis import hillstrom as hs\nr = hs.run()\nprint(r['n']); r['srm']"),
        md("## Arm vs control (Holm across the two arms)"),
        code("rows=[]\nfor a in r['arm_effects']:\n    for m in ('conversion','visit'):\n        x=a[m]; rows.append({'arm':a['arm'],'metric':m,'control':x['rate_control'],'treatment':x['rate_treatment'],'diff_pp':100*x['abs_diff'],'ci_low_pp':100*x['ci_low'],'ci_high_pp':100*x['ci_high'],'p_holm':x['p_holm']})\n    s=a['spend']; rows.append({'arm':a['arm'],'metric':'spend (bootstrap)','control':s['mean_control'],'treatment':s['mean_treatment'],'diff_pp':s['estimate'],'ci_low_pp':s['ci_low'],'ci_high_pp':s['ci_high']})\npd.DataFrame(rows)"),
        md("## Interaction tests first (likelihood-ratio, Holm across 12 per outcome)"),
        code("display(r['interaction_tests_conversion']); display(r['interaction_tests_visit'])"),
        md("## Subgroup effects on conversion (Holm main, BH 10% sensitivity)"),
        code("print(r['subgroup_summary'])\ndisplay(Image(FIG / 'hillstrom_subgroups.png'))"),
        md("""**Reading.** 44 subgroup effects were examined. 32 are nominally significant, and 17 survive Holm, most of them Mens-arm subgroups that share the strong overall effect. The only interaction that survives Holm for conversion is **Womens e-mail × newbie**: the Womens e-mail works for new customers and shows no detectable effect for existing ones. The a priori expectation (each e-mail works best for buyers of its own product line) is **not** supported for conversion. For visits, product line interacts strongly with the Womens e-mail."""),
        md("## CUPED on spend (covariate: past-year spend)"),
        code("pd.DataFrame([{ 'arm': c['arm'], 'theta': c['theta'], 'variance_reduction': c['variance_reduction'], 'ci_width_ratio': c['ci_width_ratio']} for c in r['cuped_spend']])"),
        md("CUPED achieves almost no variance reduction (< 0.1%), because past-year spend is barely correlated with spend in a two-week window. That result is reported as it came out."),
        md("## Uplift model: EXPLORATORY\nT-learner (gradient boosting), visit outcome, Womens e-mail vs control, 50% holdout. The economics are a scenario with assumed values."),
        code("display({k: v for k, v in r['uplift'].items() if k != 'qini_curve'}); display(Image(FIG / 'hillstrom_qini.png'))"),
    ],
    "05_simulation_study.ipynb": [
        md("""# 05 · Simulation study: SIMULATED, method validation only
Parameters are anchored on the Cookie Cats sample size and D7 baseline. **No business conclusions.**"""),
        code(SETUP),
        code("from src.analysis import simulation_study as ss\nr = ss.run()\nr['anchor']"),
        md("## A/A tests (10,000)"),
        code("{k: v for k, v in r['aa'].items() if k != 'p_values_sample'}"),
        md("## Power curves: simulation vs analytic"),
        code("pg = r['power']\nsim = pd.DataFrame(pg['simulated_power'], index=pg['n_per_arm']).add_prefix('sim_')\nana = pd.DataFrame(pg['analytic_power'], index=pg['n_per_arm']).add_prefix('analytic_')\npd.concat([sim, ana], axis=1)"),
        md("## Peeking (10 looks)"),
        code("{k: v for k, v in r['peeking'].items() if k != 'obf_z_bounds'}"),
        md("## Multiple comparisons"),
        code("pd.DataFrame(r['multiple_comparisons']['rows'])"),
        md("## Underpowered scenario: a real effect missed"),
        code("r['underpowered']"),
        code("display(Image(FIG / 'simulation_study.png'))"),
        md("""**Reading (simulated).** The A/A false-positive rate is close to 5%, and the p-values are uniform. Simulated power matches the analytic power. Stopping at the first p < 0.05 across 10 looks raises false positives to about 19%, while Pocock and O'Brien-Fleming boundaries hold them near 5%. With 44 null subgroups, at least one false positive is nearly certain without correction (about 89%); Holm keeps that probability near 5%. With 3,000 users per arm, a true 1 pp effect is missed about 84% of the time, and the significant estimates exaggerate it about 2.5 times. A null result is not proof of no effect."""),
    ],
}


def build(execute: bool = True) -> None:
    for name, cells in NOTEBOOKS.items():
        nb = nbf.v4.new_notebook()
        nb.cells = cells
        nb.metadata["kernelspec"] = {"name": KERNEL, "display_name": "PulseIQ (.venv)", "language": "python"}
        if execute:
            ExecutePreprocessor(timeout=1800, kernel_name=KERNEL).preprocess(nb, {"metadata": {"path": str(HERE)}})
        nbf.write(nb, HERE / name)
        print(f"wrote {name}{' (executed)' if execute else ''}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-exec", action="store_true")
    build(not ap.parse_args().no_exec)

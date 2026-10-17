"""Cookie Cats A/B test analysis, following docs/pre_analysis_plan_cookie_cats.md exactly."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402
from statsmodels.stats.power import NormalIndPower  # noqa: E402
from statsmodels.stats.proportion import proportion_effectsize  # noqa: E402

from src.analysis.stats_utils import (  # noqa: E402
    SEED, bootstrap_diff, bootstrap_prop_diff, compare_proportions, fig_path, save_results, srm_test)
from src.analytics.experiments import minimum_detectable_effect  # noqa: E402
from src.connectors.public_datasets import load  # noqa: E402

ALPHA = 0.05
MIN_EFFECT_PP = 0.5
NI_D1_PP = 1.0
NI_ROUNDS = 2.5
SRM_THRESHOLD = 0.001


def decide(primary: dict, d1_pass: bool, rounds_pass: bool, srm_flag: bool) -> tuple[str, str]:
    lo, hi = primary["ci_low"] * 100, primary["ci_high"] * 100
    if srm_flag:
        return "hold", "SRM flagged: assignment must be investigated before any decision."
    if lo > 0 and d1_pass and rounds_pass:
        return "ship", "D7 improved and guardrails passed."
    if lo > 0:
        return "iterate", "D7 improved but a guardrail did not pass."
    if hi < 0:
        return "do_not_ship", ("gate_40 lowered D7 retention (CI entirely below 0). Keep the gate at level 30. "
                               "This is the failed-experiment case.")
    return "hold", "D7 CI includes 0."


def run() -> dict:
    df = load("cookie_cats")
    ctrl, trt = df[df.version == "gate_30"], df[df.version == "gate_40"]
    srm = srm_test(len(ctrl), len(trt), 0.5, threshold=SRM_THRESHOLD)
    srm["flag_at_0_01"] = bool(srm["p_value"] < 0.01)
    dupes = int(df.userid.duplicated().sum())
    p999 = float(df.sum_gamerounds.quantile(0.999))
    outliers = {"max": int(df.sum_gamerounds.max()), "p99_9": p999,
                "n_above_p99_9": int((df.sum_gamerounds > p999).sum()),
                "n_zero_rounds": int((df.sum_gamerounds == 0).sum()),
                "max_row": df.loc[df.sum_gamerounds.idxmax()].to_dict()}

    def prop(col):
        r = compare_proportions(int(trt[col].sum()), len(trt), int(ctrl[col].sum()), len(ctrl), ALPHA)
        r["bootstrap"] = bootstrap_prop_diff(int(ctrl[col].sum()), len(ctrl), int(trt[col].sum()), len(trt), seed=SEED)
        return r

    d7, d1 = prop("retention_7"), prop("retention_1")
    d1_pass = d1["ci_low"] * 100 > -NI_D1_PP

    w_ctrl = np.minimum(ctrl.sum_gamerounds.to_numpy(), p999)
    w_trt = np.minimum(trt.sum_gamerounds.to_numpy(), p999)
    rounds_w = bootstrap_diff(w_ctrl, w_trt, seed=SEED)
    rounds_raw = bootstrap_diff(ctrl.sum_gamerounds.to_numpy(), trt.sum_gamerounds.to_numpy(), seed=SEED)
    rounds_median = bootstrap_diff(ctrl.sum_gamerounds.to_numpy(), trt.sum_gamerounds.to_numpy(),
                                   stat=np.median, n_boot=2000, seed=SEED)
    mw = stats.mannwhitneyu(trt.sum_gamerounds, ctrl.sum_gamerounds, alternative="two-sided")
    rounds_pass = rounds_w["ci_low"] > -NI_ROUNDS

    base7 = float(ctrl.retention_7.mean())
    mde7 = minimum_detectable_effect(base7, len(ctrl), len(trt), ALPHA, 0.8)
    pw = NormalIndPower()
    curve_effects = np.linspace(0.001, 0.02, 40)
    curve = [float(pw.power(abs(proportion_effectsize(base7 + e, base7)), nobs1=len(ctrl),
                            ratio=len(trt) / len(ctrl), alpha=ALPHA)) for e in curve_effects]
    n_for_min_effect = int(np.ceil(pw.solve_power(abs(proportion_effectsize(base7 - MIN_EFFECT_PP / 100, base7)),
                                                  alpha=ALPHA, power=0.8, ratio=1)))
    decision, reason = decide(d7, d1_pass, rounds_pass, srm["srm_detected"])
    rules_out = {
        "ci_pp": [d7["ci_low"] * 100, d7["ci_high"] * 100],
        "rules_out_gain_of_min_effect": bool(d7["ci_high"] * 100 < MIN_EFFECT_PP),
        "rules_out_any_gain": bool(d7["ci_high"] < 0),
    }
    res = {
        "label": "Real public A/B test (retrospective analysis; plan committed before analysis)",
        "n": {"gate_30": len(ctrl), "gate_40": len(trt)}, "srm": srm, "duplicate_userids": dupes,
        "rounds_outliers": outliers,
        "primary_d7": d7, "guardrail_d1": {**d1, "ni_margin_pp": NI_D1_PP, "status": "pass" if d1_pass else "not_pass"},
        "guardrail_rounds": {"winsorized_mean_diff": rounds_w, "raw_mean_diff": rounds_raw,
                             "median_diff": rounds_median, "mann_whitney_p": float(mw.pvalue),
                             "winsor_cap": p999, "ni_margin_rounds": NI_ROUNDS,
                             "status": "pass" if rounds_pass else "not_pass",
                             "means": {"gate_30_winsorized": float(w_ctrl.mean()), "gate_40_winsorized": float(w_trt.mean())}},
        "power": {"baseline_d7": base7, "mde_at_80pct_power_pp": mde7 * 100,
                  "n_per_arm_for_0_5pp": n_for_min_effect,
                  "curve": {"effect_pp": (curve_effects * 100).tolist(), "power": curve}},
        "interpretation_of_interval": rules_out,
        "decision": decision, "decision_reason": reason,
        "missing_guardrail": "Revenue / in-app purchases are not in the data; gate placement drives monetisation.",
        "cannot_support": ["revenue impact", "day-30 retention", "novelty effects (no timestamps)",
                           "segment effects (no pre-treatment covariates)"],
    }
    _plot(res)
    save_results("cookie_cats", res)
    return res


def _plot(r):
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    ax = axes[0]
    rows = [("D7 retention (primary)", r["primary_d7"]), ("D1 retention (guardrail)", r["guardrail_d1"])]
    for i, (name, m) in enumerate(rows):
        ax.errorbar(m["abs_diff"] * 100, i, xerr=[[100 * (m["abs_diff"] - m["ci_low"])], [100 * (m["ci_high"] - m["abs_diff"])]],
                    fmt="o", capsize=4, color="#a5523b")
    ax.axvline(0, color="grey", ls="--"); ax.axvline(-NI_D1_PP, color="#999", ls=":", label="D1 NI margin")
    ax.set_yticks([0, 1], [n for n, _ in rows]); ax.set_xlabel("gate_40 - gate_30 (pp, 95% Newcombe CI)")
    ax.legend(fontsize=8); ax.set_title("Cookie Cats effects")
    ax = axes[1]
    c = r["power"]["curve"]
    ax.plot(c["effect_pp"], c["power"], color="#3b6ea5")
    ax.axhline(0.8, color="grey", ls=":"); ax.axvline(r["power"]["mde_at_80pct_power_pp"], color="#a5523b", ls="--",
                                                     label=f"MDE {r['power']['mde_at_80pct_power_pp']:.2f} pp")
    ax.set_xlabel("true |effect| on D7 (pp)"); ax.set_ylabel("power"); ax.set_title("Power at realised n"); ax.legend()
    fig.tight_layout(); fig.savefig(fig_path("cookie_cats_effects"), dpi=130); plt.close(fig)


if __name__ == "__main__":
    r = run()
    for k in ("n", "srm", "duplicate_userids", "rounds_outliers"):
        print(k, r[k])
    for k in ("primary_d7", "guardrail_d1"):
        m = r[k]; print(k, f"{m['rate_control']:.4f} -> {m['rate_treatment']:.4f} diff {100*m['abs_diff']:+.3f}pp CI [{100*m['ci_low']:+.3f},{100*m['ci_high']:+.3f}] boot [{100*m['bootstrap']['ci_low']:+.3f},{100*m['bootstrap']['ci_high']:+.3f}] p={m['p_value']:.4g} lift={m['relative_lift']:+.3%}")
    print(r["guardrail_d1"]["status"], r["guardrail_rounds"])
    print(r["power"]["mde_at_80pct_power_pp"], r["power"]["n_per_arm_for_0_5pp"], r["interpretation_of_interval"], r["decision"], r["decision_reason"])

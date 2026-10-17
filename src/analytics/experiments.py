"""Experiment statistics engine.

Computes results ONLY from observed assignment, exposure and event-derived
metrics (the ``experiment_user_metrics`` model). It has no access to, and no
parameter for, the generator's ground-truth effects.

Populations:
  * ``itt``     - every eligible randomized user, analysed as assigned.
                  Unbiased for the effect of *offering* the treatment.
  * ``exposed`` - eligible users who were also exposed. Exposure happens after
                  assignment and can depend on the treatment itself (e.g. a
                  slower variant loses low-intent users), so this comparison
                  can be biased. Reported as a diagnostic only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import duckdb
import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize

# Direction in which a metric change is harmful (for guardrails).
HARM_DIRECTION = {"d7_retention": -1, "onboarding_error_rate": +1}
DEFAULT_NI_MARGIN = 0.02  # non-inferiority margin, absolute (2 pp)
NI_MARGINS = {"d7_retention": 0.02, "onboarding_error_rate": 0.01}


@dataclass
class ProportionResult:
    experiment_id: str
    metric: str
    role: str
    population: str
    n_control: int
    n_treatment: int
    rate_control: float
    rate_treatment: float
    abs_diff: float
    ci_low: float
    ci_high: float
    relative_lift: float | None
    z_stat: float
    p_value: float
    significant: bool
    guardrail_status: str | None = None


def wilson_interval(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    z = stats.norm.ppf(1 - alpha / 2)
    p = x / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (centre - half, centre + half)


def newcombe_diff_ci(x_t: int, n_t: int, x_c: int, n_c: int, alpha: float = 0.05) -> tuple[float, float]:
    """Newcombe hybrid score interval (method 10) for p_t - p_c."""
    p_t, p_c = x_t / n_t, x_c / n_c
    l_t, u_t = wilson_interval(x_t, n_t, alpha)
    l_c, u_c = wilson_interval(x_c, n_c, alpha)
    d = p_t - p_c
    low = d - np.sqrt((p_t - l_t) ** 2 + (u_c - p_c) ** 2)
    high = d + np.sqrt((u_t - p_t) ** 2 + (p_c - l_c) ** 2)
    return (float(low), float(high))


def two_proportion_ztest(x_t: int, n_t: int, x_c: int, n_c: int) -> tuple[float, float]:
    p_pool = (x_t + x_c) / (n_t + n_c)
    se = np.sqrt(p_pool * (1 - p_pool) * (1 / n_t + 1 / n_c))
    if se == 0:
        return (0.0, 1.0)
    z = (x_t / n_t - x_c / n_c) / se
    return (float(z), float(2 * stats.norm.sf(abs(z))))


def compare_proportions(x_t, n_t, x_c, n_c, alpha=0.05) -> dict:
    lo, hi = newcombe_diff_ci(x_t, n_t, x_c, n_c, alpha)
    z, p = two_proportion_ztest(x_t, n_t, x_c, n_c)
    rc, rt = x_c / n_c, x_t / n_t
    return {"rate_control": rc, "rate_treatment": rt, "abs_diff": rt - rc, "ci_low": lo, "ci_high": hi,
            "relative_lift": (rt - rc) / rc if rc > 0 else None, "z_stat": z, "p_value": p,
            "significant": bool(p < alpha)}


def srm_test(n_control: int, n_treatment: int, treatment_share: float = 0.5, threshold: float = 0.001) -> dict:
    """Sample-ratio-mismatch chi-square test on group sizes."""
    n = n_control + n_treatment
    expected = [n * (1 - treatment_share), n * treatment_share]
    chi2, p = stats.chisquare([n_control, n_treatment], f_exp=expected)
    return {"n_control": n_control, "n_treatment": n_treatment, "expected_share": treatment_share,
            "observed_share": n_treatment / n if n else np.nan, "chi2": float(chi2), "p_value": float(p),
            "srm_detected": bool(p < threshold), "threshold": threshold}


def minimum_detectable_effect(baseline: float, n_control: int, n_treatment: int,
                              alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest absolute lift detectable with the given sizes (pre-specified power, not observed power)."""
    es = NormalIndPower().solve_power(nobs1=n_control, ratio=n_treatment / n_control, alpha=alpha,
                                      power=power, alternative="two-sided")
    # invert Cohen's h: h = 2*asin(sqrt(p2)) - 2*asin(sqrt(p1))
    p2 = np.sin(np.arcsin(np.sqrt(baseline)) + es / 2) ** 2
    return float(p2 - baseline)


def required_sample_size_per_arm(baseline: float, mde: float, alpha: float = 0.05, power: float = 0.8) -> int:
    h = abs(proportion_effectsize(baseline + mde, baseline))
    return int(np.ceil(NormalIndPower().solve_power(effect_size=h, alpha=alpha, power=power, ratio=1.0)))


def guardrail_status(metric: str, ci_low: float, ci_high: float, margin: float | None = None) -> str:
    """Non-inferiority framing for a guardrail.

    pass         - the CI rules out harm larger than the margin
    fail         - the CI lies entirely on the harmful side of zero
    inconclusive - neither (interval too wide to tell)
    """
    margin = NI_MARGINS.get(metric, DEFAULT_NI_MARGIN) if margin is None else margin
    if HARM_DIRECTION.get(metric, -1) < 0:  # lower is worse
        if ci_low > -margin:
            return "pass"
        return "fail" if ci_high < 0 else "inconclusive"
    if ci_high < margin:  # higher is worse
        return "pass"
    return "fail" if ci_low > 0 else "inconclusive"


def load_experiment_data(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame]:
    defs = con.execute("SELECT * FROM raw_experiment_definitions").df()
    metrics = con.execute("SELECT * FROM experiment_user_metrics").df()
    return defs, metrics


def analyze_experiment(defn: pd.Series, metrics: pd.DataFrame, population: str = "itt",
                       ni_margin: float | None = None) -> dict:
    """Analyse one experiment from observed data. Returns SRM, per-metric results, power and decision."""
    if population not in ("itt", "exposed"):
        raise ValueError("population must be 'itt' or 'exposed'")
    alpha = float(defn["significance_level"])
    df = metrics[(metrics.experiment_id == defn["experiment_id"]) & metrics.eligible_at_assignment
                 & metrics.observable_d7]
    if population == "exposed":
        df = df[df.is_exposed]
    c, t = df[df.variant == "control"], df[df.variant == "treatment"]
    srm = srm_test(len(c), len(t), float(defn["treatment_share"]))

    roles = [(defn["primary_metric"], "primary")]
    roles += [(m, "secondary") for m in defn["secondary_metrics"]]
    roles += [(m, "guardrail") for m in defn["guardrail_metrics"]]
    results = []
    for metric, role in roles:
        r = compare_proportions(int(t[metric].sum()), len(t), int(c[metric].sum()), len(c), alpha)
        res = ProportionResult(defn["experiment_id"], metric, role, population, len(c), len(t), **r)
        if role == "guardrail":
            res.guardrail_status = guardrail_status(metric, res.ci_low, res.ci_high, ni_margin)
        results.append(res)

    primary = results[0]
    mde = minimum_detectable_effect(primary.rate_control, len(c), len(t), alpha, float(defn["target_power"]))
    guard = [r.guardrail_status for r in results if r.role == "guardrail"]
    if srm["srm_detected"]:
        decision, reason = "hold", "Sample ratio mismatch: randomization or logging is suspect; do not trust effects."
    elif primary.significant and primary.abs_diff > 0 and all(g == "pass" for g in guard):
        decision, reason = "ship", "Primary metric improved and all guardrails passed non-inferiority."
    elif primary.significant and primary.abs_diff > 0:
        decision, reason = "iterate", "Primary improved but at least one guardrail failed or is inconclusive."
    elif primary.significant and primary.abs_diff < 0:
        decision, reason = "do_not_ship", "Primary metric got worse."
    elif "fail" in guard:
        decision, reason = "do_not_ship", "No detectable primary gain and a guardrail was harmed."
    else:
        decision, reason = "hold", (f"Inconclusive: the 95% CI [{primary.ci_low:+.3f}, {primary.ci_high:+.3f}] "
                                    "includes zero. A null result is not proof of no effect.")
    return {
        "experiment_id": defn["experiment_id"],
        "population": population,
        "srm": srm,
        "results": [asdict(r) for r in results],
        "power": {"pre_specified_mde": float(defn["minimum_detectable_effect"]),
                  "achieved_mde_at_target_power": mde,
                  "required_n_per_arm_for_pre_specified_mde": required_sample_size_per_arm(
                      primary.rate_control, float(defn["minimum_detectable_effect"]), alpha,
                      float(defn["target_power"]))},
        "exposure_rate": {"control": float(c.is_exposed.mean()) if len(c) else np.nan,
                          "treatment": float(t.is_exposed.mean()) if len(t) else np.nan},
        "decision": decision,
        "decision_reason": reason if population == "itt" else "Exposed-only analysis is diagnostic; decide on ITT.",
    }


def analyze_all(con: duckdb.DuckDBPyConnection, populations=("itt", "exposed")) -> list[dict]:
    defs, metrics = load_experiment_data(con)
    return [analyze_experiment(row, metrics, pop) for _, row in defs.iterrows() for pop in populations]


def results_frame(analyses: list[dict]) -> pd.DataFrame:
    rows = []
    for a in analyses:
        for r in a["results"]:
            rows.append({**r, "decision": a["decision"], "srm_p_value": a["srm"]["p_value"]})
    return pd.DataFrame(rows)

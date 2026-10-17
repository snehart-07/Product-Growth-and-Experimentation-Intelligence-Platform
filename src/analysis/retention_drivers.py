"""Retention-driver analysis (GA4, observational). Associations only - never causal.

Outcome: any event in [t0 + 24h, t0 + 31d) for new, non-bot users with t0 + 31d <= sample end.
Features: first 24 hours only, so they cannot overlap the outcome window (leakage control).
"""

from __future__ import annotations

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import statsmodels.api as sm  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402
from statsmodels.stats.outliers_influence import variance_inflation_factor  # noqa: E402

from src.analysis.stats_utils import SEED, fig_path, rate_with_ci, save_results  # noqa: E402
from src.analytics.experiments import required_sample_size_per_arm  # noqa: E402
from src.settings import PATHS  # noqa: E402

NUMERIC = {  # feature: transform description
    "log_product_views": "log1p(view_item count, first 24h)",
    "log_page_views": "log1p(page_view count, first 24h)",
    "log_engagement_min": "log1p(engagement minutes, first 24h)",
}
HYPOTHESES = {
    "log_page_views": ("Adding a 'continue browsing' module (related collections) to landing pages increases "
                       "first-visit page depth and, through it, 30-day return among new visitors."),
    "log_engagement_min": ("Adding an interactive size/product guide increases first-visit engagement time and "
                           "30-day return among new visitors."),
}
BINARY = {"added_to_cart": "any add_to_cart in first 24h", "used_search": "any view_search_results in first 24h"}


def load() -> pd.DataFrame:
    con = duckdb.connect(str(PATHS.processed_ga4 / "pulseiq_ga4.duckdb"), read_only=True)
    df = con.execute("SELECT * FROM retention_features WHERE NOT is_suspected_bot AND obs_31d").df()
    daily_new = con.execute("SELECT count(*) / count(DISTINCT d0) FROM new_users WHERE NOT is_suspected_bot").fetchone()[0]
    con.close()
    df["log_product_views"] = np.log1p(df.product_views_24h)
    df["log_page_views"] = np.log1p(df.page_views_24h)
    df["log_engagement_min"] = np.log1p(df.engagement_sec_24h / 60)
    df["added_to_cart"] = (df.add_to_cart_24h > 0).astype(int)
    df["used_search"] = (df.searches_24h > 0).astype(int)
    top = df.country.value_counts().index[:8]
    df["country_grp"] = np.where(df.country.isin(top), df.country, "Other")
    df["y"] = df.returned_30d_after_24h.astype(int)
    df.attrs["daily_new_users"] = float(daily_new)
    return df


def design(df: pd.DataFrame, standardize: bool = True) -> tuple[pd.DataFrame, dict]:
    X = df[list(NUMERIC) + list(BINARY)].astype(float).copy()
    scales = {}
    if standardize:
        for c in NUMERIC:
            scales[c] = float(X[c].std())
            X[c] = (X[c] - X[c].mean()) / X[c].std()
    dummies = pd.get_dummies(df[["channel", "device", "country_grp"]].astype(str), drop_first=False, dtype=float)
    # explicit reference levels: largest groups
    ref = [f"channel_{df.channel.mode()[0]}", f"device_{df.device.mode()[0]}", f"country_grp_{df.country_grp.mode()[0]}"]
    X = pd.concat([X, dummies.drop(columns=ref)], axis=1)
    return X, {"scales_sd_of_log_feature": scales, "reference_levels": ref}


def vif_table(X: pd.DataFrame) -> pd.DataFrame:
    cols = list(NUMERIC) + list(BINARY)
    Xc = sm.add_constant(X[cols])
    return pd.DataFrame({"feature": cols,
                         "vif": [variance_inflation_factor(Xc.values, i + 1) for i in range(len(cols))]})


def fit_logit(X: pd.DataFrame, y: pd.Series) -> pd.DataFrame:
    m = sm.Logit(y, sm.add_constant(X)).fit(disp=False, maxiter=200)
    ci = m.conf_int()
    out = pd.DataFrame({"feature": m.params.index, "coef": m.params.values, "odds_ratio": np.exp(m.params.values),
                        "or_ci_low": np.exp(ci[0].values), "or_ci_high": np.exp(ci[1].values),
                        "p_value": m.pvalues.values})
    out = out[out.feature != "const"].copy()
    out["abs_log_or"] = out.coef.abs()
    out.attrs["pseudo_r2"] = float(m.prsquared)
    return out.sort_values("abs_log_or", ascending=False)


def compare_models(X: pd.DataFrame, y: pd.Series) -> dict:
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=SEED, stratify=y)
    lr = LogisticRegression(max_iter=2000).fit(Xtr, ytr)
    gb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=SEED).fit(Xtr, ytr)
    sub = Xte.sample(n=min(20_000, len(Xte)), random_state=SEED)
    pi = permutation_importance(gb, sub, yte.loc[sub.index], scoring="roc_auc", n_repeats=5, random_state=SEED)
    imp = pd.DataFrame({"feature": X.columns, "auc_drop_mean": pi.importances_mean, "auc_drop_sd": pi.importances_std})
    return {"holdout_n": int(len(yte)), "auc_logistic": float(roc_auc_score(yte, lr.predict_proba(Xte)[:, 1])),
            "auc_gradient_boosting": float(roc_auc_score(yte, gb.predict_proba(Xte)[:, 1])),
            "gb_permutation_importance": imp.sort_values("auc_drop_mean", ascending=False).head(12)}


def design_follow_up(df: pd.DataFrame, top_behaviour: str) -> dict:
    base = float(df.y.mean())
    daily = df.attrs["daily_new_users"]
    designs = []
    for mde_pp in (0.5, 1.0):
        n = required_sample_size_per_arm(base, mde_pp / 100, alpha=0.05, power=0.8)
        days = int(np.ceil(2 * n / daily))
        designs.append({"mde_pp_absolute": mde_pp, "n_per_arm": n, "enrolment_days_at_observed_traffic": days,
                        "total_duration_days": days + 31})
    return {
        "candidate_behaviour": top_behaviour,
        "hypothesis": HYPOTHESES.get(top_behaviour, f"Increasing {top_behaviour} in the first 24 hours increases 30-day return."),
        "randomization_unit": "user_pseudo_id (new visitors at first_visit), 50/50",
        "primary_metric": "return within [t0+24h, t0+31d) (same definition as the driver model)",
        "secondary_metrics": [f"{top_behaviour} in first 24h (manipulation check)", "add-to-cart in first 24h",
                              "7-day purchase conversion"],
        "guardrails": ["7-day purchase conversion (non-inferiority, margin 0.1 pp)", "page load time",
                       "bounce rate (single-event sessions)"],
        "baseline_rate": base, "alpha": 0.05, "power": 0.8,
        "observed_new_users_per_day": daily, "designs": designs,
        "note": "Sample sizes from statsmodels NormalIndPower (Cohen's h). Duration adds the 31-day outcome window.",
    }


def plot_or(table: pd.DataFrame, path):
    t = table.sort_values("odds_ratio")
    fig, ax = plt.subplots(figsize=(8, max(4, 0.32 * len(t))))
    y = np.arange(len(t))
    ax.errorbar(t.odds_ratio, y, xerr=[t.odds_ratio - t.or_ci_low, t.or_ci_high - t.odds_ratio], fmt="o",
                color="#a5523b", capsize=3)
    ax.axvline(1, color="grey", ls="--")
    ax.set_xscale("log"); ax.set_yticks(y, t.feature)
    ax.set_xlabel("odds ratio for 30-day return (95% CI, log scale) - ASSOCIATION, not effect")
    ax.set_title("Retention drivers (GA4, observational)")
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def run() -> dict:
    df = load()
    X, meta = design(df)
    vif = vif_table(X)
    logit = fit_logit(X, df.y)
    models = compare_models(X, df.y)
    behaviours = logit[logit.feature.isin(list(NUMERIC) + list(BINARY))]
    strongest_abs = behaviours.iloc[0].feature  # largest |log OR| (may be negative)
    imp = models["gb_permutation_importance"].set_index("feature").auc_drop_mean
    positive = behaviours[behaviours.or_ci_low > 1].assign(importance=lambda d: d.feature.map(imp))
    top = positive.sort_values("importance", ascending=False).iloc[0].feature  # strongest positive candidate
    # intent check: do associations shrink once purchase-intent proxies are added?
    Xi = X.assign(began_checkout=(df.begin_checkout_24h > 0).astype(float),
                  purchased_24h=(df.purchase_24h > 0).astype(float))
    logit_intent = fit_logit(Xi, df.y)
    descriptives = {f: {"returned_if_1": rate_with_ci(int(df.y[df[f] == 1].sum()), int((df[f] == 1).sum())),
                        "returned_if_0": rate_with_ci(int(df.y[df[f] == 0].sum()), int((df[f] == 0).sum()))}
                    for f in BINARY}
    res = {
        "label": "OBSERVATIONAL ASSOCIATIONS - not causal effects",
        "n": int(len(df)), "outcome_rate": float(df.y.mean()),
        "outcome": "any event in [t0+24h, t0+31d)", "population": "new non-bot users with first_visit <= 2021-01-01",
        "feature_definitions": {**NUMERIC, **BINARY, "channel/device/country": "first-touch / first event"},
        "design": meta, "vif": vif, "logit_pseudo_r2": logit.attrs["pseudo_r2"], "logit": logit,
        "model_comparison": models, "top_behaviour": top, "strongest_absolute_association": strongest_abs,
        "strongest_absolute_or": logit[logit.feature == strongest_abs].to_dict("records")[0],
        "strongest_absolute_or_intent_adjusted": logit_intent[logit_intent.feature == strongest_abs].to_dict("records")[0],
        "intent_adjusted_top_or": logit_intent[logit_intent.feature == top].to_dict("records")[0],
        "unadjusted_top_or": logit[logit.feature == top].to_dict("records")[0],
        "intent_proxies_or": logit_intent[logit_intent.feature.isin(["began_checkout", "purchased_24h"])],
        "binary_descriptives": descriptives,
        "follow_up_experiment": design_follow_up(df, top),
    }
    plot_or(logit, fig_path("retention_driver_or"))
    save_results("retention_drivers", res)
    return res


if __name__ == "__main__":
    r = run()
    print(r["logit"].to_string(index=False))
    print(r["vif"]); print({k: v for k, v in r["model_comparison"].items() if k != "gb_permutation_importance"})
    print(r["model_comparison"]["gb_permutation_importance"])
    print("top", r["top_behaviour"], r["unadjusted_top_or"]["odds_ratio"], "->", r["intent_adjusted_top_or"]["odds_ratio"])
    print(r["follow_up_experiment"]["designs"], r["outcome_rate"], r["n"])

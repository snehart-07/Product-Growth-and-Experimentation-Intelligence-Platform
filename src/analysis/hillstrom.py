"""Hillstrom e-mail test, following docs/pre_analysis_plan_hillstrom.md."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import statsmodels.formula.api as smf  # noqa: E402
from scipy import stats  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402

from src.analysis.stats_utils import (  # noqa: E402
    SEED, bh, bootstrap_diff, compare_proportions, fig_path, holm, save_results)
from src.connectors.public_datasets import load  # noqa: E402

CONTROL = "No E-Mail"
ARMS = ("Mens E-Mail", "Womens E-Mail")
COVARIATES = ("recency_grp", "history_segment", "newbie", "zip_code", "channel", "product_line")
MIN_PER_ARM = 200


def prepare() -> pd.DataFrame:
    df = load("hillstrom")
    df["recency_grp"] = pd.cut(df.recency, [0, 3, 6, 9, 12], labels=["1-3", "4-6", "7-9", "10-12"]).astype(str)
    df["product_line"] = np.select([(df.mens == 1) & (df.womens == 1), df.mens == 1], ["both", "mens_only"], "womens_only")
    df["newbie"] = df.newbie.astype(str)
    return df


def arm_effects(df: pd.DataFrame) -> list[dict]:
    c = df[df.segment == CONTROL]
    out = []
    for arm in ARMS:
        t = df[df.segment == arm]
        row = {"arm": arm, "n_treatment": len(t), "n_control": len(c)}
        for m in ("conversion", "visit"):
            row[m] = compare_proportions(int(t[m].sum()), len(t), int(c[m].sum()), len(c))
        row["spend"] = bootstrap_diff(c.spend.to_numpy(), t.spend.to_numpy(), seed=SEED)
        row["spend"]["mean_control"] = float(c.spend.mean()); row["spend"]["mean_treatment"] = float(t.spend.mean())
        out.append(row)
    for m in ("conversion", "visit"):
        adj = holm([r[m]["p_value"] for r in out])
        for r, p in zip(out, adj):
            r[m]["p_holm"] = float(p)
    return out


def interaction_tests(df: pd.DataFrame, outcome: str) -> pd.DataFrame:
    rows = []
    for arm in ARMS:
        d = df[df.segment.isin([arm, CONTROL])].assign(treat=lambda x: (x.segment == arm).astype(int))
        for cov in COVARIATES:
            m0 = smf.logit(f"{outcome} ~ treat + C({cov})", d).fit(disp=False)
            m1 = smf.logit(f"{outcome} ~ treat * C({cov})", d).fit(disp=False)
            lr = 2 * (m1.llf - m0.llf)
            dof = int(m1.df_model - m0.df_model)
            rows.append({"outcome": outcome, "arm": arm, "covariate": cov, "lr_stat": lr, "dof": dof,
                         "p_value": float(stats.chi2.sf(lr, dof))})
    out = pd.DataFrame(rows)
    out["p_holm"] = holm(out.p_value)
    out["p_bh"] = bh(out.p_value)
    return out


def subgroup_effects(df: pd.DataFrame, outcome: str = "conversion") -> pd.DataFrame:
    rows = []
    for arm in ARMS:
        for cov in COVARIATES:
            for level, g in df.groupby(cov):
                t, c = g[g.segment == arm], g[g.segment == CONTROL]
                r = {"arm": arm, "covariate": cov, "level": str(level), "n_treatment": len(t), "n_control": len(c)}
                if min(len(t), len(c)) < MIN_PER_ARM:
                    r["tested"] = False
                else:
                    r.update(compare_proportions(int(t[outcome].sum()), len(t), int(c[outcome].sum()), len(c)))
                    r["tested"] = True
                rows.append(r)
    out = pd.DataFrame(rows)
    tested = out.tested
    out.loc[tested, "p_holm"] = holm(out.loc[tested, "p_value"])
    out.loc[tested, "p_bh"] = bh(out.loc[tested, "p_value"])
    out["survives_holm"] = out.p_holm < 0.05
    out["survives_bh10"] = out.p_bh < 0.10
    return out


def cuped(df: pd.DataFrame) -> list[dict]:
    theta = np.cov(df.spend, df.history)[0, 1] / df.history.var()
    df = df.assign(spend_cuped=df.spend - theta * (df.history - df.history.mean()))
    c = df[df.segment == CONTROL]
    out = []
    for arm in ARMS:
        t = df[df.segment == arm]
        raw = bootstrap_diff(c.spend.to_numpy(), t.spend.to_numpy(), seed=SEED)
        adj = bootstrap_diff(c.spend_cuped.to_numpy(), t.spend_cuped.to_numpy(), seed=SEED)
        pooled = df[df.segment.isin([arm, CONTROL])]
        out.append({"arm": arm, "theta": float(theta),
                    "variance_reduction": float(1 - pooled.spend_cuped.var() / pooled.spend.var()),
                    "raw": raw, "cuped": adj,
                    "ci_width_ratio": (adj["ci_high"] - adj["ci_low"]) / (raw["ci_high"] - raw["ci_low"])})
    return out


def uplift(df: pd.DataFrame, arm: str = "Womens E-Mail", outcome: str = "visit", top_share: float = 0.3,
           cost_per_email: float = 0.10, value_per_visit: float = 1.00) -> dict:
    """EXPLORATORY T-learner uplift with a Qini curve on a holdout."""
    d = df[df.segment.isin([arm, CONTROL])].assign(treat=lambda x: (x.segment == arm).astype(int))
    X = pd.get_dummies(d[list(COVARIATES)], dtype=float).assign(history=d.history, recency=d.recency)
    tr, te = train_test_split(np.arange(len(d)), test_size=0.5, random_state=SEED, stratify=d.treat * 2 + d[outcome])
    y, w = d[outcome].to_numpy(), d.treat.to_numpy()
    models = {}
    for k in (0, 1):
        idx = tr[w[tr] == k]
        models[k] = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, random_state=SEED).fit(X.iloc[idx], y[idx])
    s = models[1].predict_proba(X.iloc[te])[:, 1] - models[0].predict_proba(X.iloc[te])[:, 1]
    order = te[np.argsort(-s)]
    yt, wt = y[order], w[order]
    cum_t, cum_c = np.cumsum(yt * wt), np.cumsum(yt * (1 - wt))
    n_t, n_c = np.cumsum(wt), np.cumsum(1 - wt)
    qini = cum_t - cum_c * np.divide(n_t, n_c, out=np.zeros_like(n_t, dtype=float), where=n_c > 0)
    frac = np.arange(1, len(order) + 1) / len(order)
    k = int(top_share * len(order))
    seg = order[:k]

    def rate(ix, arm_flag):
        m = w[ix] == arm_flag
        return y[ix][m].mean()

    top_uplift = rate(seg, 1) - rate(seg, 0)
    all_uplift = rate(te, 1) - rate(te, 0)
    n_customers = 100_000
    econ = {"label": "SCENARIO - assumed cost and value", "assumptions": {
                "cost_per_email_usd": cost_per_email, "value_per_incremental_visit_usd": value_per_visit,
                "customers": n_customers},
            "email_everyone_net": n_customers * (all_uplift * value_per_visit - cost_per_email),
            f"email_top_{int(top_share*100)}pct_net": n_customers * top_share * (top_uplift * value_per_visit - cost_per_email)}
    return {"label": "EXPLORATORY", "arm": arm, "outcome": outcome, "holdout_n": int(len(te)),
            "qini_auc_normalized": float(np.trapezoid(qini / len(order), frac) - 0.5 * qini[-1] / len(order)),
            "qini_curve": {"fraction": frac[:: max(1, len(frac) // 100)].tolist(),
                           "qini": (qini / len(order))[:: max(1, len(frac) // 100)].tolist()},
            "uplift_top_share": float(top_uplift), "uplift_all": float(all_uplift), "top_share": top_share,
            "economics": econ}


def _plot(sub: pd.DataFrame, up: dict):
    t = sub[sub.tested].copy()
    t["label"] = t.arm.str.replace(" E-Mail", "") + " | " + t.covariate + "=" + t.level
    t = t.sort_values(["arm", "abs_diff"])
    fig, ax = plt.subplots(figsize=(9, max(6, 0.22 * len(t))))
    y = np.arange(len(t))
    colors = np.where(t.survives_holm, "#a5523b", "#9aa9bd")
    for yi, (_, r), col in zip(y, t.iterrows(), colors):
        ax.errorbar(r.abs_diff * 100, yi, xerr=[[100 * (r.abs_diff - r.ci_low)], [100 * (r.ci_high - r.abs_diff)]],
                    fmt="o", color=col, capsize=2, ms=3)
    ax.axvline(0, color="grey", ls="--")
    ax.set_yticks(y, t.label.str.replace("$", r"\$", regex=False), fontsize=7)  # no mathtext
    ax.set_xlabel("conversion uplift vs No E-Mail (pp, 95% Newcombe CI; red = survives Holm)")
    ax.set_title(f"Hillstrom subgroup effects ({len(t)} tested, Holm-corrected)")
    fig.tight_layout(); fig.savefig(fig_path("hillstrom_subgroups"), dpi=130); plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(up["qini_curve"]["fraction"], up["qini_curve"]["qini"], label="T-learner")
    ax.plot([0, 1], [0, up["qini_curve"]["qini"][-1]], "--", color="grey", label="random")
    ax.set_xlabel("share targeted (by predicted uplift)"); ax.set_ylabel("incremental visits / holdout n")
    ax.set_title("Qini curve, Womens e-mail, visit (EXPLORATORY)"); ax.legend()
    fig.tight_layout(); fig.savefig(fig_path("hillstrom_qini"), dpi=130); plt.close(fig)


def run() -> dict:
    df = prepare()
    counts = df.segment.value_counts()
    chi2, p = stats.chisquare(counts.values)
    sub = subgroup_effects(df)
    up = uplift(df)
    res = {
        "label": "Real public marketing experiment (retrospective; plan committed before analysis)",
        "n": counts.to_dict(), "srm": {"chi2": float(chi2), "p_value": float(p), "srm_detected": bool(p < 0.001)},
        "arm_effects": arm_effects(df),
        "interaction_tests_conversion": interaction_tests(df, "conversion"),
        "interaction_tests_visit": interaction_tests(df, "visit"),
        "subgroups_conversion": sub,
        "subgroup_summary": {"n_examined": int(len(sub)), "n_tested": int(sub.tested.sum()),
                             "n_survive_holm": int(sub.survives_holm.sum()),
                             "n_survive_bh10": int(sub.survives_bh10.sum()),
                             "n_nominal_p05": int((sub.p_value < 0.05).sum())},
        "cuped_spend": cuped(df), "uplift": up,
    }
    _plot(sub, up)
    save_results("hillstrom", res)
    return res


if __name__ == "__main__":
    r = run()
    print(r["n"], r["srm"])
    for a in r["arm_effects"]:
        print(a["arm"], {m: (round(100 * a[m]["abs_diff"], 3), round(100 * a[m]["ci_low"], 3), round(100 * a[m]["ci_high"], 3), a[m]["p_holm"]) for m in ("conversion", "visit")}, a["spend"])
    print(r["interaction_tests_conversion"].to_string()); print(r["interaction_tests_visit"].to_string())
    print(r["subgroup_summary"])
    s = r["subgroups_conversion"]; print(s[s.survives_holm][["arm", "covariate", "level", "abs_diff", "ci_low", "ci_high", "p_holm"]])
    for c in r["cuped_spend"]:
        print(c["arm"], c["variance_reduction"], c["ci_width_ratio"], c["raw"], c["cuped"])
    print({k: v for k, v in r["uplift"].items() if k != "qini_curve"})

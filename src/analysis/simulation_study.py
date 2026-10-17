"""Simulation study: method validation only. EVERY figure here is SIMULATED.

Parameters are anchored on the Cookie Cats sample size and D7 baseline so the
methods are validated at the scale of the real test. No business conclusions.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402
from statsmodels.stats.power import NormalIndPower  # noqa: E402
from statsmodels.stats.proportion import proportion_effectsize  # noqa: E402

from src.analysis.stats_utils import SEED, fig_path, load_results, save_results  # noqa: E402

N_SIMS = 10_000
ALPHA = 0.05


def _z_p(xt, nt, xc, nc):
    pt, pc = xt / nt, xc / nc
    pp = (xt + xc) / (nt + nc)
    se = np.sqrt(pp * (1 - pp) * (1 / nt + 1 / nc))
    z = np.where(se > 0, (pt - pc) / np.where(se > 0, se, 1), 0)
    return z, 2 * stats.norm.sf(np.abs(z))


def aa_test(n_c, n_t, p, rng, sims=N_SIMS) -> dict:
    xc, xt = rng.binomial(n_c, p, sims), rng.binomial(n_t, p, sims)
    _, pv = _z_p(xt, n_t, xc, n_c)
    fpr = float((pv < ALPHA).mean())
    se = np.sqrt(fpr * (1 - fpr) / sims)
    ks = stats.kstest(pv, "uniform")
    return {"sims": sims, "false_positive_rate": fpr, "mc_ci": [fpr - 1.96 * se, fpr + 1.96 * se],
            "p_value_uniformity_ks_p": float(ks.pvalue), "p_values_sample": pv[:2000].tolist()}


def power_grid(p, rng, sims=2000) -> dict:
    ns = [2_000, 5_000, 10_000, 20_000, 45_000, 80_000]
    effects = [0.0025, 0.005, 0.0075, 0.01, 0.015]
    sim, analytic = {}, {}
    for e in effects:
        sim[e], analytic[e] = [], []
        for n in ns:
            xc, xt = rng.binomial(n, p, sims), rng.binomial(n, p + e, sims)
            _, pv = _z_p(xt, n, xc, n)
            sim[e].append(float((pv < ALPHA).mean()))
            analytic[e].append(float(NormalIndPower().power(abs(proportion_effectsize(p + e, p)), nobs1=n, alpha=ALPHA)))
    return {"n_per_arm": ns, "effects": effects, "simulated_power": {str(k): v for k, v in sim.items()},
            "analytic_power": {str(k): v for k, v in analytic.items()}, "sims_per_cell": sims}


def peeking(n_per_arm, p, rng, looks=10, sims=N_SIMS) -> dict:
    """Stop at the first look with p < 0.05 vs a Pocock-style constant boundary vs O'Brien-Fleming."""
    sizes = np.linspace(n_per_arm / looks, n_per_arm, looks).astype(int)
    inc = np.diff(np.concatenate([[0], sizes]))
    xc = np.cumsum(rng.binomial(inc[None, :], p, (sims, looks)), axis=1)
    xt = np.cumsum(rng.binomial(inc[None, :], p, (sims, looks)), axis=1)
    z, pv = _z_p(xt, sizes[None, :], xc, sizes[None, :])
    naive = float((pv < ALPHA).any(axis=1).mean())
    final_only = float((pv[:, -1] < ALPHA).mean())
    # Calibrated boundaries from an independent null simulation (alpha-spending equivalents).
    cal = rng.standard_normal((50_000, looks))
    cal = np.cumsum(cal, axis=1) / np.sqrt(np.arange(1, looks + 1))
    pocock_c = float(np.quantile(np.abs(cal).max(axis=1), 1 - ALPHA))
    t = np.arange(1, looks + 1) / looks
    obf_scale = float(np.quantile((np.abs(cal) * np.sqrt(t)).max(axis=1), 1 - ALPHA))
    obf_bounds = obf_scale / np.sqrt(t)
    pocock = float((np.abs(z) > pocock_c).any(axis=1).mean())
    obf = float((np.abs(z) > obf_bounds[None, :]).any(axis=1).mean())
    cum_naive = [float((pv[:, :k] < ALPHA).any(axis=1).mean()) for k in range(1, looks + 1)]
    return {"looks": looks, "sims": sims, "n_per_arm_final": int(n_per_arm),
            "fpr_final_look_only": final_only, "fpr_peeking_naive": naive,
            "fpr_pocock_boundary": pocock, "pocock_z": pocock_c,
            "fpr_obrien_fleming": obf, "obf_z_bounds": obf_bounds.tolist(),
            "cumulative_naive_fpr_by_look": cum_naive}


def multiple_comparisons(rng, m_values=(1, 5, 10, 20, 44), sims=N_SIMS) -> dict:
    out = []
    for m in m_values:
        p = rng.uniform(size=(sims, m))  # independent null tests
        any_raw = float((p < ALPHA).any(axis=1).mean())
        sorted_p = np.sort(p, axis=1)
        holm_thr = ALPHA / (m - np.arange(m))
        rejected = np.cumprod(sorted_p <= holm_thr[None, :], axis=1).any(axis=1)
        out.append({"m": m, "fwer_uncorrected": any_raw, "fwer_holm": float(rejected.mean()),
                    "analytic_uncorrected": 1 - (1 - ALPHA) ** m})
    return {"sims": sims, "rows": out}


def underpowered(p, rng, sims=N_SIMS) -> dict:
    true_effect, n = 0.01, 3_000
    xc, xt = rng.binomial(n, p, sims), rng.binomial(n, p + true_effect, sims)
    _, pv = _z_p(xt, n, xc, n)
    sig = pv < ALPHA
    est = xt / n - xc / n
    return {"true_effect_pp": 100 * true_effect, "n_per_arm": n, "sims": sims,
            "share_missed": float(1 - sig.mean()),
            "mean_estimate_when_significant_pp": float(100 * est[sig].mean()),
            "exaggeration_ratio_when_significant": float(est[sig].mean() / true_effect),
            "share_wrong_sign_when_significant": float((est[sig] < 0).mean())}


def _plots(r):
    tag = "SIMULATED - method validation only"
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    ax = axes[0, 0]
    ax.hist(r["aa"]["p_values_sample"], bins=20, color="#888", edgecolor="white")
    ax.set_title(f"A/A p-values (FPR {r['aa']['false_positive_rate']:.3f})"); ax.set_xlabel("p")
    ax = axes[0, 1]
    pg = r["power"]
    for e, v in pg["simulated_power"].items():
        ax.plot(pg["n_per_arm"], v, marker="o", label=f"{float(e)*100:.2f} pp")
    ax.axvline(r["anchor"]["n_per_arm"], color="#a5523b", ls="--", label="Cookie Cats n")
    ax.axhline(0.8, color="grey", ls=":"); ax.set_xscale("log"); ax.set_title("Power vs n per arm")
    ax.legend(fontsize=7); ax.set_xlabel("n per arm")
    ax = axes[1, 0]
    pk = r["peeking"]
    ax.plot(range(1, pk["looks"] + 1), pk["cumulative_naive_fpr_by_look"], marker="o", label="naive peeking")
    ax.axhline(pk["fpr_pocock_boundary"], color="green", ls="--", label="Pocock boundary")
    ax.axhline(pk["fpr_obrien_fleming"], color="purple", ls=":", label="O'Brien-Fleming")
    ax.axhline(0.05, color="grey"); ax.set_title("False positives with repeated looks"); ax.set_xlabel("looks")
    ax.legend(fontsize=8)
    ax = axes[1, 1]
    mc = r["multiple_comparisons"]["rows"]
    ax.plot([x["m"] for x in mc], [x["fwer_uncorrected"] for x in mc], marker="o", label="uncorrected")
    ax.plot([x["m"] for x in mc], [x["fwer_holm"] for x in mc], marker="o", label="Holm")
    ax.set_title("P(at least one false positive), m null subgroups"); ax.set_xlabel("m"); ax.legend()
    for a in axes.flat:
        a.text(0.99, 0.01, tag, transform=a.transAxes, ha="right", va="bottom", fontsize=7, color="#a5523b")
    fig.suptitle("Simulation study (SIMULATED)"); fig.tight_layout()
    fig.savefig(fig_path("simulation_study"), dpi=130); plt.close(fig)


def run() -> dict:
    rng = np.random.default_rng(SEED)
    cc = load_results("cookie_cats")
    n_c, n_t, p = cc["n"]["gate_30"], cc["n"]["gate_40"], cc["power"]["baseline_d7"]
    n = (n_c + n_t) // 2
    res = {
        "label": "SIMULATED - method validation only; no business conclusions",
        "anchor": {"source": "Cookie Cats sizes and D7 baseline", "n_per_arm": n, "baseline": p,
                   "real_test_mde_pp": cc["power"]["mde_at_80pct_power_pp"]},
        "aa": aa_test(n_c, n_t, p, rng), "power": power_grid(p, rng),
        "peeking": peeking(n, p, rng), "multiple_comparisons": multiple_comparisons(rng),
        "underpowered": underpowered(p, rng),
    }
    _plots(res)
    out = dict(res)
    out["aa"] = {k: v for k, v in res["aa"].items() if k != "p_values_sample"}
    save_results("simulation_study", out)
    return res


if __name__ == "__main__":
    r = run()
    print({k: v for k, v in r["aa"].items() if k != "p_values_sample"})
    print({k: v for k, v in r["peeking"].items() if k not in ("obf_z_bounds",)})
    print(r["multiple_comparisons"]); print(r["underpowered"])
    print(r["power"]["simulated_power"]); print(r["power"]["analytic_power"])

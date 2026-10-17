"""Shared statistics helpers for the GrowthLab analyses."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from src.analytics.experiments import compare_proportions, newcombe_diff_ci, srm_test, wilson_interval  # noqa: F401
from src.settings import ROOT

RESULTS_DIR = ROOT / "reports" / "results"
FIGURES_DIR = ROOT / "reports" / "figures"
SEED = 2026


def holm(pvalues) -> np.ndarray:
    p = np.asarray(pvalues, dtype=float)
    return multipletests(p, method="holm")[1] if len(p) else p


def bh(pvalues) -> np.ndarray:
    p = np.asarray(pvalues, dtype=float)
    return multipletests(p, method="fdr_bh")[1] if len(p) else p


def bootstrap_diff(a: np.ndarray, b: np.ndarray, stat=np.mean, n_boot: int = 10_000, seed: int = SEED,
                   alpha: float = 0.05, chunk: int = 250) -> dict:
    """Percentile bootstrap CI for stat(b) - stat(a), resampling each arm independently (chunked)."""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    out = []
    for start in range(0, n_boot, chunk):
        k = min(chunk, n_boot - start)
        ia = rng.integers(0, len(a), size=(k, len(a)))
        ib = rng.integers(0, len(b), size=(k, len(b)))
        if stat is np.mean:
            out.append(b[ib].mean(axis=1) - a[ia].mean(axis=1))
        else:
            out.append(np.apply_along_axis(stat, 1, b[ib]) - np.apply_along_axis(stat, 1, a[ia]))
    d = np.concatenate(out)
    est = float(stat(b) - stat(a))
    return {"estimate": est, "ci_low": float(np.quantile(d, alpha / 2)),
            "ci_high": float(np.quantile(d, 1 - alpha / 2)), "n_boot": n_boot, "seed": seed}


def bootstrap_prop_diff(x_a: int, n_a: int, x_b: int, n_b: int, n_boot: int = 10_000, seed: int = SEED) -> dict:
    """Parametric bootstrap for a difference of proportions (equivalent to resampling binary rows)."""
    rng = np.random.default_rng(seed)
    d = rng.binomial(n_b, x_b / n_b, n_boot) / n_b - rng.binomial(n_a, x_a / n_a, n_boot) / n_a
    return {"estimate": x_b / n_b - x_a / n_a, "ci_low": float(np.quantile(d, 0.025)),
            "ci_high": float(np.quantile(d, 0.975)), "n_boot": n_boot, "seed": seed}


def rate_with_ci(x: int, n: int) -> dict:
    lo, hi = wilson_interval(int(x), int(n))
    return {"n": int(n), "x": int(x), "rate": (x / n) if n else np.nan, "ci_low": lo, "ci_high": hi}


def chi2_independence(table) -> dict:
    chi2, p, dof, _ = stats.chi2_contingency(np.asarray(table))
    return {"chi2": float(chi2), "p_value": float(p), "dof": int(dof)}


def _to_jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.DataFrame):
        return o.to_dict("records")
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def save_results(name: str, results: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(_clean(results), indent=2, default=_to_jsonable))
    return path


def _clean(o):
    """Replace NaN/inf with None recursively so the JSON is valid."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return _clean(o.to_dict("records"))
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    return o


def load_results(name: str) -> dict:
    return json.loads((RESULTS_DIR / f"{name}.json").read_text())


def fig_path(name: str) -> Path:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    return FIGURES_DIR / f"{name}.png"

"""Tests for the GrowthLab analysis layer.

Unit tests run on constructed data. Tests marked `data` re-check headline results
against the downloaded public datasets and are skipped if the files are absent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import cookie_cats, funnel_cohort, simulation_study
from src.analysis.stats_utils import bh, bootstrap_diff, bootstrap_prop_diff, holm
from src.connectors.public_datasets import DATASETS, raw_dir
from src.pipeline.run_sql import build_connection, run_models
from tests.fixtures import ga4_fixture as fx


def test_holm_and_bh_known_values():
    p = [0.01, 0.04, 0.03, 0.005]
    assert np.allclose(holm(p), [0.03, 0.06, 0.06, 0.02])
    assert np.allclose(bh(p), [0.02, 0.04, 0.04, 0.02])


def test_bootstrap_ci_covers_true_difference():
    rng = np.random.default_rng(1)
    a, b = rng.exponential(1.0, 4000), rng.exponential(1.2, 4000)
    r = bootstrap_diff(a, b, n_boot=2000)
    assert r["ci_low"] < 0.2 < r["ci_high"]
    p = bootstrap_prop_diff(400, 1000, 450, 1000, n_boot=4000)
    assert p["ci_low"] < 0.05 < p["ci_high"]


@pytest.mark.parametrize("ci, d1, rounds, srm, expected", [
    ((-0.013, -0.003), True, True, False, "do_not_ship"),
    ((0.002, 0.01), True, True, False, "ship"),
    ((0.002, 0.01), False, True, False, "iterate"),
    ((-0.004, 0.006), True, True, False, "hold"),
    ((0.002, 0.01), True, True, True, "hold"),
])
def test_cookie_cats_decision_rule_matches_plan(ci, d1, rounds, srm, expected):
    assert cookie_cats.decide({"ci_low": ci[0], "ci_high": ci[1]}, d1, rounds, srm)[0] == expected


def _funnel_df(n=4000, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.random(n) < 0.3
    c = v & (rng.random(n) < 0.4)
    b = c & (rng.random(n) < 0.5)
    p = b & (rng.random(n) < 0.5)
    return pd.DataFrame({"strict_view_item": v, "strict_add_to_cart": c, "strict_begin_checkout": b,
                         "strict_purchase": p, "any_view_item": v, "any_add_to_cart": c, "any_begin_checkout": b,
                         "any_purchase": p, "channel": rng.choice(["A", "B"], n), "device": rng.choice(["desktop", "mobile"], n)})


def test_stage_counts_are_monotone_and_reconcile():
    df = _funnel_df()
    s = funnel_cohort.stage_counts(df)
    assert list(s.users) == sorted(s.users, reverse=True)
    assert s.users.iloc[0] == len(df)
    assert (s.users_lost.iloc[1:] == -s.users.diff().iloc[1:]).all()


def test_simpson_check_detects_mix_driven_gap():
    """Mobile converts better within EVERY channel but worse overall, because mobile is concentrated in a
    low-converting channel. The check must show the within-channel sign, not the raw sign."""
    rows = []
    spec = {("hi", "desktop"): (9000, 0.10), ("hi", "mobile"): (1000, 0.12),
            ("lo", "desktop"): (1000, 0.01), ("lo", "mobile"): (9000, 0.02)}
    for (ch, dev), (n, p) in spec.items():
        k = int(n * p)
        rows += [{"channel": ch, "device": dev, "strict_purchase": i < k} for i in range(n)]
    r = funnel_cohort.simpson_device_check(pd.DataFrame(rows))
    assert r["raw"]["gap_pp"] < 0
    assert (r["within_channel"].gap_pp_mobile_minus_desktop > 0).all()
    assert r["channel_standardized_gap_pp"] > 0


def test_ga4_new_user_marts_on_fixture():
    con = build_connection("ga4", frames={"events": fx.table()}, observation_end=fx.OBSERVATION_END)
    run_models(con, "ga4")
    n = con.execute("SELECT user_pseudo_id FROM new_users ORDER BY 1").fetchall()
    assert [r[0] for r in n] == ["A", "B", "C"]  # D has no first_visit in the window
    f = con.execute("SELECT * FROM funnel_new_users ORDER BY user_pseudo_id").df().set_index("user_pseudo_id")
    assert f.loc["A", "strict_purchase"] and not f.loc["B", "strict_purchase"]
    r = con.execute("SELECT * FROM user_return_flags ORDER BY 1").df().set_index("user_pseudo_id")
    assert r.loc["A", "return_d1"] and not r.loc["B", "return_d7"]
    feat = con.execute("SELECT * FROM retention_features ORDER BY 1").df().set_index("user_pseudo_id")
    assert feat.loc["A", "product_views_24h"] == 1 and feat.loc["A", "add_to_cart_24h"] == 1
    # A returns Nov 2 10:00 = t0 (Nov 1 09:00) + 25h -> inside [t0+24h, t0+31d); B never returns.
    assert bool(feat.loc["A", "returned_30d_after_24h"]) is True
    assert bool(feat.loc["B", "returned_30d_after_24h"]) is False
    # the return visit on Nov 2 must NOT leak into first-24h features
    assert feat.loc["A", "sessions_24h"] == 1
    con.close()


def test_simulation_methods_behave():
    rng = np.random.default_rng(0)
    aa = simulation_study.aa_test(20_000, 20_000, 0.2, rng, sims=3000)
    assert 0.035 < aa["false_positive_rate"] < 0.065
    pk = simulation_study.peeking(20_000, 0.2, rng, looks=10, sims=3000)
    assert pk["fpr_peeking_naive"] > 0.12
    assert pk["fpr_pocock_boundary"] < 0.065 and pk["fpr_obrien_fleming"] < 0.065
    mc = simulation_study.multiple_comparisons(rng, m_values=(20,), sims=3000)["rows"][0]
    assert mc["fwer_uncorrected"] > 0.55 and mc["fwer_holm"] < 0.065


needs_cc = pytest.mark.skipif(not (raw_dir("cookie_cats") / DATASETS["cookie_cats"]["file"]).exists(),
                              reason="Cookie Cats CSV not downloaded")


@needs_cc
def test_cookie_cats_headline_reproduces():
    r = cookie_cats.run()
    assert r["n"] == {"gate_30": 44_700, "gate_40": 45_489}
    assert r["duplicate_userids"] == 0
    assert r["primary_d7"]["abs_diff"] == pytest.approx(-0.0082, abs=2e-4)
    assert r["primary_d7"]["ci_high"] < 0
    assert r["decision"] == "do_not_ship"

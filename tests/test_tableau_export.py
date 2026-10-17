"""Tests for the Tableau export: schema, metric calculations, row counts, and that validation catches defects.

Skipped when the inputs (GA4 marts, results files, experiment CSVs) have not been built.
"""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from src.analysis.stats_utils import RESULTS_DIR, load_results
from src.connectors.public_datasets import DATASETS, raw_dir
from src.reporting import tableau_export as E
from src.reporting import tableau_schema as S
from src.reporting.tableau_validate import validate

INPUTS = [E.GA4_DB, RESULTS_DIR / "funnel_cohort.json", RESULTS_DIR / "hillstrom.json",
          raw_dir("cookie_cats") / DATASETS["cookie_cats"]["file"], raw_dir("hillstrom") / DATASETS["hillstrom"]["file"]]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in INPUTS), reason="Tableau export inputs not built")


@pytest.fixture(scope="module")
def tables():
    return E.build()


def failures(t) -> list[str]:
    return [c["check"] for c in validate(t) if not c["passed"]]


# ----------------------------------------------------------------- schema
def test_every_schema_table_is_built_with_exact_columns(tables):
    assert set(tables) == set(S.BY_NAME)
    for name, df in tables.items():
        assert list(df.columns) == list(S.BY_NAME[name].columns), name


def test_column_types_match_schema(tables):
    for name, df in tables.items():
        for col, (typ, _) in S.BY_NAME[name].columns.items():
            s = df[col].dropna()
            if s.empty:
                continue
            if typ == "int":
                assert pd.api.types.is_integer_dtype(df[col]), (name, col)
            elif typ == "float":
                assert pd.api.types.is_float_dtype(df[col]), (name, col)
            elif typ == "date":
                assert all(hasattr(v, "year") and not hasattr(v, "hour") for v in s.head(50)), (name, col)


def test_conform_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        E.conform("game_arms", pd.DataFrame({"variant": ["gate_30"]}))


def test_full_validation_passes(tables):
    assert failures(tables) == []


# ----------------------------------------------------------------- row counts
@pytest.mark.parametrize("name, rows", [
    ("dim_date", 92), ("dataset_catalog", 4), ("funnel_steps", 10), ("retention_tests", 8), ("followup_test_design", 2),
    ("game_arms", 2), ("game_effects", 7), ("game_power_curve", 40), ("email_arms", 9), ("email_effects", 6),
    ("email_subgroups", 44), ("email_interactions", 24),
])
def test_expected_row_counts(tables, name, rows):
    assert len(tables[name]) == rows


def test_long_tables_have_no_row_multiplication(tables):
    d = tables["store_daily"]
    assert len(tables["funnel_daily_long"]) == 5 * int((d.funnel_eligible > 0).sum())
    rc = tables["retention_cohorts"]
    cells = rc[["cohort_week", "source_raw", "device", "country_group"]].drop_duplicates()
    assert len(rc) == 12 * len(cells)
    bg = tables["behaviour_groups"]
    assert bg.groupby("behaviour_key").users.sum().nunique() == 1  # every behaviour partitions the same population


# ----------------------------------------------------------------- metric calculations
def test_store_daily_reproduces_published_funnel_and_retention(tables):
    fc = load_results("funnel_cohort")
    d = tables["store_daily"]
    assert d.purchased.sum() / d.funnel_eligible.sum() == pytest.approx(fc["funnel_strict"][4]["share_of_entrants"], abs=1e-12)
    assert d.d7_returned.sum() / d.d7_eligible.sum() == pytest.approx(fc["return_rates"]["d7_window"][0]["rate"], abs=1e-12)
    assert d.d30_returned.sum() / d.d30_eligible.sum() == pytest.approx(fc["return_rates"]["d30_window"][0]["rate"], abs=1e-12)


def test_filtered_rate_matches_published_segment(tables):
    """A device filter in Tableau (SUM/SUM on store_daily) must equal the published segment rate."""
    fc = load_results("funnel_cohort")
    d = tables["store_daily"]
    mob = d[d.device == "Mobile"]
    pub = next(r for r in fc["return_rates"]["d7_by_device"] if r["segment"] == "mobile")
    assert mob.d7_returned.sum() / mob.d7_eligible.sum() == pytest.approx(pub["rate"], abs=1e-12)


def test_incomplete_cohort_cells_are_flagged_for_masking(tables):
    rc = tables["retention_cohorts"]
    late = rc[rc.cohort_week_start >= pd.Timestamp("2021-01-17").date()]
    assert (late.is_fully_observed == 0).all()  # the data ends before any of their week-1 windows complete
    # Unknown cells can hold *partial* activity (some visitors reached week k before the data ended).
    # They must therefore be masked via is_fully_observed, never shown as a rate (or as zero).
    unknown = rc[rc.is_fully_observed == 0]
    assert len(unknown) > 0 and (unknown.active_users > 0).any()
    known = rc[rc.is_fully_observed == 1]
    assert known.cohort_week.max() < late.cohort_week.min()


def test_experiment_effects_equal_arm_differences(tables):
    a = tables["game_arms"].set_index("variant")
    e = tables["game_effects"]
    prim = e[(e.metric_key == "game_d7") & (e.role == "primary")].iloc[0]
    assert prim.estimate == pytest.approx(100 * (a.loc["gate_40", "d7_rate"] - a.loc["gate_30", "d7_rate"]), abs=1e-9)
    assert prim.ci_high < 0 and prim.status == "clearly worse"
    arms = tables["email_arms"].set_index(["arm", "metric_key"]).value
    ef = tables["email_effects"].set_index(["arm", "metric_key"]).estimate
    for arm in ("Mens E-Mail", "Womens E-Mail"):
        assert ef[(arm, "conversion")] == pytest.approx(100 * (arms[(arm, "conversion")] - arms[("No E-Mail", "conversion")]), abs=1e-9)


def test_experiments_stay_separate(tables):
    for name in ("game_arms", "game_effects", "game_checks", "game_power_curve"):
        assert S.BY_NAME[name].dataset == "cookie_cats"
    for name in ("email_arms", "email_effects", "email_subgroups", "email_interactions", "email_checks"):
        assert S.BY_NAME[name].dataset == "hillstrom"
    assert "arm" not in tables["game_arms"].columns and "variant" not in tables["email_arms"].columns


def test_observational_tables_are_labelled_and_never_causal(tables):
    for name, spec in S.BY_NAME.items():
        if spec.dataset == "ga4" and spec.nature != S.REF:
            assert spec.nature == S.OBS
    cat = tables["dataset_catalog"].set_index("dataset_key")
    assert int(cat.loc["ga4", "causal_claims_allowed"]) == 0
    assert cat.loc["synthetic", "nature"] == "simulated"


# ----------------------------------------------------------------- negative fixtures: validator catches defects
def _mutate(tables, name, fn):
    t = {k: v.copy() for k, v in tables.items()}
    t[name] = fn(t[name].copy())
    return failures(t)


def test_validator_catches_duplicate_key(tables):
    f = _mutate(tables, "game_arms", lambda d: pd.concat([d, d.iloc[[0]]], ignore_index=True))
    assert any("primary key unique" in x for x in f) and any("section ran without error" in x or "players" in x for x in f)


def test_validator_catches_out_of_range_rate(tables):
    def bad(d):
        d.loc[0, "rate"] = 1.4
        return d
    assert any("within [0, 1]" in x for x in _mutate(tables, "retention_rates", bad))


def test_validator_catches_tampered_metric(tables):
    def bad(d):
        d.loc[d.role == "primary", "estimate"] = -0.5
        return d
    assert any("primary estimate" in x for x in _mutate(tables, "game_effects", bad))


def test_validator_catches_row_multiplication(tables):
    assert any("no row multiplication" in x or "published strict funnel" in x
               for x in _mutate(tables, "funnel_daily_long", lambda d: pd.concat([d, d.head(100)], ignore_index=True)))


def test_validator_catches_causal_wording_in_observational_table(tables):
    def bad(d):
        d["direction"] = d.direction.astype(object)
        d.loc[0, "direction"] = "page views caused returns"
        return d.astype({"direction": "string"})
    assert any("no causal wording" in x for x in _mutate(tables, "behaviour_model", bad))


def test_validator_catches_missing_null_key(tables):
    def bad(d):
        d["stage_key"] = d.stage_key.astype(object)
        d.loc[0, "stage_key"] = None
        return d
    assert any("no nulls" in x for x in _mutate(tables, "funnel_steps", bad))


# ----------------------------------------------------------------- writers
def test_csv_and_hyper_round_trip(tables, tmp_path):
    paths = E.write_csv(tables, tmp_path)
    for name, p in paths.items():
        back = pd.read_csv(p)
        assert len(back) == len(tables[name]) and list(back.columns) == list(tables[name].columns)
    pytest.importorskip("tableauhyperapi")
    from tableauhyperapi import Connection, HyperProcess, Telemetry

    status = E.write_hyper(tables, tmp_path / "t.hyper")
    assert status == "written"
    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_dir": str(tmp_path)}) as h, \
            Connection(h.endpoint, str(tmp_path / "t.hyper")) as c:
        for name, df in tables.items():
            assert c.execute_scalar_query(f'SELECT COUNT(*) FROM "Extract"."{name}"') == len(df)


def test_data_dictionary_documents_every_column():
    md = E.data_dictionary_md()
    for t in S.TABLES:
        assert f"## `{t.name}`" in md
        for col in t.columns:
            assert f"`{col}`" in md

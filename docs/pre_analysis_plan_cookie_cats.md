# Pre-analysis plan: Cookie Cats gate test

This plan is committed before any outcome comparison. It is a retrospective analysis of public data, so the plan is a timestamped commit, **not a true pre-registration**.

**Disclosure.** Before writing this plan I had seen the group sizes (gate_30: 44,700; gate_40: 45,489) and a pooled summary of `sum_gamerounds` (median 16, max 49,854) while validating the download. Web-search snippets of published analyses had also shown approximate retention rates per arm. The plan is therefore not blind. The decision rule below was fixed regardless of those numbers.

## Design

- **Change:** the first gate moves from level 30 (control, `gate_30`) to level 40 (treatment, `gate_40`).
- **Unit:** player (`userid`). Assignment at install. The expected split is 50/50 (assumed; not documented by the source).
- **Data:** 90,189 players (Kaggle `yufengsui/mobile-games-ab-testing`).

## Hypotheses

- H0: D7 retention(gate_40) = D7 retention(gate_30).
- H1: they differ (two-sided).

## Metrics

| Role | Metric | Analysis |
| --- | --- | --- |
| Primary | `retention_7` | difference in pp (gate_40 − gate_30), Newcombe 95% CI, bootstrap 95% CI (10,000 resamples, seed 2026), relative lift, two-proportion z-test |
| Guardrail | `retention_1` | non-inferiority: **pass** if the lower 95% bound of the difference is > −1.0 pp |
| Guardrail | `sum_gamerounds` | non-inferiority on the mean winsorized at the pooled 99.9th percentile: pass if the lower 95% bootstrap bound is > −2.5 rounds. Robustness: raw-mean bootstrap, median difference, Mann-Whitney U |
| Missing guardrail | revenue / in-app purchases | not in the data. Stated as a limit |

α = 0.05 (two-sided). Minimum effect worth acting on: **0.5 pp** in D7 retention, in either direction.

## Validity checks (reported whatever they show)

1. SRM: chi-square goodness of fit against 50/50. Flag if p < 0.001. The p-value is also reported against 0.01. If SRM is flagged, the effect is reported as conditional on the imbalance being benign, with no ship decision.
2. Duplicate `userid`s.
3. Outliers in `sum_gamerounds`: max, 99.9th percentile, and the count above it. The analysis uses winsorization, not deletion.

## Power

- MDE at the realised n with α = 0.05 and power 0.80 (statsmodels), plus a power curve over effect sizes.
- **No observed power.** A null result is interpreted through its CI: which effects it rules out.

## Decision rule (gate_40 is the candidate)

| Condition | Decision |
| --- | --- |
| SRM flagged | hold: investigate assignment |
| D7 CI lower bound > 0 and both guardrails pass | ship gate_40 |
| D7 CI lower bound > 0 and a guardrail does not pass | iterate |
| D7 CI upper bound < 0 | do not ship gate_40 (keep gate_30). Reported as the failed-experiment case |
| CI includes 0 | hold. Report which effects (≥ 0.5 pp?) are ruled out |

## Not done, by design

- No segmentation by `sum_gamerounds`. It is measured after assignment, so the treatment can change it.
- No claims about revenue, D30 retention or novelty effects. There is no time-resolved or monetisation data.

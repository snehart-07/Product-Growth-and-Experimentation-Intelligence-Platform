# Pre-analysis plan: Hillstrom e-mail test

This plan is committed before any outcome comparison. It is a retrospective analysis of public data, so the plan is a timestamped commit, not a true pre-registration.

**Disclosure.** Before writing this plan I had seen the group sizes (Womens 21,387, Mens 21,307, No E-Mail 21,306) and the first three rows.

## Design

- **Arms:** Mens E-Mail, Womens E-Mail, No E-Mail (control). Customer-level randomization, expected 1/3 each. The follow-up window is two weeks.
- **Comparisons:** each e-mail arm vs control. Holm correction across the two primary comparisons.

## Metrics

| Role | Metric | Analysis |
| --- | --- | --- |
| Primary | conversion | difference (pp), Newcombe CI, z-test, Holm across 2 arms |
| Secondary | visit | same |
| Secondary | spend per customer (zeros included) | mean difference, bootstrap 95% CI (10,000 resamples, seed 2026) |

SRM: 3-group chi-square, flag if p < 0.001.

## Heterogeneity (pre-specified)

Only pre-treatment covariates are used:

| Covariate | Levels |
| --- | --- |
| recency (months since last purchase) | 1–3, 4–6, 7–9, 10–12 |
| history_segment (past-year spend band) | 7 bands as given |
| newbie | 0 / 1 |
| zip_code | Urban / Suburban / Rural |
| channel (past purchase channel) | Phone / Web / Multichannel |
| product_line (last year) | mens only / womens only / both (from the `mens` and `womens` flags) |

1. **Interaction first.** For each arm × covariate (2 × 6 = 12 tests per outcome), compare logistic `outcome ~ treat + C(cov)` with `outcome ~ treat * C(cov)` by likelihood-ratio test. Holm across the 12 tests. The primary outcome family is **conversion**. Visit is a secondary family, corrected separately.
2. **Subgroup effects.** Difference in conversion per level, per arm, with Newcombe CIs. Holm across **all** subgroup tests (main correction) and BH at 10% FDR (sensitivity). The number of subgroups examined is reported. Subgroups with fewer than 200 customers per arm are reported but not tested.
3. **A priori expectation:** the Mens e-mail works best for last-year mens buyers, and the Womens e-mail for womens buyers.

## Variance reduction (CUPED)

Spend, with `history` (past-year spend) as the covariate: θ = cov(Y, X) / var(X) on pooled data. The adjusted metric is Y − θ(X − mean X). Report the variance reduction (= 1 − var(adj) / var(Y)) and the adjusted CI.

## Exploratory (labelled as such)

T-learner uplift model (gradient boosting) for **visit**, Womens e-mail vs control. 50/50 stratified train/holdout split (seed 2026). Qini curve on the holdout. Compare targeting the top 30% by predicted uplift with e-mailing everyone. Targeting economics are a **scenario** with stated assumptions about message cost and margin.

## Not supported

Retention, long-term effects, product-change effects (this is a marketing test), and causal claims about post-treatment variables.

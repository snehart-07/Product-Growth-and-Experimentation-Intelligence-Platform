# GrowthLab Tableau data dictionary

Generated from `src/reporting/tableau_schema.py` by `scripts/export_tableau_data.py`. Do not edit by hand.

## `dim_date`

Calendar for the GA4 observation window. Relate to store tables on date fields.

- **Dataset:** calendar · **Nature:** reference
- **Grain:** one row per calendar day 2020-11-01 .. 2021-01-31
- **Primary key:** date

| Column | Type | Description |
| --- | --- | --- |
| `date` | date | Calendar day |
| `week_start` | date | Start of the 7-day block counted from 2020-11-01 |
| `week_label` | text | e.g. 'Nov 01 – Nov 07' |
| `month` | text | YYYY-MM |
| `day_of_week` | text | Monday .. Sunday |
| `is_weekend` | int | 1 if Saturday or Sunday |

## `dataset_catalog`

What each dataset is, what it can and cannot support, and whether causal claims are allowed.

- **Dataset:** all · **Nature:** reference
- **Grain:** one row per dataset
- **Primary key:** dataset_key

| Column | Type | Description |
| --- | --- | --- |
| `dataset_key` | text | ga4 | cookie_cats | hillstrom | synthetic |
| `dataset` | text | Dataset name |
| `nature` | text | observational | randomized experiment | simulated |
| `period` | text | Observation period |
| `rows` | int | Source rows used |
| `unit` | text | Unit of analysis |
| `causal_claims_allowed` | int | 1 only for randomized experiments |
| `can_support` | text | Supported analyses |
| `cannot_support` | text | Unsupported analyses / caveats |
| `source_url` | text | Public source |
| `exported` | int | 1 if tables from this dataset are exported |

## `metric_definitions`

Definitions used in tooltips. Mirrors docs/metric_definitions.md.

- **Dataset:** all · **Nature:** reference
- **Grain:** one row per metric
- **Primary key:** metric_key

| Column | Type | Description |
| --- | --- | --- |
| `metric_key` | text | Stable key used by other tables |
| `label` | text | Display label |
| `dataset_key` | text | Dataset the metric belongs to |
| `role` | text | primary | secondary | guardrail | diagnostic |
| `numerator` | text | Numerator |
| `denominator` | text | Denominator |
| `window` | text | Time window |
| `unit` | text | proportion | percentage points | USD | rounds | count |
| `caveat` | text | Caveat to show in tooltips |

## `overview_kpis`

Headline numbers exactly as published in reports/results/*.json.

- **Dataset:** all · **Nature:** reference
- **Grain:** one row per headline metric
- **Primary key:** metric_key

| Column | Type | Description |
| --- | --- | --- |
| `metric_key` | text | Key into metric_definitions |
| `section` | text | Funnel | Retention | Drivers | Game | Email |
| `label` | text | Display label |
| `value` | float | Value (proportions as 0–1; effects in percentage points) |
| `unit` | text | proportion | percentage points | count | odds ratio | USD |
| `ci_low` | float | 95% interval lower bound (blank when not applicable) |
| `ci_high` | float | 95% interval upper bound |
| `numerator` | float | Numerator count when applicable |
| `denominator` | float | Denominator count when applicable |
| `dataset_key` | text | Dataset |
| `nature` | text | observational | randomized experiment |
| `interpretation` | text | Plain-language reading |

## `store_daily`

Additive counts for new, non-bot visitors. Rates are SUM(numerator)/SUM(denominator) in Tableau, so every filter works.

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** first-visit date × source × device × country_group
- **Primary key:** first_visit_date, source_raw, device, country_group
- **Caveat:** Observational. Visitor = browser cookie. Values partly obfuscated by Google.

| Column | Type | Description |
| --- | --- | --- |
| `first_visit_date` | date | Date of the visitor's first_visit event (property time zone) |
| `week_start` | date | Cohort week start |
| `source` | text | First-touch acquisition source, plain-language label (e.g. 'Google search', 'Hidden in sample') |
| `source_raw` | text | Source as stored in the GA4 marts, e.g. 'Organic Search', '(obfuscated)' |
| `device` | text | First device category: Desktop, Mobile or Tablet |
| `country_group` | text | Country (top 10 by new visitors) or 'Other' |
| `new_visitors` | int | New non-bot visitors (first_visit inside the sample) |
| `funnel_eligible` | int | New visitors whose 7-day window is fully observed (funnel denominator) |
| `viewed_product` | int | Of funnel_eligible: viewed a product within 7 days |
| `added_to_cart` | int | Of funnel_eligible: added to cart after viewing (strict order) |
| `started_checkout` | int | Of funnel_eligible: began checkout after cart (strict order) |
| `purchased` | int | Of funnel_eligible: purchased after checkout (strict order) |
| `d7_eligible` | int | New visitors observable for days 1–7 |
| `d7_returned` | int | Of d7_eligible: any visit on days 1–7 |
| `d7_exact_returned` | int | Of d7_eligible: a visit on exactly day 7 (sensitivity) |
| `d30_eligible` | int | New visitors observable for days 1–30 (first visit ≤ 2021-01-01) |
| `d30_returned` | int | Of d30_eligible: any visit on days 1–30 |

## `funnel_daily_long`

store_daily's funnel counts unpivoted to one row per stage, for funnel charts.

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** first-visit date × source × device × country_group × stage
- **Primary key:** first_visit_date, source_raw, device, country_group, stage_key
- **Caveat:** Strict order: a stage counts only after the previous one. Denominator is stage 1.

| Column | Type | Description |
| --- | --- | --- |
| `first_visit_date` | date | First-visit date |
| `week_start` | date | Cohort week start |
| `source` | text | First-touch acquisition source, plain-language label (e.g. 'Google search', 'Hidden in sample') |
| `source_raw` | text | Source as stored in the GA4 marts, e.g. 'Organic Search', '(obfuscated)' |
| `device` | text | First device category: Desktop, Mobile or Tablet |
| `country_group` | text | Country (top 10 by new visitors) or 'Other' |
| `stage_order` | int | 1 Visited site … 5 Purchased |
| `stage_key` | text | first_visit | view_item | add_to_cart | begin_checkout | purchase |
| `stage` | text | Plain-language stage name |
| `users` | int | Visitors who reached the stage (strict order, 7 days) |

## `funnel_steps`

Published funnel for all eligible visitors, in strict and any order, with Wilson 95% intervals.

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** order_type × stage (all visitors)
- **Primary key:** order_type, stage_key

| Column | Type | Description |
| --- | --- | --- |
| `order_type` | text | strict | any |
| `stage_order` | int | 1..5 |
| `stage_key` | text | Stage key |
| `stage` | text | Stage name |
| `users` | int | Visitors reaching the stage |
| `share_of_entrants` | float | users / visitors at stage 1 |
| `conversion_from_previous` | float | users / users at previous stage |
| `ci_low` | float | Wilson 95% lower bound of conversion_from_previous |
| `ci_high` | float | Wilson 95% upper bound |
| `users_lost` | int | Previous-stage users minus this stage's users |
| `drop_rate` | float | 1 − conversion_from_previous |
| `is_largest_drop` | int | 1 for the stage with the most users lost (within order_type) |
| `median_minutes_from_previous` | float | Median minutes from the previous stage (strict order only) |

## `funnel_segments`

Stage conversion per segment vs everyone else (Newcombe CI; Holm across all segment tests in a dimension).

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** segment_type × segment × stage
- **Primary key:** segment_type, segment, stage_key
- **Caveat:** Segments with fewer than 500 visitors entering the stage are excluded. Associations only.

| Column | Type | Description |
| --- | --- | --- |
| `segment_type` | text | source | device | country |
| `segment` | text | Segment label |
| `segment_raw` | text | Segment as stored |
| `stage_key` | text | Stage reached |
| `stage` | text | Stage name |
| `n_entering` | int | Visitors at the previous stage |
| `n_converted` | int | Visitors reaching this stage |
| `rate` | float | n_converted / n_entering |
| `ci_low` | float | Wilson 95% lower |
| `ci_high` | float | Wilson 95% upper |
| `overall_rate` | float | Rate for all visitors |
| `diff_vs_rest_pp` | float | Segment minus everyone else, percentage points |
| `diff_ci_low_pp` | float | Newcombe 95% lower, pp |
| `diff_ci_high_pp` | float | Newcombe 95% upper, pp |
| `p_value` | float | Two-proportion z-test |
| `p_holm` | float | Holm-adjusted p |
| `clearly_different` | int | 1 if p_holm < 0.05 |

## `retention_cohorts`

Weekly cohort grid. Cell rate = SUM(active_users)/SUM(cohort_size) where fully observed.

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** cohort week × source × device × country_group × age week
- **Primary key:** cohort_week, source_raw, device, country_group, age_week
- **Caveat:** Mask cells with is_fully_observed = 0. Never treat missing follow-up as zero.

| Column | Type | Description |
| --- | --- | --- |
| `cohort_week` | int | 0 = week starting 2020-11-01 |
| `cohort_week_start` | date | Cohort week start |
| `cohort_label` | text | e.g. 'Nov 01 – Nov 07' |
| `source` | text | First-touch acquisition source, plain-language label (e.g. 'Google search', 'Hidden in sample') |
| `source_raw` | text | Source as stored in the GA4 marts, e.g. 'Organic Search', '(obfuscated)' |
| `device` | text | First device category: Desktop, Mobile or Tablet |
| `country_group` | text | Country (top 10 by new visitors) or 'Other' |
| `age_week` | int | 1..12; week k = days 7k..7k+6 after first visit |
| `cohort_size` | int | New non-bot visitors in the cohort cell |
| `active_users` | int | Of cohort_size: any visit in week k |
| `is_fully_observed` | int | 1 if the whole week-k window of the cohort's last day is inside the data; else the cell is unknown, not zero |

## `retention_rates`

Published return rates with Wilson 95% intervals.

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** window × segment_type × segment
- **Primary key:** window, segment_type, segment

| Column | Type | Description |
| --- | --- | --- |
| `window` | text | d7 | d7_exact_day | d30 |
| `segment_type` | text | all | source | device |
| `segment` | text | Segment label |
| `segment_raw` | text | Segment as stored |
| `eligible` | int | Visitors observable for the window |
| `returned` | int | Of eligible: returned |
| `rate` | float | returned / eligible |
| `ci_low` | float | Wilson 95% lower |
| `ci_high` | float | Wilson 95% upper |

## `retention_tests`

Chi-square tests: do segments differ in return rate; do cohorts differ at equal age (Holm-adjusted).

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** one row per statistical test
- **Primary key:** test_type, dimension, metric, age_week

| Column | Type | Description |
| --- | --- | --- |
| `test_type` | text | segment_difference | cohort_equal_age |
| `dimension` | text | source | device | cohort |
| `metric` | text | return_d7 | return_d30 | weekly_retention |
| `age_week` | int | Cohort age for cohort tests; 0 otherwise |
| `n_groups` | int | Groups compared |
| `chi2` | float | Chi-square statistic |
| `dof` | int | Degrees of freedom |
| `p_value` | float | p-value |
| `p_holm` | float | Holm-adjusted p within the family |
| `min_rate` | float | Lowest group rate (cohort tests) |
| `max_rate` | float | Highest group rate (cohort tests) |

## `behaviour_groups`

First-24-hour behaviour vs return in [24h, 31d). Raw comparisons; aggregate with SUM.

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** cohort week × source × device × country_group × behaviour × did_it
- **Primary key:** cohort_week, source_raw, device, country_group, behaviour_key, did_it
- **Caveat:** Association, not causation. Only first visits up to 2021-01-01 (31-day window observable).

| Column | Type | Description |
| --- | --- | --- |
| `cohort_week` | int | First-visit cohort week |
| `week_start` | date | Cohort week start |
| `source` | text | First-touch acquisition source, plain-language label (e.g. 'Google search', 'Hidden in sample') |
| `source_raw` | text | Source as stored in the GA4 marts, e.g. 'Organic Search', '(obfuscated)' |
| `device` | text | First device category: Desktop, Mobile or Tablet |
| `country_group` | text | Country (top 10 by new visitors) or 'Other' |
| `behaviour_key` | text | pages_3plus | engaged_1min | viewed_product | added_to_cart | used_search |
| `behaviour` | text | Plain-language behaviour |
| `did_it` | int | 1 if the visitor did it in the first 24 hours |
| `users` | int | Visitors followed for a full 31 days |
| `returned` | int | Of users: any event from 24h to 31 days |

## `behaviour_model`

Adjusted logistic-regression odds ratios (behaviours held equal, segment controls).

- **Dataset:** ga4 · **Nature:** observational
- **Grain:** one row per model term
- **Primary key:** model, feature
- **Caveat:** Associations. Numeric behaviours are log1p-transformed and standardized (OR per SD).

| Column | Type | Description |
| --- | --- | --- |
| `model` | text | main | intent_adjusted |
| `feature` | text | Model term |
| `label` | text | Plain-language term |
| `term_type` | text | behaviour | source | device | country | intent_proxy |
| `odds_ratio` | float | Odds ratio for returning |
| `or_ci_low` | float | 95% lower |
| `or_ci_high` | float | 95% upper |
| `p_value` | float | Wald p-value |
| `direction` | text | more likely | less likely | no clear link |
| `vif` | float | Variance inflation factor (behaviour terms, main model) |

## `followup_test_design`

Sample size for the randomized test that would establish causality for the top positive association.

- **Dataset:** ga4 · **Nature:** reference
- **Grain:** one row per minimum detectable effect
- **Primary key:** mde_pp

| Column | Type | Description |
| --- | --- | --- |
| `mde_pp` | float | Minimum detectable effect, percentage points |
| `n_per_arm` | int | Visitors per group (80% power, α 0.05) |
| `enrolment_days` | int | Days to enrol at observed traffic |
| `total_days` | int | Enrolment + 31-day outcome window |
| `baseline_rate` | float | Current 30-day return rate |
| `hypothesis` | text | Test hypothesis |

## `game_arms`

Per-variant sample sizes and outcomes.

- **Dataset:** cookie_cats · **Nature:** randomized experiment
- **Grain:** one row per variant
- **Primary key:** variant

| Column | Type | Description |
| --- | --- | --- |
| `variant` | text | gate_30 | gate_40 |
| `variant_label` | text | Level 30 (control) | Level 40 (treatment) |
| `role` | text | control | treatment |
| `players` | int | Players assigned |
| `d1_retained` | int | Came back on day 1 |
| `d1_rate` | float | d1_retained / players |
| `d7_retained` | int | Came back on day 7 |
| `d7_rate` | float | d7_retained / players |
| `rounds_mean_winsorized` | float | Mean rounds, capped at pooled 99.9th percentile |
| `rounds_mean_raw` | float | Mean rounds, uncapped |
| `rounds_median` | float | Median rounds |

## `game_effects`

Treatment − control effects with intervals, tests and guardrail status.

- **Dataset:** cookie_cats · **Nature:** randomized experiment
- **Grain:** one row per metric × method
- **Primary key:** metric_key, method

| Column | Type | Description |
| --- | --- | --- |
| `metric_key` | text | Metric |
| `metric` | text | Label |
| `role` | text | primary | guardrail | robustness |
| `method` | text | Estimator / interval method |
| `estimate` | float | Level 40 − level 30 |
| `ci_low` | float | 95% lower |
| `ci_high` | float | 95% upper |
| `unit` | text | percentage points | rounds |
| `relative_change` | float | Estimate / control (proportions only) |
| `p_value` | float | Test p-value |
| `ni_margin` | float | Non-inferiority margin (guardrails), as a negative number |
| `status` | text | clearly worse | clearly better | unclear | pass | not pass |
| `interpretation` | text | Plain-language reading |

## `game_checks`

Validity checks, power, decision and missing data.

- **Dataset:** cookie_cats · **Nature:** randomized experiment
- **Grain:** one row per check
- **Primary key:** check_key

| Column | Type | Description |
| --- | --- | --- |
| `check_key` | text | Check |
| `check` | text | Label |
| `value` | float | Observed value |
| `threshold` | float | Threshold, when applicable |
| `status` | text | pass | note | flag | missing | info |
| `detail` | text | Explanation |

## `game_power_curve`

Power to detect a true change in day-7 retention at the realised sample size (not observed power).

- **Dataset:** cookie_cats · **Nature:** randomized experiment
- **Grain:** one row per hypothetical effect size
- **Primary key:** effect_pp

| Column | Type | Description |
| --- | --- | --- |
| `effect_pp` | float | Hypothetical true effect, percentage points |
| `power` | float | Probability of detection |
| `mde_pp` | float | MDE at 80% power (same on every row, for a reference line) |
| `observed_abs_effect_pp` | float | |Observed effect| (reference line) |

## `email_arms`

Per-arm outcomes for all three arms and all three measures.

- **Dataset:** hillstrom · **Nature:** randomized experiment
- **Grain:** arm × metric
- **Primary key:** arm, metric_key

| Column | Type | Description |
| --- | --- | --- |
| `arm` | text | No E-Mail | Mens E-Mail | Womens E-Mail |
| `arm_label` | text | No email | Men's email | Women's email |
| `role` | text | control | treatment |
| `metric_key` | text | conversion | visit | spend |
| `metric` | text | Label |
| `customers` | int | Customers in the arm |
| `events` | int | Customers with the outcome (blank for spend) |
| `value` | float | Rate (conversion, visit) or mean USD per customer (spend) |
| `ci_low` | float | Wilson 95% lower (rates only) |
| `ci_high` | float | Wilson 95% upper (rates only) |

## `email_effects`

Each e-mail vs no e-mail.

- **Dataset:** hillstrom · **Nature:** randomized experiment
- **Grain:** treatment arm × metric
- **Primary key:** arm, metric_key

| Column | Type | Description |
| --- | --- | --- |
| `arm` | text | Mens E-Mail | Womens E-Mail |
| `arm_label` | text | Label |
| `metric_key` | text | conversion | visit | spend |
| `metric` | text | Label |
| `role` | text | primary | secondary |
| `estimate` | float | Treatment − control |
| `ci_low` | float | 95% lower |
| `ci_high` | float | 95% upper |
| `unit` | text | percentage points | USD |
| `method` | text | Newcombe + z-test | percentile bootstrap |
| `p_value` | float | Unadjusted p (rates) |
| `p_holm` | float | Holm across the two arms (rates) |
| `status` | text | clearly better | clearly worse | unclear |

## `email_subgroups`

Conversion lift by pre-treatment customer type. Holm across all 44 subgroup tests.

- **Dataset:** hillstrom · **Nature:** randomized experiment
- **Grain:** arm × covariate × level
- **Primary key:** arm, covariate, level
- **Caveat:** Subgroup effects are causal only for the assigned email within that subgroup; differences between subgroups need the interaction test.

| Column | Type | Description |
| --- | --- | --- |
| `arm` | text | Arm |
| `arm_label` | text | Label |
| `covariate` | text | Pre-treatment covariate |
| `covariate_label` | text | Label |
| `level` | text | Level as stored |
| `level_label` | text | Plain-language level |
| `n_treatment` | int | Customers emailed |
| `n_control` | int | Customers not emailed |
| `rate_control` | float | Control conversion |
| `rate_treatment` | float | Treatment conversion |
| `estimate_pp` | float | Lift, percentage points |
| `ci_low_pp` | float | Newcombe 95% lower |
| `ci_high_pp` | float | Newcombe 95% upper |
| `p_value` | float | z-test |
| `p_holm` | float | Holm across 44 |
| `p_bh` | float | Benjamini-Hochberg (sensitivity) |
| `survives_holm` | int | 1 if p_holm < 0.05 |

## `email_interactions`

Treatment × covariate likelihood-ratio tests (Holm across the 12 per outcome).

- **Dataset:** hillstrom · **Nature:** randomized experiment
- **Grain:** outcome × arm × covariate
- **Primary key:** outcome, arm, covariate

| Column | Type | Description |
| --- | --- | --- |
| `outcome` | text | conversion | visit |
| `arm` | text | Arm |
| `arm_label` | text | Label |
| `covariate` | text | Covariate |
| `covariate_label` | text | Label |
| `lr_stat` | float | Likelihood-ratio statistic |
| `dof` | int | Degrees of freedom |
| `p_value` | float | p-value |
| `p_holm` | float | Holm-adjusted |
| `p_bh` | float | BH-adjusted |
| `real_difference` | int | 1 if p_holm < 0.05 |

## `email_checks`

Balance check, CUPED variance reduction, and the exploratory uplift result.

- **Dataset:** hillstrom · **Nature:** randomized experiment
- **Grain:** one row per check
- **Primary key:** check_key

| Column | Type | Description |
| --- | --- | --- |
| `check_key` | text | Check |
| `check` | text | Label |
| `value` | float | Observed value |
| `status` | text | pass | flag | info | exploratory |
| `detail` | text | Explanation |

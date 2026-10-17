# Metric definitions

This file was committed before any funnel, cohort, driver or experiment statistic was computed. See the git history.

**Already seen at commit time:** row counts, event-name counts, first-touch channel and device counts, and the data-quality profile of the GA4 extract. For Cookie Cats, the group sizes and a pooled summary of `sum_gamerounds`. For Hillstrom, the group sizes and the first rows. Search-result snippets of published Cookie Cats analyses had also been seen, so the Cookie Cats plan is not blind (see its pre-analysis plan).

## Data mapping (GA4)

The GA4 sample has no account sign-ups. **`first_visit` stands in for sign-up**, **`purchase` stands in for conversion**, and **return visits stand in for retention**.

## Core definitions

| Term | Definition |
| --- | --- |
| User (GA4) | `user_pseudo_id`, a browser/device cookie. Not a person or an account. One person on two devices counts as two users. |
| New user (GA4) | A user whose `first_visit` event is inside the sample (2020-11-01 to 2021-01-31). Only new users enter the funnel, cohort and driver analyses. |
| t0 | Timestamp (UTC) of the user's first `first_visit` event. d0 = that event's `event_date` (property time zone). |
| Session (GA4) | `user_pseudo_id` + `ga_session_id`. |
| Cohort (GA4) | 7-day block of d0, starting 2020-11-01: week index = floor((d0 − 2020-11-01) / 7). The last block (2021-01-31) is one day long. |
| Funnel, any order | The user fired the stage event within the window, regardless of order. |
| Funnel, strict order | Each stage's first qualifying event happens at or after the previous stage's first qualifying event. |
| Observation window | GA4: 2020-11-01 to 2021-01-31 by event_date. The UTC end is 2021-02-01 00:00, which is conservative. Cookie Cats: 14 days after install (dataset description). Hillstrom: two weeks after the e-mail. |
| Randomization unit | Cookie Cats: player (`userid`). Hillstrom: customer (row). GA4: none (observational). |
| Analysis unit | Same as the randomization unit for the experiments. User for GA4. |
| Suspected bot/internal (GA4) | A user with more than 500 events on any single day, or more than 100 sessions in the sample. These users are flagged and excluded from the funnel, cohort and driver analyses, and the excluded count is reported. |
| Minimum cell sizes | Funnel segments: at least 500 users entering the stage. Cohort cells: at least 100 users (greyed out below that). Subgroups: at least 200 users per arm. |

## Metrics

| Metric | Role | Numerator | Denominator | Unit | Window | Inclusions / exclusions | Source | Caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Activation rate (GA4) | Diagnostic | New users with ≥ 1 `view_item` **and** ≥ 1 `add_to_cart` in [t0, t0+24h) | New users with t0 + 24h ≤ end | proportion | 24 h | excludes suspected bots | `retention_features` | Proxy, since there is no sign-up event |
| Return rate D7 (GA4) | **Primary** | New users with any event on calendar day d0+1 … d0+7 | New users with d0 + 7 ≤ 2021-01-31 | proportion | days 1–7 | excludes bots. Users not observable for 7 days are excluded (censoring) | `user_return_flags` | Window-based, not exact-day |
| Return rate D30 (GA4) | **Primary** | as above, days 1–30 | d0 + 30 ≤ 2021-01-31 | proportion | days 1–30 | same | same | Only cohorts up to 2021-01-01 are observable |
| Exact-day return D7 (GA4) | Sensitivity | any event on day d0+7 exactly | as D7 | proportion | day 7 | same | same | Sensitivity check only |
| Weekly cohort retention (GA4) | Diagnostic | cohort users with any event on days 7k … 7k+6 | cohort size | proportion | week k ≥ 1 | a cell is shown only if the **last** day of the cohort + 7k + 6 ≤ 2021-01-31 | `cohort_retention_weekly` | Censored cells are masked |
| Purchase conversion (GA4) | Secondary | New users with a `purchase` in [t0, t0+7d) | New users with t0 + 7d ≤ end | proportion | 7 days | excludes bots | `funnel_new_users` | User level, not session level |
| Funnel stage conversion (GA4) | Diagnostic | users reaching stage s+1 | users reaching stage s | proportion | [t0, t0+7d) | new, non-bot users observable for 7 days. Stages: first_visit → view_item → add_to_cart → begin_checkout → purchase | `funnel_new_users` | Reported in strict and any order |
| Return-after-24h, 30 days (GA4) | Outcome of the driver model | New users with any event in [t0+24h, t0+31d) | t0 + 31d ≤ end | binary | 30 days after the first 24 h | features use [t0, t0+24h) only, so they never overlap the outcome | `retention_features` | Timestamp-based, to rule out leakage |
| Average order value (GA4) | Sizing input | Σ `purchase_revenue_in_usd` over distinct transactions | distinct transaction_id | USD | sample | purchases without a transaction ID excluded | `stg_events` | Obfuscated sample. The value is an estimate for sizing only |
| Day-7 retention (Cookie Cats) | **Primary** | players with `retention_7` = True | players in the arm | proportion | exact-day flag | all rows. Duplicates are checked | raw CSV | Exact-day flag |
| Day-1 retention (Cookie Cats) | Guardrail | `retention_1` = True | players in the arm | proportion | day 1 | same | raw CSV | Non-inferiority margin **1.0 pp** |
| Rounds played (Cookie Cats) | Guardrail | Σ `sum_gamerounds` | players in the arm | rounds per player | first 14 days (dataset text says 14; some copies say 1 week) | mean winsorized at the pooled 99.9th percentile. Raw mean, median and Mann-Whitney U as robustness | raw CSV | Measured after assignment. Heavy right tail. Never used for segmentation. Non-inferiority margin **2.5 rounds** |
| Revenue (Cookie Cats) | **Missing guardrail** | — | — | — | — | — | not in data | Revenue is the real guardrail for a gate change. The gate drives in-app purchases, and it is absent |
| Conversion (Hillstrom) | **Primary** | customers with `conversion` = 1 | customers in the arm | proportion | 2 weeks | all rows | raw CSV | Binary |
| Visit (Hillstrom) | Secondary | `visit` = 1 | customers in the arm | proportion | 2 weeks | | raw CSV | |
| Spend per customer (Hillstrom) | Secondary | Σ spend (zeros included) | customers in the arm | USD | 2 weeks | | raw CSV | Zero-inflated and heavy-tailed: bootstrap CI (10,000 resamples) |

Intervals are 95% unless stated otherwise. Proportions use Wilson intervals. Differences of proportions use the Newcombe hybrid score interval. Multiple comparisons use Holm, with Benjamini-Hochberg as a sensitivity where stated.

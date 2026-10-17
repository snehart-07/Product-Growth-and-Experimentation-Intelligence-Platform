# Correlation vs causation, applied to this project's results

Every number below comes from `reports/results/*.json`, produced by the code in `src/analysis/`. The GA4 results are observational. The Cookie Cats and Hillstrom results come from randomized experiments.

## 1. The driver model shows associations, not levers

The model is a logistic regression for "returned in [t0+24h, t0+31d)", fit on 168,471 new GA4 users (8.1% returned). AUC is 0.691 for the logistic model and 0.716 for gradient boosting on a 30% holdout.

| First-24h behaviour | Raw return rate (yes vs no) | Adjusted OR (95% CI) |
| --- | --- | --- |
| log page views (per SD) | — | **1.62 (1.57–1.68)** |
| log engagement minutes (per SD) | — | 1.30 (1.26–1.34) |
| used site search | 10.8% vs 8.0% | **0.41 (0.38–0.45)** |
| added to cart | 19.7% vs 7.7% | 0.86 (0.79–0.92) |

**Intent confounding.** Page depth is the strongest positive association, and it is the candidate chosen for the follow-up test. The simplest explanation is that visitors who already intend to shop (gift buyers, Google fans) both browse more pages and come back. The model has no measure of intent, so the OR is not "the effect of one more page". Adding purchase-intent proxies (began checkout, purchased in the first 24h) moves the page-view OR from 1.62 to **1.80**. If intent drove the association, adjusting for it should have shrunk the OR, so these proxies do not explain it away. They only capture *purchase* intent, though, and the unobserved part of intent remains.

**Sign flips are a warning, not a finding.** Search users and cart users return *more* often in raw terms, but have adjusted ORs below 1. Both behaviours are strongly correlated with page views and engagement time (VIF up to 4.6). The adjusted coefficient answers "searching at a *fixed* number of pages and minutes", which is a comparison few real users make. Neither flip means that search or the cart drives users away.

**Reverse causality.** Within the first 24 hours, a user who has already decided to come back (to finish a purchase, say) can browse differently *because* of that plan. The feature and the outcome may share a cause that comes earlier than both.

**Selection and survivorship.** Only users who produced events are in the table. Features exist only for users who stayed long enough to generate them, and later behaviour is only observed for users who returned. The model also excludes users first seen after 2021-01-01 (outcome not observable), which is a censoring choice: the result describes November-to-December visitors, including the holiday season.

**Leakage control.** Features use [t0, t0+24h) and the outcome uses [t0+24h, t0+31d). An SQL assertion (`retention_features_no_post_24h_leakage`) independently recounts the feature window.

## 2. Simpson's paradox: the device check, done before concluding

Desktop and mobile 7-day purchase conversion differ by **+0.03 pp** in raw terms (mobile minus desktop, 95% CI −0.04 to +0.10). Standardizing to a common channel mix gives +0.03 pp. Within channels, only Direct shows a gap that survives Holm (+0.20 pp, 95% CI +0.05 to +0.36, Holm p = 0.038).

Mix could not create a paradox here, because every channel is about 59% desktop. In this sample, the device split is almost identical across channels. That uniformity, together with funnel rates that barely move across channels, devices and countries (no segment-vs-rest gap survives Holm), is consistent with the sample's obfuscation having weakened the link between dimensions and behaviour. It is a hypothesis we cannot test without the original data. The check still matters: in real data, a device gap that appears overall but disappears within channel would be a channel effect, not a device effect.

## 3. What would establish causality

Randomization: the follow-up experiment in `reports/results/retention_drivers.json` → `follow_up_experiment`.

- Randomize new visitors (`user_pseudo_id`, 50/50) into a "continue browsing" module that raises page depth, or a control.
- Primary metric: the same 30-day return outcome. Manipulation check: page views in the first 24h. Guardrail: 7-day purchase conversion (non-inferiority).
- At baseline 8.1%, α = 0.05 and power 0.8, the test needs **12,387 users per arm** for a 1.0 pp MDE (about 9 days of enrolment plus 31 days of outcome) or 48,272 per arm for 0.5 pp (about 35 + 31 days).
- If page depth rises but return does not, the observational association was intent rather than impact.

## 4. Where the experiments do and do not support causal claims

**Supported.** Randomization makes the arms comparable on everything, observed or not. The effect of the assigned treatment in that population and period is causal:
- Cookie Cats: moving the gate to level 40 **lowered** D7 retention by 0.82 pp (95% CI −1.33 to −0.31).
- Hillstrom: the Mens e-mail raised conversion by 0.68 pp (95% CI 0.50–0.86), and the Womens e-mail by 0.31 pp (95% CI 0.15–0.48).

**Not supported:**
- Post-treatment variables. Cookie Cats `sum_gamerounds` is measured after assignment, so "players who played more retained more" compares groups the treatment itself created. That is why rounds are a guardrail and never a segment.
- Subgroups that were not pre-specified, or that do not survive correction. In Hillstrom, 32 of 44 subgroup effects are nominally significant, but only **17 survive Holm**, and most of those are Mens-arm subgroups that simply share the strong overall effect. One treatment × covariate interaction survives Holm for conversion: Womens e-mail × newbie (Holm p = 0.015). Effects that differ between subgroups are only claimed where the interaction test supports them.
- Mechanisms. "The gate lowers retention *because* players get bored later" is a story. The test only identifies the total effect.
- Other times and populations. Cookie Cats is one game in one period (no timestamps, so novelty effects cannot be assessed). Hillstrom is one 2008 e-mail campaign.

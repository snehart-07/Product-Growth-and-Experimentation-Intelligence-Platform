# GrowthLab in Tableau Public: report build guide

This guide specifies six dashboards built on the validated exports in this folder. The workbook itself is **not** in the repository. You build it in Tableau Public Desktop by following these steps. Every number comes from the export tables, so nothing is typed in by hand.

Regenerate the data at any time with:

```bash
make tableau            # = .venv/bin/python scripts/export_tableau_data.py
```

`manifest.json` lists each table's row count, grain, data nature and SHA-256 hash, plus the results of all validation checks. `data_dictionary.md` documents every column.

---

## 0. Before you start

### Connect the data

| Option | How | Notes |
| --- | --- | --- |
| **CSV (recommended)** | Connect → **Text file** → open any file in `exports/tableau/csv/`. Each CSV appears as a table in the left pane. | Works in every Tableau Public version. |
| `growthlab.hyper` | Connect → **More…** → Tableau extract, if your version offers it. All 23 tables are in schema `Extract`. | Typed columns (dates and integers). Not verified in Tableau Public on this machine. If the option is missing, use the CSVs. |

**One data source per logical grain.** Do not join tables of different grains, such as daily counts with experiment arms. Create these data sources and give them these names:

| Data source | Tables | Used on |
| --- | --- | --- |
| `Store daily` | `store_daily` (relate `dim_date` on `first_visit_date` = `date` if you want weekday or month) | Overview, Visitor Journey, Retention KPIs |
| `Funnel stages` | `funnel_daily_long` | Visitor Journey |
| `Funnel reference` | `funnel_steps`, `funnel_segments` (separate sources) | Visitor Journey |
| `Cohorts` | `retention_cohorts` | Retention |
| `Retention reference` | `retention_rates`, `retention_tests` | Retention |
| `Behaviour` | `behaviour_groups` | Returning Users |
| `Behaviour model` | `behaviour_model`, `followup_test_design` | Returning Users |
| `Game` | `game_arms`, `game_effects`, `game_checks`, `game_power_curve` (four sources) | Game Experiment |
| `Email` | `email_arms`, `email_effects`, `email_subgroups`, `email_interactions`, `email_checks` (five sources) | Email Experiment |
| `Reference` | `overview_kpis` related to `metric_definitions` on `metric_key`, plus `dataset_catalog` | Overview, tooltips, limits |

`relationships.json` lists the only relationships that are safe to model.

**Cross-source filters.** Store tables share the field names `source`, `device` and `country_group`, so a filter set to *Apply to worksheets → All using related data sources* works across `Store daily`, `Funnel stages` and `Behaviour`. For week filters, map `week_start` to `cohort_week_start` in *Data → Edit Blend Relationships*. The `Behaviour` table only covers first visits up to Jan 1, so a later week shows an empty state there.

### Style system (editorial, restrained)

1. Copy `Preferences.tps` to `~/Documents/My Tableau Public Repository/` (merge it if one already exists), then restart Tableau. This adds the palettes *GrowthLab categorical / status / retention / effect*.
2. *Format → Workbook*: font **Tableau Book**, titles **Tableau Semibold**, text `#0E1631`.
3. Dashboard shading `#F7F6F2`, worksheet shading *None*, no borders. Row and column dividers *None*, grid lines light dotted `#ECEAE3`, zero lines `#0E1631` at 1 pt.

| Role | Hex | Use |
| --- | --- | --- |
| Ink | `#0E1631` | Text, navigation bar background |
| Indigo | `#5B4BEA` | Neutral product data, treatment, selected items |
| Indigo 2 / 3 | `#8E83F3` / `#CFCAFA` | Secondary bars, flows |
| Teal | `#0F9F8F` | Favourable results, guardrail passed |
| Coral | `#E5534B` | Adverse results; use sparingly |
| Amber | `#C7881B` | Uncertain, warning, scenario |
| Grey | `#A3A9B8` | Control, unclear |

- **Status rule:** colour is never used alone. Every status field below has an icon and a word: ✓ Passed, ✕ Not passed, – Unclear, ! Not measured.
- **Titles state the takeaway** ("79% of new visitors leave before seeing a product") and are built from fields, never typed numbers. Keep the subtitle to one line. Statistical detail goes in tooltips and a single "Evidence" container at the bottom of each dashboard.

### Shared parameters

| Parameter | Type | Values | Used by |
| --- | --- | --- | --- |
| `p_scale` | String | `Visitors`, `Share` | Funnel chart |
| `p_email_metric` | String | `conversion`, `visit`, `spend` (aliases: Bought something / Visited / Spend) | Email |
| `p_email_arm` | String | `Mens E-Mail`, `Womens E-Mail` | Email subgroups |
| `p_email_covariate` | String | `newbie`, `recency_grp`, `history_segment`, `zip_code`, `channel`, `product_line` | Email subgroups |
| `p_extra_conv_pts` | Float | 0.5–10, step 0.5, default 1 | What-if (scenario) |
| `p_aov` | Float | default = the `value` of `avg_order_value` in `overview_kpis.csv` (observed mean order value) | What-if (scenario) |
| `p_customers`, `p_cost_per_email` | Integer / Float | 100,000 / 0.10 | Email revenue scenario |

### Navigation and layout (every dashboard)

- **Size:** Fixed 1366 × 1800 for desktop. Add a *Phone* device layout: one column, the navigation reduced to a single "Menu" button, KPI tiles stacked two per row, the heatmap replaced by the D7 trend.
- **Top bar:** a horizontal container, background `#0E1631`, height 56 px. It holds the logo text "GrowthLab" (white, Tableau Semibold 14) and six **Navigation buttons** (*Dashboard → Objects → Navigation*): Overview · Visitor Journey · Retention · Returning Users · Game Experiment · Email Experiment. Button text `#B8BDD0`; the current page's button is white with a `#232E55` background.
- **Body:** a vertical container with 48 px outer padding:
  1. Kicker (11 pt, indigo, uppercase)
  2. Takeaway title (28–34 pt)
  3. One-line subtitle
  4. Compact filter row (dropdowns, *Show "All"*, no titles)
  5. Inline KPI tiles (text worksheets, no borders, thin vertical dividers)
  6. **One anchor chart** on a white container with 16 px padding
  7. Secondary views
  8. A collapsed *Evidence & limits* container: use a *Show/Hide* button on a floating container
- **Footer** (9 pt grey): "Public data · store data is observational · experiments are randomized · numbers computed in this repository".

### Calculated fields used everywhere

```text
// Wilson 95% interval for any rate x/n. Create one pair per rate, replacing [x] and [n].
[CI low]  = ( [x]/[n] + 1.96^2/(2*[n]) - 1.96*SQRT( ([x]/[n])*(1-[x]/[n])/[n] + 1.96^2/(4*[n]^2) ) ) / (1 + 1.96^2/[n])
[CI high] = ( [x]/[n] + 1.96^2/(2*[n]) + 1.96*SQRT( ([x]/[n])*(1-[x]/[n])/[n] + 1.96^2/(4*[n]^2) ) ) / (1 + 1.96^2/[n])

// Status label with icon (game_effects, email_effects, funnel_segments)
[Status label] = CASE [status]
  WHEN "clearly better" THEN "✓ Clearly better" WHEN "clearly worse" THEN "✕ Clearly worse"
  WHEN "pass" THEN "✓ Passed" WHEN "not pass" THEN "✕ Not passed" WHEN "missing" THEN "! Not measured"
  ELSE "– Unclear" END

// CI bar width (for Gantt-bar interval plots)
[CI width] = [ci_high] - [ci_low]
```

---

## 1. Overview: key metrics and trends

**Kicker:** PRODUCT GROWTH CASE STUDY · **Title** (calculated): "Where GrowthLab loses users, and which changes are worth shipping"

| Worksheet | Data source | Chart | Rows / Columns / Marks |
| --- | --- | --- | --- |
| `OV KPI tiles` | Reference (`overview_kpis`) | Text tiles (BANs) | Filter `metric_key` ∈ {new_visitors, product_view_rate, purchase_conversion, return_d7, game_d7_effect, email_mens_conversion}. Columns: `label`. Text: `[KPI value]` (32 pt) over `label` (10 pt grey) over `[KPI interval]`. |
| `OV findings` | Reference | Three text blocks | `section` = Funnel / Game / Email; value coloured by `[Tone]`. |
| `OV trend` | Store daily | Dual-axis line + area | Columns: DAY(`first_visit_date`). Rows: SUM(`new_visitors`) (area, indigo 30%) and SUM(`purchased`) (line, teal), dual axis, not synchronized. |
| `OV channels` | Store daily | Ranked horizontal bar | Rows: `source` sorted by SUM(`new_visitors`) desc. Columns: `[Share of new visitors]`. Label: percent. |
| `OV devices` | Store daily | Donut (pie + white centre circle on dual axis) | Angle: SUM(`new_visitors`); Colour: `device`. |
| `OV data strip` | Reference (`dataset_catalog`) | Text table | Columns: `dataset`, `nature`, `can_support`, `cannot_support`. |

**Calculated fields**

```text
[KPI value] = IF [unit] = "proportion" THEN STR(ROUND([value]*100, 1)) + "%"
              ELSEIF [unit] = "percentage points" THEN (IIF([value] >= 0, "+", "")) + STR(ROUND([value], 2)) + " pts"
              ELSEIF [unit] = "odds ratio" THEN STR(ROUND([value], 2)) + "×"
              ELSEIF [unit] = "USD" THEN "$" + STR(ROUND([value], 2))
              ELSE STR(ROUND([value], 0)) END
[KPI interval] = IF NOT ISNULL([ci_low]) THEN "95% CI " + STR(ROUND(IIF([unit]="proportion",100,1)*[ci_low],2))
                 + " to " + STR(ROUND(IIF([unit]="proportion",100,1)*[ci_high],2)) END
[Tone] = IF [ci_low] > 0 AND [unit] = "percentage points" THEN "favourable"
         ELSEIF [ci_high] < 0 AND [unit] = "percentage points" THEN "adverse" ELSE "neutral" END
[Share of new visitors] = SUM([new_visitors]) / TOTAL(SUM([new_visitors]))
```

- **Filters:** none on the KPI tiles, which show the published headline numbers. The trend, channel and device charts share a compact `source` / `device` / `country_group` filter row.
- **Actions:** clicking a channel bar filters the trend (*Filter action*, Select, clearing shows all values). Each finding block links to its dashboard with a *Go to Sheet* action.
- **Tooltips:** KPI tiles show `label`, `[KPI value]`, `[KPI interval]`, and `caveat` from the related `metric_definitions`. The trend shows the date, new visitors, purchases, and "Purchases are attributed to the first-visit date (7-day window)".
- **Limits** (Evidence container): the store data is observational and obfuscated, and a visitor is a browser cookie. The game data has no revenue.

## 2. Visitor Journey: funnel and largest drop-off

**Kicker:** FUNNEL · VISITOR JOURNEY · **Title** (calculated from `Store daily`): `[Leave before product]` + "% of new visitors leave before they see a single product"

| Worksheet | Data source | Chart | Specification |
| --- | --- | --- | --- |
| `VJ KPIs` | Store daily | Five inline tiles | Eligible visitors = SUM(`funnel_eligible`); rates below. Each tile shows a Δ vs all visitors when filtered. |
| `VJ funnel` *(anchor)* | Funnel stages | Breakaway bar chart | Columns: `stage` sorted by `stage_order`. Rows: Measure Values = [`Stage value`, `Lost below axis`]. Marks: bar, Colour: Measure Names (indigo 2 for reached, coral for lost) plus `[Is largest drop]` on detail, so the largest loss is full coral and the others are 40% coral. Labels: `[Stage label]` on top, `[Lost label]` below. Table calculations compute using `stage_order`. |
| `VJ drop insight` | Funnel stages | Text callout (coral left border) | "Biggest drop: " + lost + " of " + previous + " visitors leave between …". Computed with the same table calculations, filtered to `[Is largest drop]`. |
| `VJ segments` | Funnel reference (`funnel_segments`) | Bar + whisker (Gantt) | Rows: `segment` sorted by `rate`. Columns: `rate` (bar) and a Gantt from `ci_low` with size `[CI width]`. Colour: `clearly_different` (indigo vs indigo 3). Reference line: AVG(`overall_rate`). Filters: `segment_type` (single-value list: source / device / country), `stage` (single value). |
| `VJ fair device` | Store daily | Side-by-side bars | Rows: `source`. Columns: `[Purchase conversion]`. Colour: `device` (Desktop vs Mobile only). |
| `VJ what-if` | Store daily | Scenario tiles | Uses `p_extra_conv_pts`, `p_aov`. Amber badge "Scenario, not a forecast". |
| `VJ evidence` | Funnel reference (`funnel_steps`) | Text table | `order_type` (strict / any), `stage`, `users`, `conversion_from_previous`, `ci_low`, `ci_high`, `users_lost`, `median_minutes_from_previous`. |

**Calculated fields** (`Store daily`)

```text
[Product view rate]   = SUM([viewed_product])   / SUM([funnel_eligible])
[Cart rate]           = SUM([added_to_cart])    / SUM([funnel_eligible])
[Checkout rate]       = SUM([started_checkout]) / SUM([funnel_eligible])
[Purchase conversion] = SUM([purchased])        / SUM([funnel_eligible])
[Leave before product] = STR(ROUND(100 * (1 - [Product view rate]), 0))   // responds to the filters
[What-if extra viewers] = SUM([funnel_eligible]) * [p_extra_conv_pts] / 100
[What-if extra buyers]  = [What-if extra viewers] * SUM([purchased]) / SUM([viewed_product])
[What-if extra sales]   = [What-if extra buyers] * [p_aov]     // label: "Scenario, not a forecast"
```

**Calculated fields** (`Funnel stages`; table calculations along `stage_order`)

```text
[Stage users]       = SUM([users])
[Share of entrants] = SUM([users]) / LOOKUP(SUM([users]), FIRST())
[Step conversion]   = SUM([users]) / LOOKUP(SUM([users]), -1)
[Lost at step]      = LOOKUP(SUM([users]), -1) - SUM([users])
[Lost below axis]   = -1 * IIF([p_scale] = "Share", [Lost at step] / LOOKUP(SUM([users]), FIRST()), [Lost at step])
[Stage value]       = IIF([p_scale] = "Share", [Share of entrants], [Stage users])
[Is largest drop]   = [Lost at step] = WINDOW_MAX([Lost at step])
[Stage label]       = STR([Stage users]) + "  ·  " + STR(ROUND([Share of entrants]*100, 2)) + "%"
[Lost label]        = IF NOT ISNULL([Lost at step]) THEN "−" + STR([Lost at step]) + " left" END
```

- **Filters:** `source`, `device`, `country_group`, `week_start`, all with *Apply to worksheets → All using related data sources*. Add a **Reset** button: a *Navigation* button pointing to this dashboard, which reloads with default filters.
- **Actions:**
  - Clicking a funnel stage sets the `stage` filter on `VJ segments` (*Filter action*, target field `stage`).
  - Hovering a stage highlights it.
  - Clicking a segment bar filters `VJ funnel` to that source or device. The filter action maps the label field `segment` to `source` or `device`; use `segment_raw` when mapping to `source_raw`.
- **Tooltips:** for each stage, show visitors, share of entrants, step conversion, and the 95% interval (Wilson fields on the funnel counts), plus visitors lost. Add "Strict order: a step counts only after the previous one, within 7 days of the first visit."
- **Limits:**
  - Observational, so this shows where visitors leave, not why.
  - Groups under 500 visitors at a stage are excluded from `funnel_segments`.
  - Holm correction is applied across groups.
  - Revenue in the sample is altered for privacy, so the what-if output is a scale estimate only.

## 3. Retention: cohort heatmap and trends

**Kicker:** RETENTION · WHO COMES BACK · **Title:** "Only " + `[D7 return rate]` + " of new visitors return within a week"

| Worksheet | Data source | Chart | Specification |
| --- | --- | --- | --- |
| `RT KPIs` | Store daily | Tiles | D7 rate, D30 rate, eligible visitors (`d7_eligible`), returned (`d7_returned`). Each tile shows its 95% interval (Wilson). |
| `RT heatmap` *(anchor)* | Cohorts | Heatmap with a two-layer unknown mask | Rows: `cohort_label` sorted by `cohort_week`. Columns: `age_week` (discrete). Rows also get **MIN(0)** twice on a dual axis (synchronized, hidden). Layer 1: Square, colour fixed `#EFEDE7`, label "–", so every cell is grey. Layer 2: Square, colour = `[Retention (known)]` with the *GrowthLab retention* palette, fixed range 0–3.5%, label `[Retention label]`. Unknown cells are null on layer 2, so the grey shows through. They are never coloured as 0. |
| `RT cohort sizes` | Cohorts | Text column beside the heatmap | Rows: `cohort_label`. Text: `[Cohort size]` (age 1 only). |
| `RT trend` | Store daily | Two lines | Columns: `week_start`. Rows: `[D7 return rate]`, `[D30 return rate]` on a shared axis, indigo and teal. |
| `RT trend insight` | Retention reference (`retention_tests`) | Text | Filter `test_type` = cohort_equal_age, `age_week` = 1: "Return rates differ by first-visit week (Holm p …)" if `p_holm` < 0.05, else "Return rate is stable". |
| `RT segments` | Retention reference (`retention_rates`) | Bar + whisker | Filter `window` = d7 (parameter-switchable to d30), `segment_type` ∈ {source, device}. Reference line: the `all` row. Text box built from `retention_tests` (`segment_difference`): "No clear difference" when `p_holm` ≥ 0.05. |

**Calculated fields**

```text
[D7 return rate]    = SUM([d7_returned])  / SUM([d7_eligible])
[D30 return rate]   = SUM([d30_returned]) / SUM([d30_eligible])     // null-safe: only cohorts with full 30 days have eligibles
[D7 exact-day rate] = SUM([d7_exact_returned]) / SUM([d7_eligible]) // sensitivity, show in Evidence only
// Cohorts:
[Retention (known)] = IF MIN([is_fully_observed]) = 1 THEN SUM([active_users]) / SUM([cohort_size]) END
[Retention label]   = IF MIN([is_fully_observed]) = 1 THEN STR(ROUND([Retention (known)]*100, 1)) + "%" END
[Cohort size]       = SUM(IF [age_week] = 1 THEN [cohort_size] END)
[Small cohort]      = [Cohort size] < 100           // set opacity 35% via colour legend if any appear
[Cell status]       = IF MIN([is_fully_observed]) = 1 THEN "Known" ELSE "Not known yet: the data ends before this week completes" END
```

- **Filters:** `source`, `device`, `country_group` on the heatmap and the trend. Add a `Weeks shown` slider on `age_week` from 4 to 12.
- **Actions:**
  - Clicking a cohort row highlights the same week in the trend. This needs a blend mapping from `cohort_week_start` to `week_start`.
  - Hovering a column highlights that age.
- **Tooltips:** first-visit week, weeks later, came back (% and people), cohort size and `[Cell status]`.
- **Limits:**
  - Unknown cells are masked, never zero.
  - D30 exists only for first visits up to 2021-01-01.
  - Exact-day D7 is far lower (see Evidence), so the definition matters.
  - Patterns are observational.

## 4. Returning Users: behavioural comparisons and uncertainty

**Kicker:** DRIVERS · ASSOCIATION, NOT CAUSATION · **Title:** "Visitors who " + top behaviour + " on day one come back " + `[Ratio]` + "× as often". The top behaviour is the first row of `BU ladder` sorted by `[Gap pts]`.

| Worksheet | Data source | Chart | Specification |
| --- | --- | --- | --- |
| `BU KPIs` | Behaviour | Tiles | Add a data-source filter `behaviour_key` = pages_3plus on this sheet only (each behaviour partitions the same population, so one behaviour gives the population). Users = SUM(`users`), return rate = SUM(`returned`)/SUM(`users`). |
| `BU ladder` *(anchor)* | Behaviour | Dumbbell ("association ladder") | Rows: `behaviour` sorted by `[Gap pts]` desc. Columns: `[Return rate]`, dual axis with the same field. Layer 1: Line, Path = `did_it`, colour indigo 3, size 5. Layer 2: Circle, colour = `[Did it label]` (indigo filled vs white with a grey border). Label: `[Gap label]` on the "did it" end. |
| `BU warning` | none | Amber text callout | "Linked, not proven: people who already plan to shop may browse more and return. Only a randomized test can show cause." |
| `BU adjusted` | Behaviour model | Forest plot, log axis | Filter `model` = main, `term_type` = behaviour. Rows: `label` sorted by `odds_ratio`. Columns: Gantt from `or_ci_low`, size `or_ci_high − or_ci_low`, dual axis with a Circle at `odds_ratio`. Colour: `direction`. Axis: logarithmic, fixed 0.3–2.3. Reference line at 1. Subtitle: "Whole population; not affected by filters." |
| `BU intent check` | Behaviour model | Text | Page-view OR in the main model vs `intent_adjusted`. |
| `BU next test` | Behaviour model (`followup_test_design`) | Tiles | `mde_pp`, `n_per_arm`, `enrolment_days`, `total_days`, plus the `hypothesis` text. |

**Calculated fields** (`Behaviour`)

```text
[Return rate]   = SUM([returned]) / SUM([users])
[Did it label]  = IF [did_it] = 1 THEN "Did it on day one" ELSE "Didn't" END
[Rate did]      = SUM(IF [did_it] = 1 THEN [returned] END) / SUM(IF [did_it] = 1 THEN [users] END)
[Rate didn't]   = SUM(IF [did_it] = 0 THEN [returned] END) / SUM(IF [did_it] = 0 THEN [users] END)
[Gap pts]       = 100 * ([Rate did] - [Rate didn't])
[Ratio]         = ROUND([Rate did] / [Rate didn't], 1)
[Gap label]     = IIF([Gap pts] >= 0, "+", "") + STR(ROUND([Gap pts], 1)) + " pts"
```

- **Filters:** `source`, `device`, `country_group`, `week_start` on the ladder only. The adjusted model always uses the whole population, and its caption says so.
- **Actions:** clicking a behaviour on the ladder highlights the matching model term. Set up a highlight action on the field `behaviour`, after creating an alias map in `behaviour_model.label`.
- **Tooltips:** behaviour, return rate if they did it and if they didn't, the gap, visitors who did it, and "Association, not causation." Model tooltips show the OR, its 95% CI, the p-value and VIF.
- **Limits:**
  - Raw comparisons are not adjusted.
  - In the adjusted model, search and add-to-cart fall below 1× because they overlap with page views. They are not harmful.
  - Only first visits up to Jan 1 are included (31-day window). Never describe these results as effects.

## 5. Game Experiment: treatment effect, CI and guardrails

Lay this dashboard out as a decision memo:

1. Header with the decision pill
2. Primary result and its interval
3. Why this decision
4. Guardrails
5. What we learned
6. Next action
7. Evidence appendix

**Kicker:** EXPERIMENT · COOKIE CATS · **Title:** "Moving the gate to level 40 reduced day-7 retention" (show only when the primary `status` = "clearly worse". Otherwise build the title from `[Status label]`).

| Worksheet | Data source | Chart | Specification |
| --- | --- | --- | --- |
| `GM decision pill` | `game_checks` | Small text | Filter `check_key` = decision: `[Decision label]` coloured by `[Decision tone]`. Place it beside the kicker. |
| `GM big number` | `game_effects` | Large text | Filter `role` = primary: `[Effect label]` (60 pt, coral if clearly worse), then "95% CI … · relative … · p = …". |
| `GM CI plot` *(anchor)* | `game_effects` | Interval plot with decision zones | Filter `role` ∈ {primary, guardrail} and `metric_key` ≠ game_rounds. Rows: `metric`. Columns: Gantt from `ci_low` with size `[CI width]`, dual axis with a Circle at `estimate`. Colour: `status` (*GrowthLab status* palette). Analytics: reference line at 0 (ink, 1 pt). Reference bands: from axis minimum to 0 (coral 8%, "Worse"), 0 to 0.5 (grey 8%, "Too small"), 0.5 upward (teal 8%, "Worth shipping"). Reference line at `ni_margin` for the guardrail row (amber dashed). |
| `GM arms` | `game_arms` | Bars | Rows: `variant_label`. Columns: `d7_rate` (and `d1_rate` in a second pane). Colour: `role` (grey control, indigo treatment). |
| `GM why` | `game_effects` + `game_checks` | Numbered text list | 1) interval below zero; 2) day-1 guardrail `status`; 3) MDE vs observed effect (`game_checks` mde); 4) decision rule fixed in advance. |
| `GM guardrails` | `game_effects` + `game_checks` | Status list | Filter `role` = guardrail, plus the `revenue` row from `game_checks`: icon, metric, `[Status label]`, estimate and CI. |
| `GM power` | `game_power_curve` | Line | Columns: `effect_pp`. Rows: `power`. Reference lines: AVG(`mde_pp`) (amber dashed) and AVG(`observed_abs_effect_pp`) (coral), and 80% (grey dotted). |
| `GM checks` | `game_checks` | Text table (Evidence) | `check`, `value`, `threshold`, `[Status label]`, `detail`. |
| `GM robustness` | `game_effects` | Text table (Evidence) | Filter `role` = robustness: `method`, estimate, CI. |

**Calculated fields**

```text
[Effect label]   = IIF([estimate] >= 0, "+", "") + STR(ROUND([estimate], 2)) + IIF([unit] = "rounds", " rounds", " pts")
[CI label]       = "95% CI " + STR(ROUND([ci_low], 2)) + " to " + STR(ROUND([ci_high], 2))
[Decision label] = CASE [status] WHEN "do_not_ship" THEN "✕ Don't ship" WHEN "ship" THEN "✓ Ship"
                   WHEN "iterate" THEN "! Iterate" WHEN "hold" THEN "– Hold" END
[Decision tone]  = CASE [status] WHEN "do_not_ship" THEN "adverse" WHEN "ship" THEN "favourable" ELSE "uncertain" END
```

- **Filters:** none. This is a single experiment, and the data has no pre-treatment covariates, so it cannot be segmented. `sum_gamerounds` is measured after assignment and must never be used to split players.
- **Actions:** hovering a CI row highlights the matching arm bars and guardrail row.
- **Tooltips:** metric, role, method, estimate, 95% CI, p-value, non-inferiority margin, `[Status label]` and `interpretation`.
- **Limits:**
  - **Revenue is missing.** It is the key guardrail for a gate change.
  - There is no 30-day retention, and no dates, so novelty effects can't be checked.
  - The analysis is retrospective. The plan is a timestamped commit, not a pre-registration.
  - The sample-ratio p-value is about 0.009. That is below 0.01 but not flagged at the 0.001 threshold set in advance; show it in Evidence.

## 6. Email Experiment: results and recommendations

Use the same memo structure as the game experiment. **Keep it separate from the game data**: these are different populations and a different source.

**Kicker:** EXPERIMENT · HILLSTROM E-MAIL · **Title:** "The men's email more than doubled purchases". Show this title only when the men's email `value` for conversion in `email_arms` is more than twice the No E-Mail value. Otherwise use "Both emails raised purchases".

| Worksheet | Data source | Chart | Specification |
| --- | --- | --- | --- |
| `EM decision pill` | `email_effects` | Small text | "✓ Ship" when every `role` = primary row has `status` = "clearly better", else "– Hold". |
| `EM big number` | `email_effects` | Large text | Filter `arm` = Mens E-Mail and `metric_key` = `[p_email_metric]`: effect, CI, Holm p. |
| `EM CI plot` *(anchor)* | `email_effects` | Interval plot | Filter `metric_key` = `[p_email_metric]`. Rows: `arm_label`. Gantt + circle as in the game dashboard. Reference line at 0, plus bands "Worse" (coral) and "Better than no email" (teal). |
| `EM arms` | `email_arms` | Bars + whiskers | Filter `metric_key` = `[p_email_metric]`. Rows: `arm_label`. Columns: `value` with a Gantt from `ci_low` (rates only). Colour: `arm_label` (grey, indigo, indigo 2). |
| `EM why` | `email_effects` | Numbered list | Both arms vs control (Holm), visit and spend direction, and the one interaction that matters. |
| `EM who it works for` | `email_subgroups` | Forest plot | Filter `arm` = `[p_email_arm]` and `covariate` = `[p_email_covariate]`. Rows: `level_label`. Gantt `ci_low_pp`–`ci_high_pp` + circle `estimate_pp`. Colour: `survives_holm` (teal vs grey). |
| `EM interaction note` | `email_interactions` | Text | Filter `outcome` = conversion and the same two parameters: "This split really matters" if `real_difference` = 1, else "No real difference between these groups (Holm p …)". |
| `EM checks` | `email_checks` | Status list | Balance (`srm`), CUPED rows, and the uplift row labelled "Exploratory". |
| `EM revenue scenario` | `email_effects` | Tiles | Filter `metric_key` = spend: `[Net scenario]` per arm, with an amber "Scenario, not a forecast" badge. |

**Calculated fields**

```text
[Net scenario] = [p_customers] * (SUM([estimate]) - [p_cost_per_email])     // spend is revenue, not profit
[Subgroup verdict] = IF [survives_holm] = 1 THEN "✓ Clear lift (strict test)" ELSE "– Not clear" END
[Arm value label]  = IF [metric_key] = "spend" THEN "$" + STR(ROUND([value], 2)) ELSE STR(ROUND([value]*100, 2)) + "%" END
```

- **Filters / parameters:** `p_email_metric` (a segmented-looking parameter control), `p_email_arm`, `p_email_covariate`.
- **Actions:** a *Change parameter* action on clicking an arm in `EM CI plot` sets `p_email_arm`.
- **Tooltips:**
  - Effects: arm, metric, estimate, 95% CI, method, p and Holm p.
  - Subgroups: n treatment and control, rates, lift and CI, Holm and BH p.
  - All: "Customers were randomized, so the email caused this lift for these customers in this campaign."
- **Limits:**
  - Two-week window only, so nothing can be said about retention or long-term effects.
  - It was a 2008 marketing test, not a product change.
  - The uplift model is exploratory, and CUPED gave about 0% variance reduction.
  - Subgroup gaps need the interaction test before they are claimed.

---

## 7. Data that is not available

| Wanted | Status | Show as |
| --- | --- | --- |
| Game revenue / in-app purchases | Not in the data | "! Not measured" guardrail row |
| Game day-30 retention, novelty effects | Not in the data | Limits note |
| Store D30 for first visits after Jan 1 | Not observable | Null, masked |
| Store revenue truth | Obfuscated by Google | Scenario label |
| Accounts and sign-ups | Not in GA4 (cookies only) | `first_visit` used as a proxy, stated in the subtitle |
| Email long-term effects | 2-week window | Limits note |
| Synthetic sandbox | Simulated, so **not exported** | `dataset_catalog` row with `exported` = 0 |

## 8. Before you publish

- [ ] Every title and number is bound to a field. Nothing is typed.
- [ ] Every interval appears in a tooltip or label, and every status has an icon and a word.
- [ ] Store dashboards never say "effect", "impact", "drives" or "caused". The experiments may say "caused", but only for the assigned treatment.
- [ ] The heatmap's unknown cells are grey "–", never 0.
- [ ] Spot-check against `overview_kpis.csv`: D7 return, product view rate, the game effect and both email lifts.
- [ ] Tableau Public publishes the workbook **and its data** publicly. That is fine here because all sources are public, but don't add private data.
- [ ] Publish with *Show sheets as tabs* off. Use the dashboards' own navigation buttons instead.

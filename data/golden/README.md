# Golden sets

Hand labels used by the `-m db` acceptance tests. Each file holds `accident_id` plus labels only: no narrative text, place names, or MP data. Labels are made by the owner reading the source record, never by a model.

- `activity_v1.csv` — `accident_id,label` (`climbing|climbing_approach|non_climbing`), 60 rows drawn by `r4-golden-export` with quotas toward ski-approach, other ski/snowmobile, NPS, climbing and non-climbing rows (the rule's hard cases, not a uniform sample); R4 must score ≥95%. Rows with a blank activity are not drawn: R4 never classifies them, the owner does (`r4-review-export`/`r4-review-import`).
- `experience_v1.csv` — `accident_id,exp_years_climbing,exp_stated_level,exp_first_season,guided`, 80 rows drawn by `r12-golden-export`: 60 where R12 fills at least one value, 20 where it fills none (to measure recall); R12 precision ≥95% on filled values, recall reported. `exp_first_season` is `true`, `false` or blank.
- The exports never show the rule's decision to the labeller.

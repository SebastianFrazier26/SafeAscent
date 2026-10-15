# Manual facts CSVs (R11)

`accident_facts.template.csv` is the only file committed here: the header of the facts-only CSV
that `app.pipelines.accident_facts` accepts. Filled files (AAC entries typed from ANAC/The
Prescription, CAIC and NPS exports from the private tools) live in
`~/Developer/safeascent-private/manual/` until legal question Q6 is answered (Decision D12).
`.gitignore` ignores every other `data/manual/*.csv`, so a filled file is never committable.

## Rules

- Facts only: date, place, activity, type, severity, experience facts, URL. `summary` is our own
  one-line wording, at most 200 characters; never paste report text. It is stored in
  `accidents.description`. No scraped HTML, ever.
- `source_url` must be `https://`.
- Text values must fit their database columns: `source` 50 characters, `source_id` 100, `state`
  100, `mountain` and `route` 255, `activity` and `accident_type` 100, `injury_severity` 50.
  Anything longer is quarantined `invalid`.
- A blank cell means "not given". `exp_stated_level` and `guided` then become `unknown` (their
  default); the other optional facts stay empty (NULL). A blank is never read as 0.
- Dates after the load day are quarantined.
- The country comes from the data and is never assumed:
  - with coordinates, the point must be in the US and `state` must be a US state (a two-letter
    code or the full name). A foreign state is quarantined `outside_us`, and an unrecognised one
    is quarantined `unknown_country`;
  - without coordinates, `geocode_precision` must be `unknown` and `state` must be a US state, or
    the row is quarantined `unknown_country`.
- `source` must map to a source family (AAC, avalanche, NPS), `injury_severity` to a known
  severity, and `activity` to a known activity. The placeholders `unknown` and `other`, and
  AAC `ski mountaineering`, are quarantined `activity_needs_review`: fix the activity in the CSV
  and reload.
- A `(source, source_id)` already in the database is quarantined `already_present`. The source is
  compared ignoring case and surrounding spaces (`aac` is `AAC`), and a new row is stored with the
  spelling the source already has in the table. Existing incidents are never changed or deleted.
- Loads and `--apply-route-ids` runs take one advisory lock, so two runs never insert the same row
  twice; a second run waits for the first. `--dry-run` takes no lock.

## Loading

From `backend/`, as `ingest` (`INGEST_DATABASE_URL`):

```
uv run python -m app.pipelines.accident_facts --file ~/Developer/safeascent-private/manual/<file>.csv --dry-run
uv run python -m app.pipelines.accident_facts --file ~/Developer/safeascent-private/manual/<file>.csv
```

`--dry-run` opens a read-only transaction and reports what would be inserted, what is
`already_present`, and how many rows would need a route decision. It writes nothing.

The run log (`source_ingest_log`, source `accident_facts`) lists post-2024-08 months with no
incident for each source family in the file, under `problems`. Only day- and month-precision
dates count: a year-precision date does not fill a month.

## Linking rows to Mountain Project routes

`mp_route_id` is optional.

- If you fill it, it must be an id in `mp_routes`, or the row is quarantined `unknown_route`.
- If you leave it blank, a climbing row with a trusted point (`exact`, `crag`, `area` or
  `park_centroid`) is matched against the MP routes within 1000 m. The link is set only when
  exactly one of them has the same normalised name as `route`. Case, punctuation, accents, a
  leading "the" and "Rte" for "Route" are ignored. Near names such as "Northeast Ridge" and
  "Northwest Ridge" never match.
- Every other climbing row is loaded with `mp_route_id` empty. Each load rewrites
  `data/review/r11_route_matches.csv` with every R11 climbing row still without a link, from
  this load and earlier ones, so `--overwrite` never drops an unresolved row. Reasons: no point,
  an untrusted point, no route text, no route nearby, no equal name, or several equal names. The
  file is gitignored, UTF-8 with a BOM, and holds ids, names and distances only. It is never
  replaced unless you pass `--overwrite`; a load that would replace it is refused and rolled back.
- The review file is never read back. Its last column, `note`, is for your own notes only.
  **Route ids go in the facts CSV's `mp_route_id` column**, not in the review file.

The round trip:

1. Read the review file and pick the route, if any.
2. Put its id in the row's `mp_route_id` in the facts CSV.
3. Run:

   ```
   uv run python -m app.pipelines.accident_facts --file <the same CSV> --apply-route-ids [--dry-run]
   ```

`--apply-route-ids` is needed because a reload would only report the row as `already_present`.

It sets `mp_route_id` only where it is still empty, and only on rows R11 loaded. Each change is
recorded in `internal.accident_revisions` (field `mp_route_id`, method `facts_csv`). A legacy row
from the frozen 0004 snapshot is quarantined `not_an_r11_row`. A row whose link is already set
to a different id is quarantined `route_already_set`.

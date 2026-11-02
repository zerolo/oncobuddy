# Contributing curated guidelines

Curated guideline entries are clinical content. Be conservative: link, do not copy.

## The link-and-own-words rule

- Never paste text from a guideline whose licence is restricted. For `licence: restricted-link-only`
  the `summary` stays EMPTY; the entry only points to the source (`url`).
- A summary is allowed only for a verified open licence (e.g. an SPDX id such as `CC-BY-4.0`); it
  must be written in your own words (`own_words: true`) and be at most 400 characters.
- Never state a drug, stage, biomarker or evidence grade the source does not itself state.
  `interventions: []` means "a guideline exists for this scope; no drug asserted".

## Required fields

One guideline per file in `src/oncobuddy/curated/guidelines/`. All keys are required and unknown
keys are rejected: `id`, `title`, `issuer` (the source), `url` (https), `region` (a region ID in
`src/oncobuddy/regions.yaml`), `published`, `valid_as_of` (YYYY-MM-DD in quotes, or `"unknown"`;
never invented), `licence`, `reviewed_by`, `evidence_grade`, `own_words`, `summary`, and
`applies_to` (`conditions` as ICD-10 3-character categories such as `C50`, `stages`,
`required_biomarkers`, `line_of_therapy`, `interventions`). Quote dates and region IDs such as
`"NO"` so YAML does not coerce them.

`valid_as_of` is the date of the CITED EDITION (online publication date), NOT a claim that the
guideline is current; every record is marked `may_be_superseded: true`. `stages` must come from
the guideline itself; where a seed infers them from the title ("early" => I-III, "metastatic" =>
IV) that is an inference, not a statement of the guideline. Files over 64 KiB are rejected.

## Reference-only entries

`reference_only: true` marks a guideline issued for a region outside the EU subtree (US, UK). The
loader rejects it for EU-scope regions and global. Such entries are listed only in
`reference_elsewhere`, never mixed into results, matching or options.

## Adding a country

Add the region to `src/oncobuddy/regions.yaml` first (with its parent), then use its ID in
`region`. Entries with an unknown region fail validation.

## Reviewer checklist

- The source link works and points to the guideline itself.
- Every claim in the entry matches the source.
- The licence was verified on the source and recorded (see `docs/sources/guidelines.md`).
- `reviewed_by` is set to the reviewer's name. It is never defaulted or pre-filled for someone else.

## Adding a country

A new country region needs BOTH an entry in `src/oncobuddy/regions.yaml` and an entry in the
`COUNTRY_TO_REGION` map in `src/oncobuddy/connectors/trials.py`. With only one of them, trials
for that region are not searched.

## CI

CI validates every curated YAML through the same loader (`load_guidelines`), via
`tests/curated/test_seed_files.py`. Run `uv run pytest -q` and `uv run ruff check .` before
opening a pull request. Guideline links must point to a host in `ALLOWED_HOSTS` in
`tests/curated/test_seed_files.py`; extend that constant when adding a new source.

## Entries that name interventions

An entry that lists interventions asserts a clinical claim, so it must also carry `reviewed_by`
(a person, never `unreviewed-seed`), `reviewed_on` (a past date, not before `valid_as_of`),
stages, a stated line of therapy (not `any`) and a `recommendation_ref`: the locator (table,
figure or section) in the cited guideline where the recommendation appears. The loader rejects
anything less. Scope-only entries (no interventions) need none of this. Restricted-licence
entries keep `summary` empty. Files with duplicate keys or exotic line separators (U+0085,
U+2028, U+2029, bare CR) are rejected.

onco@medbuddy does not depend on such entries: for most questions the agent reads the source itself
through the browser handoff (see the access blocks below).

## Seed entries

Entries marked `reviewed_by: unreviewed-seed` are placeholders pending clinical review. They are
reported with `review_status: "unreviewed"` and must not be relied on clinically.

## Access blocks

A guideline whose source cannot be fetched by a program may declare `access` (`method: browser`,
`reason`, `read_recipe`, optional `entry_url`). A recipe says only where to look and what to
report: no doses, links or domains, and no drug or regimen names (a heuristic rejects common INN
stems). `entry_url` must be on the record's own host or in `ALLOWED_HOSTS` in
`src/oncobuddy/core/access.py`; extend that set deliberately when adding a source.

## Biomarker keys

`required_biomarkers` keys must be canonical keys from `src/oncobuddy/core/biomarkers.py`
(the loader rejects aliases and unknown keys). To add a marker, add it to `CANONICAL` with a
one-line meaning of "positive" in your own words, plus any aliases, and extend its tests.

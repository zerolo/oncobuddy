# oncoBuddy v2 slice D: source discovery and read plans - Design Spec

Date: 2026-11-02
Status: Draft, awaiting user review
Builds on: `2026-11-01-oncobuddy-design.md`, `2026-11-01-browser-handoff-design.md`,
`2026-11-01-reviewed-guidelines-design.md` (its drafts/promote workflow was removed on 2026-11-02;
only the loader rules for hand-curated entries that name interventions remain)

## 1. Purpose

oncoBuddy is a plugin for Claude and other agents that gives an oncologist's agent oncology
knowledge with reliable references. For sources a program cannot fetch (ESMO behind a bot check,
INFARMED Infomed) the agent reads them in the user's own browser, with the user's consent (the
browser handoff). What is still missing is **discovery**: the tool knows four ESMO guidelines, all
typed by hand. This slice makes the tool find the right sources for a condition and a profile,
without any clinical claim and without per-document sign-off.

Success: for a condition (and optionally a profile) the tool returns every relevant ESMO guideline,
most specific first, each with its access instructions; for a profile it adds a short `read_plan`
telling the agent what to look for in each source. The agent then asks the user, reads, reasons and
cites. The server stores nothing the agent reads.

## 2. Non-goals and queue

Not in this slice, queued to start right after it (maintainer decision, 2026-11-02), in order:
1. More registry sources of the same shape: NCCN, ASCO, NICE, DGS (Portugal). Reachability and the
   maintainer's access are verified first.
2. A Crossref/PubMed edition check at query time (flag superseded or corrected guidelines).
3. Plugin packaging: manifest and one-step install.

Also out of scope: any server-side fetching of `browser` sources, headless or simulated browsers,
clinical claims in registry entries (no drug, no recommendation, no stage not stated in the
title), changes to matching logic, and any change to the agent protocol.

## 3. Registry

A registry entry is the existing scope-only guideline file (`curated/guidelines/*.yaml`), so no new
loader path:
- `reviewed_by: unreviewed-seed`, `interventions: []`, `licence: restricted-link-only`,
  `summary: ""`, `required_biomarkers: {}`, `line_of_therapy: any`, `evidence_grade: null`;
- `url`: the DOI link of the guideline (`https://doi.org/...`), `published` and `valid_as_of`: the
  cited edition's online date, read from the DOI record;
- `applies_to.conditions`: ICD-10 3-character categories taken from a fixed topic table (below);
  `applies_to.stages`: only if the title states a stage scope, otherwise `[]` (the matcher already
  reports "guideline states no stage scope" as a caveat);
- `access`: `method: browser`, `reason: bot_check`, the shared recipe from the browser-handoff
  slice (where to look and what to report; no drug, no dose).
- New optional field `last_verified: YYYY-MM-DD` (date the entry's link and metadata were last
  checked); it appears in the record payload and in a note when older than 540 days.

**Topic table.** `src/oncobuddy/curated/esmo_topics.yaml` maps each ESMO tumour-type topic name to
a list of ICD-10 categories (for example breast cancer to `C50`, colorectal cancer to `C18, C19,
C20`). It is the only place that decides routing, it holds no clinical claim, and the maintainer
reviews it once (about 30 lines), not per guideline. A guideline whose topic is not in the table is
not added.

## 4. Refresh procedure (agent-assisted, outside the server)

ESMO cannot be fetched by a program, so the registry is refreshed by an agent working in the
maintainer's own Chrome, following `docs/runbooks/refresh-esmo-index.md`:
1. With the maintainer's consent and `esmo.org` allowed in the extension, read ESMO's guideline
   index pages and list each clinical practice guideline: title, topic, DOI.
2. Create or update one YAML per guideline, keeping ids stable (`esmo-<slug>`); the four existing
   seeds keep their ids.
3. Run `scripts/verify_dois.py`, which asks the doi.org handle API (not behind a bot check) whether
   each DOI exists and reports mismatches; it also fails on a DOI that is not in the registry
   format. It changes no file.
4. Run `uv run pytest -q`: the loader validates every file, and a registry test checks id
   uniqueness, allowed hosts, topic coverage and that no entry names an intervention.

The DOI and the date come from Crossref; the date is Crossref's record creation date cross-checked
against ESMO's listing date.

The procedure copies no guideline text; only titles, DOIs and dates are recorded.

## 5. Discovery

- `guidelines_for` and `regional_status` return registry records exactly as today (region scope
  chain unchanged; ESMO is EU and therefore applies to PT). Ordering gains one rule: within a
  region, an entry with fewer condition categories comes first, then by id.
- Records with an `access` block keep carrying it; `last_verified` is shown in the payload.

## 6. Read plan

`treatment_options` adds a single top-level `read_plan` to its result. It applies to every browser
guideline listed (the text is identical for all of them), decided at implementation. Deterministic,
built only from closed fields:
- stage (I to IV) when known;
- canonical biomarker keys with status `positive` or `negative` (unknown statuses and keys outside
  the vocabulary are omitted; their count is already in the notes);
- line of therapy: `first` when `prior_lines == []`, `later` when non-empty, omitted when unknown;
- intent (`curative` or `palliative`) when known.

Format: at most 4 strings, each at most 200 characters, built from fixed templates, for example
`Find the recommendations for stage II, HER2 positive, first-line treatment` and
`Report each named drug with its printed evidence level and the setting it applies to`. No
free text, no ICD code of the patient, no prior-drug names. The plan is an instruction to the
agent, never a recommendation, and every plan ends with the fixed line `This plan is a reading
instruction, not advice`. The existing privacy rule holds: nothing profile-supplied outside the
closed enums and the canonical vocabulary appears in any output, log or cache.

## 7. Testing
- Loader: `last_verified` format and the 540-day note; new entries validate; no registry entry
  names an intervention; ids unique; every `conditions` category belongs to the topic table.
- Registry: all files load; every ESMO entry has `access.method: browser`; hosts allowed.
- Topic table: parses; categories are valid ICD-10 3-character categories; no duplicates.
- Ordering: `guidelines_for("C50.9", "PT")` returns the more specific entry first (fixture).
- Read plan: built only from the closed fields (property test over profiles with unknown
  biomarker keys and prior drugs: neither ever appears); respects the 4-string and 200-character
  limits; `unknown` fields are omitted; ends with the fixed line; present for in-scope browser
  guidelines and absent for guidelines without `access`.
- `scripts/verify_dois.py`: unit-tested with a fake handle API (no network in CI).
- Existing suites stay green; the privacy test still asserts a patient-specific biomarker key
  and prior drug never appear in any output.

## 8. Risks
- A wrong topic-table mapping misroutes a guideline; mitigated by the one-time review, the tests
  on category validity and the `last_verified` note.
- ESMO may reorganise its index or add guidelines; the registry then lags until the next refresh.
  The `last_verified` date and the edition check queued as item 2 make staleness visible.
- A read plan may be mistaken for advice; the fixed closing line, the protocol and the `agent-read`
  label mitigate it.

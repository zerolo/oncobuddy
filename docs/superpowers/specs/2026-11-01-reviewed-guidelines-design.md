# oncoBuddy v2 slice B: reviewed guideline workflow - Design Spec

Date: 2026-11-01
Status: Draft, awaiting user review
Builds on: `2026-11-01-oncobuddy-design.md` (sections 2.6, 4)

## 1. Purpose

`treatment_options` can already return named interventions when a guideline entry lists them
(`applies_to.interventions`), but all four shipped entries are scope-only seeds. This slice adds the
process and validation that let a clinician add entries naming interventions safely, and ships
one or two **draft** candidates for the user to review. No unreviewed clinical claim may reach an
agent.

## 2. Non-goals
- No clinical content shipped as reviewed in this slice. The user reviews drafts later.
- No copying of restricted guideline text; restricted entries stay summary-free.
- No change to matching logic or to the MCP tool schemas.
- No automated sourcing of clinical claims by the server (it contains no LLM).

## 3. Review states

| State | Where | `reviewed_by` | May name interventions | Served by server | Packaged in wheel |
|---|---|---|---|---|---|
| Seed | `curated/guidelines/` | `unreviewed-seed` | No | Yes (scope only) | Yes |
| Draft | `curated/drafts/` | `draft:<author-label>` | Yes | **Never** | **No** |
| Reviewed | `curated/guidelines/` | clinician name | Yes | Yes | Yes |

The only route from draft to served is a file in `curated/guidelines/` carrying a reviewer name
and date. The loader used by the server reads `guidelines/` only; `drafts/` is excluded from the
build (hatch exclude) and from `load_guidelines` default.

## 4. Validation (pydantic, in `curated/loader.py`)

New optional fields on `GuidelineFile`: `reviewed_on` (YYYY-MM-DD), `recommendation_ref`
(string, 1-80 chars, a locator such as "Figure 2" or "Table 4" - a pointer, never quoted text).

Rules when `applies_to.interventions` is non-empty:
- Live (`guidelines/`): `reviewed_by` must not be `unreviewed-seed` or start with `draft:`;
  `reviewed_on` and `recommendation_ref` are required; `reviewed_on` must not be in the future and
  must not precede `valid_as_of` when that is a date.
- Draft (`drafts/`): `reviewed_by` must start with `draft:`; `reviewed_on` must be absent.
- Seed entries with interventions are rejected.
- `stages` must be non-empty; `line_of_therapy` may not be `any` when interventions are listed
  (the setting must be stated).
- Restricted licence: `summary` empty, as today. Error text never echoes file content.

The payload `review_status` becomes `unreviewed | reviewed` as today; drafts never become records
in the served path. `reviewed_by` and `reviewed_on` appear in the record payload for provenance.

## 5. Review tooling

New entry point `oncobuddy-guidelines` (module `oncobuddy.curated.cli`, stdlib `argparse`):
- `check [--dir DIR]`: validates `guidelines/` and `drafts/`, prints counts; non-zero exit on error.
- `review ID`: prints a checklist for a draft: source URL, edition date, each claimed intervention
  with line, stage, biomarkers, and `recommendation_ref`, plus the verification prompts
  (does the guideline list this intervention for this setting; is the edition current; are
  biomarker requirements correct).
- `promote ID --reviewer NAME --on YYYY-MM-DD`: moves the file to `guidelines/`, setting
  `reviewed_by` and `reviewed_on`, then re-validates; refuses on any validation error or an
  existing id. It edits no clinical field. Without `--reviewer` and `--on` it refuses.

The commands are offline and file-local. They never print patient data (none exists here).

## 6. Candidate entries

An independent sonnet subagent drafts one or two entries into `curated/drafts/` (user choice:
no tumour type preference given; agent picks the best-documented among the four existing seed
guidelines). Inputs allowed: the guideline's open abstract, the PubMed record, and the DOI landing
page. Each drafted intervention needs a `recommendation_ref` and the agent lists in its report
anything it could not confirm. I verify structure, source URLs and that no guideline text is
copied; clinical correctness is the user's review (the drafts' README says so).

## 7. Documentation and CI
- CONTRIBUTING.md: review-state table, promote workflow, rule that drafts are never served.
- `docs/sources/guidelines.md`: records the draft candidates and their status.
- CI runs `oncobuddy-guidelines check`.
- Tests: draft never loaded by default loader; each validation rule above (rejects and accepts);
  promote success and refusals; wheel contents exclude `drafts/`; `treatment_options` with a
  reviewed fixture returns a named option while a draft fixture returns none; CLI exits non-zero
  on invalid files; error messages do not echo file content.

## 8. Risks
- A reviewer name is self-asserted; the tool cannot prove clinical review. Mitigation: the name and
  date are surfaced in every record, and the live folder is under version control.
- A reviewed entry may go stale. Mitigation: existing `may_be_superseded` and edition caveats stay.

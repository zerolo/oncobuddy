# Reviewed Guideline Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a clinician add guideline entries that name interventions behind explicit review states, with drafts that can never be served.

**Architecture:** Extend the existing curated loader (`GuidelineFile` + `load_guidelines`) with review-state validation and a drafts mode; add a small offline CLI (`check`, `review`, `promote`); exclude `curated/drafts/` from the wheel and sdist; add two model-drafted candidate entries that stay in `drafts/`.

**Tech Stack:** Python 3.11+, pydantic v2, pyyaml, stdlib argparse, pytest, hatchling.

**Spec:** `docs/superpowers/specs/2026-11-01-reviewed-guidelines-design.md` (builds on `2026-11-01-oncobuddy-design.md`).

## Global Constraints

- Branch `v2-infarmed-pointers`, worktree `/home/ze/oncoBuddy/.claude/worktrees/v1`. Run `export PATH="$HOME/.local/bin:$PATH"`; tests `uv run pytest -q`, lint `uv run ruff check .`. Bash must use plain separate commands (no compound `cd`+git).
- Restricted-licence entries: `summary` stays empty; no guideline text is ever copied. `recommendation_ref` is a locator (1-80 chars), never quoted text.
- Error messages name file and field only and never echo file content (existing `_describe` pattern).
- Drafts (`reviewed_by` starting `draft:`) are never loaded by the default loader, never served, and excluded from wheel and sdist.
- No clinical field is ever edited by tooling; `promote` only sets `reviewed_by` and `reviewed_on` and moves the file.
- No LLM in the server; no network in tests; no change to MCP tool schemas or matching logic.
- Never use the "fable" model for subagents.
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A draft file placed in `guidelines/` (live dir) by mistake: loader must reject it, not serve it.
- A reviewed entry naming interventions with `line_of_therapy: any` or empty `stages`: rejected.
- `reviewed_on` in the future or earlier than `valid_as_of`: rejected.
- `promote` on an id that exists live, a non-draft, or an invalid draft: refuses and leaves both folders unchanged.
- A bad file's error text must not contain its content; a wheel build must not contain `drafts/`.

---

### Task 1: Review-state validation in the loader

**Files:**
- Modify: `src/oncobuddy/curated/loader.py`
- Create: `src/oncobuddy/curated/drafts/.gitkeep`
- Test: `tests/curated/test_review_states.py`

**Interfaces:**
- Consumes: existing `GuidelineFile`, `load_guidelines`, `_to_record`, `_describe`.
- Produces:
  - `GuidelineFile` gains `reviewed_on: str | None = None` and `recommendation_ref: str | None = None` (1-80 chars when set; `reviewed_on` validated as a date).
  - `def drafts_directory() -> Path` (the packaged `curated/drafts`).
  - `def load_guidelines(directory: Path | None = None, *, drafts: bool = False) -> list[Record]`: with `drafts=True` and no directory, reads `drafts_directory()`, applies draft rules and returns records (for validation only; the server never calls it).
  - Payload gains `reviewed_by`, `reviewed_on`, `recommendation_ref` (when set) for provenance.

- [ ] **Step 1: Write failing tests** in `tests/curated/test_review_states.py`, using `tmp_path` YAML fixtures built from a helper `entry(**overrides)` (copy a seed dict, restricted licence, `interventions: ["trastuzumab"]`, `line_of_therapy: first`, `stages: ["II"]`, `reviewed_by: "Dr Example"`, `reviewed_on: "2026-11-01"`, `recommendation_ref: "Table 4"`). Tests, each with exact assertions:
  - `test_reviewed_with_interventions_loads`: live load returns a record whose payload `applies_to.interventions == ["trastuzumab"]`, `review_status == "reviewed"`, `reviewed_on == "2026-11-01"`.
  - `test_live_rejects_seed_reviewer_with_interventions`, `test_live_rejects_draft_reviewer`, `test_live_requires_reviewed_on_and_ref` (each `pytest.raises(ValueError)` with the field name in the message).
  - `test_live_rejects_any_line_and_empty_stages_with_interventions`.
  - `test_reviewed_on_future_rejected` and `test_reviewed_on_before_valid_as_of_rejected` (valid_as_of date later than reviewed_on).
  - `test_draft_mode_accepts_draft_and_rejects_reviewed_on`, `test_draft_mode_rejects_non_draft_reviewer`.
  - `test_default_loader_never_reads_drafts`: a draft file in the default dir list is not returned; `load_guidelines()` (packaged) returns no record whose `reviewed_by` starts with `draft:`.
  - `test_error_does_not_echo_content`: a file with a secret-looking value `"SECRET-MARKER"` in a bad field yields a message without that string.
  - `test_seed_with_interventions_rejected`.
  Seed entries with `interventions: []` and `reviewed_by: unreviewed-seed` must still load unchanged (existing tests stay green).

- [ ] **Step 2: Run** `uv run pytest tests/curated/test_review_states.py -q`; expect failures (new fields unknown).

- [ ] **Step 3: Implement** in `loader.py`: add the two fields with validators; add `_check_review(spec, drafts: bool)` called from `load_guidelines` after model validation, raising `ValueError` with messages formed from field names only, implementing exactly the rules in spec section 4. Rules when `interventions` non-empty: live needs non-seed non-`draft:` `reviewed_by`, `reviewed_on`, `recommendation_ref`, `stages` non-empty, `line_of_therapy != "any"`, `reviewed_on <= today` and not before a date-valued `valid_as_of`; draft mode needs `reviewed_by` starting `draft:`, `recommendation_ref`, no `reviewed_on`. Any `draft:` reviewer in live mode is rejected regardless of interventions. `review_status` is `unreviewed` for seed and `reviewed` otherwise. Add provenance keys to the payload. Create `drafts/.gitkeep`.

- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect all green.

- [ ] **Step 5: Commit** `feat: review-state validation for curated guidelines`.

### Task 2: `oncobuddy-guidelines` CLI

**Files:**
- Create: `src/oncobuddy/curated/cli.py`
- Modify: `pyproject.toml` (add `oncobuddy-guidelines = "oncobuddy.curated.cli:main"` to `[project.scripts]`)
- Test: `tests/curated/test_cli.py`

**Interfaces:**
- Consumes: `load_guidelines`, `default_directory`, `drafts_directory`, `GuidelineFile` from Task 1.
- Produces: `def main(argv: list[str] | None = None) -> int` returning the exit code. Subcommands, all accepting `--live-dir DIR` and `--drafts-dir DIR` (defaults packaged) so tests use `tmp_path`:
  - `check`: validates live dir and drafts dir; prints `live: N, drafts: M`; exit 1 and a one-line error on any `ValueError`.
  - `review ID`: finds the draft; prints source URL, `valid_as_of`, then per intervention a line `<inn> | line=<..> | stages=<..> | biomarkers=<..> | ref=<..>` and the three verification prompts from the spec; exit 1 if not found.
  - `promote ID --reviewer NAME --on YYYY-MM-DD`: all of `--reviewer` and `--on` required (argparse). Refuses (exit 1, both dirs unchanged) when the id is not a draft, a live file with that id exists, the name is empty, or the promoted result fails live validation. Otherwise rewrites only the `reviewed_by:` line and inserts `reviewed_on: "<date>"` after it by text edit (comments preserved), validates in a temp location first, then moves the file into the live dir.

- [ ] **Step 1: Write failing tests** with `main([...])` and `capsys`: `check` ok and error exit codes; `review` output contains the URL and the `ref=` pointer and exits 0, unknown id exits 1; `promote` success (file moved, live load returns a `reviewed` record whose `reviewed_by` equals the given name, draft gone, other lines and comments byte-identical apart from the two lines); promote refusals each leave both dirs unchanged: existing live id, id not found, reviewer `""`, future `--on`, draft with `line_of_therapy: any`; missing `--reviewer` exits non-zero via `SystemExit`; no output contains file content from a bad file.

- [ ] **Step 2: Run** `uv run pytest tests/curated/test_cli.py -q`; expect import failure.

- [ ] **Step 3: Implement** `cli.py` per the interface (argparse; no network; errors are one line and never echo YAML content). Register the script in `pyproject.toml`.

- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.

- [ ] **Step 5: Commit** `feat: oncobuddy-guidelines check/review/promote CLI`.

### Task 3: Packaging exclusion, CI, integration, docs

**Files:**
- Modify: `pyproject.toml` (`[tool.hatch.build.targets.wheel]` add `exclude = ["src/oncobuddy/curated/drafts"]`; sdist `exclude = ["src/oncobuddy/curated/drafts"]`), `.github/workflows/ci.yml` (add step `uv run oncobuddy-guidelines check`), `CONTRIBUTING.md`, `docs/sources/guidelines.md`
- Test: `tests/curated/test_packaging.py`, `tests/core/test_options_reviewed.py`

**Interfaces:**
- Consumes: loader and CLI from Tasks 1-2; existing `treatment_options` and test helpers for building a Reconciler with a curated guideline connector (read `tests/core/test_options.py` for the pattern).
- Produces: documented workflow; guarantees from tests.

- [ ] **Step 1: Write failing tests.**
  - `test_packaging.py::test_wheel_excludes_drafts`: build a wheel into `tmp_path` with `uv build --wheel --out-dir <tmp>` (skip with `pytest.skip` if `uv` not on PATH), open with `zipfile`, assert no name contains `curated/drafts/` and that `oncobuddy/curated/guidelines/` entries are present. Same for sdist (`--sdist`, tarfile).
  - `test_options_reviewed.py::test_reviewed_entry_yields_named_option`: reviewed fixture dir with one entry (C50, stage II, trastuzumab, first line) drives `treatment_options` for a matching profile; assert one option with `intervention == "trastuzumab"`, guideline id matches, caveats include `"guideline content is link-only; not evaluated"` and `"cited edition; later versions may exist"` and not `"guideline entry unreviewed (seed)"`.
  - `test_draft_fixture_yields_no_option`: a draft-only directory loaded via the default (non-draft) loader raises `ValueError` (drafts rejected in live mode), and the packaged server guidelines contain no draft.

- [ ] **Step 2: Run** the two test files; expect failures.

- [ ] **Step 3: Implement** the pyproject excludes, CI step, and docs: CONTRIBUTING gains the review-state table from the spec, the `check`/`review`/`promote` workflow, the `recommendation_ref` rule and the statement that drafts are never served; `docs/sources/guidelines.md` gains a "Review states" section. README one-line mention of `oncobuddy-guidelines`.

- [ ] **Step 4: Run** `uv run pytest -q`, `uv run ruff check .`, `uv run oncobuddy-guidelines check`; expect green.

- [ ] **Step 5: Commit** `feat: exclude drafts from builds; docs and CI for review workflow`.

### Task 4: Candidate draft entries (independent sonnet drafter)

**Files:**
- Create: `src/oncobuddy/curated/drafts/<id>.yaml` (one or two), `src/oncobuddy/curated/drafts/README.md` (excluded from builds with the folder; states drafts are model-derived, clinically unverified, and become served only via `promote`)
- Modify: `docs/sources/guidelines.md` (list the candidates as drafts)
- Test: `tests/curated/test_seed_files.py` (extend: every file in `drafts/` validates in draft mode and uses an `ALLOWED_HOSTS` URL)

This task is performed by the controller dispatching ONE independent `general-purpose` subagent with `model: "sonnet"` (never fable), not by editing code directly.

- [ ] **Step 1: Dispatch the drafter** with this brief: starting from the four existing seed guidelines in `src/oncobuddy/curated/guidelines/`, pick the one or two with the best open sources (abstract, PubMed record, DOI landing page); draft entries in `drafts/` using the same file as the seed plus `interventions`, `line_of_therapy` (not `any`), `recommendation_ref`, `reviewed_by: draft:sonnet-agent`, no `reviewed_on`, `licence: restricted-link-only`, empty `summary`. Hard rules: use only open pages (no paywall circumvention); copy no text; list only interventions the source itself states for the stated setting; if unsure about a drug, stage or biomarker, omit it and say so. The report (to a given scratchpad path) lists, per intervention, the source URL, what in the source supports it, and anything unconfirmed.
- [ ] **Step 2: Controller verifies** structure only: `uv run oncobuddy-guidelines check`, URL hosts allowed, no long verbatim text, no `reviewed_on`. Clinical correctness is explicitly left to the user's review; do not mark anything reviewed.
- [ ] **Step 3: Extend the test** to validate drafts in draft mode; run `uv run pytest -q`, `uv run ruff check .`.
- [ ] **Step 4: Update docs/sources/guidelines.md and drafts/README.md**, then commit `feat: draft candidate guideline entries (unreviewed, not served)`.

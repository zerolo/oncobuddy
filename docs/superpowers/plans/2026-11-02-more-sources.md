# More Guideline Sources and Reference Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DGS (Portugal) to the normal results and NICE, NCCN and ASCO as a labelled `reference_elsewhere` group that never mixes into region-eligible data.

**Architecture:** `US` and `UK` join the region tree. A guideline YAML flag `reference_only` makes the reconciler route ineligible guideline records into `Section.reference_elsewhere` (guidelines section only). Tools and `treatment_options` surface the group without using it for matching. The DOI verifier generalises to a registry verifier. Four data tasks build the registry entries with an agent in the maintainer's Chrome.

**Tech Stack:** Python 3.11+, pydantic v2, pyyaml, httpx (script only), pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-more-sources-design.md` (builds on the source-discovery and browser-handoff specs).

## Global Constraints

- Worktree `/home/ze/oncoBuddy/.claude/worktrees/v1`, branch `v2-infarmed-pointers`. `export PATH="$HOME/.local/bin:$PATH"`; tests `uv run pytest -q`; lint `uv run ruff check .`. Bash: plain separate commands (no compound `cd`+git).
- Region safety stays the top priority: the ONLY records that may leave their region rules are guideline records flagged `reference_only`, and they go ONLY into `Section.reference_elsewhere` of the `guidelines` section. Medicines, trials, literature, coding and unflagged guidelines of ineligible regions are dropped exactly as today.
- Registry entries make no clinical claim: `reviewed_by: unreviewed-seed`, `interventions: []`, `licence: restricted-link-only`, `summary: ""`, `required_biomarkers: {}`, `line_of_therapy: any`, shared browser `access` block, quoted `last_verified`; no drug names, no recommendations, no copied text.
- Server never contacts these sources; tests never use the network. Profile-supplied values outside the closed enums and the canonical vocabulary never reach any output, log or cache.
- Agents in the browser never bypass a block, permission prompt or login; they stop and report.
- Error text names fields only. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never use the "fable" model for subagents.

## Review Focus

- A US/UK reference guideline leaking into `records`, `no_data`, a "showing EU-level" note, `treatment_options` options/exclusions/caveats, or any section other than `guidelines`.
- `reference_only` accepted on a region that is already eligible (EU, PT, ES, global).
- `treatment_options` early-return path (no in-scope guideline) dropping or inventing the reference group.
- A source failure presented as an empty reference group instead of `source_unavailable`.
- Registry entries per issuer with the wrong region/flag/id prefix/access reason, a drug name in a title-derived field, or a host outside the allowlist.

---

### Task 1: Regions, `reference_only` flag, allowed hosts

**Files:**
- Modify: `src/oncobuddy/regions.yaml` (add `US: global`, `UK: global`), `src/oncobuddy/curated/loader.py` (field `reference_only: bool = False` on `GuidelineFile`; payload key `reference_only` only when true; load-time check using the region tree), `src/oncobuddy/core/access.py` (`ALLOWED_HOSTS` gains `www.nccn.org`, `www.asco.org`, `ascopubs.org`, `www.nice.org.uk`, `normas.dgs.min-saude.pt`, `www.dgs.pt`)
- Test: `tests/core/test_regions.py` (extend), `tests/curated/test_reference_only.py`

**Interfaces:**
- Produces: payload key `reference_only: True` on flagged guideline records; chains `US -> [US, global]`, `UK -> [UK, global]`.

- [ ] **Step 1: Write failing tests**: `RegionTree.load()` chains for `US` and `UK` as above and PT/EU/ES unchanged; a guideline YAML (copy of a seed with access) with `region: US` and `reference_only: true` loads and its payload has `reference_only is True`; the same with `region` EU, PT, ES and global is rejected with `reference_only` in the message; an entry without the flag has no `reference_only` key; `reference_only: true` with an unknown region is rejected by the existing region check; `ALLOWED_HOSTS` contains the six new hosts.
- [ ] **Step 2: Run** `uv run pytest tests/curated/test_reference_only.py tests/core/test_regions.py -q`; expect failures.
- [ ] **Step 3: Implement**: the flag, the check (reject when `"EU"` is in the region's scope chain or the region is `global`), the payload key, regions and hosts.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green (existing region-safety tests unchanged).
- [ ] **Step 5: Commit** `feat: US and UK regions and reference_only guideline flag`.

### Task 2: Reconciler `reference_elsewhere`

**Files:**
- Modify: `src/oncobuddy/core/reconciler.py` (`Section.reference_elsewhere: list[Record] = Field(default_factory=list)`; in `_build_section`, for the connector section `guidelines` only, partition ineligible records into reference group when `kind == "guideline"` and `payload.get("reference_only") is True`; order the group by region, then `len(entities.conditions)`, then original order; add the section note; everything else unchanged)
- Test: `tests/core/test_reconciler.py` (extend)

**Interfaces:**
- Consumes: payload flag from Task 1.
- Produces: `Section.reference_elsewhere`; note text built by `reference_note(count: int, regions: list[str], query_region: str) -> str` returning `"{count} reference guideline(s) issued outside {query_region}'s scope ({regions joined by ', '}) are listed separately; national approval, reimbursement and practice may differ"`, exported from `reconciler.py`.

- [ ] **Step 1: Write failing tests** (fake connectors, mixed-region fixtures): for query PT, a US reference guideline and a UK reference guideline land in `reference_elsewhere` ordered `UK` before `US`, not in `records`; `no_data` is `not_found` when only reference records exist; no "showing EU-level" note is produced by them; an unflagged US guideline, a flagged US record of kind `medicine`, and a flagged US guideline in section `authorisation` are all dropped; for query US the US reference guideline is eligible and stays in `records` with an empty reference group; a failing guidelines connector yields `source_unavailable` and an empty group; the section note text equals `reference_note(...)` exactly and appears once; region-safety test file still green.
- [ ] **Step 2: Run** `uv run pytest tests/core/test_reconciler.py -q`; expect failures.
- [ ] **Step 3: Implement** the partition and note in `_build_section` (pointer handling untouched).
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: labelled reference_elsewhere group in the guidelines section`.

### Task 3: Tools, options, protocol

**Files:**
- Modify: `src/oncobuddy/core/options.py` (`OptionsResult.reference_elsewhere: list[Record] = []`; set it from the guidelines section on every path including the early return; `read_plan` also set when any reference record has `access.method == "browser"`; reference records never enter matching), `src/oncobuddy/server.py` (remember `reference_elsewhere` records for `get_record` in `_section_tool`, `regional_status`, `treatment_options`), `src/oncobuddy/core/access.py` (rule 4 of `AGENT_PROTOCOL` gains: `If a record comes from reference_elsewhere, also state the region it was issued for.`), `plugin/skills/oncobuddy/SKILL.md` (regenerate the protocol block from the constant; add a short "Reference group" paragraph)
- Test: `tests/core/test_options_reference.py`, `tests/test_server.py` (extend), `tests/test_skill_sync.py` (existing sync test must stay green)

**Interfaces:**
- Consumes: `Section.reference_elsewhere` from Task 2.
- Produces: `OptionsResult.reference_elsewhere`; JSON key `reference_elsewhere` inside the `guidelines` section (via `Section.model_dump`).

- [ ] **Step 1: Write failing tests**: `treatment_options` over a fixture with an in-scope EU guideline plus a US reference guideline returns identical `options`, `excluded`, `guidelines_in_scope` and caveats as without the US record, and lists the US record in `reference_elsewhere`; with ONLY a reference record the result has no options, `no_data.reason == "not_found"`, still lists the group, and `read_plan` is non-empty when it has browser access; `guidelines_for("C50.9","PT")` over the real packaged registry returns `reference_elsewhere == []` today (no reference entries yet); server tools remember reference records so `get_record` returns one with the query's region and chain; the existing privacy test (unknown biomarker key and prior drug never in any output) stays green; protocol rule 4 contains `reference_elsewhere` and the skill sync test passes.
- [ ] **Step 2: Run** the new tests; expect failures.
- [ ] **Step 3: Implement** the wiring and regenerate the skill's protocol block from `AGENT_PROTOCOL` (do not hand-edit it).
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: reference group in tools and treatment_options; protocol region label`.

### Task 4: Registry verifier

**Files:**
- Create: `scripts/verify_registry.py` (logic moved from `scripts/verify_dois.py`); Modify: `scripts/verify_dois.py` (becomes a three-line alias that calls `verify_registry.main`), `tests/test_verify_dois.py` (extend/rename imports as needed)
- Test: `tests/test_verify_registry.py`

**Interfaces:**
- Produces: `ALLOWED_JOURNALS: dict[str, set[str]] = {"ESMO": {"Annals of Oncology", "ESMO Open"}, "ASCO": {"Journal of Clinical Oncology"}}`; `verify(directory, lookup, crossref, sleep)` as now but the journal set comes from the record's `source` (records whose source is not in `ALLOWED_JOURNALS` and whose url is not a doi.org link are NOT DOI-checked); `main` prints problems and a final line `ok: <N> entries (<M> not machine-checkable)` where M counts entries without a DOI whose url is https on an `ALLOWED_HOSTS` host; an entry without a DOI on a host outside `ALLOWED_HOSTS` is a problem `"<record id>: url host is not allowed"`.

- [ ] **Step 1: Write failing tests** (offline fakes): an ASCO record with Journal of Clinical Oncology passes and with Annals of Oncology is `unexpected journal`; an ESMO record with Journal of Clinical Oncology is `unexpected journal`; a NICE-style non-DOI record on `www.nice.org.uk` is counted as not machine-checkable and raises no problem; a non-DOI record on `evil.example` is a problem; final line format; `scripts/verify_dois.py` still runs `main` (alias); no network (httpx.get patched to raise).
- [ ] **Step 2: Run** `uv run pytest tests/test_verify_registry.py -q`; expect failures.
- [ ] **Step 3: Implement** by moving the existing verifier logic and generalising as above.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: registry verifier for DOI and non-DOI sources`.

### Task 5: DGS registry data (PT, normal results)

**Files:**
- Create: `src/oncobuddy/curated/guidelines/dgs-<norma>-<year>.yaml` per oncology norm; Test: `tests/curated/test_registry.py` (extend with per-issuer pins)

This task is performed by ONE independent `general-purpose` subagent (`model: "sonnet"`) using `mcp__claude-in-chrome__*`, following `docs/runbooks/refresh-esmo-index.md` adapted below. Requires the maintainer's Chrome with `dgs.pt` and `normas.dgs.min-saude.pt` allowed. If a permission is denied or a block appears, it stops and reports.

- [ ] **Step 1: Extend `test_registry.py` first**: for entries whose id starts `dgs-`: `source == "DGS"`, region `PT`, no `reference_only`, `access.reason == "no_api"`, url host in `{normas.dgs.min-saude.pt, www.dgs.pt}`, conditions equal a topic list or a declared subset (as for ESMO), no intervention, id equals file stem; count floor set to 80 percent of the final count.
- [ ] **Step 2: Dispatch the drafter**: from the DGS norms index (read index pages only), list the clinical norms and orientations about oncology (tumour-specific, screening excluded unless tumour-specific, general health norms excluded); record title exactly as shown, norm number and year, issue date (and last-update date if the index shows one), and the norm's page URL; skip and list anything whose topic is not in `esmo_topics.yaml` or whose date is not a full day. One YAML per norm in the registry format with `issuer: DGS`, `region: PT`, `published` = issue date, `valid_as_of` = last-update date if shown else issue date, `access.reason: no_api`, recipe from the shared block; no text copied; report files created, skips, doubts.
- [ ] **Step 3: Verify**: `uv run python scripts/verify_registry.py` (reports not machine-checkable count), `uv run pytest -q`, `uv run ruff check .`; spot-check five entries against the report.
- [ ] **Step 4: Commit** `feat: DGS oncology norms registry (PT)`.

### Task 6: NICE registry data (UK reference)

**Files:**
- Create: `src/oncobuddy/curated/guidelines/nice-<code>.yaml` per cancer clinical guideline; Test: `tests/curated/test_registry.py` (extend)

Performed by ONE `general-purpose` sonnet subagent with Chrome; requires `nice.org.uk` allowed.

- [ ] **Step 1: Extend the registry test first**: for `nice-` ids: `source == "NICE"`, region `UK`, `reference_only is True`, `access.reason == "no_api"`, host `www.nice.org.uk`, url path starts `/guidance/ng` or `/guidance/cg`, conditions per the ESMO rule, no intervention, id equals stem (`nice-ng101`).
- [ ] **Step 2: Dispatch the drafter**: from NICE's cancer guidance index (index pages only) list clinical guidelines (codes NG or CG) only; exclude technology appraisals (TA), diagnostics, quality standards and interventional procedures; record exact title, code, published date and last-updated date as shown; skip and list anything not in the topic table or without full dates. YAML with `issuer: NICE`, `region: UK`, `reference_only: true`, `published`, `valid_as_of` = last-updated date if shown else published; report as before.
- [ ] **Step 3: Verify** with the verifier, the suite and ruff; spot-check five entries.
- [ ] **Step 4: Commit** `feat: NICE cancer guidelines registry (UK reference)`.

### Task 7: NCCN registry data (US reference)

**Files:**
- Create: `src/oncobuddy/curated/guidelines/nccn-<slug>.yaml` per tumour-type guideline; Test: `tests/curated/test_registry.py` (extend)

Performed by ONE `general-purpose` sonnet subagent with Chrome; requires `nccn.org` allowed. The agent reads only the PUBLIC index page(s); it must never log in or open a login-gated guideline. If the public index does not list guideline titles, it stops and reports.

- [ ] **Step 1: Extend the registry test first**: for `nccn-` ids: `source == "NCCN"`, region `US`, `reference_only is True`, `access.reason == "login_required"`, host `www.nccn.org`, `valid_as_of == "unknown"`, `published == "unknown"`, the recipe contains the log-in stop step, conditions per the rule, no intervention.
- [ ] **Step 2: Dispatch the drafter**: from the public NCCN guideline index list the treatment guidelines by cancer type (exclude detection/prevention/supportive-care categories and patient guidelines); record the exact title and the guideline page URL as linked from the public index; `published` and `valid_as_of` are `"unknown"` (living documents); recipe = shared block plus the step `If the page asks you to log in, tell the user and stop; do not log in for them`; report as before.
- [ ] **Step 3: Verify** with the verifier, the suite and ruff; spot-check five entries.
- [ ] **Step 4: Commit** `feat: NCCN guidelines registry (US reference)`.

### Task 8: ASCO registry data (US reference)

**Files:**
- Create: `src/oncobuddy/curated/guidelines/asco-<slug>.yaml` per ASCO guideline; Test: `tests/curated/test_registry.py` (extend)

Performed by ONE `general-purpose` sonnet subagent with Chrome (titles from `asco.org` guideline index pages) and Bash (Crossref REST API for DOI and date, one request per second, header `User-Agent: oncoBuddy-registry/0.1`, no key or email); requires `asco.org` allowed.

- [ ] **Step 1: Extend the registry test first**: for `asco-` ids: `source == "ASCO"`, region `US`, `reference_only is True`, `access.reason == "bot_check"`, `doi.org` url, conditions per the rule, no intervention, a `last_verified` date.
- [ ] **Step 2: Dispatch the drafter**: list ASCO clinical practice guidelines for tumour types from the index (exclude rapid recommendation updates unless they are the current version of a listed guideline, supportive care, and anything not in the topic table); match each to a Crossref record (type `journal-article`, container `Journal of Clinical Oncology`, title equal after normalisation, multi-page, not an Editorial or correspondence); `published`/`valid_as_of` = Crossref `created` date accepted only within 7 days of the ASCO page date shown; skip and list unmatched.
- [ ] **Step 3: Verify**: `uv run python scripts/verify_registry.py` (network on the maintainer's machine), the suite, ruff; spot-check five DOI-title pairs.
- [ ] **Step 4: Commit** `feat: ASCO guidelines registry (US reference)`.

### Task 9: Docs, runbook, skill

**Files:**
- Modify: `README.md`, `docs/sources/guidelines.md` (sources table, counts, coverage gaps per source, reference group), `docs/runbooks/refresh-esmo-index.md` (rename to `docs/runbooks/refresh-registry.md` with a section per source: DGS, NICE, NCCN, ASCO as in Tasks 5-8), `plugin/skills/oncobuddy/SKILL.md` (reference group paragraph already added in Task 3; add the source list)
- Test: `tests/test_skill_sync.py` (extend: the skill names `reference_elsewhere`)

- [ ] **Step 1: Write the failing test**: skill text contains `reference_elsewhere`.
- [ ] **Step 2: Run** `uv run pytest tests/test_skill_sync.py -q`; expect failure if missing.
- [ ] **Step 3: Write** the docs with exact counts taken from the data tasks' reports, and the queue (Crossref edition check, plugin packaging, NICE technology appraisals).
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `docs: reference group and new sources`.

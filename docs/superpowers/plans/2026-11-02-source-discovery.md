# Source Discovery and Read Plans Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the registry with every ESMO guideline (scope-only, browser access), order discovery results by specificity, and give `treatment_options` a deterministic `read_plan` built only from closed profile fields.

**Architecture:** Registry entries are the existing scope-only guideline YAMLs, with a new optional `last_verified` date and a topic table that fixes ICD-10 routing. Ordering gets a secondary key in the reconciler. A small `core/readplan.py` builds the plan from enums and canonical biomarkers; `OptionsResult.read_plan` carries it. A verifier script and a runbook support the agent-assisted refresh; the refresh itself runs in the maintainer's Chrome.

**Tech Stack:** Python 3.11+, pydantic v2, pyyaml, httpx (script only), pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-source-discovery-design.md`.

## Global Constraints

- Worktree `/home/ze/oncoBuddy/.claude/worktrees/v1`, branch `v2-infarmed-pointers`. `export PATH="$HOME/.local/bin:$PATH"`; tests `uv run pytest -q`; lint `uv run ruff check .`. Bash: plain separate commands (no compound `cd`+git).
- No registry entry names an intervention, a dose or a recommendation; `licence: restricted-link-only`, `summary: ""`, `reviewed_by: unreviewed-seed`, `access.method: browser`.
- No guideline text is copied anywhere; only titles, DOIs and dates are recorded.
- The server fetches only open APIs; it never contacts `browser` sources. Network is never used in tests.
- Nothing profile-supplied outside the closed enums and the canonical biomarker vocabulary appears in any output, log or cache (unknown keys, prior drug names and the ICD code never appear in a read plan).
- Error text names fields only. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never use the "fable" model for subagents.

## Review Focus

- A read plan built from a profile that has an unknown biomarker key or prior drug names must never contain them.
- A read plan longer than 4 lines or 200 characters per line, or missing the fixed closing line.
- A registry entry with an intervention, a category outside the topic table, a non-`browser` access block or a host outside the allowlist.
- Ordering change must not reorder non-guideline sections (medicines, trials) or change region precedence.
- A `last_verified` date in the future, malformed, or silently ignored; staleness shown as a caveat, never as a failure.

---

### Task 1: `last_verified` and staleness

**Files:**
- Modify: `src/oncobuddy/curated/loader.py` (`GuidelineFile.last_verified: str | None = None`, date-validated like `published`; payload gets `last_verified` when set and `verification_stale: true` when the date is more than 540 days before the load date)
- Modify: `src/oncobuddy/core/options.py` (`_guideline_caveats` adds `"registry entry last verified more than 540 days ago"` when `payload.get("verification_stale")`)
- Test: `tests/curated/test_last_verified.py`, `tests/core/test_options_access.py` (extend)

**Interfaces:**
- Produces: `STALE_DAYS = 540` in `loader.py`; payload keys `last_verified: str`, `verification_stale: bool`.

- [ ] **Step 1: Write failing tests**: a tmp YAML (copy of a seed with `access`) with `last_verified: "2026-10-01"` loads and the payload has `last_verified == "2026-10-01"` and `verification_stale is False`; `"2020-01-01"` gives `verification_stale is True`; a future date, `"01/11/2026"` and `"unknown"` are rejected with `last_verified` in the message; absent field gives no `last_verified` key; `treatment_options` over a stale fixture adds the caveat text above to `guidelines_in_scope` notes (`"<id>: registry entry last verified more than 540 days ago"`) and over a fresh one does not.
- [ ] **Step 2: Run** `uv run pytest tests/curated/test_last_verified.py -q`; expect failures.
- [ ] **Step 3: Implement** the field, validator (`YYYY-MM-DD`, not in the future), payload keys and caveat.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: last_verified date and staleness caveat for registry entries`.

### Task 2: Topic table

**Files:**
- Create: `src/oncobuddy/curated/esmo_topics.yaml`, `src/oncobuddy/curated/topics.py`
- Test: `tests/curated/test_topics.py`

**Interfaces:**
- Produces: `def load_topics() -> dict[str, list[str]]` (topic name to ICD-10 3-character categories; raises `ValueError` naming the problem, never file content) and `def known_categories() -> set[str]`.
- Table content (routing only; the maintainer reviews it once): breast `[C50]`; colorectal `[C18, C19, C20]`; anal `[C21]`; lung `[C34]`; mesothelioma `[C45]`; thymic `[C37]`; oesophageal `[C15]`; gastric `[C16]`; small bowel `[C17]`; pancreatic `[C25]`; hepatocellular `[C22]`; biliary `[C23, C24]`; neuroendocrine `[C7A]` is NOT valid ICD-10 so list neuroendocrine under the organ topics instead and omit it; melanoma `[C43]`; skin non-melanoma `[C44]`; prostate `[C61]`; bladder `[C67]`; kidney `[C64]`; testicular `[C62]`; penile `[C60]`; ovarian `[C56]`; cervical `[C53]`; endometrial `[C54]`; vulvar and vaginal `[C51, C52]`; head and neck `[C00, C01, C02, C03, C04, C05, C06, C09, C10, C11, C12, C13, C14, C30, C31, C32]`; thyroid `[C73]`; sarcoma `[C49, C40, C41]`; central nervous system `[C70, C71, C72]`; lymphoma `[C81, C82, C83, C84, C85]`; myeloma `[C90]`; leukaemia `[C91, C92, C93, C94, C95]`; cancer of unknown primary `[C80]`.

- [ ] **Step 1: Write failing tests**: `load_topics()` returns a non-empty dict; every category matches `^C[0-9]{2}$`; no topic is empty; no category appears under two topics unless listed in an explicit `SHARED` set in the test (start with none); `known_categories()` equals the union; `topics.py` raises `ValueError` for a malformed temp table (non-list value, bad category) without echoing the content.
- [ ] **Step 2: Run** `uv run pytest tests/curated/test_topics.py -q`; expect import failure.
- [ ] **Step 3: Implement** `topics.py` (strict YAML via `parse_yaml_text` from the loader) and the table with a header comment saying it holds routing only.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: ESMO topic table for ICD-10 routing`.

### Task 3: Specificity ordering

**Files:**
- Modify: `src/oncobuddy/core/reconciler.py` (`_build_section`: the `real.sort` key becomes `(position[r.region], specificity(r))` where `specificity(r)` is `len(r.entities.conditions)` for `kind == "guideline"` and `0` otherwise; pointers unchanged)
- Test: `tests/core/test_reconciler.py` (extend)

**Interfaces:**
- Produces: guideline records inside a region ordered by fewer condition categories first; stable otherwise.

- [ ] **Step 1: Write failing tests**: a fake guidelines connector returning, in this order, a record with conditions `[C18, C19, C20]` and one with `[C18]`, both region EU, yields the `[C18]` record first; two guidelines with equal specificity keep connector order; a PT record still sorts before an EU record regardless of specificity; medicine records (kind `medicine`) keep their original order.
- [ ] **Step 2: Run** `uv run pytest tests/core/test_reconciler.py -q`; expect the first test to fail.
- [ ] **Step 3: Implement** the key.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: order guideline records by specificity within a region`.

### Task 4: Read plan

**Files:**
- Create: `src/oncobuddy/core/readplan.py`
- Modify: `src/oncobuddy/core/options.py` (`OptionsResult.read_plan: list[str] = []`, set to `build_read_plan(profile)` when at least one record in `guidelines_in_scope` or an option's guideline has `payload["access"]["method"] == "browser"`)
- Test: `tests/core/test_readplan.py`, `tests/core/test_options_access.py` (extend)

**Interfaces:**
- Consumes: `ClinicalProfile`, `is_canonical` from `core/biomarkers.py`.
- Produces: `READ_PLAN_CLOSING = "This plan is a reading instruction, not advice"`, `MAX_LINES = 4`, `MAX_CHARS = 200`, and `def build_read_plan(profile: ClinicalProfile) -> list[str]` returning exactly: (1) `"Find the recommendations for <descriptor>"` where `<descriptor>` joins, in this order and only when known: `stage <I|II|III|IV>`, canonical biomarkers with status positive or negative as `<KEY> positive`/`<KEY> negative`, `first-line treatment` when `prior_lines == []`, `later-line treatment` when `prior_lines` is non-empty, `curative intent`/`palliative intent`; when nothing is known the line is `"Find the recommendations for the condition in the entry"`; (2) `"Report each named drug with its printed evidence level and the setting it applies to"`; (3) `READ_PLAN_CLOSING`. Biomarker keys listed alphabetically; unknown statuses and non-canonical keys omitted; the descriptor is cut to fit `MAX_CHARS` by dropping trailing biomarkers.

- [ ] **Step 1: Write failing tests**: stage II + `{HER2: positive, ER: negative}` + `prior_lines=[]` gives line 1 `"Find the recommendations for stage II, ER negative, HER2 positive, first-line treatment"`; unknown stage and `{HER2: unknown}` are omitted; an unknown key `ZZQXKEY9` and prior drug `zzqxdrug` never appear in any line (also over `repr`/`json` of the result); empty profile gives the fallback line 1; a profile with 30 biomarkers keeps every line within `MAX_CHARS` and the list within `MAX_LINES`; the last line is `READ_PLAN_CLOSING`; options result carries the plan for in-scope seeds (they have browser access) and `[]` for a fixture guideline without `access`; the existing privacy test still passes.
- [ ] **Step 2: Run** `uv run pytest tests/core/test_readplan.py -q`; expect import failure.
- [ ] **Step 3: Implement** `readplan.py` and the `OptionsResult` wiring (compute once per call).
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: deterministic read plan in treatment_options`.

### Task 5: DOI verifier and runbook

**Files:**
- Create: `scripts/verify_dois.py`, `docs/runbooks/refresh-esmo-index.md`
- Test: `tests/test_verify_dois.py`

**Interfaces:**
- Produces: in `scripts/verify_dois.py`: `def doi_of(url: str) -> str | None` (the DOI of an `https://doi.org/<doi>` URL, else `None`), `def verify(directory: Path, lookup: Callable[[str], int]) -> list[str]` returning problem strings (`"<file>: DOI not found (status N)"`, `"<file>: url is not a doi.org link"`) and `def main(argv: list[str] | None = None) -> int` that uses `httpx.get(f"https://doi.org/api/handles/{doi}")` as `lookup` (status code), prints problems and returns 1 when any, else prints `ok: N entries` and returns 0. It reads guideline YAML only via `load_guidelines` (no new parser) and changes no file.

- [ ] **Step 1: Write failing tests** (load the script with `importlib.util.spec_from_file_location`): `doi_of` cases; `verify` with a fake `lookup` returning 200 for known DOIs and 404 for one gives exactly one problem naming that file; a non-doi URL gives the url problem; `main` with `lookup` patched returns 0 or 1 accordingly; no network is used (patch `httpx.get` to raise).
- [ ] **Step 2: Run** `uv run pytest tests/test_verify_dois.py -q`; expect failure.
- [ ] **Step 3: Implement** the script and the runbook (steps from spec section 4, consent and `esmo.org` allow step, the rule that only titles, DOIs and dates are recorded, the topic-table rule, then `uv run python scripts/verify_dois.py` and `uv run pytest -q`).
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: DOI verifier script and ESMO refresh runbook`.

### Task 6: ESMO registry data (agent in the maintainer's Chrome)

**Files:**
- Create/Modify: `src/oncobuddy/curated/guidelines/esmo-*.yaml` (one per guideline; the four existing keep their ids and gain `last_verified`)
- Test: `tests/curated/test_registry.py`

This task is performed by ONE independent `general-purpose` subagent (`model: "sonnet"`; never fable) that uses the `mcp__claude-in-chrome__*` tools, following `docs/runbooks/refresh-esmo-index.md`. It requires the maintainer to have allowed `esmo.org` (and `doi.org`) in the extension; if a permission is denied the task stops and reports.

- [ ] **Step 1: Write the failing registry test** first: all guideline files load; ids unique; every entry with `issuer == "ESMO"` has `access.method == "browser"`, empty interventions, `licence == "restricted-link-only"`, a `last_verified` date, conditions all in `known_categories()`, hosts allowed (`doi.org` URL); at least 20 ESMO entries (set after the refresh, record the actual count in the test with a lower bound of 80 percent of it).
- [ ] **Step 2: Dispatch the drafter** with the runbook, the topic table and the seed format; it records title, topic, DOI and online date only, creates `esmo-<slug>.yaml` for every ESMO clinical practice guideline whose topic is in the table (skipping others and listing them), sets `stages` only when the title states a stage scope, copies no guideline text, and writes a report listing every file created, every topic skipped and every doubt.
- [ ] **Step 3: Verify**: run `uv run python scripts/verify_dois.py` (needs network on the maintainer's machine), `uv run pytest -q`, `uv run ruff check .`; spot-check five entries against the report.
- [ ] **Step 4: Commit** `feat: ESMO guideline registry (scope-only, browser access)`.

### Task 7: Docs and skill

**Files:**
- Modify: `plugin/skills/oncobuddy/SKILL.md` (read plan section: what it is, that it is an instruction not advice, use it with the protocol), `README.md`, `docs/sources/guidelines.md` (registry description, `last_verified`, refresh runbook link, queue), `docs/superpowers/specs/2026-11-02-source-discovery-design.md` (section 6: a single top-level `read_plan` on the options result applies to every browser guideline listed, decided at implementation because the plan text is identical for all)
- Test: `tests/test_skill_sync.py` (extend: the skill names `read_plan`)

- [ ] **Step 1: Write the failing test**: the skill text contains `read_plan` and the closing line from `READ_PLAN_CLOSING`.
- [ ] **Step 2: Run** `uv run pytest tests/test_skill_sync.py -q`; expect failure.
- [ ] **Step 3: Write** the docs.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `docs: read plan and registry documentation`.

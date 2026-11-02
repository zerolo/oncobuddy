# Source Access Registry and Browser Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let records declare how their source must be read (`api`, `link`, `browser`), carry an agent protocol on every successful response, and ship a skill that teaches agents to read `browser` sources in the user's own browser with consent.

**Architecture:** A small `Access` pydantic model (`core/access.py`) validates an optional `access` block on guideline YAML and on the INFARMED pointer payloads; records carry it unchanged in their payload. A constant `AGENT_PROTOCOL` is added to every successful tool envelope and to `OptionsResult`. A skill file mirrors the constant, with a sync test. The server never contacts a `browser` source.

**Tech Stack:** Python 3.11+, pydantic v2, pyyaml, pytest, existing MCP server.

**Spec:** `docs/superpowers/specs/2026-11-01-browser-handoff-design.md` (builds on the v1 design and the reviewed-guidelines spec).

## Global Constraints

- Worktree `/home/ze/oncoBuddy/.claude/worktrees/v1`, branch `v2-infarmed-pointers`. `export PATH="$HOME/.local/bin:$PATH"`; tests `uv run pytest -q`; lint `uv run ruff check .`. Bash: plain separate commands (no compound `cd`+git).
- The server never contacts, fetches, caches or stores content from any `browser` or `link` source; no headless or simulated browser; no write-back channel from the agent.
- No tool input schema gains a free-text field. Profile values are never logged or stored.
- Error text names fields only and never echoes file content.
- A recipe is instructions only: at most 6 steps, each at most 160 chars, no dose pattern. `access.method: browser` is allowed only on records whose licence is `restricted-link-only` or a non-open tag.
- INFARMED terms forbid reproduction or distribution: the protocol's "short factual statements only" rule applies; the server asserts no INFARMED status.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never use the "fable" model for subagents.

## Review Focus

- A `browser` access block with no recipe, a recipe of 7 steps or one containing a dose (e.g. "10 mg") must be rejected.
- A successful response from ANY tool (`get_record`, `lookup_code`, `find_trials`, `treatment_options`, `regional_status`, `guidelines_for`, `get_medicine`, `search_literature`) missing `agent_protocol`.
- INFARMED pointers must no longer read as "no status exists": the note must say the status has to be read in the user's browser.
- No network call to a `browser` source (guard test) and no new free-text input in any tool schema.
- The skill text and the server constant drifting apart.

---

### Task 1: `Access` model and protocol constant

**Files:**
- Create: `src/oncobuddy/core/access.py`
- Test: `tests/core/test_access.py`

**Interfaces:**
- Produces:
  - `class Access(BaseModel)` (`extra="forbid"`): `method: Literal["api","link","browser"]`; `reason: Literal["bot_check","login_required","no_api","terms_restrict_automation"] | None = None`; `read_recipe: list[str] = []`; `entry_url: str | None = None`.
  - Validation: `entry_url` must start with `https://`; each recipe step 1-160 chars and not matching `\b\d+([.,]\d+)?\s?(mg|mcg|µg|ug|g|ml|iu|gy|mg/m2|mg/kg)\b` (case-insensitive); at most 6 steps; `method == "browser"` requires a non-empty recipe and a `reason`; `method != "browser"` requires an empty recipe.
  - `def check_licence_for_access(access: Access, licence: str) -> None`: raises `ValueError` if `access.method == "browser"` and `licence` is an open SPDX-style tag (rule: allowed only for `restricted-link-only` or `LicenseRef-*`/`proprietary`/`unknown`-style non-open tags; implement as: allowed iff `licence == RESTRICTED_LICENCE` or licence starts with `LicenseRef-`).
  - `AGENT_PROTOCOL: str` (module constant), six numbered rules exactly as spec section 5, each on its own line, plain ASCII.
  - `PROVENANCE_VOCAB: dict[str, str]` with keys `api`, `reviewed`, `agent-read` and one-line meanings per spec section 4.

- [ ] **Step 1: Write failing tests** in `tests/core/test_access.py`: valid browser block loads; browser without recipe rejected; link with a recipe rejected; 7 steps rejected; step of 161 chars rejected; recipe with "10 mg" and "5 mg/kg" rejected but "read section 3" accepted; non-https `entry_url` rejected; unknown key rejected; unknown reason rejected; `check_licence_for_access` accepts `restricted-link-only` and rejects `CC-BY-4.0` for browser and accepts `CC-BY-4.0` for `api`; `AGENT_PROTOCOL` has exactly six numbered lines, contains the words "permission", "patient", "agent-read", and "Cite"; `PROVENANCE_VOCAB` keys are the three names. Error-message test: a rejected recipe's `str(error)` never contains the secret marker `SECRET-STEP` placed in the step text.
- [ ] **Step 2: Run** `uv run pytest tests/core/test_access.py -q`; expect import failure.
- [ ] **Step 3: Implement** `core/access.py` per the interface. Validators raise fixed messages that name the field only.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: access model and agent protocol constant`.

### Task 2: Guideline loader support and ESMO seeds

**Files:**
- Modify: `src/oncobuddy/curated/loader.py` (add optional `access: Access | None = None` to `GuidelineFile`; call `check_licence_for_access` in `load_guidelines`; payload gains `access` as a dict when set)
- Modify: the four files `src/oncobuddy/curated/guidelines/esmo-*.yaml` (add an `access` block)
- Modify: `src/oncobuddy/curated/drafts/*.yaml` (add the same block to both drafts so they stay promotable)
- Test: `tests/curated/test_access_loader.py`, `tests/curated/test_seed_files.py` (assert every seed has `access.method == "browser"`)

**Interfaces:**
- Consumes: `Access`, `check_licence_for_access` from Task 1.
- Produces: guideline records whose `payload["access"]` is a plain dict `{"method","reason","read_recipe","entry_url"}` (entry_url defaulting to the record `source_url` when absent).

- [ ] **Step 1: Write failing tests**: a tmp YAML with a valid access block loads and its payload carries the dict with `entry_url` filled from `url`; browser on `licence: CC-BY-4.0` is rejected at load with field name only; an `access` with a dose in the recipe is rejected; seeds all load with `method == "browser"` and `reason == "bot_check"` and a 3-6 step recipe; recipes contain no drug names from a small deny list (`trastuzumab`, `pertuzumab`, `pembrolizumab`, `cetuximab`, `panitumumab`, `bevacizumab`, `osimertinib`, `alectinib`); drafts still pass `uv run oncobuddy-guidelines check`.
- [ ] **Step 2: Run** the new tests; expect failures.
- [ ] **Step 3: Implement** the loader changes. Recipes for the seeds are written in your own words and say only where to look and what to report, e.g.: open the DOI link in the user's browser; locate the Recommendations list and the treatment algorithm figure for the patient's setting; report each named drug with its printed evidence level; do not paraphrase beyond that. Draft files get the same block (no drug names in it).
- [ ] **Step 4: Run** `uv run pytest -q`, `uv run ruff check .`, `uv run oncobuddy-guidelines check`; expect green.
- [ ] **Step 5: Commit** `feat: access registry on guideline entries; ESMO seeds use browser handoff`.

### Task 3: INFARMED pointers use the handoff

**Files:**
- Modify: `src/oncobuddy/connectors/infarmed.py`, `src/oncobuddy/core/options.py` (POINTER_CAVEAT text if it conflicts), `src/oncobuddy/core/reconciler.py` (POINTER_ONLY_NOTE text)
- Test: `tests/connectors/test_infarmed.py` and any test asserting the old wording (update literally, preserving intent)

**Interfaces:**
- Consumes: `Access` from Task 1.
- Produces: every INFARMED pointer record payload gains `access` = `{"method": "browser", "reason": "no_api", "read_recipe": [...], "entry_url": <Infomed or SIATS url>}`; `status` text becomes `"not retrieved - read in the user's browser"`; `note` text says the Portuguese national status must be read in the user's browser at the linked source, searching by active substance; `retrieval` text says `"none by oncoBuddy - read by the user's agent in the user's browser; INFARMED terms forbid reproduction"`.

- [ ] **Step 1: Write failing tests**: each pointer (authorisation and reimbursement) carries a valid `Access` with method `browser`, reason `no_api`, a recipe of 3-5 steps, no dose pattern, and `entry_url` equal to the record's `source_url`; the status and note texts contain "browser"; the connector still makes no network call (reuse the existing guard test pattern); authorisation recipe says to search by active substance and report authorisation and marketing status with their dates as printed; reimbursement recipe says to report the evaluation outcome with its date as printed; neither recipe asks for lists or tables.
- [ ] **Step 2: Run** `uv run pytest tests/connectors -q`; expect failures.
- [ ] **Step 3: Implement** the payload changes and update the reconciler/options wording constants where they restate the old "not retrieved" text; update existing tests that assert the old literals.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: INFARMED pointers declare browser handoff`.

### Task 4: `agent_protocol` on every successful response, browser notes

**Files:**
- Modify: `src/oncobuddy/server.py` (`_envelope` and the `regional_status`/`treatment_options` return paths add `agent_protocol`; the bare `{"error": ...}` payloads are unchanged), `src/oncobuddy/core/options.py` (`OptionsResult.agent_protocol: str = AGENT_PROTOCOL`; scope-only or intervention-less guideline records with `access.method == "browser"` add the note `"source requires browser reading: ask the user"`), `src/oncobuddy/core/reconciler.py` if `RegionalStatus` needs the field
- Test: `tests/test_server.py` (extend), `tests/core/test_options_access.py`

**Interfaces:**
- Consumes: `AGENT_PROTOCOL` from Task 1, access payloads from Tasks 2-3.
- Produces: JSON key `agent_protocol` (string, equal to `AGENT_PROTOCOL`) on every successful tool response; the constant `BROWSER_NOTE = "source requires browser reading: ask the user"` exported from `core/options.py`.

- [ ] **Step 1: Write failing tests**: for each of the eight tools a successful call (use the existing fake-reconciler fixtures in `tests/test_server.py`) returns `agent_protocol == AGENT_PROTOCOL`; error payloads do not require it; `treatment_options` for a profile matching an ESMO seed returns the guideline in `guidelines_in_scope` with `payload["access"]["method"] == "browser"` and the note `BROWSER_NOTE` present exactly once per guideline; a reviewed fixture entry that names interventions still yields options and no browser note for that guideline; no tool input schema gains a property of type string beyond the existing ones (compare the property-name sets with the pre-change sets stored in the test); a network guard (monkeypatch `httpx.AsyncClient.send` to raise) shows the guidelines tools never call out to a browser source.
- [ ] **Step 2: Run** the new tests; expect failures.
- [ ] **Step 3: Implement** the envelope and options changes.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: agent protocol on every response; browser notes in treatment options`.

### Task 5: Skill, docs, spec wording

**Files:**
- Create: `plugin/skills/oncobuddy/SKILL.md`
- Modify: `README.md`, `docs/sources/infarmed.md` (record the reported read permission, scope read-not-reproduce, date 2026-11-01), `docs/sources/guidelines.md` (ESMO access note), `docs/superpowers/specs/2026-11-01-browser-handoff-design.md` (section 4: "every successful tool response")
- Modify: `pyproject.toml` sdist include list to add `plugin`
- Test: `tests/test_skill_sync.py`

**Interfaces:**
- Consumes: `AGENT_PROTOCOL`, `PROVENANCE_VOCAB` from Task 1.
- Produces: a SKILL.md with front matter (`name: oncobuddy`, `description:` one sentence of when to use it) containing the protocol verbatim between the markers `<!-- protocol:start -->` and `<!-- protocol:end -->`, the provenance vocabulary, and a short "which tool when" list for the eight tools.

- [ ] **Step 1: Write failing test** `tests/test_skill_sync.py`: the text between the markers equals `AGENT_PROTOCOL` exactly; front matter parses with `name == "oncobuddy"`; every tool name registered in `create_server()` appears in the file; every `PROVENANCE_VOCAB` key appears; the sdist build includes `plugin/skills/oncobuddy/SKILL.md` (reuse the build helper from `tests/curated/test_packaging.py`, skipping if `uv` is absent).
- [ ] **Step 2: Run** `uv run pytest tests/test_skill_sync.py -q`; expect failure.
- [ ] **Step 3: Write** the skill, docs and spec edit. The docs state: ESMO and INFARMED permission as reported by the maintainer, scope read only; `agent-read` results are unverified; the user's consent and own access are what make browser reading allowed; nothing is stored by the server.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .`; expect green.
- [ ] **Step 5: Commit** `feat: oncobuddy skill with agent protocol; docs for browser handoff`.

# oncoBuddy v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first MCP server that serves region-tagged, provenance-stamped oncology data (EMA, PubMed, trials, ICD, curated guideline index) and evidence-grounded `treatment_options` for EU and Portugal.

**Architecture:** Connectors convert each source into one validated `Record` type. A deterministic reconciler filters records by region scope chain and assembles sections with explicit `no_data` reasons. A stdlib-SQLite cache stores API responses keyed by normalised entities only. The MCP server is a thin layer over the reconciler and the options matcher.

**Tech Stack:** Python 3.11+, `mcp` (official SDK), `httpx` (async), `pydantic` v2, `pyyaml`, stdlib `sqlite3`; dev: `pytest`, `pytest-asyncio`, `respx`, `ruff`; managed with `uv`.

**Spec:** `docs/superpowers/specs/2026-11-01-oncobuddy-design.md`

## Global Constraints

- Python `>=3.11`; no native-module dependencies; distribution name `oncobuddy-mcp`, import package `oncobuddy` under `src/oncobuddy/`.
- Every `Record` has `region`, `source`, `valid_as_of`, `licence`; a missing value is rejected at the connector boundary; a missing date is the string `"unknown"`, never defaulted.
- `licence == "restricted-link-only"` records expose metadata and link only, never copied text.
- Scope chain for `PT` is `["PT", "EU", "global"]`; `US` and sibling-region records are never returned.
- No silent fallback: a result that falls back from `PT` to `EU` says so in the section's `notes`.
- Source failure is `source_unavailable`, never "not found" or "not approved".
- Cache keys are normalised entities (INN, ICD code, region), never free text; free-text searches are not cached; `ONCOBUDDY_CACHE=memory` disables disk.
- The clinical profile is closed-schema (`extra="forbid"`), no free text, never cached or logged.
- Fixed notice on every response: `Decision support for a qualified clinician; not a substitute for clinical judgement. Verify against the cited source before acting.`
- No live network in CI; connectors tested against recorded responses in `tests/fixtures/`.
- Out of scope in v1: INFARMED, Portuguese rules, EU HTA, CIViC, regions other than EU/PT, encoded decision pathways.

## Review Focus

1. A region string that is not in the tree (`"Portugal"`, `"pt "`, `"US"`) must raise a clear `UnknownRegion` error, never return empty or global-only data. (Task 2)
2. ClinicalTrials.gov returns sites worldwide; a trial with only US sites must not appear for a `PT` query, and a multi-country trial is tagged by the in-scope site country. (Task 8)
3. Drug name variants (`"Trastuzumab "`, brand names, mixed case) must normalise to the INN or return `not_found`; never match a different drug on a substring. (Tasks 4, 6)
4. Source HTTP 429/5xx, timeout, or malformed JSON must yield `source_unavailable`; expired cache entries must never be served as fresh. (Tasks 3, 4, 6, 7, 8)
5. A profile with a missing stage or `unknown` biomarker yields caveats, not silent exclusion; and a validation error raised on a bad profile must not echo profile contents into the message or logs. (Tasks 5, 11)

---

## File Structure

```
pyproject.toml
src/oncobuddy/
  regions.yaml                # region tree (data)
  core/records.py             # Record, Entities, validation
  core/regions.py             # RegionTree, scope chain, UnknownRegion
  core/query.py               # Query
  core/cache.py               # Cache (SQLite, TTL, memory mode)
  core/normalise.py           # normalise_drug, normalise_condition
  core/reconciler.py          # Reconciler, Section, NoData, RegionalStatus
  core/profile.py             # ClinicalProfile
  core/options.py             # match_options, TreatmentOption, OptionsResult
  connectors/base.py          # Connector protocol, SourceUnavailable
  connectors/{ema,pubmed,trials,icd}.py
  curated/loader.py           # load_guidelines(dir) -> list[Record]
  curated/guidelines/*.yaml   # seed entries
  server.py                   # MCP tools, notice, CLI entry points
tests/ (mirrors src), tests/fixtures/
docs/sources/<source>.md      # endpoint, rate limits, licence, verified date
CONTRIBUTING.md, README.md, .github/workflows/ci.yml
```

---

### Task 1: Project scaffold and Record schema

**Files:**
- Create: `pyproject.toml`, `src/oncobuddy/__init__.py`, `src/oncobuddy/core/records.py`, `tests/core/test_records.py`, `.gitignore`

**Interfaces:**
- Produces: `Entities(drugs: list[str]=[], atc: list[str]=[], conditions: list[str]=[], biomarkers: list[str]=[])`; `Record` with fields `id: str, kind: Literal["medicine","trial","guideline","code","publication"], entities: Entities, region: str, source: str, source_url: str, valid_as_of: date | Literal["unknown"], retrieved_at: datetime, licence: str, payload: dict, reviewed_by: str | None = None`.

- [ ] **Step 1: Write failing tests** in `tests/core/test_records.py`: `test_record_requires_region_source_date_licence` (each omitted field raises `ValidationError`); `test_unknown_date_is_kept_as_unknown` (`valid_as_of="unknown"` round-trips, a missing `valid_as_of` still raises); `test_restricted_record_payload_has_no_text_field` (licence `restricted-link-only` with payload key `text` or `abstract` raises).
- [ ] **Step 2: Run** `uv run pytest tests/core/test_records.py -v`; expect FAIL (module missing).
- [ ] **Step 3: Create** `pyproject.toml` (deps and dev deps per Tech Stack, `pytest` config `asyncio_mode = "auto"`, `ruff`), `.gitignore`, and implement `Record`/`Entities` in `records.py` with pydantic `model_validator` for the restricted-payload rule and `extra="forbid"`.
- [ ] **Step 4: Run** the tests; expect PASS. Run `uv run ruff check .`.
- [ ] **Step 5: Commit** `feat: add Record schema and project scaffold`.

### Task 2: Region tree and scope chain

**Files:**
- Create: `src/oncobuddy/regions.yaml`, `src/oncobuddy/core/regions.py`, `tests/core/test_regions.py`

**Interfaces:**
- Consumes: none.
- Produces: `class UnknownRegion(ValueError)`; `RegionTree.load(path: Path | None = None) -> RegionTree` (default: packaged `regions.yaml`); `RegionTree.scope_chain(region: str) -> list[str]`; `RegionTree.eligible(record_region: str, target: str) -> bool`.

- [ ] **Step 1: Write failing tests**: `test_pt_chain_is_pt_eu_global` (`["PT","EU","global"]`); `test_eu_chain` (`["EU","global"]`); `test_unknown_region_raises` for `"Portugal"`, `"pt "`, `"US"` (US absent from v1 tree); `test_eligibility` (PT query accepts `PT`,`EU`,`global`, rejects `ES`); `test_tree_from_fixture_with_us_branch` using a tmp `regions.yaml` containing `US` proving a US record is rejected for a PT query.
- [ ] **Step 2: Run** the tests; expect FAIL.
- [ ] **Step 3: Write** `regions.yaml` (`global > EU > [PT, ES]`, parent-pointer mapping) and implement `RegionTree`; matching is exact and case-sensitive; no normalisation of input.
- [ ] **Step 4: Run** the tests; expect PASS.
- [ ] **Step 5: Commit** `feat: region tree with scope chain`.

### Task 3: SQLite cache

**Files:**
- Create: `src/oncobuddy/core/cache.py`, `tests/core/test_cache.py`

**Interfaces:**
- Consumes: `Record`.
- Produces: `cache_key(*, drug: str | None = None, condition: str | None = None, region: str | None = None) -> str` (rejects any value containing whitespace or longer than 64 chars with `ValueError`); `Cache(path: Path | None, ttl_seconds: dict[str, int], clock: Callable[[], float] = time.time)`; `Cache.get(source: str, key: str) -> list[Record] | None`; `Cache.put(source: str, key: str, records: list[Record]) -> None`; `Cache.clear() -> None`; `Cache.from_env() -> Cache` (honours `ONCOBUDDY_CACHE=memory`, default path in the OS user cache dir).

- [ ] **Step 1: Write failing tests**: `test_roundtrip`; `test_expired_entry_returns_none` (fake clock past TTL); `test_free_text_key_rejected` (`"breast cancer her2 patient john"`); `test_memory_mode_writes_no_file` (tmp dir stays empty); `test_clear`; `test_two_connections_same_file_wal` (second `Cache` writes while first is open without `database is locked`).
- [ ] **Step 2: Run** the tests; expect FAIL.
- [ ] **Step 3: Implement** with stdlib `sqlite3`, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, records stored as JSON, size cap of 50 MB enforced by deleting oldest rows on `put`.
- [ ] **Step 4: Run** the tests; expect PASS.
- [ ] **Step 5: Commit** `feat: privacy-conscious SQLite cache`.

### Task 4: Normalisation, connector interface and reconciler

**Files:**
- Create: `src/oncobuddy/core/query.py`, `src/oncobuddy/core/normalise.py`, `src/oncobuddy/connectors/base.py`, `src/oncobuddy/core/reconciler.py`, `tests/core/test_reconciler.py`, `tests/core/test_normalise.py`

**Interfaces:**
- Consumes: `Record`, `RegionTree`, `Cache`.
- Produces:
  - `Query(drug: str | None, condition: str | None, region: str, filters: dict[str, str] = {})`.
  - `normalise_drug(name: str) -> str | None` (strip, lowercase, exact lookup in a bundled INN list `core/data/inn.txt`; returns `None` if not an exact INN; no substring or fuzzy matching).
  - `class SourceUnavailable(Exception)`; `class Connector(Protocol)`: `name: str`, `section: str`, `async fetch(query: Query) -> list[Record]`.
  - `NoData(reason: Literal["not_found","source_unavailable","out_of_scope_v1"])`; `Section(records: list[Record], no_data: NoData | None, notes: list[str])`; `RegionalStatus(query_region: str, scope_chain: list[str], sections: dict[str, Section], notice: str)`.
  - `Reconciler(connectors: list[Connector], tree: RegionTree, cache: Cache, timeout_s: float = 10.0)`; `async Reconciler.regional_status(drug: str, indication: str, region: str) -> RegionalStatus`.

- [ ] **Step 1: Write failing tests** with fake connectors: `test_filters_to_scope_chain` (mixed PT/EU/ES/US records, assert only PT/EU/global returned, most specific first); `test_fallback_note_when_no_pt_record` (notes contains `"no PT record; showing EU-level"`); `test_timeout_gives_source_unavailable` and `test_exception_gives_source_unavailable` (never `not_found`); `test_empty_result_gives_not_found`; `test_unknown_drug_returns_not_found_without_calling_connectors`; `test_sections_for_v1_unsupported_source_are_out_of_scope_v1`; normalise tests: `"Trastuzumab "` → `"trastuzumab"`, `"trastuz"` → `None`, brand `"Herceptin"` → `None` (brand mapping is not v1).
- [ ] **Step 2: Run** the tests; expect FAIL.
- [ ] **Step 3: Implement** the modules. The reconciler gathers connectors with `asyncio.gather(return_exceptions=True)` wrapped in `asyncio.wait_for`; consults the cache before calling a connector (only when the cache key is valid); filters via `tree.eligible`; sorts by chain index. Section name comes from `connector.section`.
- [ ] **Step 4: Run** the tests; expect PASS.
- [ ] **Step 5: Commit** `feat: reconciler with explicit no_data reasons`.

### Task 5: Clinical profile

**Files:**
- Create: `src/oncobuddy/core/profile.py`, `tests/core/test_profile.py`

**Interfaces:**
- Produces: `ClinicalProfile` (pydantic, `extra="forbid"`): `cancer_type: str` (ICD-O-3 topography/morphology or ICD-10 code pattern), `stage: Literal["I","II","III","IV","unknown"]`, `biomarkers: dict[str, Literal["positive","negative","unknown"]]` (keys match `^[A-Z0-9_-]{1,20}$`), `ecog: int | None` (0-4), `prior_lines: list[str]` (INN, each `^[a-z-]{2,40}$`), `intent: Literal["curative","palliative","unknown"]`, `comorbidity_flags: list[Literal["renal_impairment","hepatic_impairment","cardiac_disease","pregnancy"]] = []`.

- [ ] **Step 1: Write failing tests**: `test_unknown_field_rejected` (`name`, `dob`, `notes`); `test_free_text_in_biomarker_key_rejected` (`"HER2 positive on biopsy"`); `test_ecog_range`; `test_validation_error_does_not_echo_values` (build a profile with a secret-looking `cancer_type` string, assert the string is absent from `str(exc)` after a custom error wrapper `ProfileError`).
- [ ] **Step 2: Run** the tests; expect FAIL.
- [ ] **Step 3: Implement** `ClinicalProfile` plus `parse_profile(data: dict) -> ClinicalProfile` that catches pydantic `ValidationError` and raises `ProfileError` listing field names and error types only, no input values.
- [ ] **Step 4: Run** the tests; expect PASS.
- [ ] **Step 5: Commit** `feat: closed-schema clinical profile`.

### Task 6: EMA connector

**Files:**
- Create: `src/oncobuddy/connectors/ema.py`, `docs/sources/ema.md`, `tests/connectors/test_ema.py`, `tests/fixtures/ema_medicines.json`

**Interfaces:**
- Consumes: `Connector`, `Query`, `Record`, `SourceUnavailable`.
- Produces: `EmaConnector(client: httpx.AsyncClient, url: str = EMA_MEDICINES_JSON_URL)` with `name="EMA"`, `section="authorisation"`.

- [ ] **Step 1: Verify the source.** Confirm the current EMA "medicines" JSON data download URL, update cadence and reuse licence on ema.europa.eu; record them with the verification date in `docs/sources/ema.md`. Save a trimmed real response (about 5 medicines including one oncology product) as the fixture.
- [ ] **Step 2: Write failing tests** using `respx`: `test_returns_eu_region_record_for_authorised_inn` (record `region="EU"`, `kind="medicine"`, `valid_as_of` taken from the source's revision date, `licence` from `docs/sources/ema.md`); `test_unknown_inn_returns_empty_list`; `test_429_and_500_raise_source_unavailable`; `test_malformed_json_raises_source_unavailable`; `test_missing_revision_date_gives_unknown`.
- [ ] **Step 3: Run** the tests; expect FAIL.
- [ ] **Step 4: Implement** `EmaConnector.fetch`: download the dataset (cache the parsed index in memory per process), match on exact normalised INN against the active-substance field, emit one `Record` per authorised product-indication with payload `{product, status, authorisation_date, indication_text_url}`; link to the SmPC/EPAR rather than copying indication text unless the licence permits.
- [ ] **Step 5: Run** the tests; expect PASS; commit `feat: EMA connector`.

### Task 7: PubMed connector

**Files:**
- Create: `src/oncobuddy/connectors/pubmed.py`, `docs/sources/pubmed.md`, `tests/connectors/test_pubmed.py`, `tests/fixtures/pubmed_*.xml|json`

**Interfaces:**
- Produces: `PubMedConnector(client: httpx.AsyncClient, email: str | None = None, api_key: str | None = None, max_results: int = 10)` with `name="PubMed"`, `section="literature"`; emits `kind="publication"`, `region="global"`.

- [ ] **Step 1: Verify the source.** Confirm E-utilities (`esearch`/`esummary`) usage policy, rate limits (with and without API key), and licence of abstracts; record in `docs/sources/pubmed.md`. Save fixtures.
- [ ] **Step 2: Write failing tests** (`respx`): `test_builds_query_from_inn_and_condition` (asserts the request params contain the INN and the condition code term, and the `tool`/`email` params); `test_record_has_pmid_url_and_pubdate_as_valid_as_of`; `test_payload_has_title_not_abstract_when_licence_restricted`; `test_rate_limit_429_raises_source_unavailable`; `test_free_text_query_is_not_cached` (via the reconciler with a tmp `Cache`: cache file contains no search string).
- [ ] **Step 3: Run** the tests; expect FAIL.
- [ ] **Step 4: Implement** with `esearch` then `esummary` (JSON); respect the documented request rate with an `asyncio.Semaphore`/delay.
- [ ] **Step 5: Run** the tests; expect PASS; commit `feat: PubMed connector`.

### Task 8: Trials connector (ClinicalTrials.gov, CTIS best effort)

**Files:**
- Create: `src/oncobuddy/connectors/trials.py`, `docs/sources/trials.md`, `tests/connectors/test_trials.py`, `tests/fixtures/ctgov_*.json`

**Interfaces:**
- Produces: `TrialsConnector(client: httpx.AsyncClient, tree: RegionTree)` with `name="ClinicalTrials.gov"`, `section="trials"`; emits `kind="trial"`, one `Record` per (trial, in-tree country) pair, `region` = ISO country code present in the tree (e.g. `PT`, `ES`) .

- [ ] **Step 1: Verify the sources.** Confirm ClinicalTrials.gov API v2 `studies` query params (condition, intervention, `filter.overallStatus`, location country) and terms. Check whether CTIS offers any stable public machine-readable access; if not, record in `docs/sources/trials.md` that CTIS is not covered in v1 and the section note says so.
- [ ] **Step 2: Write failing tests**: `test_us_only_trial_not_returned_for_pt` (fixture trial with only US sites yields zero records for region `PT`, via the reconciler); `test_multi_country_trial_tagged_per_site_country` (PT and ES sites yield two records; PT query returns only the PT one); `test_only_recruiting_by_default`; `test_site_countries_outside_tree_are_dropped`; `test_http_error_raises_source_unavailable`.
- [ ] **Step 3: Run** the tests; expect FAIL.
- [ ] **Step 4: Implement** `fetch` mapping location country names to tree region IDs through a small `COUNTRY_TO_REGION` dict in the module (only countries in `regions.yaml`); payload carries `nct_id`, `title`, `phase`, `status`, `sponsor`.
- [ ] **Step 5: Run** the tests; expect PASS; commit `feat: trials connector`.

### Task 9: ICD coding connector

**Files:**
- Create: `src/oncobuddy/connectors/icd.py`, `src/oncobuddy/connectors/data/icd_onc.yaml`, `docs/sources/icd.md`, `tests/connectors/test_icd.py`

**Interfaces:**
- Produces: `IcdConnector()` with `name="WHO ICD"`, `section="coding"`, `region="global"`, `kind="code"`; `async fetch(query)` resolves `query.condition` (a code or exact term in the bundled subset) to code records; plus `lookup_code(code_or_term: str) -> list[Record]`.

- [ ] **Step 1: Verify the source and licence.** Check WHO ICD API access (OAuth client credentials) and ICD-10 / ICD-O-3 redistribution terms; record in `docs/sources/icd.md`. v1 ships a bundled oncology subset (neoplasm chapter C00–D48 for ICD-10 plus ICD-O-3 topography) only if the licence permits redistribution; otherwise the bundled file holds code and short label pairs under the permitted terms and a link, and the live WHO API is optional behind credentials.
- [ ] **Step 2: Write failing tests**: `test_exact_code_lookup` (`C50.9` returns a record with label and `valid_as_of` set to the bundled edition date); `test_unknown_code_returns_empty`; `test_term_lookup_is_exact_not_substring` (`"breast"` does not silently pick a code; returns candidates list payload or empty per spec decision documented in the test); `test_all_records_region_global`.
- [ ] **Step 3: Run** the tests; expect FAIL.
- [ ] **Step 4: Implement** loading the YAML once, with every row validated into `Record`.
- [ ] **Step 5: Run** the tests; expect PASS; commit `feat: ICD coding connector`.

### Task 10: Curated guideline index

**Files:**
- Create: `src/oncobuddy/curated/loader.py`, `src/oncobuddy/curated/guidelines/*.yaml` (seed), `CONTRIBUTING.md`, `tests/curated/test_loader.py`

**Interfaces:**
- Produces: `load_guidelines(directory: Path) -> list[Record]` (`kind="guideline"`); guideline `payload` schema `GuidelinePayload(title: str, issuer: str, applies_to: Applicability, summary: str, evidence_grade: str | None)`, `Applicability(conditions: list[str], stages: list[str], required_biomarkers: dict[str, Literal["positive","negative"]], line_of_therapy: Literal["first","later","any"], interventions: list[str])`. `GuidelineConnector(directory: Path)` with `section="guidelines"`.

- [ ] **Step 1: Write failing tests**: `test_seed_files_all_validate`; `test_missing_licence_fails_with_filename` (tmp YAML without `licence`); `test_restricted_guideline_has_no_summary_text` (the YAML summary must be empty or own-words flagged `own_words: true`, enforced); `test_reviewed_by_required_for_curated`; `test_guideline_connector_filters_by_condition_and_region`.
- [ ] **Step 2: Run** the tests; expect FAIL.
- [ ] **Step 3: Implement** the loader and connector. Seed 3 to 5 entries from guidelines whose licence has been checked (record each licence check in the entry's `licence` and in `docs/sources/guidelines.md`); `CONTRIBUTING.md` states the link-and-own-words rule and the required fields.
- [ ] **Step 4: Run** the tests; expect PASS.
- [ ] **Step 5: Commit** `feat: curated guideline index and loader`.

### Task 11: Treatment options matcher

**Files:**
- Create: `src/oncobuddy/core/options.py`, `tests/core/test_options.py`

**Interfaces:**
- Consumes: `ClinicalProfile`, `Reconciler`, `Applicability`.
- Produces: `TreatmentOption(intervention: str, line: str, guideline: Record, authorisation: list[Record], trials: list[Record], caveats: list[str])`; `OptionsResult(query_region: str, scope_chain: list[str], options: list[TreatmentOption], no_data: NoData | None, notes: list[str], notice: str)`; `async treatment_options(profile: ClinicalProfile, region: str, reconciler: Reconciler) -> OptionsResult`.

- [ ] **Step 1: Write failing tests** with fake connectors: `test_every_option_has_a_guideline_record`; `test_no_supporting_guideline_returns_no_data_not_empty_options`; `test_unknown_biomarker_gives_caveat_not_exclusion`; `test_negative_biomarker_excludes_option_requiring_positive`; `test_missing_stage_gives_caveat`; `test_us_guideline_never_used_for_pt`; `test_restricted_guideline_noted_as_link_only_not_evaluated`; `test_profile_absent_from_cache_file_and_logs` (run with a tmp disk `Cache` and `caplog`, then scan the SQLite file bytes and log text for profile field values); `test_pt_reimbursement_reported_out_of_scope_v1`.
- [ ] **Step 2: Run** the tests; expect FAIL.
- [ ] **Step 3: Implement** deterministic matching: select guideline records in scope whose `applies_to` matches condition, stage and line; classify biomarker requirements as match / mismatch (exclude) / unknown (caveat); attach authorisation and trial records by intervention INN via the reconciler; no logging of the profile.
- [ ] **Step 4: Run** the tests; expect PASS.
- [ ] **Step 5: Commit** `feat: evidence-grounded treatment options`.

### Task 12: MCP server, CLI, docs and CI

**Files:**
- Create: `src/oncobuddy/server.py`, `README.md`, `.github/workflows/ci.yml`, `tests/test_server.py`, `tests/test_region_safety.py`
- Modify: `pyproject.toml` (console scripts `oncobuddy-mcp` and `oncobuddy-clear-cache`)

**Interfaces:**
- Consumes: everything above.
- Produces: MCP tools `regional_status(drug, indication, region)`, `guidelines_for(condition, region)`, `find_trials(condition, region, filters)`, `treatment_options(profile, region)`, `get_medicine(drug, region)`, `search_literature(drug, condition)`, `lookup_code(code_or_term)`, `get_record(id)`; `main() -> None` (stdio server); `clear_cache_main() -> None`. Every tool result is a JSON object with `notice`, `query_region`, `scope_chain`.

- [ ] **Step 1: Write failing tests**: `test_every_tool_response_has_notice_and_scope_chain` (call each tool function with fake connectors); `test_unknown_region_returns_error_object_not_data`; `test_treatment_options_tool_schema_has_no_free_text_field` (inspect the generated JSON schema: no unconstrained `string` property except pattern-constrained ones); `test_region_safety_matrix` in `tests/test_region_safety.py` (for every region in the tree, a mixed-region fixture through the full reconciler returns only that region's scope chain); `test_server_starts_and_lists_tools` (via the SDK's in-memory client if available).
- [ ] **Step 2: Run** the tests; expect FAIL.
- [ ] **Step 3: Implement** `server.py` with the official MCP Python SDK; wire connectors with one shared `httpx.AsyncClient` and `Cache.from_env()`; tools are thin wrappers returning model dumps; add `README.md` (install via `uvx oncobuddy-mcp`, client config snippet, privacy notes, disk-encryption caveat, scope and non-goals) and `.github/workflows/ci.yml` (ruff, pytest, validating `curated/` on every PR).
- [ ] **Step 4: Run** `uv run pytest -v && uv run ruff check .`; expect all PASS. Run the server manually with an MCP inspector or client and call `regional_status` once against fixtures.
- [ ] **Step 5: Commit** `feat: MCP server, docs and CI`.

---

## Open items carried from the spec

Settled inside tasks: endpoints, rate limits and licences (verification step in Tasks 6-9), TTL values (set in `Cache.from_env` per source and documented in `docs/sources/*.md`), guideline seed list (Task 10), normalisation (Tasks 4 and 9). Still outside this plan: the EU MDR regulatory check before any public release, and per-cancer-type profile fields beyond the closed v1 schema.

# oncoBuddy handoff (written 2026-11-02, for a clean session)

Read this first. It replaces the long chat history. Then read `README.md` and, if needed, the specs in
`docs/superpowers/specs/` (design intent) and plans in `docs/superpowers/plans/`.

## What oncoBuddy is
A local-first Python MCP server and agent plugin (`uvx oncobuddy-mcp`; skill at `plugin/skills/oncobuddy/SKILL.md`)
that gives an oncologist's AI agent region-tagged, provenance-stamped oncology data (EU + Portugal), plus a
**source registry** of guidelines the agent reads in the user's own browser (with consent) when a program cannot fetch
them. The server contains no LLM, stores nothing the agent reads, and never impersonates a browser.

Owner: a Portuguese oncologist/developer. Product direction (decided by them): the tool **finds sources and says
how to read them; the agent reasons and cites**. No per-document clinician sign-off on the main path (the
draft/promote workflow was removed). ESMO and INFARMED reportedly granted READ permission (not reproduction).

## Where things are
- Repo: `/home/ze/oncoBuddy` (git, branch `master` has only the first 3 spec/plan commits, no remote).
- All real work is in the worktree `/home/ze/oncoBuddy/.claude/worktrees/v1`:
  - branch `worktree-v1` = v1 (28 commits, unmerged); branch **`v2-infarmed-pointers`** (current, stacked on it) = v1 + all v2.
  - **Merge into `master` is paused by the owner: ask before doing it.** Never `--force`, never `git stash`.
- Tooling: `export PATH="$HOME/.local/bin:$PATH"`; `uv run pytest -q` (760 passing); `uv run ruff check .`.
  In the worktree use plain separate Bash commands (compound `cd`+git commands are refused by a guard).
- `uv run python scripts/verify_registry.py` checks the registry against Crossref (network, ~2 min; `verify_dois.py` is an alias).

## Architecture (src/oncobuddy)
`core/` records, regions (tree: global > EU > {PT, ES}; US, UK are siblings under global), reconciler (sections:
authorisation, reimbursement, guidelines, trials, literature, coding), options (`treatment_options`), profile (closed
de-identified schema), biomarkers (canonical vocabulary + aliases), access (access blocks, `AGENT_PROTOCOL`, allowed
hosts), readplan, crossref (shared helpers), cache (SQLite, API responses only). `connectors/` ema, pubmed, trials,
icd, guidelines (curated YAML registry), infarmed (link-only pointers), editions (new). `curated/guidelines/*.yaml`
= 153 registry entries (ESMO 39, DGS 14, NCCN 48, ASCO 52), `esmo_topics.yaml` = shared topic -> ICD-10 routing table.
`server.py` = MCP tools: regional_status, guidelines_for, find_trials, treatment_options, get_medicine,
search_literature, lookup_code, get_record. Every success response carries `notice` and `agent_protocol`.

## Invariants (never break; each has tests)
1. **Region safety is top priority.** Only `reference_only` guideline records of an ineligible region may escape, and only
   into `Section.reference_elsewhere` of the guidelines section (never records/no_data/options/other sections).
2. **Privacy:** nothing profile-supplied outside the closed enums and canonical biomarker vocabulary may appear in any
   output, log or cache (test: `test_profile_absent_from_cache_file_and_logs`). Unknown biomarker keys are only counted.
3. A source failure is `source_unavailable`, never `not_found`/`current`. Missing INFARMED status means "not retrieved".
4. Registry entries make no clinical claim: `reviewed_by: unreviewed-seed`, `interventions: []`, restricted-link-only,
   empty summary, no drug names in id/title (pinned by a test), recipes contain no doses/links/drug names.
5. Agents never log in, create accounts or bypass bot checks. Do not build headless/simulated browsing (declined: it
   evades the site owner's control; ESMO cannot grant more).
6. Error text names fields only and never echoes content.

## State at handoff
Done (all committed on `v2-infarmed-pointers`): INFARMED pointers; browser handoff (access blocks + protocol + skill);
biomarker vocabulary; source discovery (ESMO registry, read plans, ordering, `last_verified`); DGS/NCCN/ASCO registry and
the `reference_elsewhere` group; registry verifier; edition checker.

**Edition check: DONE (2026-11-02, last commit `de84f03`).** Serialised Crossref requests (1, or 3 with `ONCOBUDDY_EMAIL`),
0.25 s spacing, cap 6, 8 s batch deadline (unfinished -> `unknown`, finished cached 7 days), on by default
(`ONCOBUDDY_EDITION_CHECK=off` disables). Docs done. Sonnet review done; fixed in one pass. Open minors: cap is spent in
input order (ineligible-region reference records can take slots; needs owner call), first of a record's two requests is wasted
if the deadline lands between them, no in-flight DOI dedup, no tests for cache TTL expiry / GuidelineConnector end-to-end.

## Queue after that (owner order)
1. Plugin packaging: manifest and one-step install (skill + `uvx oncobuddy-mcp`).
2. NICE was SKIPPED by the owner (all NICE listing pages return 403 "Temporary Service Interruption", also in their Chrome;
   single pages like `/guidance/ng12` load). Revive only if asked: owner exports the list from their browser, or NICE's
   official syndication API (registration/licence). NICE technology appraisals are moot meanwhile.

## Decisions waiting for the owner
- Merge `worktree-v1` + `v2-infarmed-pointers` into `master` (run tests on the merged result first).
- Topic-table routing: C65/C66 (renal pelvis/ureter), C48 (retroperitoneum/peritoneum), C33 (trachea), C69 (eye), C93/C94
  (AML subtypes; the AML entry carries C92 only), intrahepatic cholangiocarcinoma (C22.1 routes only to HCC), NCCN cutaneous
  lymphomas (C84 only), ASCO breast risk-reduction guideline (prevention, routes to C50).
- Coverage gaps: ESMO thyroid/GIST/sarcomas not found on Crossref, six date-conflict skips (Merkel, non-epithelial ovarian,
  ALL, CML, primary cutaneous lymphoma, high-grade glioma), neuroendocrine/hereditary/supportive topics; DGS keyword search
  (prostate, melanoma, haematology norms and the Orientacoes list may be missing; 3 entries are public-consultation pages:
  dgs-017-2013, dgs-023-2013, dgs-032-2013); NCCN 21 skipped; ASCO entries from 2019-2021 and DGS 2012-2015 may be superseded.
- Owner-only items: replace the placeholder User-Agent contact URL (`https://github.com/`) in `ema.py`, `pubmed.py`,
  `trials.py` and set `ONCOBUDDY_EMAIL`; choose a LICENSE; EU MDR check (is `treatment_options` medical-device software?);
  clinician review of the v1 ICD names/search terms; a scheduled real-API drift CI job does not exist.

## Deferred minors (small, none blocking)
Time-bomb fixtures (a "fresh" date goes stale ~2028-03); no 540/541-day boundary tests; `reference_only` accepts `1`/`"true"`
(use StrictBool); "outside global's scope" note wording; verifier fail-open for a DOI record from an issuer outside
`ALLOWED_JOURNALS`; reconciler has no second guard against an EU-scope record flagged `reference_only` from a non-loader
connector; missing region-safety edge tests (UK/EU/PT queries, two connectors in one section, failure alongside
reference records); `stages: []` on ASCO entries though some titles state a stage; clearer error when two biomarker
aliases disagree; topic table keeps its ESMO-specific filename; `main(argv)` in verifier ignores argv.

## How to work with this owner (standing preferences)
- **Minimise token use:** pick the cheaper option each time; native (inline) execution with ONE final review beats
  per-task subagent loops; delegate mechanical edits to `haiku`, code to `sonnet`, final review to `opus`; NEVER use the
  `fable` model for subagents (global rule). Keep messages short; redirect long outputs to files.
- Confirm before hard-to-reverse or outward actions (merge, push, publish). Do not ask INFARMED for permission.
- Workflow used: brainstorm (short) -> spec (committed) -> compact plan (committed) -> TDD -> verify yourself (never trust a
  subagent's report: it once claimed tests that were not committed) -> final review -> fix in one pass.
- Chrome extension tools (`mcp__claude-in-chrome__*`) work in the owner's real Chrome after they allow each site; stop and report
  if a permission is denied or a block appears. Crossref (`api.crossref.org`, no key/email, ONE request at a time) supplies DOIs
  and dates for ESMO/ASCO; matching rules live in `docs/runbooks/refresh-registry.md`.
- Pronouns: use they/them for the owner; the owner's email is only for identification.

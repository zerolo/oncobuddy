# Edition Check Implementation Plan

> **For agentic workers:** executed inline (native), one final whole-branch review. Steps use checkbox syntax.

**Goal:** Flag DOI-bearing registry guidelines (ESMO, ASCO) as `current`, `newer_found`, `corrected`, `retracted` or `unknown` from the open Crossref API, at query time, cached, never blocking.

**Architecture:** shared Crossref helpers move to `core/crossref.py`; `connectors/editions.py` holds a pure `classify` and an `EditionChecker` that annotates guideline records (payload `edition_check`) through the existing SQLite cache as `edition` Records; the reconciler adds a note and sorts retracted last; options adds caveats; protocol rule 3 gains one sentence.

**Spec:** `docs/superpowers/specs/2026-11-02-edition-check-design.md`.

## Global Constraints
- Worktree `/home/ze/oncoBuddy/.claude/worktrees/v1`, branch `v2-infarmed-pointers`; `export PATH="$HOME/.local/bin:$PATH"`; `uv run pytest -q`; `uv run ruff check .`. Tests never use the network (httpx.MockTransport).
- Only registry DOIs are sent to Crossref; never a profile value. A failure is `unknown`, never `current`, never an error. `not_checked` for NCCN/DGS/disabled.
- Cap 10 records per call, 3 s timeout each, cache TTL 7 days (source `Crossref-edition`, key `condition=<sha1 hex of lower-case DOI>`). `mailto` only from `ONCOBUDDY_EMAIL`. Switch `ONCOBUDDY_EDITION_CHECK=off`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus
- A profile value, or anything but a registry DOI/title, in any request; a Crossref failure shown as `current`.
- Cap and timeout honoured; cache hit makes no request; in-memory cache works.
- Region logic, ordering of non-retracted records, and the privacy test unchanged.

### Task 1: shared helpers + record kind + TTL
Files: create `src/oncobuddy/core/crossref.py` (`ALLOWED_JOURNALS`, `MIN_PAGES`, `normalise_title`, `page_span`, `first`), modify `scripts/verify_registry.py` (import them, keep names re-exported), `core/records.py` (kind literal gains `"edition"`), `core/cache.py` (`DEFAULT_TTLS["Crossref-edition"] = 7*86400`). Test: existing verifier tests stay green; `tests/core/test_crossref.py` for the helpers and the kind/TTL.
- [ ] tests first (helpers importable from the package, `Record(kind="edition")` valid, TTL present), RED, implement, GREEN, commit `refactor: shared Crossref helpers; edition record kind`.

### Task 2: classify + EditionChecker
Files: create `src/oncobuddy/connectors/editions.py`; test `tests/connectors/test_editions.py` (MockTransport, records every request URL).
Interfaces: `def classify(title: str, entry_date: date, journals: set[str], notices: list[dict], candidates: list[dict]) -> dict` returning `{"status", "newer": {doi,title,date}|None, "notices": [{type,doi,date}]}`; `class EditionChecker(client, cache, *, mailto=None, enabled=True, timeout=3.0, cap=10)` with `async def annotate(self, records: list[Record]) -> None` setting `payload["edition_check"]` = classify result plus `checked_at` (ISO), or `{"status": "not_checked"}` / `{"status": "unknown"}`.
Rules: notices from `/works?filter=updates:<doi>`: `retraction` -> retracted; `correction|erratum|addendum|expression_of_concern` -> corrected. Newer: candidate pre-colon title equals entry's (normalised), container in journals, created date after entry date, DOI differs, (>=3 pages or article number). Precedence retracted > newer_found > corrected > current.
- [ ] tests (each status, precedence, rejections: single-page, other journal, other title, same DOI, older; cap 10; timeout -> unknown; HTTP error/bad JSON -> unknown; NCCN/DGS and `enabled=False` -> not_checked; cache hit -> zero requests; requests contain only the DOI/title, never anything else), RED, implement, GREEN, commit `feat: Crossref edition checker`.

### Task 3: wiring, surfacing, protocol
Files: `connectors/guidelines.py` (`GuidelineConnector(directory=None, checker=None)`; `fetch` awaits `checker.annotate(out)` when present), `server.py` (`build_reconciler` creates the checker from `client`, `cache`, `ONCOBUDDY_EMAIL`, `ONCOBUDDY_EDITION_CHECK`), `core/reconciler.py` (guidelines-section note `edition check: N newer edition(s), N corrected, N retracted` when any nonzero and `edition check unavailable for N guideline(s)` when unknown>0; sort key (region, retracted, specificity)), `core/options.py` (`_guideline_caveats`: `newer edition found (<date>); read it instead`, `corrections published; read the notice`, `retracted`), `core/access.py` (rule 3 gains `If edition_check shows a newer edition, read that one and say so; if it shows a retraction, say the guideline was retracted.`), `plugin/skills/oncobuddy/SKILL.md` (protocol block regenerated from the constant).
- [ ] tests (connector with fake checker; reconciler note + retracted-last + non-guideline order unchanged; options caveats; protocol sentence + skill sync; server builds with the switch off; region-safety and privacy tests green), RED, implement, GREEN, commit `feat: surface edition checks in guidelines, options and protocol`.

### Task 4: docs
Files: `README.md`, `docs/sources/guidelines.md` (edition check section, statuses and the "no newer edition found is not verified current" caveat, NICE wording changed to "skipped by decision on 2026-11-02: listing pages blocked"), `plugin/skills/oncobuddy/SKILL.md` (edition_check paragraph + env switches).
- [ ] write docs; `uv run pytest -q`; commit `docs: edition check and NICE decision`.

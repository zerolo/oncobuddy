# oncoBuddy v2 slice F: edition check - Design Spec

Date: 2026-11-02
Status: Draft, awaiting user review
Builds on: `2026-11-02-source-discovery-design.md`, `2026-11-02-more-sources-design.md`

## 1. Purpose

A guideline registry goes stale quietly. 21 ASCO entries date from 2019 to 2021, the DGS norms from
2012 to 2015, and ESMO publishes updates. The registry already shows each entry's edition date and
a `last_verified` caveat, but nothing tells the agent that a **newer edition, a correction or a
retraction exists**. This slice adds that signal for DOI-bearing entries (ESMO, ASCO), at query
time, from the open Crossref API.

Success: `guidelines_for`, `regional_status` and `treatment_options` mark a DOI-bearing guideline
record as `current`, `newer_found`, `corrected`, `retracted` or `unknown`, with the newer DOI, title
and date when one exists, so the agent can say "a newer edition exists; read it" instead of citing a
superseded one. A Crossref failure never blocks a response and is never shown as `current`.

## 2. Non-goals

- NCCN (living, no API) and DGS (no DOI) are marked `not_checked`, never `current`.
- NICE is out of scope (skipped by the maintainer on 2026-11-02).
- The check does not change the registry files, matching, region logic or options; it adds a
  payload field, caveats and notes, and moves retracted records last.
- No fetching of any publisher page; Crossref metadata only. No patient data is ever sent.

## 3. Signals (Crossref REST API, public)

1. **Corrections and retractions:** `GET /works?filter=updates:<doi>&rows=10&select=DOI,type,
   update-to,title,created`. Notices whose `update-to` entry names the checked DOI with type
   `retraction` give `retracted`; types `correction`, `erratum`, `addendum`,
   `expression_of_concern` give `corrected`.
2. **Newer edition:** `GET /works?query.bibliographic=<title before the colon>&filter=type:
   journal-article,from-created-date:<entry date + 1 day>,container-title:<journal>&rows=5&select=
   DOI,title,container-title,page,article-number,volume,issue,created`. A result is a newer edition
   only if: its title before the colon equals the entry's after normalisation, the container is the
   entry issuer's journal set (same `ALLOWED_JOURNALS` as the verifier), it spans at least 3 pages
   or has an article number, and its DOI differs. Interim updates and Express Updates with the same
   pre-colon title count as newer editions on purpose. Editorials and single-page items are
   ignored.
3. Status precedence: `retracted`, then `newer_found`, then `corrected`, then `current`. The payload
   keeps all findings, not only the winning status.

## 4. Where and how

- New `connectors/editions.py`: `EditionChecker(client, cache, mailto=None, enabled=True)` with
  `async def check(records: list[Record]) -> dict[str, EditionStatus]`. It only looks at records
  whose `source` is in `ALLOWED_JOURNALS` and whose URL is a doi.org link; all others are
  `not_checked`.
- `GuidelineConnector` takes an optional checker; after loading the matching records it calls the
  checker for at most **6 DOI-bearing records** per call (those first in the connector's order).
  Requests are **serialised** (Crossref's public pool allows one at a time and answers bursts with
  429; 3 at a time if `ONCOBUDDY_EMAIL` is set), ~0.25 s apart, each with a 3-second timeout, under a
  **total 8-second deadline** per batch. Records that finish are cached; unfinished ones are
  `unknown` and complete on later calls (a request takes ~2 s, two per record). It writes the result into
  `payload["edition_check"] = {"status", "checked_at", "newer": {doi, title, date} | None,
  "notices": [{type, doi, date}]}`. Records beyond the cap get `status: "not_checked"`.
- Cache: results are stored through the existing SQLite `Cache` as a `Record` of kind `edition`
  (source `Crossref-edition`, key = normalised DOI), TTL 7 days, added to `DEFAULT_TTLS`. A cache
  hit makes no request. In-memory mode (`ONCOBUDDY_CACHE=memory`) keeps working unchanged.
- Failure: a timeout, HTTP error or malformed answer gives `status: "unknown"` for that record and a
  section note `"edition check unavailable for N guideline(s)"`. Never `current`, never an error.
- Politeness: one `User-Agent` of the form `oncobuddy-mcp/<version>`; `mailto` is sent only if the
  user set `ONCOBUDDY_EMAIL` (their explicit configuration). Requests are limited to the cap and
  cached, so a typical call makes zero to twelve requests.
- Switch: `ONCOBUDDY_EDITION_CHECK=off` disables (on by default) the checker (offline use); statuses are then
  `not_checked`.

## 5. Surfacing

- Section note (guidelines section), once: `"edition check: N newer edition(s), N corrected, N
  retracted"` when any are non-zero.
- `treatment_options` caveats for a guideline in scope: `newer edition found (<date>); read it
  instead`, `corrections published; read the notice`, `retracted`. Reference-group records carry
  the field but add no caveats (they are never matched).
- Ordering: within a region group, retracted records sort last (a third component of the existing
  guideline sort key); everything else keeps its order.
- Agent protocol rule 3 gains: `If edition_check shows a newer edition, read that one and say so;
  if it shows a retraction, say the guideline was retracted.` Skill regenerated from the constant.

## 6. Testing (all offline with a fake Crossref)

- Each status from fixtures: no notices and no newer item gives `current`; a retraction notice;
  a correction notice; a newer same-title multi-page article; precedence among them.
- Rejections for newer items: single-page item, editorial with the same title, wrong journal,
  different pre-colon title, same DOI, older date.
- Cap of 6 per call (7th is `not_checked`); serialised requests (peak 1, or 3 with email); spacing;
  the batch deadline keeps finished records and marks the rest `unknown`; failures never produce `current`.
- Cache: second call makes no request; TTL expiry; in-memory mode; the cached Record round-trips.
- NCCN and DGS records are `not_checked`; switch off gives `not_checked` everywhere.
- Surfacing: notes, caveats and retracted-last ordering; region safety and the profile-privacy
  tests stay green; no profile value is ever in a request (the fake records every request URL).
- Protocol rule 3 text and skill sync.

## 7. Risks

- Crossref's relationships for guidelines are incomplete: a newer edition under a different title
  is missed (status stays `current`, which means "no newer edition found", not "verified current").
  The status wording and docs say so.
- A same-title update by a different guideline panel could be flagged; the caveat tells the agent
  to read it, which is the safe direction.
- Rate limits: bounded by the cap, the 7-day cache and the optional `mailto`.
- Latency: a cold call can add up to the 8-second batch deadline; failures degrade to `unknown`.

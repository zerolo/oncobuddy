# onco@medbuddy

**An oncology research buddy for your AI assistant.** onco@medbuddy is a local [MCP](https://modelcontextprotocol.io)
server that lets Claude (or any MCP client) look up oncology information for **Portugal, the EU and the wider
world**, and show exactly where each piece came from: EMA medicine authorisations, ClinicalTrials.gov trials,
PubMed literature, ICD-10 coding, and a curated registry of 153 guideline sources (ESMO, DGS, NCCN, ASCO).

What makes it different:

- **Every record is stamped** with its source, date, licence and region. Records outside your region
  (for example US guidelines in a Portuguese query) are never mixed in; they appear only in a separate,
  clearly labelled "reference elsewhere" group.
- **It finds sources; the assistant reads and cites them.** Guidelines that cannot be fetched by a program
  are read by the assistant in *your own browser*, with your consent. onco@medbuddy never scrapes, never logs in
  anywhere, and stores nothing the assistant reads.
- **No patient data.** Queries use a closed, de-identified schema (ICD code, stage, biomarkers, ECOG...). Nothing
  free-text is accepted, logged or cached.

## Quick start: Claude Desktop

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (it runs the server for you).
2. Open Claude Desktop, then **Settings > Developer > Edit Config**, and add this to
   `claude_desktop_config.json` (merge it into any existing `mcpServers`):

```json
{
  "mcpServers": {
    "onco-medbuddy": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/zerolo/onco-medbuddy", "onco-medbuddy"],
      "env": { "ONCOBUDDY_EMAIL": "you@example.org" }
    }
  }
}
```

3. Fully quit and reopen Claude Desktop. The eight tools (`regional_status`, `guidelines_for`, `find_trials`,
   `treatment_options`, `get_medicine`, `search_literature`, `lookup_code`, `get_record`) appear under the tools menu.
4. Ask, for example: *"Which guidelines and recruiting trials in Portugal cover osimertinib in stage IV EGFR-positive NSCLC?"*

`ONCOBUDDY_EMAIL` is optional: it is sent to NCBI and Crossref as their usage policies request. Use any contact
address, or drop the `env` block. To read browser-only sources (ESMO, INFARMED) the assistant needs a browser
tool such as the Claude in Chrome extension; without one it gives you the link and manual instructions.

Using Claude Code instead? See [Claude Code plugin](#claude-code-plugin).

**Not a medical device.** onco@medbuddy is decision support for qualified clinicians. A
regulatory (EU MDR) check is pending and must be completed before any public release or
clinical use. It does not diagnose, prescribe or replace clinical judgement.

## Scope and non-goals

In scope (v1): EMA central authorisations, curated guideline links, ClinicalTrials.gov
trials per country, PubMed publication links, and a curated ICD-10 site index.

Non-goals: patient data storage, free-text clinical notes, dosing advice, reimbursement
decisions, and anything that names an intervention without a cited guideline record.

## Install

Requires [uv](https://docs.astral.sh/uv/). Run it straight from GitHub (no clone needed):

```
uvx --from git+https://github.com/zerolo/onco-medbuddy onco-medbuddy
```

Or from a local clone: `uvx --from /path/to/onco-medbuddy onco-medbuddy`. Any MCP client that launches a
stdio server can use the same command and `env` block as the Claude Desktop example above.

### Claude Code plugin

Installs the skill (browser-reading protocol) and the server in one step:

```
/plugin marketplace add zerolo/onco-medbuddy
/plugin install onco-medbuddy@onco-medbuddy
```

## Environment variables

| Variable | Meaning |
| --- | --- |
| `ONCOBUDDY_CACHE` | Set to `memory` to keep the cache in memory only (nothing on disk). |
| `ONCOBUDDY_CACHE_DIR` | Directory for the on-disk cache (default: OS user cache directory). |
| `ONCOBUDDY_EMAIL` | Contact email sent to NCBI and Crossref as requested by their usage policies; lets edition checks run three requests at a time. |
| `ONCOBUDDY_EDITION_CHECK` | `on` (default) or `off`. Checks DOI-bearing ESMO/ASCO entries against Crossref for corrections, retractions and newer editions. |
| `NCBI_API_KEY` | Optional NCBI API key (raises the PubMed rate limit). |

Email and API key are never written to tool output or logs. Logs go to stderr at WARNING
level and never contain request arguments or profiles.

## Privacy

- No tool accepts patient identifiers or free text. `treatment_options` takes a closed,
  structured profile (ICD code, stage, biomarker status, ECOG, prior drug names, intent,
  comorbidity flags). Invalid profiles are rejected with field paths and error types only,
  never the submitted values.
- The clinical profile itself is never stored or logged.
- Cache keys contain the normalised drug INN, the condition code and the region, plus public
  source records. These can reveal that a query for that cancer type was made. The cache is a
  local SQLite file with owner-only permissions; onco@medbuddy relies on your disk encryption for
  protection at rest, which is strongly advised.
- Local curated sources (guidelines, ICD index) are never cached.
- `ONCOBUDDY_CACHE=memory` writes nothing to disk.
- Clear the cache with `oncobuddy-clear-cache` (in memory mode it reports that nothing is stored).
- External queries contain only normalised drug names and ICD-derived search terms.

## Tools

- `regional_status(drug, indication, region)`: all sections (authorisation, reimbursement,
  guidelines, trials, literature, coding) for a drug and indication in a region.
- `guidelines_for(condition, region)`: curated guideline links for a condition.
- `find_trials(drug, condition, region)`: recruiting trials; a drug is required.
- `treatment_options(profile, region)`: guideline-grounded options for a structured profile.
- `get_medicine(drug, region)`: marketing authorisation records (for PT, plus a link-only
  INFARMED pointer record).
- `search_literature(drug, condition?, region?)`: PubMed publication links.
- `lookup_code(code_or_term)`: ICD-10 code or exact site name lookup.
- `get_record(id)`: a record returned earlier in this session (never fetches).

An unknown region returns `{"error": {"code": "unknown_region", "valid_regions": [...]}}`
and no data.

## Sources, licences and limits

Details, endpoints, rate limits and licence findings are in `docs/sources/`:
[EMA](docs/sources/ema.md), [ClinicalTrials.gov](docs/sources/trials.md),
[PubMed](docs/sources/pubmed.md), [ICD index](docs/sources/icd.md),
[guidelines](docs/sources/guidelines.md), [INFARMED pointers](docs/sources/infarmed.md).
Records with a restricted licence carry links
and metadata only, never copied text.

## Finding sources

The guideline registry holds 153 entries as of 2026-11-02: ESMO 39, DGS 14, NCCN 48 and ASCO 52
(NICE is skipped: its listing pages are blocked). Coverage gaps are in [docs/sources/guidelines.md](docs/sources/guidelines.md). Entries are scope-only and browser-access (the agent reads them in the user's own
browser after consent). Each carries a `last_verified` date; an entry older than 540 days gets a
staleness caveat in the response.

Guidelines from US and UK bodies (NCCN, ASCO and, NICE if ever added) form a separate
`reference_elsewhere` group. US and UK records are never mixed into PT or EU results, and they never
count as `no_data`. Each one is labelled with its issuing region (US or UK), and it is never used
for options, exclusions or caveats, because national approval, reimbursement and practice may differ.
These entries are reference-only links (`reference_only: true`).

- `guidelines_for` returns the most specific guideline first within a region.
- `treatment_options` adds a `read_plan`: a short reading instruction for the browser sources, built
  from the profile's closed fields. It is never advice.
- Guideline entries are refreshed by an agent in the maintainer's Chrome, following
  [docs/runbooks/refresh-registry.md](docs/runbooks/refresh-registry.md). Details, coverage
  gaps and the topic table are in [docs/sources/guidelines.md](docs/sources/guidelines.md).

Edition check: for ESMO and ASCO entries with a DOI, the server asks Crossref (sending only the
registry's own DOI and title) about corrections, retractions and newer editions, up to 6 per call,
one request at a time, within 8 seconds; results are cached for 7 days and fill in over later calls.
`current` means only "no newer edition found", not "verified current"; a failed lookup is `unknown`.

NICE was skipped by decision on 2026-11-02 (listing pages blocked); it will be revisited only on request.

## Known limitations

- Seed guidelines are scope-only and unreviewed, so `treatment_options` returns guideline
  links and no named interventions in v1. Matching is on the ICD site category only;
  histology and subtype are not checked. ECOG, intent and comorbidity flags are accepted but
  not used by matching in v1 (the response says so, by field name).
- ICD-O-3 morphology codes (e.g. 8500/3) are accepted but not indexed in v1, so the guideline
  and coding sections return `not_found` for them.
- Guideline editions may have been superseded; check the cited source.
- EU-level trial queries are not searched (the trials section reports `out_of_scope_v1` with a
  note): trials are searched per country region (PT, ES). Trials require a drug and read one
  page of up to 50 results.
- EMA covers centrally authorised products only; authorisation is per product and the
  indication is not checked.
- Portuguese status is not retrieved by onco@medbuddy. For region PT the `authorisation` and
  `reimbursement` sections carry a `pointer` record per drug that links to INFARMED's Infomed
  database and the SIATS process tracker. Its `access` block (`method: browser`) tells the agent
  to read the status in the user's own browser, with the user's consent; the pointer states
  `not retrieved - read in the user's browser` and asserts no authorisation or reimbursement
  status. `no_data` describes the real sources only, so a PT section can hold a pointer and
  `no_data` (`not_found` or `source_unavailable`) together. onco@medbuddy never contacts INFARMED
  hosts and never fetches, caches or stores INFARMED data (see
  [docs/sources/infarmed.md](docs/sources/infarmed.md)). For EU and other regions the
  `reimbursement` section reports `not_found`.
- EU HTA, CIViC and CTIS are v2.
- Partial (month-only) PubMed and ClinicalTrials.gov dates become `unknown` by design.
- Adding a country requires BOTH `src/oncobuddy/regions.yaml` and the `COUNTRY_TO_REGION`
  map in `src/oncobuddy/connectors/trials.py`.
- ICD site names and search terms are unreviewed heuristics.
- The scheduled real-API drift check described in the spec is v2.
- Decision support only; the EU MDR regulatory check is pending; there is no LICENSE yet.

## Development

```
uv sync
uv run ruff check .
uv run pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Curated guideline entries are validated by the test suite.

## Sources that need the user's browser

Some authoritative sources (ESMO guidelines behind a bot check, INFARMED's Infomed) cannot be
fetched by a program. onco@medbuddy does not impersonate a browser. A record for such a source carries
an `access` block: `method: browser`, a `reason`, an `entry_url` and a short `read_recipe` (where
to look and what to report; never source content). Every successful tool response carries a fixed
`agent_protocol`: the agent asks the user for permission, reads the page in the user's own
browser, puts no patient data in any URL or form, reports only what the page states, cites the URL
and date, and labels the result `agent-read` (not clinician-reviewed). The same protocol is in
[`plugin/skills/oncobuddy/SKILL.md`](plugin/skills/oncobuddy/SKILL.md); a test keeps the two
identical. The server never contacts these sources and never stores what the agent reads.

## Biomarker vocabulary

Profiles and curated guideline entries share one vocabulary, so a marker matches however an agent
names it. Canonical keys: `HER2`, `ER`, `PR`, `MSI-H`, `RAS`, `KRAS`, `NRAS`, `BRAF-V600E`, `EGFR`, `ALK`, `ROS1`, `NTRK`, `BRCA`. Aliases (for example `DMMR`,
`ERBB2`; `PMMR` inverts the status to `MSI-H`, and `MSS` positive maps to `MSI-H` negative while
`MSS` negative stays unknown, because it could still be MSI-low) are mapped on input, keys are
case-insensitive, and an alias that disagrees with its canonical key rejects the profile. A key
outside the list is accepted but not used for matching; the response notes how many such keys
there were without naming them.
Curated `required_biomarkers` must use canonical keys. The full table is in
[`plugin/skills/oncobuddy/SKILL.md`](plugin/skills/oncobuddy/SKILL.md).

# oncoBuddy — Design Spec

Date: 2026-11-01
Status: Approved; v1 implemented (see plan and README for known limitations)

## 1. Purpose

oncoBuddy is a local-first MCP server that gives the AI agents used by oncologists reliable, **region-tagged, provenance-stamped** oncology reference data. It is built for any oncologist, not one subspecialty or one country. It covers evidence-grounded treatment decision support, literature and guideline discovery, and administrative lookups (coding, authorisation status).

Its differentiator is **deterministic cross-source regional reconciliation**: for a drug and indication it assembles, in code and not in the model, what is authorised, what guidelines say, which trials are open, and which literature exists, filtered by region and with every claim traced to a dated source.

### Scope
- **v1 regions:** EU and Portugal (PT). Region model is extensible to other regions by data only.
- **v1 sources:** EMA, PubMed (NCBI E-utilities), ClinicalTrials.gov and EU CTIS, WHO ICD-10 / ICD-O-3, plus a small curated guideline index.
- **v2 (deferred):** INFARMED authorisation and reimbursement status, Portuguese rules and norms (DGS, national networks), EU HTA joint clinical assessments, open biomarker knowledge (e.g. CIViC).

### Non-goals
- Patient identifiers or free-text patient data. Tools accept only a structured, de-identified clinical profile (section 2.6).
- Deterministic encoded decision pathways (guidelines compiled into rules). Deferred beyond v1.
- Regions beyond EU and PT in v1.
- Hosting. v1 is local-first; the same code may later run remotely.

## 2. Architecture

Chosen approach: **common schema plus deterministic reconciler**.

- Every connector converts its source into one `Record` type.
- A reconciler (plain code, no LLM) gathers records, filters by region scope chain, and assembles sections.
- A small SQLite cache stores API responses only (not an ingested copy of sources).

### 2.1 Region model
Regions form a tree defined in `regions.yaml`:

```
global
└── EU
    ├── PT
    ├── ES
    └── ...
```

- A query names a target region; its **scope chain** is `[PT, EU, global]`, most specific first.
- Records tagged with a region in the chain are eligible. Records tagged `US`, or with a sibling region, are never eligible.
- `global` records (e.g. WHO coding, PubMed) apply everywhere but keep their source.
- Adding a country or a US branch means editing `regions.yaml` only.

### 2.2 Record schema
| Field | Meaning |
|---|---|
| `id` | Stable ID derived from source and source key |
| `kind` | `medicine`, `trial`, `guideline`, `code`, `publication`, … |
| `entities` | Normalised refs: drug (INN/ATC), condition (ICD-O-3/ICD-10), biomarker |
| `region` | Single region ID from the tree |
| `source` | Source name, e.g. `EMA` |
| `source_url` | Link to the original |
| `valid_as_of` | Date the source states the content valid; `unknown` if absent |
| `retrieved_at` | Fetch time |
| `licence` | SPDX-style tag or `restricted-link-only` |
| `payload` | Source-specific data |

Enforced in code:
- A record missing `region`, `source`, `valid_as_of` or `licence` is rejected at the connector boundary.
- `restricted-link-only` records return metadata and links, never copied text.
- A missing date is `unknown` and displayed as such, never defaulted.

### 2.3 Reconciler
For a query `(drug, indication, region)`:
1. **Gather:** call relevant connectors in parallel, each with a timeout.
2. **Filter:** keep records whose `region` is in the scope chain; order most specific first.
3. **Assemble sections:** `authorisation` (EMA), `guidelines` (curated index), `trials` (ClinicalTrials.gov, CTIS), `literature` (PubMed), `coding` (ICD-O-3, ICD-10). Each section holds records or `no_data` with a reason: `not_found`, `source_unavailable`, or `out_of_scope_v1`.

Safety rules:
- **No silent fallback.** If `PT` has nothing, the result says "no PT record; showing EU-level", and each record keeps its own `region`.
- **Source failures are visible.** A timeout yields `source_unavailable`, never "not approved".

### 2.4 MCP tools
High-level (agents should prefer):
- `regional_status(drug, indication, region)`
- `guidelines_for(condition, region)`
- `find_trials(condition, region, filters)`
- `treatment_options(profile, region)` (section 2.6)

Low-level: `get_medicine`, `search_literature`, `lookup_code`, `get_record(id)`.

Every response includes `query.region`, the scope chain used, per-record provenance, and the notice: *"Decision support for a qualified clinician; not a substitute for clinical judgement. Verify against the cited source before acting."*

### 2.5 Cache
Local SQLite file (stdlib `sqlite3`, WAL mode, busy timeout) with per-source TTL; `retrieved_at` always shown. It is a cache only: deletable and rebuilt on demand.

Privacy controls:
- Cache keys are normalised entities (INN, ICD code, region), never free text. Free-text search queries are not cached.
- Short TTLs, size cap, `clear-cache` command.
- Config switch for in-memory-only mode.
- Stored in the OS user cache directory; documentation states reliance on user disk encryption.

### 2.6 Treatment options (decision support)

oncoBuddy contains no LLM. The server returns **evidence-grounded options**; the doctor's agent composes the advice. Every option traces to a record, so a recommendation can always be audited back to a dated, region-tagged source.

**Clinical profile (input).** A pydantic model with closed fields only: cancer type (ICD-O-3/ICD-10), stage, biomarker statuses (enumerated), performance status (ECOG), prior lines of therapy (INN/ATC list), intent (curative/palliative), and optional enumerated comorbidity flags. No free-text fields, no identifiers, no dates of birth. Unknown fields are rejected, not ignored. The profile is held in memory for the request only: never cached, never logged.

**Output.** A list of options, each with:
- the intervention (INN/ATC) and setting (line of therapy);
- guideline support: guideline reference, evidence grade where the source states one, `valid_as_of`, link (summary and link only for restricted guidelines);
- regional status: EMA authorisation for the indication, plus PT/EU availability and reimbursement when that data exists (PT reimbursement is v2, so it reports `out_of_scope_v1`) (superseded in v2: see docs/sources/infarmed.md — link-only pointer records);
- open trials in the scope chain matching the profile;
- `caveats`: profile fields the matching could not use, and conflicts between sources.

Rules:
- Options come only from records. If no guideline or label supports an option for the profile, it is not returned. Absence is reported as `no_data` with a reason, never filled from model knowledge.
- Matching is deterministic code over structured fields. Where a record's applicability to the profile is uncertain (e.g. missing biomarker), the option is returned with a caveat, not silently included or dropped.
- Coverage gaps are explicit: where only restricted guidelines exist, the result states that guideline content is link-only and not evaluated.
- Region rules (2.1) apply unchanged.

## 3. Stack and layout

Python 3.11+, official MCP Python SDK, `httpx`, `pydantic`, stdlib `sqlite3`. Distributed on PyPI as `oncobuddy-mcp`; client config is `uvx oncobuddy-mcp` (or `pipx`). No native modules.

```
oncobuddy/
  regions.yaml
  core/            # Record schema, scope chain, cache, reconciler
  connectors/      # ema, pubmed, trials, icd — interface: fetch(query) -> list[Record]
  curated/
    guidelines/    # metadata and links only, no copied text
  server.py        # MCP tool definitions
  tests/
  docs/
```

The reconciler knows nothing about specific sources.

## 4. Curated data contributions

Entries are YAML, one per guideline or rule, validated in CI by the same Record schema. A PR missing `region`, `source`, `valid_as_of` or `licence` fails. A maintainer verifies the source link and claim and sets `reviewed_by`. `CONTRIBUTING.md` states the licensing rule: link and summarise in your own words; never paste restricted text.

## 5. Testing

- **Region safety (highest priority):** for each region, a mixed-region fixture must return only the scope chain, never `US` or sibling records. Runs in CI on every change.
- **Reconciler:** `no_data` reasons, the "no PT record; showing EU-level" fallback, `source_unavailable` versus not found.
- **Connectors:** recorded API responses, no live network in CI. A separate scheduled job pings real APIs to detect format drift.
- **Schema:** missing or `unknown` dates render correctly and are never defaulted.
- **Treatment options:** every returned option links to at least one record; a profile with no supporting records returns `no_data`; unknown profile fields and free-text are rejected; uncertain applicability yields a caveat; the profile never appears in the cache file or logs (asserted by scanning both after a run).

## 6. Open items for the plan stage
- Exact API endpoints, rate limits and licence terms per v1 source.
- TTL values per source.
- Guideline index seed list (which guidelines, which cancer types first).
- Entity normalisation approach (INN/ATC and ICD-O-3 mapping).
- Regulatory check before any public release: whether giving treatment options makes the tool medical-device software under EU MDR, and what that requires.
- Which openly licensed guidelines and label sources support `treatment_options` in v1, and which cancer types the first release can honestly cover.
- Field list and enumerations for the clinical profile, per cancer type.

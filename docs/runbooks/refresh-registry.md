# Refresh the guideline registry

Purpose: add or update pointer entries for guidelines from ESMO, DGS (Portugal) and, as
reference only, NCCN and ASCO (United States) and NICE (United Kingdom).
Entries record where a guideline lives, never what it says. Copy no guideline text.

## Shared rules

- An agent performs each refresh in the maintainer's own Chrome, with the maintainer's
  consent, and with the source's site allowed in the browser extension.
- Never bypass a block, a bot check, a permission prompt or a login. If one appears,
  stop and report to the maintainer.
- Every entry is scope-only: `reviewed_by: unreviewed-seed`, `interventions: []`,
  `required_biomarkers: {}`, `licence: restricted-link-only`, `summary: ""`, the shared
  `access` browser block, and `last_verified` set to today's date (quoted).
- Reference-only entries (NCCN, ASCO, NICE) carry `reference_only: true` and a US or UK
  region. Normal entries (ESMO, DGS) carry no `reference_only` key and an EU or PT region.
- Quote all dates in YAML. Keep existing ids stable; a newer edition replaces the old one
  under the same id.
- Conditions come from the topic table `src/oncobuddy/curated/esmo_topics.yaml`. A subset is
  allowed only when the title names one organ or entity; declare it in the matching
  `*_DECLARED_SUBSETS` mapping in `tests/curated/test_registry.py`. A topic that is not a key
  of the table is skipped and listed in the report.
- Set `stages` only when the title states a stage scope; otherwise `[]`.
- Verify with `uv run python scripts/verify_registry.py`, then `uv run pytest -q`. The
  verifier makes about one network request per second and takes a couple of minutes. Report
  what was added, updated and skipped, with reasons.

## ESMO

Region EU, normal group, entries `esmo-<slug>.yaml`, access reason `bot_check`.

### What to record (only this)

For each guideline: title, topic, DOI, edition date and the ICD-10 conditions.

- Topics come from the `esmo.org` topic pages, which show short titles.
- DOI, edition date and the stored title come from the Crossref REST API.
- The machine running the refresh needs access to `api.crossref.org`.

### Steps

1. On `esmo.org`, open the topic pages and list each clinical practice guideline
   with its short title, its topic and the ESMO listing date shown next to it.
2. For each guideline, find its DOI and edition date with Crossref:
   - Request `https://api.crossref.org/works` with `query.bibliographic` set to the ESMO
     short title. One request per second, header `User-Agent: oncoBuddy-registry/0.1`,
     no email and no API key.
   - Match on the part of the Crossref title before the colon plus the date. Accept only
     type `journal-article` in `Annals of Oncology` or `ESMO Open`.
   - Accept Crossref's `created` date only if it is within 7 days of the ESMO listing date
     shown on the topic page next to the guideline.
   - Reject single-page items, interim updates and Express Updates, and correspondence
     carrying the same title. List any guideline with no accepted result as unmatched.
   - The stored `title` is Crossref's title, with footnote marks removed.
3. Write one YAML per guideline named `esmo-<slug>.yaml` in
   `src/oncobuddy/curated/guidelines/` (id equals the file stem), in the seed format of
   `esmo-oncogene-addicted-nsclc.yaml`:
   - `url` must be `https://doi.org/<doi>`; `published` and `valid_as_of` the accepted date.
   - `conditions` must be the ICD-10 list of the topic in `esmo_topics.yaml`, unless a
     declared subset applies (for example salivary gland uses C07, C08).
4. Verify as in the shared rules. `scripts/verify_registry.py` checks that each DOI
   resolves and that the Crossref title, journal and page span match the entry.

## DGS (Portugal)

Region PT, normal group, entries `dgs-<norma>-<year>.yaml`, access reason `no_api`.

- Open the DGS norms index on `www.dgs.pt` (the `normas.dgs.min-saude.pt` host is not usable).
  Use the DGS keyword search for oncology terms, one search per topic.
- For each oncology norm, record its number and year, its title, the issue date shown on the
  norms index and the last-update date when the page shows one. Do not open the norm's
  recommendations; the entry's read recipe tells the reader where to look.
- Use the norm's own URL on `www.dgs.pt` as `url`. `published` is the issue date and
  `valid_as_of` the date of the cited edition; norms may be superseded, so note this in the
  report.
- Report skipped norms with reasons (for example paediatric, screening, or a topic not in the
  table), and list searches that returned no oncology norm.

## NCCN (United States, reference only)

Region US, `reference_elsewhere` group, entries `nccn-<slug>.yaml`, access reason
`login_required`, `reference_only: true`.

- Use only the NCCN public guidelines index on `www.nccn.org`. Do not log in, and do not open
  the guideline PDFs or any page that needs a login.
- Never log in for the user. If a page asks for a login, stop for that page and report it;
  the user reads NCCN with their own login.
- Record the title as published, the topic key and the page URL. `published` and
  `valid_as_of` are `unknown`, because NCCN guidelines are living documents.
- Report skipped guidelines with reasons: no topic key, paediatric, or a page that asks for a
  login.

## ASCO (United States, reference only)

Region US, `reference_elsewhere` group, entries `asco-<slug>.yaml`, access reason
`bot_check`, `reference_only: true`.

- Take titles from the ASCO guideline index on `www.asco.org`, and treat the "current
  version" as the one shown on the `ascopubs.org` topic page for that guideline.
- DOI and date come from Crossref, with the same checks as ESMO: type `journal-article` in
  `Journal of Clinical Oncology`, the `created` date within 7 days of the listing date shown on
  the ASCO index, and the same rejections (single-page items, interim updates, correspondence).
- `url` is `https://doi.org/<doi>`. Living guidelines are accepted when the Crossref record is a journal article in Journal of Clinical Oncology with matching title and date, provided the page rules hold: a page field equal to the article number is accepted, and a record with no page, no volume and no issue (online ahead of print) is accepted; a record with no page but a volume or issue is rejected ("no page range") and must be skipped, and a page range of fewer than 3 pages is rejected; record the DOI of any skipped
  guideline in the report.
- Several editions date from 2019 to 2021; say so in the report when a newer version may
  exist.

## NICE (United Kingdom, reference only)

Region UK, `reference_elsewhere` group, entries `nice-<code>.yaml`, access reason `no_api`,
`reference_only: true`.

- Only NICE clinical guidelines with codes NG or CG, on the cancer guidance index. Exclude
  technology appraisals (TA): their titles name drugs and they are a different kind of source.
- Index pages only, on `www.nice.org.uk`. Record the code, the title as published and the
  published date shown on the index.
- If the listing is unavailable (for example a 403 "Temporary Service Interruption"), make no
  entries; report the outage and retry later.

## Report

For every source: entries added, updated and skipped, with the reason for each skip, the
counts per source, and any listing that could not be read.

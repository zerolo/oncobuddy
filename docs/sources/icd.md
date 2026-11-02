# ICD coding: bundled oncoBuddy site index (no WHO text)

Verified: 2026-11-01.

## Licence findings (what could and could not be established)

- WHO classifications page https://www.who.int/about/licensing/classifications/en/ (fetched
  2026-11-01): the ICD/ICF/ICHI family (now on the ICD-11 platform) is licensed CC BY-ND 3.0 IGO:
  commercial and non-commercial use allowed provided "there is no adaptation of the codes" and
  appropriate citation; ICD-11 has extra terms of use. The page does NOT address ICD-10 or ICD-O-3.
- ICD-10 (2019 edition) and ICD-O-3 reproduction terms: no WHO page stating them could be fetched.
  Secondary sources (national ICD-10 licences, e.g. UK TRUD, CIHI; a German agency's terms) say WHO
  holds the copyright, attribution is required, and redistribution needs WHO permission; a
  CAMCOPS note quotes a 2014 WHO email to the same effect (second-hand, indicative only).
  Contact for permissions: icd@who.int / https://www.who.int/about/policies/publishing/permissions
- Conclusion: WHO label text is NOT redistributable under MIT without permission. The bundle
  therefore contains no WHO label text. Codes are identifiers.
- WHO ICD API https://icd.who.int/icdapi : registration required; OAuth 2 client credentials
  (client id and secret from "View API access key"; token endpoint
  https://icdaccessmanagement.who.int/connect/token), per
  https://icd.who.int/icdapi/docs2/APIDoc-Version2/. The pages do not describe any anonymous
  access, so it is treated as unusable without credentials.
- The ICD-10 browser (https://icd.who.int/browse10/2019/en) is a JavaScript app; a scripted fetch
  returned 404, so the per-code deep-link pattern (e.g. `#/C50`) could NOT be verified. Records
  link to the browser root only.

## What is bundled

`src/oncobuddy/connectors/data/icd_onc.yaml`: 89 ICD-10 neoplasm categories C00-C97 (the ICD-10
codes that do not exist are omitted: C27-C29, C35, C36, C42, C59, C87, C89) and 9 carcinoma
in situ categories D00-D07 and D09 (D08 does not exist): 98 rows. Each row is `code`, `name`,
`group`.

- `name` is a short site name written by oncoBuddy in its own words (lowercase letters, digits,
  spaces, hyphens). It is NOT a WHO label and intentionally does not reuse WHO wording. `group` is
  our own coarse grouping.
- Licence on every row: `oncobuddy-original` (codes are identifiers; names are the project's own
  descriptions, MIT-compatible).
- `valid_as_of` = the file's own `edition_date: 2026-11-01`, the date WE authored it. It is NOT a
  WHO edition date. Only 3-character categories are indexed.
- `reviewed_by: unreviewed-seed`: clinical review is pending; a human reviewer should check the
  category list and names and replace this value.
- Payload note on each record: names are written by oncoBuddy; consult the WHO ICD browser for
  official wording.

## Display name vs search term

Each row has two own-words fields. `name` is a display name (British spelling allowed).
`search_term` is a short US-spelling phrase intended for PubMed / ClinicalTrials.gov queries.
`condition_terms(code)` returns ONLY `search_term`, never `name`. Search terms are heuristics,
unreviewed by a clinician (`reviewed_by` stays `unreviewed-seed`). `search_term: null` means
"no safe automatic search label" (generic, mixed, secondary or ill-defined categories, or where
no single honest phrase exists); the identifier record still resolves via `lookup_code`, and
`payload.search_term` is `None`.

Code shape: after normalisation only `^[A-Z][0-9]{2}$` or `^[A-Z][0-9]{2}\.[0-9A-Z]{1,4}$` resolve
(so `C500`, `C50X`, `C50.`, `C5` return nothing); `queried_code` is the normalised input.

## Behaviour

`IcdConnector` (`name="ICD index"`, `section="coding"`) is fully offline. A code such as `C50.9`
resolves to its category (`C50`) and the record carries `payload.queried_code`. Unknown categories
return nothing. ICD-O-3 morphology codes (`8500/3`) are not covered in v1 and return nothing.
`lookup_code` also accepts an exact (case-insensitive) site name; partial terms return nothing.
`condition_terms(code)` returns the own-words site name (for later use as a literature/trial
search label).

## Deferred

Live WHO ICD API access (needs registered credentials) and ICD-O-3 morphology are out of scope
for v1 and no code for them exists.

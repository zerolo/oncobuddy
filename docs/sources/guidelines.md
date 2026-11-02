# Curated guideline index

Verified: 2026-11-02. Code: `src/oncobuddy/curated/loader.py`, `src/oncobuddy/connectors/guidelines.py`.
Entries live in `src/oncobuddy/curated/guidelines/*.yaml` (packaged). Rules: `CONTRIBUTING.md`.

## Registry

The registry holds 153 guideline entries as of 2026-11-02: ESMO 39, DGS 14, NCCN 48 and ASCO 52.
NICE has no entries (skipped by decision on 2026-11-02). One YAML file per guideline lives in `src/oncobuddy/curated/guidelines/`,
named by its source prefix (`esmo-<slug>.yaml`, `dgs-<norma>-<year>.yaml`, `nccn-<slug>.yaml`,
`asco-<slug>.yaml`). Entries are scope-only and browser-access, as described below.

### Sources

| Source | Issuer | Region | Group | Count | How built | Date semantics |
| --- | --- | --- | --- | --- | --- | --- |
| ESMO | ESMO | EU | normal | 39 | agent in Chrome: `esmo.org` topic pages; DOI and date from Crossref | Crossref record-creation date, cross-checked to within 7 days of ESMO's listing date |
| DGS norms (oncology) | DGS | PT | normal | 14 | agent in Chrome: DGS norms index on `www.dgs.pt`, keyword search | issue date shown on the DGS norms index; last-update date when shown |
| NCCN guidelines | NCCN | US | `reference_elsewhere` | 48 | agent in Chrome: NCCN public index; never logs in | `unknown` (living documents) |
| ASCO guidelines | ASCO | US | `reference_elsewhere` | 52 | agent in Chrome: ASCO guideline index and `ascopubs.org` topic pages; DOI and date from Crossref | Crossref record-creation date, cross-checked to within 7 days of ASCO's listing date |
| NICE clinical guidelines (NG/CG, cancer) | NICE | UK | `reference_elsewhere` | 0 | not populated: skipped by decision on 2026-11-02 (listing pages blocked) | none yet; the published date on the index will be used |

### Reference group

NCCN, ASCO and NICE are reference-only sources. Their records carry `reference_only: true` and a US
or UK region, and they reach results only in the `reference_elsewhere` list. They never appear in
the normal results, `no_data`, option matching or any other section for PT or EU queries, and they
are labelled with the region that issued them. National approval, reimbursement and practice may
differ, so these records are never used for options, exclusions or caveats.

- Issuer: ESMO. `url` is the DOI link (`https://doi.org/<doi>`), with the DOI taken from Crossref.
- `published` and `valid_as_of`: the Crossref record-creation date, cross-checked to within 7 days
  of ESMO's listing date. These are approximate, not exact online dates; one known case, primary
  CNS lymphoma, shows the print-issue month instead.
- Latest edition only: each guideline has one entry under a stable id. The metastatic colorectal
  2022 seed was superseded on 2026-11-02 by the 2026 edition (same id, new DOI).
- `last_verified`: the date the entry's link and metadata were last checked. Older than 540 days,
  the response adds a staleness caveat.
- `applies_to.conditions`: the ICD-10 categories of the ESMO topic in
  `src/oncobuddy/curated/esmo_topics.yaml`, except where the title names one organ or entity
  (for example localised colon cancer uses C18); a test pins every entry to a topic list, a
  declared union or a declared subset. Gastric and oesophageal use the union of both topics.

Refresh procedure, for every source: [docs/runbooks/refresh-registry.md](../runbooks/refresh-registry.md).

### Coverage gaps: ESMO

- Not found in Crossref: thyroid and GIST; sarcomas (only 2014 editions were found, so no entry).
- Skipped because of date conflicts: Merkel cell carcinoma, non-epithelial ovarian cancer, acute
  lymphoblastic leukaemia (ALL), chronic myeloid leukaemia (CML), primary cutaneous lymphomas, and
  high-grade glioma.
- Not covered: uveal melanoma, Waldenstrom macroglobulinaemia, myelodysplastic syndromes,
  Ph-negative myeloproliferative neoplasms.
- Topic pages not opened: Hereditary Syndromes and Supportive Care. Interim updates and Express
  Updates are not represented.
- Neuroendocrine, consensus statements and other non-tumour topics are not in the topic table, so
  they are not routed.
- Routing questions left to the maintainer: C65/C66 (renal pelvis and ureter), C48 (retroperitoneum
  and peritoneum), C33 (trachea), C69 (eye), and intrahepatic cholangiocarcinoma coded C22.1, which
  routes only to hepatocellular carcinoma.

### Coverage gaps: DGS

- Found by keyword search only, so the registry may be missing prostate, melanoma and haematology
  norms, and the Orientações listing.
- Skipped: paediatric norm 014/2017; oral-cancer early-intervention norm 002/2014; screening norms;
  a COVID oncology norm.
- `normas.dgs.min-saude.pt` is not usable; entries link to `www.dgs.pt`.
- The norms are 2012 to 2015 editions and may be superseded.
- Three entries link to public-consultation pages (drafts, not final norms): dgs-017-2013,
  dgs-023-2013 and dgs-032-2013. Their recipes tell the agent to look for the final norm.

### Coverage gaps: NCCN

- 21 guidelines skipped: 15 without a topic key (uveal melanoma, MDS, MPN, GIST, neuroendocrine,
  amyloidosis, mastocytosis, Kaposi, DFSP, gestational trophoblastic, Castleman, histiocytic,
  eosinophilia neoplasms, neuroblastoma, Wilms) and 6 paediatric.
- Uterine neoplasms are mapped to the endometrial topic.
- Dates are `unknown` by design; the user's own NCCN login is needed to read the guidelines, and the
  agent never logs in for them.

### Coverage gaps: ASCO

- "Current version" is judged from the `ascopubs.org` topic pages. Larynx, bladder and cervical
  guidelines are not listed in JCO there.
- Several entries date from 2019 to 2021; newer versions may exist.
- Two living guidelines are skipped because the Crossref record has a volume or issue but no page range: metastatic castration-resistant
  prostate cancer (10.1200/jco-26-01898) and stage IV NSCLC without driver alterations
  (10.1200/jco-26-00842).
- Skipped because its title names a drug: "Duration of Oxaliplatin-Containing Adjuvant Therapy for
  Stage III Colon Cancer".

### Coverage gaps: NICE

- Not populated. Skipped by decision on 2026-11-02: NICE's listing pages returned 403 "Temporary
  Service Interruption", also in the owner's browser. Revive only on request. Technology appraisals are out of scope for this slice.

## What an entry means

Each entry is a LINK to a guideline plus a machine-readable scope (condition category, stages).
All seed entries are `reviewed_by: unreviewed-seed` (placeholders pending clinical review),
`licence: restricted-link-only` with an empty summary, `evidence_grade: null`, empty
`required_biomarkers`, and EMPTY `interventions`. Empty interventions = "a guideline exists for
this condition/stage; oncoBuddy asserts no drug". The connector therefore returns such entries for
any drug query. No drug recommendation was taken from any guideline.

## Entry kinds

- Scope-only (`reviewed_by: unreviewed-seed`): a link plus a machine-readable scope, no
  interventions; served.
- Hand-curated with interventions (a named reviewer, `reviewed_on`, stages, a stated line and a
  `recommendation_ref`): optional; served. None ship today. For most questions the agent reads the
  source itself through the browser handoff instead.

## Edition check

Records with a DOI from ESMO or ASCO get `payload.edition_check`, from Crossref (only the registry's
DOI and title are sent). Statuses: `current` (no newer edition or notice found), `corrected`
(correction, erratum, addendum or expression of concern), `newer_found` (same-title full article
in the same journal, newer date; `newer` gives its DOI), `retracted`, `unknown` (timeout, HTTP error
or bad answer; never cached) and `not_checked` (NCCN, DGS, disabled, or beyond the cap).
**`current` means "no newer edition found", not "verified current"**: Crossref's relationships are
incomplete, so a newer edition under another title is missed. Limits: 6 records per call, requests
serialised (3 at a time with `ONCOBUDDY_EMAIL`), 8-second deadline per batch, 7-day cache; unfinished
records are `unknown` and complete on later calls. `ONCOBUDDY_EDITION_CHECK=off` disables it.

## Edition, currency and inferred scope

- `valid_as_of` (and `published`) is the date of the CITED EDITION, i.e. its online (Epub)
  publication date. It is NOT a statement that the guideline is still current. Every record
  carries `may_be_superseded: true` and `edition_note: "cited edition; later versions or updates
  may exist - check the issuer"`.
- `stages` are INFERENCES from the title ("early" => I-III, "metastatic" => IV), not statements
  made by the guideline text.
- The connector marks each returned record `scope_only` (no drug named) and, when a drug was
  queried, `drug_match` = `named` or `scope-only`.

## PubMed verification (2026-11-01, search by DOI)

Epub dates read from PubMed: early breast PMID 38101773 "Epub 2023 Dec 13" (Ann Oncol 2024
Feb;35(2):159-182); mCRC PMID 36307056 "Epub 2022 Oct 25" (2023 Jan;34(1):10-32); oncogene-addicted
NSCLC PMID 36872130 "Epub 2023 Jan 23" (2023 Apr;34(4):339-357); non-oncogene-addicted NSCLC PMID
36669645 "Epub 2023 Jan 17" (2023 Apr;34(4):358-376). These supersede the month-only "unknown"
dates below; the Epub dates are used for `published` and `valid_as_of`. Titles matched.

## Verifications (all 2026-11-01)

The esmo.org guideline pages (esmo.org/guidelines/...) returned HTTP 404 to the fetch tool, so
they could not be used. The publisher DOI pages redirect (302) to Elsevier's linking hub, which
returned no content to the fetch tool. Bibliographic facts were therefore read from institutional
repository records of the same articles. Licence: none of these records state a licence for the
article ("Refer to copyright notice on published article" / "Copyright, Licence and Reuse" with no
licence), so every entry is `restricted-link-only` (no text copied, summary empty). A CC licence
was NOT assumed.

1. Early breast cancer. Read https://publications.petermac.org/Publications/1450 : title "Early
   breast cancer: ESMO Clinical Practice Guideline for diagnosis, treatment and follow-up";
   Loibl S et al., ESMO Guidelines Committee; Annals of Oncology 35(2):159-182, publication date
   shown only as "2024-02"; DOI 10.1016/j.annonc.2023.11.016; licence "Refer to copyright notice".
   Dates stored as "unknown" because no day is shown. Stages I-III from the word "early" in the
   title. Condition C50.
2. Metastatic colorectal cancer (2022 edition; superseded 2026-11-02, the registry now holds the 2026 edition). Read https://eprints.whiterose.ac.uk/id/eprint/198618/ : title
   "Metastatic colorectal cancer: ESMO Clinical Practice Guideline for diagnosis, treatment and
   follow-up"; "Published (online): 25 October 2022", "Published: January 2023"; Annals of
   Oncology 34(1):10-32; licence not shown. DOI 10.1016/j.annonc.2022.10.003 (from a search result
   and confirmed by the DOI resolving, 302, to Elsevier pii S0923753422041928). `published` and
   `valid_as_of` are both the online date 2022-10-25 (the page states no "current as of" date; the
   guideline has since been updated via an ESMO living guideline, per a search result, not
   verified on a primary page). Stage IV from "metastatic". Conditions C18, C19, C20.
3. Oncogene-addicted metastatic NSCLC. Read https://publications.petermac.org/Publications/352 :
   title "Oncogene-addicted metastatic non-small-cell lung cancer: ESMO Clinical Practice
   Guideline for diagnosis, treatment and follow-up"; "2023-04"; Annals of Oncology 34(4):339-357;
   DOI 10.1016/j.annonc.2022.12.009; licence "Refer to copyright notice". Dates "unknown".
   Stage IV from "metastatic". Condition C34.
4. Non-oncogene-addicted metastatic NSCLC. Read https://publications.petermac.org/Publications/353 :
   title "Non-oncogene-addicted metastatic non-small-cell lung cancer: ESMO Clinical Practice
   Guideline for diagnosis, treatment and follow-up"; "2023-04"; Annals of Oncology 34(4):358-376;
   DOI 10.1016/j.annonc.2022.12.013; licence "Refer to copyright notice". Dates "unknown".
   Stage IV from "metastatic". Condition C34.

The seed `url` is the DOI link (the stable pointer to the publisher's page).

## Known limits

- Not verified against the publisher's own page (see above); reviewers must check each link and
  that the guideline is still the current edition (ESMO publishes living-guideline updates).
- DGS norms are listed from the DGS norms index only (see Coverage gaps: DGS); the norm text itself
  is never read by the server.
- Registry entry dates (`published`, `valid_as_of`) are Crossref record-creation dates, approximate
  and not "current as of" dates. The four 2026-11-01 verified seeds above were first dated from
  PubMed Epub dates; the refresh since 2026-11-02 uses the Crossref rule.
- Registry entries (not only the early seeds) are unverified against the publisher's page; a
  reviewer must check each link and that it is the current edition.

## ESMO permission (recorded 2026-11-01)

The maintainer reports having ESMO's permission to **read every ESMO guideline available**. This is
the basis for the agent reading ESMO guidelines in the user's own browser. It is a permission to read,
not to reproduce: ESMO entries stay `licence: restricted-link-only` with an empty `summary`, and no
guideline text is copied into the repository. The written terms are held by the maintainer and are
not stored here; if they allow more (for example short own-words summaries), say so and the entry
format can be widened deliberately.

ESMO's web pages block automated fetching, so the server never reads them; the agent reads them in
the user's browser after the user agrees.

## Browser access recipes

Every ESMO registry entry carries `access: {method: browser, reason: bot_check, ...}`. The
recipe says only where to look (the Recommendations list and the treatment algorithm for the
patient's setting) and what to report (each named drug with its printed evidence level and the
setting). It names no drug and no dose; a validator rejects dose patterns. An agent following the
protocol in `plugin/skills/oncobuddy/SKILL.md` reads the guideline in the user's own browser after
the user agrees, and labels the result `agent-read`.

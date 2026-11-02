# oncoBuddy v2 slice E: more guideline sources and a labelled reference group - Design Spec

Date: 2026-11-02
Status: Draft, awaiting user review
Builds on: `2026-11-02-source-discovery-design.md`, `2026-11-01-browser-handoff-design.md`,
`2026-11-01-oncobuddy-design.md` (region model, section 2.1)

## 1. Purpose

The registry holds 39 ESMO guidelines. A Portuguese oncologist also needs Portuguese national norms
(DGS) and, as clearly labelled reference, the guidelines of other major bodies: NCCN and ASCO (US)
and NICE (UK). The region-safety rule says records tagged with a region outside the query's scope
chain are never eligible; this slice keeps that rule intact and adds one narrow, explicit
exception: **reference-only guideline links**, shown in a separate, labelled group.

Success: `guidelines_for("C50.9", "PT")` returns the ESMO and DGS guidelines in the normal results
and, separately, the NCCN, ASCO and NICE breast-cancer guidelines under `reference_elsewhere`, each
carrying its issuing region and the access instructions. Nothing from a US or UK record ever enters
the normal results, `no_data`, option matching or any other section.

## 2. Non-goals and queue

Queued after this slice, in order: (1) a Crossref/PubMed edition check at query time; (2) plugin
packaging; (3) NICE technology appraisals (drug-specific; excluded here because their titles name
drugs and they are a different kind of source).

Also out of scope: any server-side fetching of these sources, reading beyond index pages during
refresh, copying guideline text, using reference-only records in `treatment_options` matching or
options, and changes to the region-safety rule for every other kind of record (medicines, trials,
literature, coding).

## 3. Region tree

`regions.yaml` gains `US: global` and `UK: global` (siblings of EU). Chains for PT, ES and EU are
unchanged; a query for region US or UK has chain `[US, global]` or `[UK, global]`.

## 4. Reference-only records

- New optional guideline field `reference_only: bool` (default false), carried in the record
  payload. The loader rejects `reference_only: true` when the entry's region is `global` or inside
  the EU subtree (those are already eligible), with a message naming the field only.
- The reconciler, when building the `guidelines` section only, moves records that are
  `kind == "guideline"`, flagged `reference_only` and ineligible for the target region into a new
  `Section.reference_elsewhere: list[Record]` (default empty). They are excluded from `records`,
  from `no_data` and from every note about "showing EU-level". Ineligible records of any other
  kind, or ineligible guidelines without the flag, are dropped exactly as today.
- The section gets one note when the group is non-empty: `"N reference guideline(s) issued
  outside <query region>'s scope (<regions>) are listed separately; national approval,
  reimbursement and practice may differ"`.
- Ordering inside the group: issuing region (alphabetical), then fewer condition categories first,
  then connector order.
- `get_record` can fetch them (they are remembered with the query's region and chain).

## 5. Tools

- `guidelines_for` and `regional_status` return `reference_elsewhere` inside the `guidelines`
  section. No new tool, no new input.
- `treatment_options`: `OptionsResult.reference_elsewhere` repeats the group for the profile's
  condition (also on the early-return path when no in-scope guideline exists). These records are
  never used for matching, options, exclusions or caveats. `read_plan` is present when any listed
  guideline (in scope or reference) has `access.method: browser`.
- Agent protocol rule 4 gains: when a record comes from `reference_elsewhere`, state its issuing
  region next to the citation. `SKILL.md` is regenerated from the constant (the sync test stays).

## 6. Sources

| Source | Region | Group | Entry id | How built | `access.reason` | Date |
|---|---|---|---|---|---|---|
| ESMO | EU | normal | `esmo-*` | done (39 entries) | `bot_check` | Crossref created |
| DGS norms (oncology) | PT | normal | `dgs-<norma>-<year>` | agent in Chrome, DGS norms index | `no_api` | norm date on the index |
| NICE clinical guidelines (NG/CG, cancer) | UK | reference | `nice-<code>` | agent in Chrome, NICE cancer guidance index | `no_api` | published date on the index |
| NCCN guidelines (by tumour type) | US | reference | `nccn-<slug>` | agent in Chrome, NCCN public index | `login_required` | `unknown` (living documents) |
| ASCO guidelines | US | reference | `asco-<slug>` | agent in Chrome, ASCO guideline index; DOI and date from Crossref | `bot_check` | Crossref created |

Common entry shape: the existing scope-only guideline file (`reviewed_by: unreviewed-seed`,
`interventions: []`, `licence: restricted-link-only`, `summary: ""`, `required_biomarkers: {}`,
`line_of_therapy: any`, shared `access` browser block, quoted `last_verified`), `conditions` from
the shared topic table (organ subset only when the title names one), `stages` only when the title
states a scope. Titles are recorded as published; none names a recommendation. The `read_recipe`
for `login_required` sources adds the step "If the page asks you to log in, tell the user and
stop; do not log in for them".

The topic table (`esmo_topics.yaml`) is shared by all sources; its name is kept (renaming is a
deferred minor). Each source's topics are normalised to its keys; topics that are not in it are
skipped and listed.

Allowed hosts (`ALLOWED_HOSTS` in `core/access.py`) gain: `www.nccn.org`, `www.asco.org`,
`ascopubs.org`, `www.nice.org.uk`, `normas.dgs.min-saude.pt`, `www.dgs.pt`.

## 7. Verification

`scripts/verify_dois.py` becomes `scripts/verify_registry.py` (the old name kept as a thin alias for
one release): DOI entries (ESMO, ASCO) get the handle and Crossref checks, with the allowed journal
set per issuer (ESMO: Annals of Oncology, ESMO Open; ASCO: Journal of Clinical Oncology);
entries without a DOI must be https on an allowlisted host and are counted in the output as
"not machine-checkable (N)". The registry test pins, per issuer: region, `reference_only`,
id prefix, access reason, conditions rule, and that no entry names a drug.

## 8. Testing

- Regions: US and UK load; chains for PT, EU, US and UK as stated; existing region-safety tests
  unchanged and green.
- Loader: `reference_only` accepted for US and UK, rejected for EU, PT, ES and global; payload
  carries it.
- Reconciler: a US reference guideline never appears in `records`, in `no_data`, in notes about
  "showing EU-level", nor in any non-guidelines section; it appears in `reference_elsewhere` for PT
  and EU queries, not for a query where it is already eligible; an unflagged US guideline and a US
  medicine are dropped; a source failure still yields `source_unavailable`, never an empty group
  presented as "none".
- Tools: `guidelines_for` and `treatment_options` carry the group; `treatment_options` options,
  exclusions and caveats are identical with and without reference records; `read_plan` present
  with only reference records; the early-return path still lists them; the profile-privacy test
  stays green.
- Protocol: rule 4 text, skill sync.
- Registry: per-issuer pins (section 7); DOI verifier offline tests for ASCO's journal set.

## 9. Risks

- A reference record read as a recommendation for Portugal: mitigated by the separate group, the
  section note, the protocol's region labelling and the fixed `read_plan` closing line.
- NCCN and ASCO terms and access: links only, no text stored; NCCN needs the user's own login in
  their browser, and the agent must not log in for them.
- Index pages change layout; the runbook names sections, not coordinates, and `last_verified`
  dates make staleness visible.
- US and UK guidelines may recommend products not authorised in the EU; the group note says
  approval differs and `regional_status` remains the source for EU authorisation.

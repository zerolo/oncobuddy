# INFARMED (Portuguese medicines authority): link-only pointers

Verified: 2026-11-01 (feasibility spike). Code: `src/oncobuddy/connectors/infarmed.py`.

## What oncoBuddy does

For region PT, the `authorisation` and `reimbursement` sections each carry ONE `pointer` record
per drug (INN). It is a link, not data: `kind: pointer`, `source: INFARMED`, `region: PT`,
`valid_as_of: unknown`, `licence: restricted-link-only`, payload
`status: "not retrieved - read in the user's browser"` plus two links, a fixed note and an
`access` block (`method: browser`, `reason: no_api`, a short read recipe, `entry_url` = the
pointer's own link). It never states that a
medicine is authorised, reimbursed or approved. Pointers sort after real records in a section
and are ignored when deciding whether a record exists for the target region (so the EMA
"showing EU-level" note still appears).

A section may hold a pointer AND a `no_data` reason: `no_data` reflects the real sources only
(EMA failure gives `source_unavailable`, no EMA record gives `not_found`; the PT `reimbursement`
section has no real source, so it reads `not_found`). Pointer-only sections carry the note
"INFARMED: not retrieved by oncoBuddy; read the status in the user's browser (see access)".

## What is NOT done

- No HTTP request to any INFARMED host, ever. The connector has no HTTP client.
- Nothing from INFARMED is fetched, scraped, cached or stored.
- No Portuguese authorisation or reimbursement status is reported.

## Findings behind that decision

| Source | URL | Finding |
|---|---|---|
| Infomed | https://extranet.infarmed.pt/INFOMED-fo/ | JSF search UI, no export or API. Terms (https://extranet.infarmed.pt/INFOMED-fo/guia-condicoes-utilizacao.xhtml): use is "livre, gratuita", but the user may not, "sem autorizacao escrita do Infarmed, total ou parcialmente, explorar, publicar, reproduzir, distribuir, vender ou alugar" the information. Written permission: cimi@infarmed.pt. |
| robots.txt | https://extranet.infarmed.pt/robots.txt | `User-agent: *` / `Disallow: /` |
| SIATS tracker | https://www.infarmed.pt/web/infarmed/tracker-de-processos | Public form (beta, launched April 2026) over reimbursement and hospital pre-evaluation processes, searchable by active substance. No export or API and no reuse terms found. Whether final outcomes are shown is UNVERIFIED. |
| CITS bulk database | https://www.infarmed.pt/web/infarmed/cedencia-de-bases-de-dados-de-medicamentos | Official bulk route; access must be requested and paid first. Reported fee: public administration free, other entities EUR 150 (+VAT) per year (as reported on the page, UNVERIFIED). File schema and redistribution rights are not readable without access (UNVERIFIED). |
| dados.gov.pt / Portal da Transparencia | https://dados.gov.pt/api/1/organizations/?q=infarmed | Aggregate statistics only (spend, biosimilar share); no per-product status. Not used. |

## What would unlock real data

Written permission from INFARMED (cimi@infarmed.pt) or a CITS licence. The project owner has
decided not to request either for now.

## Future connector design (only after permission or licence)

- Ingest the CITS file on a schedule, join on active substance (INN), with the registration
  number or CNPEM as product key.
- Emit real `authorisation` (national marketing authorisation, marketed flag) and
  `reimbursement` (co-payment rate, price) records for PT, with provenance and the licence
  terms of the agreement; keep hospital evaluation outcomes as links to SIATS.
- Keep pointers as the fallback when no record exists.
- Do not cache or redistribute beyond what the written terms allow.

## Read permission and the browser handoff (recorded 2026-11-01)

The maintainer reports having INFARMED's permission to **read** its data. This is a permission to
read, not to reproduce or distribute: INFARMED's terms still forbid reproduction without written
permission, so the agent protocol's short-statements rule is binding (authorisation and marketing
status, or the SIATS evaluation outcome, each with its date; never lists or copied tables). The
written terms are held by the maintainer and are not stored here. Infomed has no export or API and
its robots rule disallows automated access, so the pointers declare `method: browser`: the agent
asks the user, then reads the page in the user's own browser. The server still makes no request to
any INFARMED host and stores nothing.

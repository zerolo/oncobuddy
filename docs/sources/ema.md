# EMA medicines data (centralised procedure)

Verified: 2026-11-01 (live download of the file and fetch of the EMA pages below).

## Source

- Documentation page: https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format
- Medicines JSON (used by `EmaConnector`, `EMA_MEDICINES_JSON_URL`):
  https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json
- Same data as Excel tables: https://www.ema.europa.eu/en/medicines/download-medicine-data
  (the page states the JSON and table data are the same).
- Not used: post-authorisation procedures file
  `medicines-output-post_authorisation_json-report_en.json`.

The file is ~6.8 MB (2754 records on 2026-11-01, human and veterinary). The CDN
rate-limits aggressively (a first request returned HTTP 429 with `retry-after: 10`);
the connector downloads once per instance and treats 429 as `SourceUnavailable`.

## Scope and semantics

- The dataset covers ONLY the EU centralised procedure. Nationally authorised products
  (e.g. Portuguese national procedures) are NOT in it, so `[]` means "not in the EU
  central register", not "not authorised". Every record carries
  `payload["procedure"] = "EU centrally authorised"`.
- `valid_as_of` is the per-medicine content revision date (`last_updated_date`), NOT the
  date the authorisation status took effect. `payload["dataset_timestamp"]` is the file's
  `meta.timestamp` (omitted if absent).
- `entities.drugs` is the row's INN list (split on `;`), even if matched via a salt name
  in `active_substance`.
- Rows must have non-empty string `ema_product_number`, `medicine_url`, `medicine_status`;
  otherwise the whole source is treated as unavailable (fail closed).
- The parsed index is kept in memory for 12 h (`max_age_seconds=43200`), then re-downloaded;
  a failed refresh raises `SourceUnavailable` rather than serving stale data.

## Update cadence

EMA page: "The website automatically updates these JSON data files twice a day, at
06:00 and 18:00 Amsterdam time". `meta.timestamp` in the file is UTC.

## Format

Top level: `{"meta": {"total_records", "timestamp"}, "data": [ {...}, ... ]}`.
Every record has the same flat string-valued keys. Dates are `dd/mm/yyyy`; absent
values are the empty string `""`. Multi-substance fields use `;` as delimiter.

Fields relied on:

| Field | Use |
|---|---|
| `category` | `Human` / `Veterinary`; only `Human` is indexed |
| `name_of_medicine` | `payload.product` |
| `ema_product_number` | record id (`ema:EMEA/H/C/...`) |
| `medicine_status` | `payload.status` verbatim (Authorised, Withdrawn, Refused, Suspended, Revoked, Lapsed, Expired, Opinion, Application withdrawn, ...) |
| `international_non_proprietary_name_common_name` | INN, match key (split on `;`) |
| `active_substance` | secondary match key (split on `;`; often a salt name) |
| `atc_code_human` | `entities.atc` |
| `therapeutic_area_mesh` | `payload.therapeutic_area` |
| `orphan_medicine` | `payload.orphan` (`Yes`/`No`) |
| `marketing_authorisation_date` | `payload.authorisation_date` |
| `last_updated_date` | `valid_as_of` (the medicine revision date) |
| `medicine_url` | `source_url` and `payload.indication_text_url` |

`therapeutic_indication` (indication text) exists in the file but is deliberately NOT
copied; the connector links to the EPAR page instead.

Data observations (2026-11-01): `marketing_authorisation_date` is empty for 522 records
(including 335 `Authorised` ones, e.g. "Enzalutamide Accordpharma", whose decision
date is present but MA date is not) and `last_updated_date` is empty for 89 records.
Missing dates therefore occur in real data; the connector gives `valid_as_of="unknown"`
and omits `authorisation_date`. `medicine_status` is often not "Authorised" (withdrawn,
refused, opinion-only), so presence in the file never implies approval.

## Licence / reuse

No licence or reuse statement on the JSON download page itself (only the footer
"(c) 1995 - 2026 European Medicines Agency"). The EMA legal notice
(https://www.ema.europa.eu/en/about-us/legal-notice, section "copyright and limited
reproduction notices") states:

- "Information and documents made available on EMA's webpages are public and may be
  reproduced and / or distributed, totally or in part, irrespective of the means and /
  or the formats used ... for non-commercial and commercial purposes, provided that EMA
  is always acknowledged as the source of the material. Such acknowledgement must be
  included in each copy of the material."
- "Citations may be made from such material without prior permission, provided the
  source is always acknowledged."
- "The above-mentioned permissions do not apply to content supplied by third parties."

No Creative Commons / SPDX identifier is stated, so the record `licence` tag is
**`EMA-reuse-notice`** = the terms above (reuse incl. commercial permitted with EMA
acknowledged as source). Text reuse is permitted, so `restricted-link-only` is not
used; the connector still links rather than copies indication text. `source = "EMA"`
on each record provides the required acknowledgement.

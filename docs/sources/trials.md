# Trials: ClinicalTrials.gov (API v2); EU CTIS not covered in v1

Verified: 2026-11-01. Live requests were made to the API (the fixture is a real response).
Parameter definitions come from the official OpenAPI spec served by the API itself.

## Pages consulted

- OpenAPI spec (official, live): https://clinicaltrials.gov/api/oas/v2 (API version 2.0.5,
  `https://clinicaltrials.gov/api/v2/version` returned `dataTimestamp 2026-10-30T09:00:04`)
- Metadata endpoint (field names): https://clinicaltrials.gov/api/v2/studies/metadata
- API docs page https://clinicaltrials.gov/data-api/api and Terms
  https://clinicaltrials.gov/about-site/terms-conditions are JavaScript single-page apps: a
  scripted fetch returns only the page shell, so their text could NOT be read verbatim.
  What is recorded below about terms comes from (a) a web-search summary of the official terms page
  (paraphrase, not a quote) and (b) the NLM download terms, read in full:
  https://www.nlm.nih.gov/databases/download/terms_and_conditions.html
- NLM web policies: https://www.nlm.nih.gov/web_policies.html

## Endpoint and parameters (OpenAPI spec + live-verified)

`GET https://clinicaltrials.gov/api/v2/studies`, JSON by default, no key.

- `query.cond` (conditions, Essie expression syntax; spec example `(head OR neck) AND pain`),
  `query.intr` (intervention / treatment), `query.term`, `query.locn`, etc.
- `filter.overallStatus`: "comma- or pipe-separated list of statuses" (enum values used:
  `RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`). The connector sends them pipe
  separated; live-verified.
- `pageSize` (spec: default 10, "coerced down to 1,000, if greater"), `countTotal=true` returns
  `totalCount` with the first page, `pageToken`/`nextPageToken` for further pages, `fields`
  (pipe- or comma-separated piece/field names; unspecified = everything).
- `query.cond=cancer OR neoplasm` live-verified to work (totalCount 847 with
  `query.intr=pembrolizumab` and the status filter above on 2026-11-01).

Response (live-verified, with `fields=NCTId|BriefTitle|OverallStatus|Phase|LeadSponsorName|
LastUpdatePostDate|LocationCountry|LocationStatus`): `{totalCount, studies[], nextPageToken?}`;
each study `protocolSection.{identificationModule.{nctId,briefTitle},
statusModule.{overallStatus,lastUpdatePostDateStruct.date},
sponsorCollaboratorsModule.leadSponsor.name, designModule.phases[],
contactsLocationsModule.locations[].{country,status}}`. A study with no sites has an empty or
absent `contactsLocationsModule`. `lastUpdatePostDateStruct.date` is `YYYY-MM-DD`
(other date fields can be `YYYY-MM`). Country is the English country name (`Portugal`,
`Spain`, `United States`...).

## Usage limits

The OpenAPI spec states no rate limit. Third-party pages repeat "about 50 requests per minute
per IP"; this is NOT confirmed by an official page I could read. The connector makes one request
per query and reads one page. No local throttle is implemented; HTTP 429 maps to
`SourceUnavailable`.

## Licence / terms

- Terms page (summary of its content as returned by a search tool, NOT verbatim): credit
  ClinicalTrials.gov as the source, keep the data current, say when it was processed and
  disclose modifications; the data carry international copyright outside the US and some
  records may contain third-party material; the terms apply as long as data is retained.
- NLM download terms (verbatim): acknowledge NLM as the source with "Courtesy of the U.S.
  National Library of Medicine"; do not imply NLM endorsement; redistributors "maintain the most
  current version of all distributed data, or make known in a clear and conspicuous manner" that
  they do not reflect the most current data; no warranty.
- Decision: records carry source name, a link to the study page, `valid_as_of` (update date) and
  `retrieved_at`; only ids, short brief title, phase, statuses and sponsor name are copied (no
  summaries or eligibility text). Licence tag `ClinicalTrials.gov-terms` (project tag, not a claim
  that the data are public domain). Not legal advice; re-read the live terms page in a browser
  before wide redistribution.

## Search design and limits

`query.intr=<drug INN>` (must fullmatch `[a-z][a-z-]{1,39}`) AND `query.cond=<term>` AND
`query.locn=<country name>`; term is a resolver label (letters, digits, spaces, hyphens only) or
the fixed `cancer OR neoplasm` (stated in `payload["search"]`). `query.condition` is an ICD code,
which the API cannot search. Status filter: recruiting-type only. One page of `page_size`
(default 50, 1..1000) studies; the rest are not read.

Location scoping (verified 2026-11-01): the OpenAPI spec defines `query.locn` ("Location terms"
query in Essie syntax, "LocationSearch Area"). Without it a single unfiltered page of ~850
studies may hold no PT/ES trial. Live check: `query.locn=Portugal` reduced `totalCount` from 847
to 36 and the first studies all listed Portugal among their sites. `query.locn` is a text search
over location data, so the connector still keeps only studies whose location `country` equals
the target country's name; this match is exact and case-sensitive
(`COUNTRY_TO_REGION`: Portugal -> PT, Spain -> ES; the API's names are English, e.g.
"Turkey (Türkiye)").

Region behaviour: only country-level regions are served (PT, ES). For EU, global (or an unknown
region) NO request is made and the result is `[]`: trials are available per country region only
in v1. Emitted records are only those of the target country (other tree countries on the same
trial are not emitted; the reconciler would drop them anyway).

Truncation: `search.truncated` is true when the response has a `nextPageToken` or `totalCount` >
studies returned. If it is truncated and no record survives filtering, the connector raises
`SourceUnavailable("... results truncated; coverage incomplete")` instead of returning `[]`.
`search.total`, `search.returned`, `search.location_filter` are always recorded.

Site status: payload `site_statuses_in_region` (sorted unique real statuses of sites in the
target country), `sites_in_region`, `sites_without_status`, and `recruiting_in_region` (true only
if some site there is RECRUITING, NOT_YET_RECRUITING or ENROLLING_BY_INVITATION; sites with no
status do not count). The study-level `overall_status` is NOT a site status: the fixture shows a
PT study with RECRUITING, NOT_YET_RECRUITING and WITHDRAWN sites.

## CTIS (EU Clinical Trials Information System): NOT covered in v1

https://euclinicaltrials.eu describes itself as a website with information on trials in the
EU/EEA; its page mentions no public API, bulk download or open data. An EMA presentation on the
public portal (https://www.ema.europa.eu/en/documents/presentation/presentation-new-version-public-portal-f-scotti_en.pdf)
lists download of a trial or of a search result and an RSS alert feed, and lists an accessible
API as a stakeholder expectation marked "Not yet!". A scraper vendor claims an unofficial API
exists; that is not EMA documentation and is not used. So: no stable documented machine-readable
CTIS access was found; CTIS is not covered in v1 and the trials section must say so.

## Fixtures

`tests/fixtures/ctgov_studies.json`: real API response for `query.intr=pembrolizumab`,
`query.cond=cancer OR neoplasm`, recruiting-type status filter, `pageSize=50`, `fields` as above,
fetched 2026-11-01. EDITED ONLY by dropping all but three of the 50 studies (the file keeps the
real `totalCount` 847 and `nextPageToken`): NCT04613596 (sites in Portugal and Spain among 770),
NCT07853690 (United States only), NCT05077215 (no locations). No study content was altered.

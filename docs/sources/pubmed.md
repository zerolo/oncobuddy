# PubMed (NCBI E-utilities)

Verified: 2026-11-01. Live requests were made to the endpoints below (fixtures are the real
responses). Policy text was read from the pages listed. The E-utilities Help Manual
(https://www.ncbi.nlm.nih.gov/books/NBK25497/ and .../NBK25499/) was NOT readable: NCBI served a
browser-verification interstitial to scripted clients. Parameter behaviour from that manual is
therefore confirmed only by live calls, and is marked "live-verified" below.

## Pages consulted

- NCBI Website and Data Usage Policies and Disclaimers (scripting guidelines, abstract copyright):
  https://www.ncbi.nlm.nih.gov/home/about/policies/
- NCBI Insights, "New API Keys for the E-utilities" (rate limits with/without a key):
  https://ncbiinsights.ncbi.nlm.nih.gov/2017/11/02/new-api-keys-for-the-e-utilities/
- NLM Web Policies (copyright): https://www.nlm.nih.gov/web_policies.html
- NLM data download terms (attribution; no legal advice on copyright):
  https://www.nlm.nih.gov/databases/download/terms_and_conditions.html
- PubMed help (full-text links are not NCBI's to license): https://pubmed.ncbi.nlm.nih.gov/help/

## Endpoints (live-verified, JSON via `retmode=json`)

- esearch: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi`
- esummary: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi`
- Policy page: "Send E-utilities requests to https://eutils.ncbi.nlm.nih.gov, not the standard
  NCBI Web address."

Parameters used: `db=pubmed`, `term`, `retmode=json`, `retmax`, `sort=pub_date`, `tool`,
`email`, optional `api_key`; esummary: `db=pubmed`, `id` (comma-joined), `retmode=json`, `tool`,
`email`, optional `api_key`.

esearch response: `esearchresult.{count, retmax, retstart, idlist[], translationset,
querytranslation}`; a failing query adds `esearchresult.ERROR` (the connector fails closed on it).
esummary response: `result.uids[]` plus `result["<uid>"]` objects.

## Usage policy (policies page, "Guidelines for Scripting Calls to NCBI Servers")

- "Run retrieval scripts on weekends or between 9 pm and 5 am Eastern Time weekdays for any
  series of more than 100 requests." The connector issues 2 requests per query.
- "Make no more than 3 requests every 1 second."
- "Use the URL parameter email, and tool for distributed software, so that we can track your
  project and contact you if there is a problem."

## Rate limits (NCBI Insights post)

- Without a key: more than 3 requests/second per IP address gets an error. Applies to any mix of
  EInfo, ESearch, ESummary, EFetch, ELink, EPost, ESpell, EGquery.
- With an `api_key`: 10 requests/second, counted per key regardless of IP; higher rates by
  contacting NCBI.
- Connector: min interval between requests 0.34 s (no key) or 0.1 s (key), serialised by a
  per-instance lock. This is per process; several processes on one IP can still exceed the limit.
  HTTP 429 is mapped to `SourceUnavailable`.

## Licence / reuse

- Policies page: "NLM does not claim the copyright on the abstracts in PubMed; however, journal
  publishers or authors may. NLM provides no legal advice concerning distribution of copyrighted
  materials, consult your legal counsel."
- NLM download terms: credit NLM as source ("Courtesy of the U.S. National Library of Medicine");
  do not imply endorsement; NLM provides no warranty. The connector's records name `source =
  "PubMed"` and link to the PubMed page.
- Decision: the connector never copies abstracts. Records carry only bibliographic metadata
  (PMID, title, journal, dates, publication types, DOI) plus a link to PubMed. Licence tag:
  `NLM-PubMed-metadata` (a project tag meaning "NLM bibliographic metadata; abstracts and full
  text excluded; abstract rights may belong to publishers/authors"). This is a design decision,
  not legal advice.

## Search term and scope

Term: `"<drug>"[Title/Abstract] AND neoplasms[MeSH Terms]`, optionally
` AND "<label>"[Title/Abstract]`. `<drug>` must match `^[a-z][a-z-]{1,39}$` (normalised INN);
the optional label comes from a resolver and must contain only letters, digits, spaces and
hyphens. Free text never reaches NCBI. The term is built per request and is never part of a
Record, so it is never cached.

- `sort=pub_date`: most recent first (live-verified; the response's first ids had the latest
  dates). Chosen because recency matters for clinical literature; relevance ordering can bury
  new reports. Consequence: top results are the newest mentions, not the best evidence.
- Limits: results are at most `max_results` (default 10) of possibly thousands (`count`), the
  MeSH filter `neoplasms` can miss un-indexed new records (MEDLINE indexing lags), and Title/
  Abstract matching includes passing mentions. A record is a literature pointer, NOT evidence of
  efficacy or approval. `region = "global"`.

## esummary fields (live-verified on 2026-11-01)

Present: `uid`, `pubdate` (e.g. "2026 Dec 31", "2026 Dec"), `epubdate`, `source` (journal
abbreviation), `fulljournalname`, `title`, `authors[{name,authtype,clusterid}]`, `lastauthor`,
`volume`, `issue`, `pages`, `lang[]`, `pubtype[]`, `articleids[{idtype,idtypen,value}]`
(idtypes seen: pubmed, pmc, pmcid, doi), `history[]`, `attributes[]` ("Has Abstract"),
`sortpubdate` ("YYYY/MM/DD HH:MM"), `elocationid`, `recordstatus`, `pubstatus`.

Mapping: `payload = {pmid, title, journal (source), pubdate, pubtypes, doi?}`.

`valid_as_of` rule (publication date, NOT a currency/review date): candidates are
(a) `sortpubdate` ONLY IF `pubdate` itself has a day (NCBI fills a day for month-only dates,
e.g. "2026 Dec" -> 2026/12/01), and (b) `epubdate` if it is a full "YYYY Mon D" date. Candidates
later than today are dropped (issue dates can be in the future: the fixture has pubdate
"2026 Dec 31" with epubdate "2026 May 13"). The EARLIEST remaining candidate wins, else
"unknown". Raw `pubdate` and `epubdate` stay in the payload.

Every record's `payload["search"]` = `{sort: "pub_date_desc", returned, total (esearch count or
null), filter: "neoplasms[MeSH Terms]", condition_label, warnings? }`. `warnings` (max 5, ASCII,
80 chars each) comes from esearch `warninglist`/`errorlist` `phrasesnotfound` and
`quotedphrasesnotfound`. Duplicate ids in `idlist` are collapsed; `max_results` must be an int
1..100.

## Fixtures

- `tests/fixtures/pubmed_esearch.json`: real esearch response, `retmax=3`, unedited.
- `tests/fixtures/pubmed_esummary.json`: real esummary for those 3 ids, unedited.
Both were fetched 2026-11-01 for `"pembrolizumab"[Title/Abstract] AND neoplasms[MeSH Terms]`,
`sort=pub_date`.

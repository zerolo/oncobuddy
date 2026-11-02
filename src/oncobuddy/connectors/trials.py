"""Trials connector: recruiting-type interventional studies from ClinicalTrials.gov (API v2).

Source details, terms, fields and the CTIS decision: docs/sources/trials.md.

Scope and limits:
- Only country-level regions (``REGION_TO_COUNTRY``: PT, ES) are served. For EU, global or any
  other region NO request is made and [] is returned. The request is scoped with
  ``query.locn=<country name>`` so the single page holds studies with a site there.
- ONE request per query, ONE page of at most ``page_size`` studies (no pagination).
  ``payload["search"]["truncated"]`` is true when the API offered a further page or ``total`` >
  returned. If truncated AND no record survives, SourceUnavailable is raised so a cut-off
  result never looks like "no trials".
- The search is ``query.intr=<drug INN>`` AND ``query.cond=<condition term>`` with
  ``filter.overallStatus`` limited to RECRUITING | NOT_YET_RECRUITING | ENROLLING_BY_INVITATION.
  ``query.condition`` is an ICD code, which the API cannot search; the condition term is either
  a validated label from an injected resolver or the fixed ``cancer OR neoplasm``. Both the term
  and its origin are stated in ``payload["search"]``.
- Records are emitted per study with at least one site in the TARGET region's country (exact,
  case-sensitive country-name match); other studies (US-only, no locations) yield NOTHING.
  A study-level overall status is not a site status: ``site_statuses_in_region`` carries the
  real per-site statuses, ``sites_without_status`` counts sites lacking one and
  ``recruiting_in_region`` is true only if a site there has a recruiting-type status.
- EU CTIS is NOT covered in v1 (no documented public API).
- Only identifiers, a short brief title, phase, statuses and the lead sponsor name are copied;
  no summaries, eligibility text or contact details.
- ``valid_as_of`` is the study's ``lastUpdatePostDate`` if it is a full, non-future date,
  otherwise "unknown"; it is never defaulted to today.
"""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.core.query import Query
from oncobuddy.core.records import Entities, Record
from oncobuddy.core.regions import RegionTree, UnknownRegion

CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
CTGOV_LICENCE = "ClinicalTrials.gov-terms"
SOURCE_NAME = "ClinicalTrials.gov"
STATUS_FILTER = ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION")
DEFAULT_CONDITION = "cancer OR neoplasm"
FIELDS = (
    "NCTId|BriefTitle|OverallStatus|Phase|LeadSponsorName|LastUpdatePostDate"
    "|LocationCountry|LocationStatus"
)
# ClinicalTrials.gov country names (the "country" of a location) -> region IDs in regions.yaml.
COUNTRY_TO_REGION: dict[str, str] = {"Portugal": "PT", "Spain": "ES"}
REGION_TO_COUNTRY: dict[str, str] = {v: k for k, v in COUNTRY_TO_REGION.items()}
RECRUITING_SITE_STATUSES = frozenset(STATUS_FILTER)

_REQUEST_TIMEOUT = httpx.Timeout(30.0)
_REQUEST_HEADERS = {"User-Agent": "oncobuddy-mcp/0.1 (+https://github.com/zerolo/onco-medbuddy; reference-data client)"}
_DRUG_RE = re.compile(r"[a-z][a-z-]{1,39}")
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-]{0,79}")
_NCT_RE = re.compile(r"NCT[0-9]{8}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAX_TITLE = 300


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _fail(what: str) -> SourceUnavailable:
    return SourceUnavailable(f"ClinicalTrials.gov {what}")


def _total(count: object) -> int | None:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count


def _update_date(status_module: dict, today: date) -> date | str:
    stamp = status_module.get("lastUpdatePostDateStruct")
    raw = stamp.get("date") if isinstance(stamp, dict) else None
    if not isinstance(raw, str) or not _DATE_RE.fullmatch(raw):
        return "unknown"
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return "unknown"
    return parsed if parsed <= today else "unknown"


class TrialsConnector:
    name = SOURCE_NAME
    section = "trials"

    def __init__(
        self,
        client: httpx.AsyncClient,
        tree: RegionTree,
        page_size: int = 50,
        condition_terms: Callable[[str], str | None] | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("page_size must be an int between 1 and 1000")
        self._client = client
        self._tree = tree
        self._page_size = page_size
        self._condition_terms = condition_terms
        self._today = today

    region_hint = "trials are searched per country region (e.g. PT, ES)"

    def supports_region(self, region: str) -> bool:
        """True only for country-level regions this connector can map to a CT.gov country."""
        return region in REGION_TO_COUNTRY and self._in_tree(region)

    def _in_tree(self, region: str) -> bool:
        try:
            self._tree.scope_chain(region)
        except UnknownRegion:
            return False
        return True

    def _condition(self, query: Query) -> tuple[str, str]:
        if query.condition and self._condition_terms is not None:
            label = self._condition_terms(query.condition)
            if isinstance(label, str) and _LABEL_RE.fullmatch(label.strip()):
                return label.strip(), "resolver"
        return DEFAULT_CONDITION, "fixed-neoplasm-default"

    async def _get_json(self, params: dict[str, str]) -> dict:
        try:
            resp = await self._client.get(
                CTGOV_URL, params=params, timeout=_REQUEST_TIMEOUT, headers=_REQUEST_HEADERS
            )
        except httpx.HTTPError as exc:
            raise _fail(f"request failed: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise _fail(f"request failed: HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise _fail("response is not valid JSON") from None
        if not isinstance(body, dict):
            raise _fail("response has unexpected shape")
        return body

    async def fetch(self, query: Query) -> list[Record]:
        if query.drug is None:
            return []
        if not _DRUG_RE.fullmatch(query.drug):
            raise ValueError("drug must be a normalised INN-shaped name")
        country = REGION_TO_COUNTRY.get(query.region)
        if country is None or not self._in_tree(query.region):
            return []  # trials are available per country region only (no call for EU/global)
        cond, cond_source = self._condition(query)
        body = await self._get_json(
            {
                "query.intr": query.drug,
                "query.cond": cond,
                "query.locn": country,
                "filter.overallStatus": "|".join(STATUS_FILTER),
                "pageSize": str(self._page_size),
                "countTotal": "true",
                "fields": FIELDS,
            }
        )
        studies = body.get("studies")
        if not isinstance(studies, list):
            raise _fail("response has unexpected shape")
        total = _total(body.get("totalCount"))
        truncated = _nonempty_str(body.get("nextPageToken")) or (
            total is not None and total > len(studies)
        )
        search = {
            "status_filter": list(STATUS_FILTER),
            "returned": len(studies),
            "total": total,
            "truncated": truncated,
            "page_size": self._page_size,
            "location_filter": country,
            "condition_term": cond,
            "condition_source": cond_source,
        }
        retrieved_at = datetime.now(UTC)
        today = self._today()
        records: list[Record] = []
        for study in studies:
            records.extend(self._to_records(study, query, retrieved_at, today, search))
        if truncated and not records:
            raise _fail("results truncated; coverage incomplete")
        return records

    def _to_records(
        self, study: object, query: Query, retrieved_at: datetime, today: date, search: dict
    ) -> list[Record]:
        if not isinstance(study, dict) or not isinstance(study.get("protocolSection"), dict):
            raise _fail("study has unexpected shape")
        ps = study["protocolSection"]
        ident = ps.get("identificationModule")
        status = ps.get("statusModule")
        if not isinstance(ident, dict) or not isinstance(status, dict):
            raise _fail("study has unexpected shape")
        nct, title, overall = (
            ident.get("nctId"),
            ident.get("briefTitle"),
            status.get("overallStatus"),
        )
        if not isinstance(nct, str) or not _NCT_RE.fullmatch(nct):
            raise _fail("study has invalid fields")
        if not _nonempty_str(title) or not _nonempty_str(overall):
            raise _fail("study has invalid fields")

        design = ps.get("designModule")
        phases: list[str] = []
        if isinstance(design, dict) and "phases" in design:
            raw = design["phases"]
            if not isinstance(raw, list) or not all(_nonempty_str(p) for p in raw):
                raise _fail("study has invalid fields")
            phases = [p.strip() for p in raw]

        sponsor: str | None = None
        sc = ps.get("sponsorCollaboratorsModule")
        lead = sc.get("leadSponsor") if isinstance(sc, dict) else None
        if isinstance(lead, dict) and _nonempty_str(lead.get("name")):
            sponsor = lead["name"].strip()

        locations: list = []
        cl = ps.get("contactsLocationsModule")
        if isinstance(cl, dict) and "locations" in cl:
            locations = cl["locations"]
            if not isinstance(locations, list) or not all(isinstance(x, dict) for x in locations):
                raise _fail("study has invalid fields")

        region = query.region
        statuses: list[str | None] = []
        for loc in locations:
            country = loc.get("country")
            if not isinstance(country, str) or COUNTRY_TO_REGION.get(country) != region:
                continue
            site_status = loc.get("status")
            statuses.append(site_status.strip() if _nonempty_str(site_status) else None)

        valid_as_of = _update_date(status, today)
        out: list[Record] = []
        if statuses:
            real = sorted({s for s in statuses if s})
            payload: dict = {
                "nct_id": nct,
                "title": title.strip()[:_MAX_TITLE],
                "phase": list(phases),
                "overall_status": overall.strip(),
                "site_statuses_in_region": real,
                "sites_in_region": len(statuses),
                "sites_without_status": sum(1 for s in statuses if s is None),
                "recruiting_in_region": any(s in RECRUITING_SITE_STATUSES for s in real),
            }
            if sponsor:
                payload["sponsor"] = sponsor
            payload["search"] = dict(search)
            out.append(
                Record(
                    id=f"nct:{nct}:{region}",
                    kind="trial",
                    entities=Entities(
                        drugs=[query.drug] if query.drug else [],
                        conditions=(
                            [query.condition]
                            if query.condition and search.get("condition_source") == "resolver"
                            else []
                        ),
                    ),
                    region=region,
                    source=SOURCE_NAME,
                    source_url=f"https://clinicaltrials.gov/study/{nct}",
                    valid_as_of=valid_as_of,
                    retrieved_at=retrieved_at,
                    licence=CTGOV_LICENCE,
                    payload=payload,
                )
            )
        return out

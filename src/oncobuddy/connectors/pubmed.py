"""PubMed connector: bibliographic metadata via NCBI E-utilities (esearch + esummary, JSON).

Source details, rate limits, fields and licence: docs/sources/pubmed.md.

Scope and limits:
- Records are literature POINTERS (PMID, title, journal, dates, publication types, DOI and a
  link to PubMed). They are not evidence of efficacy or approval. ``region`` is "global".
- The search is ``"<drug>"[Title/Abstract] AND neoplasms[MeSH Terms]`` (optionally
  ``AND "<label>"[Title/Abstract]``), sorted ``pub_date`` (newest first), at most
  ``max_results`` hits. Passing mentions match and un-indexed new records may be missed.
- Abstracts are NEVER copied: NLM does not claim copyright on PubMed abstracts but publishers
  or authors may. The licence tag documents that metadata-only reuse.
- ``valid_as_of`` is the EARLIEST non-future publication date among: ``sortpubdate`` (only if
  ``pubdate`` itself has a day) and ``epubdate`` (only if a full date). Otherwise "unknown".
  It is never defaulted; raw ``pubdate``/``epubdate`` stay in the payload.
- Every record carries ``payload["search"]`` (sort, returned, total, filter, condition label,
  optional esearch warnings) so a short list is not mistaken for the whole literature.
- The search term is built per request, is never put in a Record and thus never cached.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime

import httpx

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.core.query import Query
from oncobuddy.core.records import Entities, Record

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
PUBMED_LICENCE = "NLM-PubMed-metadata"
TOOL_NAME = "oncobuddy-mcp"
_REQUEST_TIMEOUT = httpx.Timeout(30.0)
_REQUEST_HEADERS = {
    "User-Agent": "oncobuddy-mcp/0.1 (+https://github.com/zerolo/onco-medbuddy; reference-data client)"
}
_KEY_MIN_INTERVAL_S = 0.1

_DRUG_RE = re.compile(r"[a-z][a-z-]{1,39}")
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-]{0,79}")
_PMID_RE = re.compile(r"[0-9]{1,12}")
_DAY_PREFIX_RE = re.compile(r"(\d{4} [A-Za-z]{3} \d{1,2})(?:\D.*)?")
_FILTER = "neoplasms[MeSH Terms]"
_MAX_WARNINGS = 5
_WARNING_KEYS = ("phrasesnotfound", "quotedphrasesnotfound")


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _full_date(value: object) -> date | None:
    """Parse "YYYY Mon D" (optionally followed by a non-digit suffix); None if not a real date."""
    if not isinstance(value, str):
        return None
    m = _DAY_PREFIX_RE.fullmatch(value.strip())
    if m is None:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y %b %d").date()
    except ValueError:
        return None


def _sort_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M").date()
    except ValueError:
        return None


def _valid_as_of(row: dict, pubdate: str, today: date) -> date | str:
    candidates: list[date | None] = [_full_date(row.get("epubdate"))]
    if _full_date(pubdate) is not None:  # sortpubdate is trusted only if pubdate has a day
        candidates.append(_sort_date(row.get("sortpubdate")))
    valid = [d for d in candidates if d is not None and d <= today]
    return min(valid) if valid else "unknown"


def _total(count: object) -> int | None:
    if isinstance(count, bool):
        return None
    if isinstance(count, int):
        return count if count >= 0 else None
    if isinstance(count, str) and count.isascii() and count.isdigit():
        return int(count)
    return None


def _warnings(result: dict) -> list[str]:
    out: list[str] = []
    for list_key in ("warninglist", "errorlist"):
        block = result.get(list_key)
        if not isinstance(block, dict):
            continue
        for key in _WARNING_KEYS:
            items = block.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str) and item.strip():
                    text = f"{key}: {item.strip()}".encode("ascii", "replace").decode("ascii")
                    out.append(text[:80])
    return out[:_MAX_WARNINGS]


class PubMedConnector:
    name = "PubMed"
    section = "literature"

    def __init__(
        self,
        client: httpx.AsyncClient,
        email: str | None = None,
        api_key: str | None = None,
        max_results: int = 10,
        condition_terms: Callable[[str], str | None] | None = None,
        min_interval_s: float = 0.34,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        today: Callable[[], date] = date.today,
    ) -> None:
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise ValueError("max_results must be an int between 1 and 100")
        self._client = client
        self._email = email
        self._api_key = api_key
        self._max_results = max_results
        self._condition_terms = condition_terms
        self._interval = _KEY_MIN_INTERVAL_S if api_key else min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._today = today
        self._lock = asyncio.Lock()
        self._last: float | None = None

    def _build_term(self, query: Query) -> tuple[str, str | None]:
        drug = query.drug or ""
        if not _DRUG_RE.fullmatch(drug):
            raise ValueError("drug must be a normalised INN-shaped name")
        term = f'"{drug}"[Title/Abstract] AND {_FILTER}'
        used: str | None = None
        if query.condition and self._condition_terms is not None:
            label = self._condition_terms(query.condition)
            if isinstance(label, str) and _LABEL_RE.fullmatch(label.strip()):
                used = label.strip()
                term += f' AND "{used}"[Title/Abstract]'
        return term, used

    def _params(self, extra: dict[str, str]) -> dict[str, str]:
        params = {"db": "pubmed", "retmode": "json", "tool": TOOL_NAME, **extra}
        if self._email:
            params["email"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def _get_json(self, url: str, params: dict[str, str], what: str) -> dict:
        async with self._lock:
            if self._last is not None:
                wait = self._last + self._interval - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            try:
                resp = await self._client.get(
                    url, params=params, timeout=_REQUEST_TIMEOUT, headers=_REQUEST_HEADERS
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"PubMed {what} failed: {type(exc).__name__}") from None
            finally:
                self._last = self._clock()
        if resp.status_code >= 400:
            raise SourceUnavailable(f"PubMed {what} failed: HTTP {resp.status_code}") from None
        try:
            body = resp.json()
        except ValueError:
            raise SourceUnavailable(f"PubMed {what} response is not valid JSON") from None
        if not isinstance(body, dict):
            raise SourceUnavailable(f"PubMed {what} response has unexpected shape") from None
        return body

    async def fetch(self, query: Query) -> list[Record]:
        if query.drug is None:
            return []
        term, label = self._build_term(query)
        body = await self._get_json(
            ESEARCH_URL,
            self._params({"term": term, "retmax": str(self._max_results), "sort": "pub_date"}),
            "esearch",
        )
        result = body.get("esearchresult")
        if not isinstance(result, dict):
            raise SourceUnavailable("PubMed esearch response has unexpected shape") from None
        if "ERROR" in result:
            raise SourceUnavailable("PubMed esearch reported an error") from None
        ids = result.get("idlist")
        if not isinstance(ids, list) or any(
            not isinstance(i, str) or not _PMID_RE.fullmatch(i) for i in ids
        ):
            raise SourceUnavailable("PubMed esearch response has unexpected shape") from None
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        search: dict = {
            "sort": "pub_date_desc",
            "returned": len(ids),
            "total": _total(result.get("count")),
            "filter": _FILTER,
            "condition_label": label,
        }
        warnings = _warnings(result)
        if warnings:
            search["warnings"] = warnings
        summary = await self._get_json(
            ESUMMARY_URL, self._params({"id": ",".join(ids)}), "esummary"
        )
        data = summary.get("result")
        if not isinstance(data, dict):
            raise SourceUnavailable("PubMed esummary response has unexpected shape") from None
        retrieved_at = datetime.now(UTC)
        today = self._today()
        return [
            self._to_record(pmid, data.get(pmid), query.drug, retrieved_at, today, search)
            for pmid in ids
        ]

    @staticmethod
    def _to_record(
        pmid: str, row: object, drug: str, retrieved_at: datetime, today: date, search: dict
    ) -> Record:
        if not isinstance(row, dict):
            raise SourceUnavailable("PubMed esummary is missing a requested record") from None
        if row.get("uid") != pmid or "error" in row:
            raise SourceUnavailable("PubMed esummary record has mismatching uid") from None
        for key in ("title", "source", "pubdate"):
            if not _nonempty_str(row.get(key)):
                raise SourceUnavailable("PubMed esummary record has invalid fields") from None
        pubtypes = row.get("pubtype")
        if not isinstance(pubtypes, list) or not all(_nonempty_str(p) for p in pubtypes):
            raise SourceUnavailable("PubMed esummary record has invalid fields") from None
        payload: dict = {
            "pmid": pmid,
            "title": row["title"].strip(),
            "journal": row["source"].strip(),
            "pubdate": row["pubdate"].strip(),
            "pubtypes": list(pubtypes),
        }
        if _nonempty_str(row.get("epubdate")):
            payload["epubdate"] = row["epubdate"].strip()
        ids = row.get("articleids")
        if isinstance(ids, list):
            for item in ids:
                if (
                    isinstance(item, dict)
                    and item.get("idtype") == "doi"
                    and _nonempty_str(item.get("value"))
                ):
                    payload["doi"] = item["value"].strip()
                    break
        payload["search"] = dict(search)
        return Record(
            id=f"pubmed:{pmid}",
            kind="publication",
            entities=Entities(drugs=[drug]),
            region="global",
            source="PubMed",
            source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            valid_as_of=_valid_as_of(row, payload["pubdate"], today),
            retrieved_at=retrieved_at,
            licence=PUBMED_LICENCE,
            payload=payload,
        )

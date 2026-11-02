"""Edition check: flag DOI-bearing registry guidelines that Crossref shows as corrected,
retracted or superseded by a newer edition.

Only the registry's own DOI and title go to Crossref (never anything profile-derived). A failure
is ``unknown``, never ``current``; ``current`` means only that no newer edition was found.
Results are cached through the shared SQLite cache as ``edition`` records.
"""

import asyncio
import hashlib
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse

import httpx

from oncobuddy.core.cache import Cache
from oncobuddy.core.crossref import ALLOWED_JOURNALS, MIN_PAGES, first, normalise_title, page_span
from oncobuddy.core.records import Entities, Record

CACHE_SOURCE = "Crossref-edition"
CROSSREF_WORKS = "https://api.crossref.org/works"
USER_AGENT = "oncobuddy-mcp/0.1 (reference-data client)"
CAP = 6
SPACING = 0.25  # seconds between request starts (Crossref's public pool is strict)
DEADLINE = 8.0  # total seconds for one batch; unfinished records become unknown
_CORRECTED = {"correction", "erratum", "addendum", "expression_of_concern"}


def _day(value: object) -> date | None:
    try:
        y, m, d = value["date-parts"][0][:3]  # type: ignore[index]
        return date(int(y), int(m), int(d))
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _stem(title: str) -> str:
    return normalise_title(title.split(":", 1)[0])


def classify(
    doi: str,
    title: str,
    entry_date: date,
    journals: set[str],
    notices: list[dict],
    candidates: list[dict],
) -> dict:
    """Pure classification of Crossref answers for one registry entry."""
    found: list[dict] = []
    for item in notices:
        for update in item.get("update-to") or []:
            if str(update.get("DOI", "")).lower() != doi.lower():
                continue
            when = _day(update.get("updated")) or _day(item.get("created"))
            found.append(
                {"type": str(update.get("type", "")), "doi": item.get("DOI", ""),
                 "date": when.isoformat() if when else None}
            )
    newest: tuple[date, dict] | None = None
    names = {j.lower() for j in journals}
    for item in candidates:
        created = _day(item.get("created"))
        pages = page_span(str(item["page"])) if item.get("page") else None
        full = bool(item.get("article-number")) or (pages is not None and pages >= MIN_PAGES)
        if (
            item.get("type") == "journal-article"
            and created is not None and created > entry_date
            and str(item.get("DOI", "")).lower() != doi.lower()
            and first(item.get("container-title")).strip().lower() in names
            and _stem(first(item.get("title"))) == _stem(title)
            and full
        ):
            entry = {"doi": item["DOI"], "title": first(item.get("title")),
                     "date": created.isoformat()}
            if newest is None or created > newest[0]:
                newest = (created, entry)
    types = {n["type"] for n in found}
    if "retraction" in types:
        status = "retracted"
    elif newest is not None:
        status = "newer_found"
    elif types & _CORRECTED:
        status = "corrected"
    else:
        status = "current"
    return {"status": status, "newer": newest[1] if newest else None, "notices": found}


def _doi_of(record: Record) -> str | None:
    if record.source not in ALLOWED_JOURNALS:
        return None
    parsed = urlparse(record.source_url)
    if parsed.scheme != "https" or parsed.hostname != "doi.org":
        return None
    return parsed.path.lstrip("/") or None


class EditionChecker:
    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: Cache | None,
        *,
        mailto: str | None = None,
        enabled: bool = True,
        timeout: float = 3.0,
        cap: int = CAP,
        spacing: float = SPACING,
        deadline: float = DEADLINE,
    ) -> None:
        self._client, self._cache, self._mailto = client, cache, mailto
        self._enabled, self._timeout, self._cap = enabled, timeout, cap
        self._spacing, self._deadline = spacing, deadline
        # the public pool allows one request at a time; a polite-pool email allows a few
        self._sem = asyncio.Semaphore(3 if mailto else 1)
        self._next = 0.0

    async def annotate(self, records: list[Record]) -> None:
        todo: list[tuple[Record, str]] = []
        for record in records:
            doi = _doi_of(record)
            if not self._enabled or doi is None or len(todo) >= self._cap:
                record.payload["edition_check"] = {"status": "not_checked"}
            else:
                todo.append((record, doi))
        tasks = [asyncio.ensure_future(self._one(r, d)) for r, d in todo]
        if tasks:
            await asyncio.wait(tasks, timeout=self._deadline)
        for (record, _), task in zip(todo, tasks):
            if task.done() and not task.cancelled():
                record.payload["edition_check"] = task.result()
            else:
                task.cancel()
                record.payload["edition_check"] = {"status": "unknown"}
        await asyncio.gather(*tasks, return_exceptions=True)  # reap cancelled tasks

    # -- one record ---------------------------------------------------------------

    @staticmethod
    def _key(doi: str, entry_date: object) -> str:
        return "condition=" + hashlib.sha1(f"{doi.lower()}|{entry_date}".encode()).hexdigest()

    async def _one(self, record: Record, doi: str) -> dict:
        key = self._key(doi, record.valid_as_of)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        try:
            result = await self._lookup(record, doi)
        except Exception:  # noqa: BLE001 - any failure degrades to unknown, never to current
            return {"status": "unknown"}
        result["checked_at"] = datetime.now(UTC).isoformat()
        self._cache_put(key, record, doi, result)
        return result

    async def _lookup(self, record: Record, doi: str) -> dict:
        title = str(record.payload.get("title", ""))
        entry_date = record.valid_as_of
        if not isinstance(entry_date, date) or not title:
            raise ValueError("record has no usable date or title")
        journals = ALLOWED_JOURNALS[record.source]
        notices, candidates = await asyncio.gather(
            self._items({"filter": f"updates:{doi}", "rows": "10",
                         "select": "DOI,type,update-to,title,created"}),
            self._items({"query.bibliographic": title.split(":", 1)[0],
                         "filter": f"type:journal-article,from-created-date:{entry_date + timedelta(days=1)}",
                         "rows": "5",
                         "select": "DOI,title,container-title,page,article-number,created,type"}),
        )
        return classify(doi, title, entry_date, journals, notices, candidates)

    async def _items(self, params: dict[str, str]) -> list[dict]:
        if self._mailto:
            params = {**params, "mailto": self._mailto}
        async with self._sem:
            loop = asyncio.get_running_loop()
            now = loop.time()
            slot = max(self._next, now)  # claim the slot before sleeping so spacing holds at 3
            self._next = slot + self._spacing
            if slot > now:
                await asyncio.sleep(slot - now)
            response = await asyncio.wait_for(
                self._client.get(
                    CROSSREF_WORKS, params=params, headers={"User-Agent": USER_AGENT},
                    timeout=self._timeout,
                ),
                self._timeout,
            )
        response.raise_for_status()
        items = response.json()["message"]["items"]
        if not isinstance(items, list):
            raise TypeError("unexpected Crossref answer")
        return items

    # -- cache (best effort) -------------------------------------------------------

    def _cache_get(self, key: str) -> dict | None:
        if self._cache is None:
            return None
        try:
            hit = self._cache.get(CACHE_SOURCE, key)
        except (ValueError, RuntimeError):
            return None
        return dict(hit[0].payload) if hit else None

    def _cache_put(self, key: str, record: Record, doi: str, result: dict) -> None:
        if self._cache is None:
            return
        entry = Record(
            id=f"edition:{doi}", kind="edition", entities=Entities(), region="global",
            source="Crossref", source_url=f"{CROSSREF_WORKS}/{doi}",
            valid_as_of=record.valid_as_of, retrieved_at=datetime.now(UTC),
            licence="CC0-1.0", payload=result,
        )
        try:
            self._cache.put(CACHE_SOURCE, key, [entry])
        except (ValueError, RuntimeError):
            pass

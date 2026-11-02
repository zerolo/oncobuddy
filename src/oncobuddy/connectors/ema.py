"""EMA connector: EU centrally authorised medicines from EMA's JSON download.

Source details, fields and licence: docs/sources/ema.md.

Scope: the dataset covers ONLY medicines in the EU centralised procedure.
Nationally authorised products (e.g. Portuguese national procedures) are NOT in
it, so an empty result means "not in the EU central register", NOT "not
authorised". Every record carries ``payload["procedure"]`` to say so.

``valid_as_of`` is the per-medicine content revision date (``last_updated_date``),
NOT the date the authorisation status took effect.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.core.query import Query
from oncobuddy.core.records import Entities, Record

EMA_MEDICINES_JSON_URL = (
    "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
)
EMA_LICENCE = "EMA-reuse-notice"
EMA_PROCEDURE = "EU centrally authorised"
_REQUEST_TIMEOUT = httpx.Timeout(30.0)
_REQUEST_HEADERS = {
    "User-Agent": "oncobuddy-mcp/0.1 (+https://github.com/zerolo/onco-medbuddy; reference-data client)"
}

_INN_KEY = "international_non_proprietary_name_common_name"
_REQUIRED_PRESENT = ("category", "name_of_medicine", _INN_KEY)
_REQUIRED_NON_EMPTY_STR = ("ema_product_number", "medicine_url", "medicine_status")


def _norm(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _split(value: object) -> list[str]:
    parts = (_norm(p) for p in str(value or "").split(";"))
    return [p for p in parts if p]


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


class _Index:
    def __init__(self, by_substance: dict[str, list[dict]], timestamp: str | None, at: float):
        self.by_substance = by_substance
        self.timestamp = timestamp
        self.at = at


class EmaConnector:
    name = "EMA"
    section = "authorisation"

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str = EMA_MEDICINES_JSON_URL,
        max_age_seconds: float = 43200,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._url = url
        self._max_age = max_age_seconds
        self._clock = clock
        self._index: _Index | None = None
        self._lock = asyncio.Lock()

    async def _download(self) -> _Index:
        try:
            resp = await self._client.get(
                self._url, timeout=_REQUEST_TIMEOUT, headers=_REQUEST_HEADERS
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"EMA download failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise SourceUnavailable(f"EMA download failed: HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SourceUnavailable("EMA response is not valid JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise SourceUnavailable("EMA response has unexpected shape")
        meta = body.get("meta")
        ts = meta.get("timestamp") if isinstance(meta, dict) else None
        timestamp = ts if isinstance(ts, str) and ts.strip() else None
        by_substance: dict[str, list[dict]] = {}
        for row in body["data"]:
            if not isinstance(row, dict) or any(k not in row for k in _REQUIRED_PRESENT):
                raise SourceUnavailable("EMA response has unexpected record shape")
            for key in _REQUIRED_NON_EMPTY_STR:
                if not isinstance(row.get(key), str) or not row[key].strip():
                    raise SourceUnavailable("EMA response has invalid record fields")
            if row["category"] != "Human":
                continue
            names = set(_split(row[_INN_KEY])) | set(_split(row.get("active_substance")))
            for name in names:
                by_substance.setdefault(name, []).append(row)
        return _Index(by_substance, timestamp, self._clock())

    async def _get_index(self) -> _Index:
        async with self._lock:
            idx = self._index
            if idx is None or self._clock() - idx.at > self._max_age:
                self._index = None  # never serve stale data; failures are not cached
                self._index = await self._download()
            return self._index

    async def fetch(self, query: Query) -> list[Record]:
        drug = _norm(query.drug)
        if not drug:
            return []
        idx = await self._get_index()
        retrieved_at = datetime.now(UTC)
        seen: set[str] = set()
        records: list[Record] = []
        for row in idx.by_substance.get(drug, []):
            rid = f"ema:{row['ema_product_number'].strip()}"
            if rid in seen:
                continue
            seen.add(rid)
            records.append(self._to_record(rid, row, idx.timestamp, retrieved_at))
        return records

    @staticmethod
    def _to_record(rid: str, row: dict, dataset_ts: str | None, retrieved_at: datetime) -> Record:
        url = row["medicine_url"].strip()
        payload: dict = {
            "product": row["name_of_medicine"],
            "status": row["medicine_status"].strip(),
            "procedure": EMA_PROCEDURE,
            "indication_text_url": url,
        }
        if dataset_ts:
            payload["dataset_timestamp"] = dataset_ts
        authorised = _parse_date(row.get("marketing_authorisation_date"))
        if authorised:
            payload["authorisation_date"] = authorised.isoformat()
        if row.get("therapeutic_area_mesh"):
            payload["therapeutic_area"] = row["therapeutic_area_mesh"]
        if row.get("orphan_medicine") in ("Yes", "No"):
            payload["orphan"] = row["orphan_medicine"] == "Yes"
        atc = _norm(row.get("atc_code_human")).upper()
        return Record(
            id=rid,
            kind="medicine",
            entities=Entities(drugs=_split(row[_INN_KEY]), atc=[atc] if atc else []),
            region="EU",
            source="EMA",
            source_url=url,
            valid_as_of=_parse_date(row.get("last_updated_date")) or "unknown",
            retrieved_at=retrieved_at,
            licence=EMA_LICENCE,
            payload=payload,
        )

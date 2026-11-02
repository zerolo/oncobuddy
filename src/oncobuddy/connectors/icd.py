"""ICD coding connector: offline index of ICD-10 neoplasm categories.

Source details, licence and limits: docs/sources/icd.md.

The bundled file holds codes (identifiers) plus short site names written by oncoBuddy in its
own words. It contains NO WHO label text. Only 3-character categories are indexed; a longer
code (C50.9) resolves to its category and the exact code asked is kept in
``payload["queried_code"]``. ICD-O-3 morphology codes (8500/3) are not covered in v1.

Two names per row: ``name`` is a display name; ``search_term`` is a short US-spelling phrase
meant for literature/trial queries (``condition_terms`` returns ONLY this, never ``name``).
Search terms are heuristics, unreviewed by a clinician; ``None`` means "no safe automatic
search label" (generic, mixed or secondary categories), though the identifier record still
resolves via ``lookup_code``.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from importlib.resources import files

import yaml

from oncobuddy.core.normalise import normalise_condition
from oncobuddy.core.query import Query
from oncobuddy.core.records import Entities, Record

# WHO's ICD-10 browser root. A per-code deep link could not be verified, so none is built.
ICD10_BROWSER_URL = "https://icd.who.int/browse10/2019/en"
SYSTEM = "ICD-10 (category)"
NOTE = "short site name written by oncoBuddy; consult the WHO ICD browser for official wording"
_CATEGORY = re.compile(r"[A-Z][0-9]{2}")
_ICD10_SHAPE = re.compile(r"[A-Z][0-9]{2}(?:\.[0-9A-Z]{1,4})?")
_NAME = re.compile(r"[a-z0-9 -]+")


@dataclass(frozen=True)
class IcdRow:
    code: str
    name: str
    group: str | None
    search_term: str | None
    source: str
    licence: str
    reviewed_by: str
    valid_as_of: date

    def to_record(self, queried_code: str | None = None) -> Record:
        payload: dict = {
            "code": self.code,
            "name": self.name,
            "search_term": self.search_term,
            "system": SYSTEM,
            "note": NOTE,
        }
        if self.group:
            payload["group"] = self.group
        if queried_code is not None:
            payload["queried_code"] = queried_code
        return Record(
            id=f"icd10:{self.code}",
            kind="code",
            entities=Entities(conditions=[self.code]),
            region="global",
            source=self.source,
            source_url=ICD10_BROWSER_URL,
            valid_as_of=self.valid_as_of,
            retrieved_at=datetime.now(UTC),
            licence=self.licence,
            payload=payload,
            reviewed_by=self.reviewed_by,
        )


@lru_cache(maxsize=1)
def load_rows() -> tuple[IcdRow, ...]:
    text = (files("oncobuddy.connectors") / "data" / "icd_onc.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    edition = doc["edition_date"]
    if not isinstance(edition, date):
        raise TypeError("icd_onc.yaml edition_date must be a date")
    rows = []
    for item in doc["categories"]:
        code, name = str(item["code"]), str(item["name"])
        if not _CATEGORY.fullmatch(code) or not _NAME.fullmatch(name):
            raise ValueError(f"bad ICD index row: {item!r}")
        term = item["search_term"]  # required key; null allowed
        if term is not None and not _NAME.fullmatch(term):
            raise ValueError(f"bad ICD search_term: {item!r}")
        rows.append(
            IcdRow(
                code=code,
                name=name,
                group=item.get("group"),
                search_term=term,
                source=doc["source"],
                licence=doc["licence"],
                reviewed_by=doc["reviewed_by"],
                valid_as_of=edition,
            )
        )
    return tuple(rows)


@lru_cache(maxsize=1)
def _by_code() -> dict[str, IcdRow]:
    return {r.code: r for r in load_rows()}


@lru_cache(maxsize=1)
def _by_name() -> dict[str, IcdRow]:
    return {r.name: r for r in load_rows()}


def _category_of(code: str) -> tuple[str, IcdRow] | None:
    """(normalised code, bundled row) for an ICD-10-style code, else None."""
    norm = normalise_condition(code)
    if norm is None or not _ICD10_SHAPE.fullmatch(norm):
        return None
    row = _by_code().get(norm[:3])
    return (norm, row) if row else None


def condition_terms(code: str) -> str | None:
    """Search term (never the display name) for a code or its 3-character parent.

    None if the code is unknown or the category has no safe automatic search label.
    """
    hit = _category_of(code)
    return hit[1].search_term if hit else None


def lookup_code(code_or_term: str) -> list[Record]:
    """Resolve a code (any case/whitespace) or an exact site name to its record."""
    hit = _category_of(code_or_term)
    if hit:
        norm, row = hit
        return [row.to_record(queried_code=norm)]
    row = _by_name().get(code_or_term.strip().lower())
    return [row.to_record()] if row else []


class IcdConnector:
    name = "ICD index"
    section = "coding"
    cacheable = False

    async def fetch(self, query: Query) -> list[Record]:
        if query.condition is None:
            return []
        if normalise_condition(query.condition) is None:
            raise ValueError(f"not an ICD code: {query.condition!r}")
        return lookup_code(query.condition)

"""Curated guideline connector: serves the YAML guideline index (see docs/sources/guidelines.md).

An entry whose ``interventions`` is empty is a scope-only entry (a guideline exists for the
condition and stage but oncoBuddy asserts no drug), so it matches any drug query. Region
filtering is the reconciler's job.
"""

from pathlib import Path

from oncobuddy.core.normalise import normalise_condition
from oncobuddy.core.query import Query
from oncobuddy.core.records import Record
from oncobuddy.curated.loader import load_guidelines


def category_of(condition: str) -> str:
    """3-character ICD-10 category of a condition code (C50.9 -> C50); ValueError otherwise."""
    norm = normalise_condition(condition)
    if norm is None or norm[0].isdigit():
        raise ValueError(f"not an ICD code: {condition!r}")
    return norm[:3]


class GuidelineConnector:
    name = "Curated guideline index"
    section = "guidelines"
    cacheable = False

    def __init__(self, directory: Path | None = None, checker=None) -> None:
        self._records = load_guidelines(directory)
        self._checker = checker  # optional EditionChecker: annotates copies, never the index

    async def fetch(self, query: Query) -> list[Record]:
        if query.condition is None:
            return []
        norm = normalise_condition(query.condition)
        if norm is not None and norm[0].isdigit():
            return []  # ICD-O-3 morphology codes are accepted but not indexed in v1
        category = category_of(query.condition)
        out = []
        for record in self._records:
            if category not in record.entities.conditions:
                continue
            drugs = record.entities.drugs
            if query.drug is not None and drugs and query.drug not in drugs:
                continue
            copy = record.model_copy(deep=True)
            copy.payload["scope_only"] = not drugs
            if query.drug is not None:
                copy.payload["drug_match"] = "named" if drugs else "scope-only"
            out.append(copy)
        if self._checker is not None:
            await self._checker.annotate(out)
        return out

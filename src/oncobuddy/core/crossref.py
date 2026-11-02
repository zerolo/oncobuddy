"""Shared Crossref helpers: journal sets per issuer and title/page normalisation.

Used by the registry verifier (scripts/verify_registry.py) and the edition checker.
"""

import re

ALLOWED_JOURNALS: dict[str, set[str]] = {
    "ESMO": {"Annals of Oncology", "ESMO Open"},
    "ASCO": {"Journal of Clinical Oncology"},
}
MIN_PAGES = 3
_MARKS = re.compile("[☆†*]")


def normalise_title(title: str) -> str:
    return " ".join(_MARKS.sub("", title).lower().split())


def page_span(page: str) -> int | None:
    numbers = re.findall(r"\d+", page)
    if not numbers:
        return None
    if len(numbers) == 1:
        return 1
    return int(numbers[-1]) - int(numbers[0]) + 1


def first(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")

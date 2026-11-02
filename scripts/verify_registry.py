"""Check curated guideline links: DOIs resolve and match Crossref; other links use allowed hosts.

Read-only: loads entries via ``load_guidelines`` and changes no file.
"""

import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

from oncobuddy.core.access import ALLOWED_HOSTS
from oncobuddy.core.crossref import ALLOWED_JOURNALS, MIN_PAGES, first, normalise_title, page_span
from oncobuddy.curated.loader import default_directory, load_guidelines


def doi_of(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "doi.org":
        return None
    doi = parsed.path.lstrip("/")
    return doi or None


_first = first


def check_message(record_id: str, stored_title: str, message: dict, journals: set[str]) -> list[str]:
    problems: list[str] = []
    if normalise_title(_first(message.get("title"))) != normalise_title(stored_title):
        problems.append(f"{record_id}: Crossref title differs")
    if message.get("type") != "journal-article":
        problems.append(f"{record_id}: not a journal article")
    if _first(message.get("container-title")).strip().lower() not in {j.lower() for j in journals}:
        problems.append(f"{record_id}: unexpected journal")
    page = message.get("page")
    if page and str(page) == str(message.get("article-number") or ""):
        return problems  # ESMO Open style: the page field is a single article number
    if not page and not message.get("volume") and not message.get("issue"):
        return problems  # online ahead of print: no pagination assigned yet
    span = page_span(str(page)) if page else None
    if span is None:
        problems.append(f"{record_id}: no page range")
    elif span < MIN_PAGES:
        problems.append(f"{record_id}: too few pages ({span})")
    return problems


def _host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def unchecked_count(directory: Path) -> int:
    """Entries that are not DOI-checked (source outside ALLOWED_JOURNALS)."""
    return sum(1 for r in load_guidelines(directory) if r.source not in ALLOWED_JOURNALS)


def verify(
    directory: Path,
    lookup: Callable[[str], int],
    crossref: Callable[[str], dict],
    sleep: Callable[[float], None],
) -> list[str]:
    problems: list[str] = []
    first_call = True
    for record in load_guidelines(directory):
        doi = doi_of(record.source_url)
        if record.source in ALLOWED_JOURNALS:
            if doi is None:
                problems.append(f"{record.id}: url is not a doi.org link")
                continue
        elif not _host_allowed(record.source_url):
            problems.append(f"{record.id}: url host is not allowed")
            continue
        else:
            continue  # allowed host, not machine-checkable
        status = lookup(doi)
        if status != 200:
            problems.append(f"{record.id}: DOI not found (status {status})")
            continue
        if not first_call:
            sleep(1)
        first_call = False
        problems.extend(check_message(record.id, record.payload["title"], crossref(doi), ALLOWED_JOURNALS[record.source]))
    return problems


def _lookup(doi: str) -> int:
    return httpx.get(f"https://doi.org/api/handles/{doi}", timeout=15).status_code


def _crossref(doi: str) -> dict:
    response = httpx.get(
        f"https://api.crossref.org/works/{doi}",
        headers={"User-Agent": "oncoBuddy-registry/0.1"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["message"]


def main(argv: list[str] | None = None) -> int:
    del argv
    directory = default_directory()
    problems = verify(directory, _lookup, _crossref, time.sleep)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"ok: {len(load_guidelines(directory))} entries ({unchecked_count(directory)} not machine-checkable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

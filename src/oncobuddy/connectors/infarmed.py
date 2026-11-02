"""INFARMED pointer connector: link-only records, fully offline (see docs/sources/infarmed.md).

INFARMED's terms forbid reproducing or distributing its data without written permission, and
its robots.txt disallows automated access. This connector therefore never contacts any
INFARMED host and never fetches, caches or stores INFARMED data. For a drug it emits exactly
one record that points the clinician at the official sources and carries an ``access`` block
telling the agent to read the status in the user's own browser, with the user's consent; it
asserts no status.
"""

from datetime import UTC, datetime
from typing import Literal

from oncobuddy.core.access import Access
from oncobuddy.core.normalise import normalise_drug
from oncobuddy.core.query import Query
from oncobuddy.core.records import RESTRICTED_LICENCE, Entities, Record

INFOMED_URL = "https://extranet.infarmed.pt/INFOMED-fo/"
SIATS_URL = "https://www.infarmed.pt/web/infarmed/tracker-de-processos"

INFOMED_NAME = "Infomed (INFARMED medicines database)"
SIATS_NAME = "SIATS process tracker (reimbursement and hospital evaluation)"

STATUS_TEXT = "not retrieved - read in the user's browser"
RETRIEVAL_TEXT = (
    "none by oncoBuddy - read by the user's agent in the user's browser; "
    "INFARMED terms forbid reproduction"
)
NOTE_TEXT = (
    "Portuguese national status was not retrieved by oncoBuddy; it must be read in "
    "the user's browser at the linked source, searching by active substance name"
)

_OPEN = "Open the entry link in the user's own browser, after the user agrees"
_SHORT = "Report short statements only; do not copy lists or tables"
_RECIPES = {
    "authorisation": [
        _OPEN,
        "Search for the medicine by its active substance name",
        "Report the authorisation status and the marketing status as printed, each with its date",
        _SHORT,
    ],
    "reimbursement": [
        _OPEN,
        "Search for the process by the active substance name",
        "Report the evaluation outcome as printed, with its date",
        _SHORT,
    ],
}

Section = Literal["authorisation", "reimbursement"]
_SECTIONS = ("authorisation", "reimbursement")

_INFOMED_LINK = {"name": INFOMED_NAME, "url": INFOMED_URL}
_SIATS_LINK = {"name": SIATS_NAME, "url": SIATS_URL}


class InfarmedPointerConnector:
    name = "INFARMED (pointer)"
    cacheable = False

    def __init__(self, section: Section) -> None:
        if section not in _SECTIONS:
            raise ValueError(f"unknown INFARMED pointer section {section!r}")
        self.section: str = section

    async def fetch(self, query: Query) -> list[Record]:
        if query.drug is None:
            return []
        inn = normalise_drug(query.drug)
        if inn is None:
            raise ValueError("drug is not an INN-shaped name")
        if self.section == "authorisation":
            url, links = INFOMED_URL, [_INFOMED_LINK, _SIATS_LINK]
        else:
            url, links = SIATS_URL, [_SIATS_LINK, _INFOMED_LINK]
        return [
            Record(
                id=f"infarmed-pointer:{self.section}:{inn}",
                kind="pointer",
                entities=Entities(drugs=[inn]),
                region="PT",
                source="INFARMED",
                source_url=url,
                valid_as_of="unknown",
                retrieved_at=datetime.now(UTC),
                licence=RESTRICTED_LICENCE,
                payload={
                    "status": STATUS_TEXT,
                    "section": self.section,
                    "inn": inn,
                    "links": [dict(link) for link in links],
                    "retrieval": RETRIEVAL_TEXT,
                    "note": NOTE_TEXT,
                    "access": Access(
                        method="browser",
                        reason="no_api",
                        read_recipe=_RECIPES[self.section],
                        entry_url=url,
                    ).model_dump(),
                },
            )
        ]

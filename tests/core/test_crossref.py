from datetime import date

from oncobuddy.core.cache import DEFAULT_TTLS
from oncobuddy.core.crossref import ALLOWED_JOURNALS, MIN_PAGES, first, normalise_title, page_span
from oncobuddy.core.records import Entities, Record


def test_helpers_live_in_the_package():
    assert ALLOWED_JOURNALS["ESMO"] == {"Annals of Oncology", "ESMO Open"}
    assert ALLOWED_JOURNALS["ASCO"] == {"Journal of Clinical Oncology"}
    assert MIN_PAGES == 3
    assert normalise_title("  Bladder☆ Cancer* ") == "bladder cancer"
    assert page_span("129-142") == 14 and page_span("561") == 1 and page_span("x") is None
    assert first(["a", "b"]) == "a" and first([]) == "" and first(None) == ""


def test_edition_record_kind_and_ttl():
    record = Record(
        id="edition:x", kind="edition", entities=Entities(), region="global",
        source="Crossref", source_url="https://api.crossref.org/works/x",
        valid_as_of=date(2026, 1, 1), retrieved_at="2026-11-02T00:00:00+00:00",
        licence="CC0-1.0", payload={"status": "current"},
    )
    assert record.kind == "edition"
    assert DEFAULT_TTLS["Crossref-edition"] == 7 * 86400

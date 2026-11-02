import json
import socket
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.connectors.infarmed import (
    INFOMED_URL,
    SIATS_URL,
    InfarmedPointerConnector,
)
from oncobuddy.core.access import Access
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.records import Entities, Record
from oncobuddy.core.regions import RegionTree

POINTER_NOTE = (
    "INFARMED: not retrieved by oncoBuddy; read the status in the user's browser (see access)"
)
STATUS_WORDS = ("authorised", "authorized", "reimbursed", "approved", "available")


def pq(drug="trastuzumab", region="PT"):
    return Query(drug=drug, condition=None, region=region)


@pytest.fixture
def tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions.yaml"
    path.write_text("regions:\n  global: null\n  EU: global\n  PT: EU\n", encoding="utf-8")
    return RegionTree.load(path)


class FakeEma:
    name, section = "EMA", "authorisation"

    def __init__(self, records=None, exc=None):
        self.records, self.exc = records or [], exc

    async def fetch(self, query):
        if self.exc:
            raise self.exc
        return list(self.records)


def ema_record(region="EU"):
    return Record(
        id=f"ema:{region}",
        kind="medicine",
        entities=Entities(drugs=["trastuzumab"]),
        region=region,
        source="EMA",
        source_url="https://example.org/ema",
        valid_as_of=date(2026, 1, 1),
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="CC-BY-4.0",
        payload={"status": "Authorised"},
    )


async def test_authorisation_pointer_shape():
    c = InfarmedPointerConnector("authorisation")
    assert c.name == "INFARMED (pointer)"
    assert c.section == "authorisation"
    (r,) = await c.fetch(pq())
    assert r.kind == "pointer"
    assert r.region == "PT"
    assert r.source == "INFARMED"
    assert r.source_url == INFOMED_URL == "https://extranet.infarmed.pt/INFOMED-fo/"
    assert r.id == "infarmed-pointer:authorisation:trastuzumab"
    assert r.entities.drugs == ["trastuzumab"]
    assert r.valid_as_of == "unknown"
    assert r.retrieved_at.tzinfo is not None
    assert r.licence == "restricted-link-only"
    assert r.reviewed_by is None
    assert r.payload == {
        "status": "not retrieved - read in the user's browser",
        "section": "authorisation",
        "inn": "trastuzumab",
        "links": [
            {"name": "Infomed (INFARMED medicines database)", "url": INFOMED_URL},
            {
                "name": "SIATS process tracker (reimbursement and hospital evaluation)",
                "url": SIATS_URL,
            },
        ],
        "retrieval": "none by oncoBuddy - read by the user's agent in the user's browser; "
        "INFARMED terms forbid reproduction",
        "note": "Portuguese national status was not retrieved by oncoBuddy; it must be read in "
        "the user's browser at the linked source, searching by active substance name",
        "access": {
            "method": "browser",
            "reason": "no_api",
            "read_recipe": [
                "Open the entry link in the user's own browser, after the user agrees",
                "Search for the medicine by its active substance name",
                (
                    "Report the authorisation status and the marketing status as printed, "
                    "each with its date"
                ),
                "Report short statements only; do not copy lists or tables",
            ],
            "entry_url": INFOMED_URL,
        },
    }


async def test_reimbursement_pointer_shape():
    c = InfarmedPointerConnector("reimbursement")
    assert c.section == "reimbursement"
    (r,) = await c.fetch(pq("Osimertinib "))
    assert r.source_url == SIATS_URL
    assert SIATS_URL == "https://www.infarmed.pt/web/infarmed/tracker-de-processos"
    assert r.id == "infarmed-pointer:reimbursement:osimertinib"
    assert r.payload["section"] == "reimbursement"
    assert r.payload["inn"] == "osimertinib"
    assert [link["url"] for link in r.payload["links"]] == [SIATS_URL, INFOMED_URL]


async def test_deterministic_id_and_flags():
    c = InfarmedPointerConnector("authorisation")
    assert c.cacheable is False
    a, b = await c.fetch(pq()), await c.fetch(pq())
    assert a[0].id == b[0].id


async def test_drug_none_returns_empty():
    for section in ("authorisation", "reimbursement"):
        assert await InfarmedPointerConnector(section).fetch(pq(None)) == []


async def test_non_inn_drug_raises_value_error():
    c = InfarmedPointerConnector("authorisation")
    for bad in ("breast cancer patient", "Herceptín", "x"):
        with pytest.raises(ValueError):
            await c.fetch(pq(bad))


def _string_values(value, out):
    if isinstance(value, dict):
        for x in value.values():
            _string_values(x, out)
    elif isinstance(value, list):
        for x in value:
            _string_values(x, out)
    elif isinstance(value, str):
        out.append(value.lower())
    return out


async def test_restricted_validity_and_no_status_words():
    for section in ("authorisation", "reimbursement"):
        (r,) = await InfarmedPointerConnector(section).fetch(pq())
        # Re-validate: restricted-link-only forbids text keys at any depth.
        Record.model_validate(r.model_dump())
        for word in STATUS_WORDS:
            for value in _string_values(r.payload, []):
                assert word not in value, (word, value)


def test_constructor_takes_no_client_and_rejects_bad_section():
    with pytest.raises(TypeError):
        InfarmedPointerConnector("authorisation", httpx.AsyncClient())  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        InfarmedPointerConnector("trials")  # type: ignore[arg-type]


async def test_fetch_makes_no_network_calls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    for section in ("authorisation", "reimbursement"):
        assert len(await InfarmedPointerConnector(section).fetch(pq())) == 1


def test_module_has_no_http_imports():
    import oncobuddy.connectors.infarmed as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "httpx" not in source and "requests" not in source and "urllib" not in source


# -- through the reconciler ---------------------------------------------------------------


def rec(tree, ema=None):
    return Reconciler(
        [
            ema or FakeEma([ema_record("EU")]),
            InfarmedPointerConnector("authorisation"),
            InfarmedPointerConnector("reimbursement"),
        ],
        tree,
    )


async def test_pointer_eligible_for_pt_not_eu(tree):
    pt = await rec(tree).regional_status("trastuzumab", "C50.9", "PT")
    assert [r.kind for r in pt.sections["reimbursement"].records] == ["pointer"]
    assert [r.kind for r in pt.sections["authorisation"].records] == ["medicine", "pointer"]
    eu = await rec(tree).regional_status("trastuzumab", "C50.9", "EU")
    assert eu.sections["reimbursement"].records == []
    assert eu.sections["reimbursement"].no_data.reason == "not_found"
    assert [r.kind for r in eu.sections["authorisation"].records] == ["medicine"]


async def test_fallback_note_ignores_pointer(tree):
    sec = await rec(tree).section("authorisation", drug="trastuzumab", condition=None, region="PT")
    assert "no PT record; showing EU-level" in sec.notes


async def test_pointer_sorted_after_real_records(tree):
    ema = FakeEma([ema_record("EU"), ema_record("global")])
    sec = await rec(tree, ema).section(
        "authorisation", drug="trastuzumab", condition=None, region="PT"
    )
    assert [r.kind for r in sec.records] == ["medicine", "medicine", "pointer"]
    assert [r.region for r in sec.records] == ["EU", "global", "PT"]


async def test_pointer_does_not_hide_ema_failure(tree):
    sec = await rec(tree, FakeEma(exc=SourceUnavailable("x"))).section(
        "authorisation", drug="trastuzumab", condition=None, region="PT"
    )
    assert [r.kind for r in sec.records] == ["pointer"]
    assert sec.no_data is not None and sec.no_data.reason == "source_unavailable"
    assert "EMA unavailable" in sec.notes
    assert POINTER_NOTE in sec.notes
    assert not any(n.startswith("no PT record") for n in sec.notes)


async def test_pt_ema_empty_gives_not_found_with_pointer(tree):
    for how in ("section", "status"):
        r = rec(tree, FakeEma([]))
        if how == "section":
            sec = await r.section("authorisation", drug="trastuzumab", condition=None, region="PT")
        else:
            sec = (await r.regional_status("trastuzumab", "C50.9", "PT")).sections["authorisation"]
        assert [x.kind for x in sec.records] == ["pointer"]
        assert sec.no_data is not None and sec.no_data.reason == "not_found"
        assert POINTER_NOTE in sec.notes


async def test_pt_ema_ok_has_no_data_none_and_order(tree):
    for how in ("section", "status"):
        r = rec(tree)
        if how == "section":
            sec = await r.section("authorisation", drug="trastuzumab", condition=None, region="PT")
        else:
            sec = (await r.regional_status("trastuzumab", "C50.9", "PT")).sections["authorisation"]
        assert sec.no_data is None
        assert [x.kind for x in sec.records] == ["medicine", "pointer"]
        assert "no PT record; showing EU-level" in sec.notes
        assert POINTER_NOTE not in sec.notes


async def test_pt_reimbursement_pointer_with_not_found(tree):
    for how in ("section", "status"):
        r = rec(tree)
        if how == "section":
            sec = await r.section("reimbursement", drug="trastuzumab", condition=None, region="PT")
        else:
            sec = (await r.regional_status("trastuzumab", "C50.9", "PT")).sections["reimbursement"]
        assert [x.kind for x in sec.records] == ["pointer"]
        assert sec.no_data is not None and sec.no_data.reason == "not_found"
        assert POINTER_NOTE in sec.notes


async def test_server_get_medicine_has_pointer_and_envelope(tree):
    from oncobuddy.core.reconciler import NOTICE
    from oncobuddy.server import OncoTools

    tools = OncoTools(rec(tree))
    out = await tools.get_medicine("trastuzumab", "PT")
    assert out["notice"] == NOTICE
    assert out["scope_chain"] == ["PT", "EU", "global"]
    kinds = [r["kind"] for r in out["section"]["records"]]
    assert kinds == ["medicine", "pointer"]
    json.dumps(out)
    status = await tools.regional_status("trastuzumab", "C50.9", "PT")
    assert status["notice"] == NOTICE
    assert "reimbursement" in status["sections"]
    assert status["sections"]["reimbursement"]["records"][0]["kind"] == "pointer"
    assert status["sections"]["reimbursement"]["no_data"]["reason"] == "not_found"
    reimb = await tools.get_record("infarmed-pointer:reimbursement:trastuzumab")
    assert reimb["record"]["kind"] == "pointer"


async def test_reimbursement_pointer_access_block():
    (r,) = await InfarmedPointerConnector("reimbursement").fetch(pq())
    access = Access.model_validate(r.payload["access"])
    assert access.method == "browser"
    assert access.reason == "no_api"
    assert access.entry_url == r.source_url == SIATS_URL
    assert 3 <= len(access.read_recipe) <= 5
    text = " ".join(access.read_recipe).casefold()
    assert "outcome" in text and "date" in text
    assert "list" in text and "table" in text  # tells the agent not to copy them


async def test_pointers_say_status_must_be_read_in_browser():
    for section in ("authorisation", "reimbursement"):
        (r,) = await InfarmedPointerConnector(section).fetch(pq())
        assert "browser" in r.payload["status"]
        assert "browser" in r.payload["note"]
        assert "browser" in r.payload["retrieval"]


async def test_pointer_entry_urls_pass_the_host_check():
    from oncobuddy.core.access import check_entry_url

    for section in ("authorisation", "reimbursement"):
        (r,) = await InfarmedPointerConnector(section).fetch(pq())
        check_entry_url(Access.model_validate(r.payload["access"]), r.source_url)

import asyncio
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.core.cache import DEFAULT_TTLS, Cache, cache_key
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import NOTICE, V1_SECTIONS, Reconciler, reference_note
from oncobuddy.core.records import Entities, Record
from oncobuddy.core.regions import RegionTree, UnknownRegion

FIXTURE_WITH_US = """\
regions:
  global: null
  EU: global
  PT: EU
  ES: EU
  US: global
"""


def make_record(
    record_id: str,
    region: str,
    kind: str = "medicine",
    conditions: list[str] | None = None,
) -> Record:
    return Record(
        id=record_id,
        kind=kind,
        entities=Entities(drugs=["trastuzumab"], conditions=list(conditions or [])),
        region=region,
        source="EMA",
        source_url="https://example.org/" + record_id,
        valid_as_of=date(2026, 1, 1),
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="CC-BY-4.0",
        payload={"name": record_id},
    )


class FakeConnector:
    def __init__(
        self,
        name: str,
        section: str,
        records: list[Record] | None = None,
        exc: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self.section = section
        self.records = list(records or [])
        self.exc = exc
        self.delay = delay
        self.calls = 0
        self.queries: list[Query] = []

    async def fetch(self, query: Query) -> list[Record]:
        self.calls += 1
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return list(self.records)


@pytest.fixture
def tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions.yaml"
    path.write_text(FIXTURE_WITH_US, encoding="utf-8")
    return RegionTree.load(path)


@pytest.fixture
def memory_cache():
    cache = Cache(None, DEFAULT_TTLS)
    yield cache
    cache.close()


def make_reconciler(connectors, tree, timeout_s: float = 10.0, cache=None) -> Reconciler:
    return Reconciler(connectors, tree, cache=cache, timeout_s=timeout_s)


async def test_filters_to_scope_chain(tree):
    connector = FakeConnector(
        "EMA",
        "authorisation",
        records=[
            make_record("us1", "US"),
            make_record("global1", "global"),
            make_record("es1", "ES"),
            make_record("eu1", "EU"),
            make_record("pt1", "PT"),
        ],
    )
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    section = result.sections["authorisation"]
    assert [r.region for r in section.records] == ["PT", "EU", "global"]
    assert section.no_data is None


async def test_most_specific_first_ordering(tree):
    connector = FakeConnector(
        "EMA",
        "authorisation",
        records=[
            make_record("g", "global"),
            make_record("e", "EU"),
            make_record("p", "PT"),
        ],
    )
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert [r.id for r in result.sections["authorisation"].records] == ["p", "e", "g"]


async def test_fallback_note_when_no_pt_record(tree):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("e", "EU")])
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert "no PT record; showing EU-level" in result.sections["authorisation"].notes


async def test_no_fallback_note_when_target_level_present(tree):
    connector = FakeConnector(
        "EMA", "authorisation", records=[make_record("p", "PT"), make_record("e", "EU")]
    )
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert not any(n.startswith("no ") for n in result.sections["authorisation"].notes)


async def test_fallback_note_for_global_level(tree):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("g", "global")])
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert "no PT record; showing global-level" in result.sections["authorisation"].notes


async def test_timeout_gives_source_unavailable(tree):
    connector = FakeConnector("EMA", "authorisation", delay=1.0)
    result = await make_reconciler([connector], tree, timeout_s=0.01).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    section = result.sections["authorisation"]
    assert section.no_data is not None
    assert section.no_data.reason == "source_unavailable"
    assert section.records == []


async def test_exception_gives_source_unavailable(tree):
    connector = FakeConnector("EMA", "authorisation", exc=SourceUnavailable("down"))
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert result.sections["authorisation"].no_data.reason == "source_unavailable"


async def test_arbitrary_exception_gives_source_unavailable_not_not_found(tree):
    connector = FakeConnector("EMA", "authorisation", exc=RuntimeError("boom"))
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert result.sections["authorisation"].no_data.reason == "source_unavailable"


async def test_empty_result_gives_not_found(tree):
    connector = FakeConnector("EMA", "authorisation", records=[])
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert result.sections["authorisation"].no_data.reason == "not_found"


async def test_only_out_of_region_records_gives_not_found(tree):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("us", "US")])
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert result.sections["authorisation"].no_data.reason == "not_found"


async def test_non_inn_shaped_drug_returns_not_found_without_calling_connectors(tree):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("e", "EU")])
    result = await make_reconciler([connector], tree).regional_status(
        "breast cancer patient john", "C50.9", "PT"
    )
    assert connector.calls == 0
    assert set(result.sections) == set(V1_SECTIONS)
    for section in result.sections.values():
        assert section.no_data is not None
        assert section.no_data.reason == "not_found"
        assert section.records == []


async def test_invalid_condition_returns_not_found_without_calling_connectors(tree):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("e", "EU")])
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "breast cancer", "PT"
    )
    assert connector.calls == 0
    assert all(s.no_data.reason == "not_found" for s in result.sections.values())


async def test_unknown_region_raises_UnknownRegion(tree):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("e", "EU")])
    with pytest.raises(UnknownRegion):
        await make_reconciler([connector], tree).regional_status(
            "trastuzumab", "C50.9", "Mars"
        )


async def test_v1_sections_without_connectors_are_out_of_scope_v1(tree):
    connector = FakeConnector("CT", "trials", records=[make_record("t", "PT", "trial")])
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert set(result.sections) == set(V1_SECTIONS)
    assert result.sections["trials"].no_data is None
    for name in ("authorisation", "reimbursement", "guidelines", "literature", "coding"):
        section = result.sections[name]
        assert section.no_data.reason == "out_of_scope_v1"
        assert section.records == []


async def test_cache_hit_skips_connector(tree, memory_cache):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("p", "PT")])
    cache = memory_cache
    reconciler = make_reconciler([connector], tree, cache=cache)
    first = await reconciler.regional_status("trastuzumab", "C50.9", "PT")
    second = await reconciler.regional_status("trastuzumab", "C50.9", "PT")
    assert connector.calls == 1
    assert [r.id for r in second.sections["authorisation"].records] == ["p"]
    assert [r.id for r in first.sections["authorisation"].records] == ["p"]


async def test_failure_not_cached(tree, memory_cache):
    connector = FakeConnector("EMA", "authorisation", exc=RuntimeError("boom"))
    cache = memory_cache
    reconciler = make_reconciler([connector], tree, cache=cache)
    failed = await reconciler.regional_status("trastuzumab", "C50.9", "PT")
    assert failed.sections["authorisation"].no_data.reason == "source_unavailable"

    connector.exc = None
    connector.records = [make_record("p", "PT")]
    recovered = await reconciler.regional_status("trastuzumab", "C50.9", "PT")
    assert connector.calls == 2
    assert recovered.sections["authorisation"].no_data is None
    assert [r.id for r in recovered.sections["authorisation"].records] == ["p"]


async def test_same_section_two_connectors_merge(tree):
    ok = FakeConnector("CT", "trials", records=[make_record("t", "PT", "trial")])
    other = FakeConnector("CT2", "trials", records=[make_record("e", "EU", "trial")])
    result = await make_reconciler([ok, other], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    section = result.sections["trials"]
    assert [r.id for r in section.records] == ["t", "e"]
    assert section.no_data is None


async def test_same_section_one_failure_keeps_other_records(tree):
    ok = FakeConnector("CT", "trials", records=[make_record("t", "PT", "trial")])
    bad = FakeConnector("CT2", "trials", exc=RuntimeError("boom"))
    result = await make_reconciler([ok, bad], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    section = result.sections["trials"]
    assert [r.id for r in section.records] == ["t"]
    assert section.no_data is None
    assert "CT2 unavailable" in section.notes


async def test_same_section_all_fail_gives_source_unavailable(tree):
    bad1 = FakeConnector("CT", "trials", exc=RuntimeError("boom"))
    bad2 = FakeConnector("CT2", "trials", exc=RuntimeError("boom"))
    result = await make_reconciler([bad1, bad2], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert result.sections["trials"].no_data.reason == "source_unavailable"


async def test_notice_present_on_result(tree):
    result = await make_reconciler([], tree).regional_status("trastuzumab", "C50.9", "PT")
    assert result.notice == NOTICE
    assert result.query_region == "PT"
    assert result.scope_chain == ["PT", "EU", "global"]


class StubbornConnector(FakeConnector):
    """Swallows cancellation and keeps sleeping until released or a 2 s deadline."""

    def __init__(self, name: str, section: str) -> None:
        super().__init__(name, section)
        self.released = False
        self.task: asyncio.Task | None = None

    async def fetch(self, query: Query) -> list[Record]:
        self.calls += 1
        self.task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while not self.released and loop.time() < deadline:
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                continue
        return []


async def test_hung_connector_cannot_stall_past_timeout(tree):
    hung = StubbornConnector("EMA", "authorisation")
    fast = FakeConnector("CT", "trials", records=[make_record("t", "PT", "trial")])
    reconciler = make_reconciler([hung, fast], tree, timeout_s=0.05)
    started = time.perf_counter()
    result = await reconciler.regional_status("trastuzumab", "C50.9", "PT")
    elapsed = time.perf_counter() - started
    try:
        assert elapsed < 0.5
        assert [r.id for r in result.sections["trials"].records] == ["t"]
        assert result.sections["authorisation"].no_data.reason == "source_unavailable"
    finally:
        hung.released = True
        await asyncio.gather(hung.task, return_exceptions=True)


async def test_cache_hit_refilters_out_of_scope_records(tree, memory_cache):
    connector = FakeConnector("EMA", "authorisation")
    cache = memory_cache
    key = cache_key(drug="trastuzumab", condition="C50.9", region="PT")
    cache.put("EMA", key, [make_record("us", "US"), make_record("pt", "PT")])
    result = await make_reconciler([connector], tree, cache=cache).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert connector.calls == 0
    assert [r.id for r in result.sections["authorisation"].records] == ["pt"]


async def test_same_section_connectors_do_not_share_cache_entries(tree, memory_cache):
    first = FakeConnector("CT", "trials", records=[make_record("a", "PT", "trial")])
    second = FakeConnector("CT2", "trials", records=[make_record("b", "PT", "trial")])
    cache = memory_cache
    reconciler = make_reconciler([first, second], tree, cache=cache)
    await reconciler.regional_status("trastuzumab", "C50.9", "PT")
    key = cache_key(drug="trastuzumab", condition="C50.9", region="PT")
    assert [r.id for r in cache.get("CT", key)] == ["a"]
    assert [r.id for r in cache.get("CT2", key)] == ["b"]


async def test_cached_empty_list_gives_not_found_without_calling_connector(tree, memory_cache):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("p", "PT")])
    cache = memory_cache
    key = cache_key(drug="trastuzumab", condition="C50.9", region="PT")
    cache.put("EMA", key, [])
    result = await make_reconciler([connector], tree, cache=cache).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert connector.calls == 0
    assert result.sections["authorisation"].no_data.reason == "not_found"


async def test_eu_target_with_only_global_record_notes_global_level(tree):
    connector = FakeConnector("EMA", "authorisation", records=[make_record("g", "global")])
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "EU"
    )
    assert "no EU record; showing global-level" in result.sections["authorisation"].notes


async def test_section_filters_to_scope_chain_and_allows_no_drug(tree):
    connector = FakeConnector(
        "G", "guidelines", records=[make_record("us1", "US"), make_record("eu1", "EU")]
    )
    other = FakeConnector("EMA", "authorisation", records=[make_record("pt1", "PT")])
    section = await make_reconciler([connector, other], tree).section(
        "guidelines", drug=None, condition="C50.9", region="PT"
    )
    assert [r.id for r in section.records] == ["eu1"]
    assert connector.queries[0].drug is None
    assert other.calls == 0


async def test_section_unusable_condition_is_not_found_without_calls(tree):
    connector = FakeConnector("G", "guidelines", records=[make_record("eu1", "EU")])
    section = await make_reconciler([connector], tree).section(
        "guidelines", drug=None, condition="not a code!", region="PT"
    )
    assert section.no_data is not None and section.no_data.reason == "not_found"
    assert connector.calls == 0


async def test_section_unknown_name_raises_value_error(tree):
    with pytest.raises(ValueError):
        await make_reconciler([], tree).section("nope", drug=None, condition=None, region="PT")


async def test_section_unknown_region_raises(tree):
    with pytest.raises(UnknownRegion):
        await make_reconciler([], tree).section(
            "guidelines", drug=None, condition="C50.9", region="XX"
        )


async def test_section_without_connector_is_out_of_scope(tree):
    section = await make_reconciler([], tree).section(
        "trials", drug="trastuzumab", condition="C50.9", region="PT"
    )
    assert section.no_data is not None and section.no_data.reason == "out_of_scope_v1"


async def test_section_failure_is_source_unavailable(tree):
    connector = FakeConnector("EMA", "authorisation", exc=SourceUnavailable("boom"))
    section = await make_reconciler([connector], tree).section(
        "authorisation", drug="trastuzumab", condition="C50.9", region="PT"
    )
    assert section.no_data is not None and section.no_data.reason == "source_unavailable"


async def test_guidelines_fewer_conditions_first_within_region(tree):
    connector = FakeConnector(
        "G",
        "guidelines",
        records=[
            make_record("broad", "EU", "guideline", ["C18", "C19", "C20"]),
            make_record("narrow", "EU", "guideline", ["C18"]),
        ],
    )
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert [r.id for r in result.sections["guidelines"].records] == ["narrow", "broad"]


async def test_guidelines_equal_specificity_keeps_connector_order(tree):
    connector = FakeConnector(
        "G",
        "guidelines",
        records=[
            make_record("first", "EU", "guideline", ["C18"]),
            make_record("second", "EU", "guideline", ["C19"]),
        ],
    )
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert [r.id for r in result.sections["guidelines"].records] == ["first", "second"]


async def test_guidelines_region_still_outranks_specificity(tree):
    connector = FakeConnector(
        "G",
        "guidelines",
        records=[
            make_record("eu_narrow", "EU", "guideline", ["C18"]),
            make_record("pt_broad", "PT", "guideline", ["C18", "C19", "C20"]),
        ],
    )
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert [r.id for r in result.sections["guidelines"].records] == ["pt_broad", "eu_narrow"]


async def test_medicine_records_ignore_condition_count(tree):
    connector = FakeConnector(
        "EMA",
        "authorisation",
        records=[
            make_record("many", "EU", "medicine", ["C18", "C19"]),
            make_record("none", "EU", "medicine", []),
        ],
    )
    result = await make_reconciler([connector], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert [r.id for r in result.sections["authorisation"].records] == ["many", "none"]


# --- reference_elsewhere group -------------------------------------------------

FIXTURE_REF = FIXTURE_WITH_US + "  UK: global\n"


@pytest.fixture
def ref_tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions_ref.yaml"
    path.write_text(FIXTURE_REF, encoding="utf-8")
    return RegionTree.load(path)


def ref_record(
    record_id: str, region: str, kind: str = "guideline", flag: object = True,
    conditions: list[str] | None = None,
) -> Record:
    rec = make_record(record_id, region, kind=kind, conditions=conditions)
    payload = dict(rec.payload)
    if flag is not None:
        payload["reference_only"] = flag
    return rec.model_copy(update={"payload": payload})


def ref_section(ref_tree, connectors, region="PT"):
    rec = Reconciler(connectors, ref_tree)
    return asyncio.run(rec.section("guidelines", drug="trastuzumab", condition="C50.9", region=region))


def test_reference_group_orders_uk_before_us_and_leaves_records(ref_tree):
    us = ref_record("us1", "US")
    uk = ref_record("uk1", "UK")
    sec = ref_section(ref_tree, [FakeConnector("g", "guidelines", [us, uk])])
    assert [r.id for r in sec.reference_elsewhere] == ["uk1", "us1"]
    assert sec.records == []
    assert sec.no_data is not None and sec.no_data.reason == "not_found"
    assert not any("showing" in n for n in sec.notes)
    expected = reference_note(2, ["UK", "US"], "PT")
    assert sec.notes.count(expected) == 1


def test_reference_note_text():
    assert reference_note(2, ["UK", "US"], "PT") == (
        "2 reference guideline(s) issued outside PT's scope (UK, US) are listed separately; "
        "national approval, reimbursement and practice may differ"
    )


def test_reference_group_orders_by_specificity_then_original(ref_tree):
    a = ref_record("a", "US", conditions=["x", "y"])
    b = ref_record("b", "US", conditions=["x"])
    c = ref_record("c", "US", conditions=["x"])
    sec = ref_section(ref_tree, [FakeConnector("g", "guidelines", [a, b, c])])
    assert [r.id for r in sec.reference_elsewhere] == ["b", "c", "a"]


def test_unflagged_and_wrong_kind_and_wrong_section_dropped(ref_tree):
    recs = [
        ref_record("unflagged", "US", flag=None),
        ref_record("false", "US", flag=False),
        ref_record("truthy", "US", flag="true"),
        ref_record("med", "US", kind="medicine"),
    ]
    sec = ref_section(ref_tree, [FakeConnector("g", "guidelines", recs)])
    assert sec.reference_elsewhere == [] and sec.records == []
    assert not any("reference guideline" in n for n in sec.notes)
    auth = Reconciler(
        [FakeConnector("a", "authorisation", [ref_record("authg", "US")])], ref_tree
    )
    sec2 = asyncio.run(auth.section("authorisation", drug="trastuzumab", condition=None, region="PT"))
    assert sec2.reference_elsewhere == [] and sec2.records == []


def test_reference_group_for_query_us_is_ordinary_record(ref_tree):
    us = ref_record("us1", "US")
    sec = ref_section(ref_tree, [FakeConnector("g", "guidelines", [us])], region="US")
    assert [r.id for r in sec.records] == ["us1"]
    assert sec.reference_elsewhere == []
    assert not any("reference guideline" in n for n in sec.notes)


def test_failing_guidelines_connector_gives_empty_group(ref_tree):
    sec = ref_section(
        ref_tree, [FakeConnector("g", "guidelines", exc=SourceUnavailable("x"))]
    )
    assert sec.no_data is not None and sec.no_data.reason == "source_unavailable"
    assert sec.reference_elsewhere == []


def test_eligible_records_unaffected_by_reference_group(ref_tree):
    eu = ref_record("eu1", "EU", flag=None)
    us = ref_record("us1", "US")
    sec = ref_section(ref_tree, [FakeConnector("g", "guidelines", [eu, us])])
    assert [r.id for r in sec.records] == ["eu1"]
    assert [r.id for r in sec.reference_elsewhere] == ["us1"]
    assert sec.no_data is None


def _checked(record: Record, status: str, **extra) -> Record:
    record.payload["edition_check"] = {"status": status, **extra}
    return record


async def test_edition_check_note_counts_and_retracted_sort_last(tree):
    connector = FakeConnector(
        "G",
        "guidelines",
        records=[
            _checked(make_record("retr", "EU", "guideline", ["C18"]), "retracted"),
            _checked(make_record("newer", "EU", "guideline", ["C18"]), "newer_found"),
            _checked(make_record("corr", "EU", "guideline", ["C18"]), "corrected"),
            _checked(make_record("unk", "EU", "guideline", ["C18"]), "unknown"),
            _checked(make_record("cur", "EU", "guideline", ["C18"]), "current"),
        ],
    )
    section = (
        await make_reconciler([connector], tree).regional_status("trastuzumab", "C18.9", "PT")
    ).sections["guidelines"]
    assert [r.id for r in section.records] == ["newer", "corr", "unk", "cur", "retr"]
    assert "edition check: 1 newer edition(s), 1 corrected, 1 retracted" in section.notes
    assert "edition check unavailable for 1 guideline(s)" in section.notes


async def test_no_edition_note_without_findings_and_other_sections_unchanged(tree):
    guidelines = FakeConnector(
        "G", "guidelines", records=[_checked(make_record("a", "EU", "guideline", ["C18"]), "current")]
    )
    meds = FakeConnector(
        "EMA", "authorisation",
        records=[_checked(make_record("m1", "EU"), "retracted"), make_record("m2", "EU")],
    )
    result = await make_reconciler([guidelines, meds], tree).regional_status(
        "trastuzumab", "C18.9", "PT"
    )
    assert not any("edition check" in n for n in result.sections["guidelines"].notes)
    assert [r.id for r in result.sections["authorisation"].records] == ["m1", "m2"]
    assert not any("edition check" in n for n in result.sections["authorisation"].notes)

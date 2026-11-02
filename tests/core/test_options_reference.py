from datetime import UTC, date, datetime

import pytest

from oncobuddy.core.options import treatment_options
from oncobuddy.core.profile import parse_profile
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.records import Entities, Record
from oncobuddy.core.regions import RegionTree


class Fake:
    def __init__(self, name, section, records=None):
        self.name, self.section = name, section
        self.records = list(records or [])

    async def fetch(self, query):
        return list(self.records)


def med(drug, region="EU"):
    return Record(
        id=f"med:{drug}:{region}", kind="medicine", entities=Entities(drugs=[drug]),
        region=region, source="EMA", source_url="https://example.org/m",
        valid_as_of=date(2026, 1, 1), retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="CC-BY-4.0", payload={},
    )


def guideline_record(id, region):
    return Record(
        id=id, kind="guideline",
        entities=Entities(conditions=["C50"], drugs=["trastuzumab"]), region=region,
        source="X", source_url="https://example.org/g", valid_as_of="unknown",
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC), licence="CC-BY-4.0",
        payload={"applies_to": {
            "conditions": ["C50"], "stages": ["I", "II", "III", "IV"],
            "required_biomarkers": {}, "line_of_therapy": "any",
            "interventions": ["trastuzumab"],
        }},
    )


@pytest.fixture
def tree(tmp_path):
    path = tmp_path / "regions.yaml"
    path.write_text(
        "regions:\n  global: null\n  EU: global\n  PT: EU\n  US: global\n", encoding="utf-8"
    )
    return RegionTree.load(path)


def profile(**kw):
    data = {"cancer_type": "C50.9", "stage": "II", "prior_lines": []}
    data.update(kw)
    return parse_profile(data)


def with_flag(record, **extra):
    payload = {**record.payload, "reference_only": True, **extra}
    return record.model_copy(update={"payload": payload})


def build(tree, guidelines):
    return Reconciler(
        [
            Fake("G", "guidelines", guidelines),
            Fake("EMA", "authorisation", [med("trastuzumab")]),
            Fake("CT", "trials"),
        ],
        tree,
    )


async def test_reference_record_never_changes_options(tree):
    eu = guideline_record("eu1", "EU")
    us = with_flag(guideline_record("us1", "US"))
    base = await treatment_options(profile(), "PT", build(tree, [eu]))
    both = await treatment_options(profile(), "PT", build(tree, [eu, us]))
    assert both.options == base.options
    assert both.excluded == base.excluded
    assert both.guidelines_in_scope == base.guidelines_in_scope
    assert [o.caveats for o in both.options] == [o.caveats for o in base.options]
    assert [r.id for r in both.reference_elsewhere] == ["us1"]
    assert base.reference_elsewhere == []
    assert both.read_plan == base.read_plan
    assert not any("us1" in n for n in both.notes)


async def test_only_reference_record_lists_group_with_no_options(tree):
    us = with_flag(guideline_record("us1", "US"), access={"method": "browser"})
    result = await treatment_options(profile(), "PT", build(tree, [us]))
    assert result.options == []
    assert result.no_data is not None and result.no_data.reason == "not_found"
    assert [r.id for r in result.reference_elsewhere] == ["us1"]
    assert result.read_plan


async def test_only_reference_record_without_browser_has_no_read_plan(tree):
    us = with_flag(guideline_record("us1", "US"))
    result = await treatment_options(profile(), "PT", build(tree, [us]))
    assert [r.id for r in result.reference_elsewhere] == ["us1"]
    assert result.read_plan == []


async def test_reference_browser_with_eu_option_sets_read_plan(tree):
    eu = guideline_record("eu1", "EU")
    us = with_flag(guideline_record("us1", "US"), access={"method": "browser"})
    result = await treatment_options(profile(), "PT", build(tree, [eu, us]))
    assert result.read_plan
    assert len(result.options) == 1


async def test_reference_never_matched_even_when_profile_would_exclude(tree):
    us = with_flag(guideline_record("us1", "US"))
    us.payload["applies_to"] = {**us.payload["applies_to"], "stages": ["IV"]}
    result = await treatment_options(profile(stage="II"), "PT", build(tree, [us]))
    assert result.excluded == []
    assert [r.id for r in result.reference_elsewhere] == ["us1"]


async def test_guidelines_for_real_registry_keeps_reference_records_out_of_records():
    from oncobuddy.connectors.guidelines import GuidelineConnector
    from oncobuddy.core.regions import RegionTree
    from oncobuddy.server import OncoTools

    tools = OncoTools(Reconciler([GuidelineConnector()], RegionTree.load()))
    for region in ("PT", "EU", "US"):
        section = (await tools.guidelines_for("C50.9", region))["section"]
        records, ref = section["records"], section["reference_elsewhere"]
        assert not {r["id"] for r in records} & {r["id"] for r in ref}, region
        if region == "US":
            assert records and all(r["region"] == "US" for r in records)
            assert ref == []
            continue
        assert records and all(r["region"] in {"PT", "EU"} for r in records), region
        assert ref and all(r["region"] in {"US", "UK"} for r in ref), region
        assert all(r["payload"]["reference_only"] is True for r in ref), region
    ids = [r["id"] for r in (await tools.guidelines_for("C50.9", "PT"))["section"]["reference_elsewhere"]]
    assert "guideline:nccn-breast-cancer" in ids
    assert all(i.startswith(("guideline:nccn-", "guideline:asco-")) for i in ids)

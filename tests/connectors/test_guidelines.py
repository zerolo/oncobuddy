from pathlib import Path

import pytest

from oncobuddy.connectors.guidelines import GuidelineConnector
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.regions import RegionTree

TEMPLATE = """\
id: {id}
title: Title {id}
issuer: Demo Society
url: https://example.org/{id}
region: {region}
published: "2024-01-31"
valid_as_of: "2024-01-31"
licence: restricted-link-only
reviewed_by: {reviewer}
{review_extra}evidence_grade: null
own_words: false
summary: ""
applies_to:
  conditions: {conditions}
  stages: ["IV"]
  required_biomarkers: {{}}
  line_of_therapy: first
  interventions: {interventions}
"""


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    entries = [
        ("breast-scope", "EU", '["C50"]', "[]"),
        ("breast-drug", "EU", '["C50"]', "[trastuzumab]"),
        ("lung-drug", "EU", '["C34"]', "[osimertinib]"),
        ("colorectal", "EU", '["C18", "C19", "C20"]', "[]"),
    ]
    for id_, region, conditions, interventions in entries:
        named = interventions != "[]"  # spec section 4: interventions need a reviewed entry
        text = TEMPLATE.format(
            id=id_,
            region=region,
            conditions=conditions,
            interventions=interventions,
            reviewer="Dr Example" if named else "unreviewed-seed",
            review_extra='reviewed_on: "2026-11-01"\nrecommendation_ref: "Table 4"\n'
            if named
            else "",
        )
        (tmp_path / f"{id_}.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def ids(records) -> set[str]:
    return {r.id for r in records}


async def test_connector_matches_category_and_drug_rules(directory):
    connector = GuidelineConnector(directory)
    assert connector.name == "Curated guideline index"
    assert connector.section == "guidelines"

    both = await connector.fetch(Query(drug="trastuzumab", condition="C50.9", region="PT"))
    assert ids(both) == {"guideline:breast-scope", "guideline:breast-drug"}  # empty = scope-only

    other_drug = await connector.fetch(Query(drug="osimertinib", condition="c50", region="PT"))
    assert ids(other_drug) == {"guideline:breast-scope"}

    no_drug = await connector.fetch(Query(drug=None, condition="C50", region="PT"))
    assert ids(no_drug) == {"guideline:breast-scope", "guideline:breast-drug"}

    crc = await connector.fetch(Query(drug="anything", condition="C19", region="PT"))
    assert ids(crc) == {"guideline:colorectal"}

    assert await connector.fetch(Query(drug=None, condition="C99", region="PT")) == []


async def test_connector_rejects_bad_condition_shape(directory):
    connector = GuidelineConnector(directory)
    with pytest.raises(ValueError):
        await connector.fetch(Query(drug=None, condition="breast cancer", region="PT"))


async def test_morphology_code_returns_empty_not_error(directory):
    connector = GuidelineConnector(directory)
    assert await connector.fetch(Query(drug=None, condition="8500/3", region="PT")) == []


def test_local_connectors_are_not_cacheable():
    from oncobuddy.connectors.icd import IcdConnector

    assert GuidelineConnector.cacheable is False
    assert IcdConnector.cacheable is False


async def test_connector_none_condition_returns_empty(directory):
    connector = GuidelineConnector(directory)
    assert await connector.fetch(Query(drug="trastuzumab", condition=None, region="PT")) == []


async def test_returned_records_are_copies(directory):
    connector = GuidelineConnector(directory)
    query = Query(drug=None, condition="C50", region="PT")
    first = await connector.fetch(query)
    first[0].entities.conditions.append("C99")
    first[0].payload["title"] = "mutated"
    first[0].payload["applies_to"]["stages"].append("I")
    second = await connector.fetch(query)
    for record in second:
        assert record.entities.conditions == ["C50"]
        assert record.payload["title"].startswith("Title ")
        assert record.payload["applies_to"]["stages"] == ["IV"]


async def test_via_reconciler_guidelines_section(directory):
    tree = RegionTree.load()
    reconciler = Reconciler([GuidelineConnector(directory)], tree)
    status = await reconciler.regional_status("trastuzumab", "C50.9", "PT")
    section = status.sections["guidelines"]
    assert section.records
    assert all(r.region == "EU" for r in section.records)
    assert "no PT record; showing EU-level" in section.notes


async def test_scope_only_and_drug_match_markers(directory):
    connector = GuidelineConnector(directory)
    by_id = {
        r.id: r
        for r in await connector.fetch(Query(drug="trastuzumab", condition="C50", region="PT"))
    }
    assert by_id["guideline:breast-scope"].payload["scope_only"] is True
    assert by_id["guideline:breast-scope"].payload["drug_match"] == "scope-only"
    assert by_id["guideline:breast-drug"].payload["scope_only"] is False
    assert by_id["guideline:breast-drug"].payload["drug_match"] == "named"

    nodrug = await connector.fetch(Query(drug=None, condition="C50", region="PT"))
    assert all("drug_match" not in r.payload for r in nodrug)
    assert {r.id: r.payload["scope_only"] for r in nodrug} == {
        "guideline:breast-scope": True,
        "guideline:breast-drug": False,
    }
    # the loaded list is not mutated
    assert all("scope_only" not in r.payload for r in connector._records)


async def test_packaged_seeds_load_through_connector():
    connector = GuidelineConnector()
    records = await connector.fetch(Query(drug=None, condition="C50", region="EU"))
    assert records and all(r.payload["review_status"] == "unreviewed" for r in records)


async def test_connector_annotates_with_checker_and_never_mutates_the_index(tmp_path):
    from oncobuddy.connectors.guidelines import GuidelineConnector
    from oncobuddy.core.query import Query

    class Marker:
        async def annotate(self, records):
            for record in records:
                record.payload["edition_check"] = {"status": "current"}

    plain = GuidelineConnector()
    marked = GuidelineConnector(checker=Marker())
    query = Query(drug=None, condition="C50.9", region="PT")
    assert all("edition_check" not in r.payload for r in await plain.fetch(query))
    got = await marked.fetch(query)
    assert got and all(r.payload["edition_check"] == {"status": "current"} for r in got)
    again = await GuidelineConnector().fetch(query)
    assert all("edition_check" not in r.payload for r in again)

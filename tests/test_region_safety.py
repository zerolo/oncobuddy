"""Region safety matrix: results never leave the queried region's scope chain."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import NOTICE, V1_SECTIONS, Reconciler
from oncobuddy.core.records import Entities, Record
from oncobuddy.core.regions import RegionTree

TREE_YAML = """\
regions:
  global: null
  EU: global
  PT: EU
  ES: EU
  US: global
"""
REGIONS = ["global", "EU", "PT", "ES", "US"]


def rec(record_id: str, region: str) -> Record:
    return Record(
        id=record_id,
        kind="medicine",
        entities=Entities(drugs=["trastuzumab"]),
        region=region,
        source="Fake",
        source_url="https://example.org/" + record_id,
        valid_as_of=date(2026, 1, 1),
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="CC-BY-4.0",
        payload={},
    )


class Fake:
    def __init__(self, section: str, records=(), exc=None) -> None:
        self.name = f"fake-{section}"
        self.section = section
        self._records = list(records)
        self._exc = exc

    async def fetch(self, query: Query) -> list[Record]:
        if self._exc:
            raise self._exc
        return list(self._records)


@pytest.fixture
def tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions.yaml"
    path.write_text(TREE_YAML, encoding="utf-8")
    return RegionTree.load(path)


def mixed(section: str) -> Fake:
    return Fake(section, [rec(f"{section}-{r}", r) for r in REGIONS])


def test_matrix_covers_every_tree_region(tree):
    assert sorted(tree.regions) == sorted(REGIONS)


@pytest.mark.parametrize("region", REGIONS)
async def test_only_scope_chain_records_returned(tree, region):
    reconciler = Reconciler([mixed(s) for s in V1_SECTIONS], tree)
    chain = tree.scope_chain(region)
    status = await reconciler.regional_status("trastuzumab", "C50.9", region)
    assert status.query_region == region
    assert status.scope_chain == chain
    assert status.notice == NOTICE
    for name, section in status.sections.items():
        assert section.records, name
        assert {r.region for r in section.records} == set(chain), name
        assert "US" not in {r.region for r in section.records} or region == "US"
    single = await reconciler.section(
        "authorisation", drug="trastuzumab", condition=None, region=region
    )
    assert {r.region for r in single.records} <= set(chain)


@pytest.mark.parametrize("region", REGIONS)
async def test_all_connectors_failing_yields_only_source_unavailable(tree, region):
    reconciler = Reconciler(
        [Fake(s, exc=SourceUnavailable("secret detail")) for s in V1_SECTIONS], tree
    )
    status = await reconciler.regional_status("trastuzumab", "C50.9", region)
    for section in status.sections.values():
        assert section.records == []
        assert section.no_data is not None
        assert section.no_data.reason == "source_unavailable"
    assert "secret detail" not in status.model_dump_json()

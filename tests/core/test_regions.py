from pathlib import Path

import pytest

from oncobuddy.core.regions import RegionTree, UnknownRegion

FIXTURE_WITH_US = """\
regions:
  global: null
  EU: global
  PT: EU
  ES: EU
  US: global
"""


@pytest.fixture
def tree() -> RegionTree:
    return RegionTree.load()


def test_pt_chain_is_pt_eu_global(tree):
    assert tree.scope_chain("PT") == ["PT", "EU", "global"]


def test_us_and_uk_chains(tree):
    assert tree.scope_chain("US") == ["US", "global"]
    assert tree.scope_chain("UK") == ["UK", "global"]
    assert tree.scope_chain("ES") == ["ES", "EU", "global"]
    assert not tree.eligible("US", "PT")
    assert not tree.eligible("UK", "PT")
    assert not tree.eligible("PT", "UK")


def test_eu_chain(tree):
    assert tree.scope_chain("EU") == ["EU", "global"]


@pytest.mark.parametrize("bad", ["Portugal", "pt ", "pt", "FR"])
def test_unknown_region_raises(tree, bad):
    with pytest.raises(UnknownRegion) as excinfo:
        tree.scope_chain(bad)
    assert repr(bad) in str(excinfo.value)
    assert "PT" in str(excinfo.value)


def test_eligibility(tree):
    assert tree.eligible("PT", "PT")
    assert tree.eligible("EU", "PT")
    assert tree.eligible("global", "PT")
    assert not tree.eligible("ES", "PT")


def test_eligibility_unknown_record_region_is_false(tree):
    assert not tree.eligible("US", "PT")


def test_eligibility_unknown_target_raises(tree):
    with pytest.raises(UnknownRegion):
        tree.eligible("PT", "Portugal")


def test_tree_from_fixture_with_us_branch(tmp_path: Path):
    path = tmp_path / "regions.yaml"
    path.write_text(FIXTURE_WITH_US, encoding="utf-8")
    tree = RegionTree.load(path)
    assert tree.scope_chain("PT") == ["PT", "EU", "global"]
    assert not tree.eligible("US", "PT")
    assert tree.eligible("US", "US")


def test_load_rejects_dangling_parent(tmp_path: Path):
    path = tmp_path / "regions.yaml"
    path.write_text("regions:\n  global: null\n  PT: EU\n", encoding="utf-8")
    with pytest.raises(ValueError):
        RegionTree.load(path)


def test_load_rejects_cycle(tmp_path: Path):
    path = tmp_path / "regions.yaml"
    path.write_text(
        "regions:\n  global: null\n  A: B\n  B: A\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        RegionTree.load(path)


def test_load_rejects_unquoted_boolean_key(tmp_path: Path):
    path = tmp_path / "regions.yaml"
    path.write_text("regions:\n  global: null\n  EU: global\n  NO: EU\n", encoding="utf-8")
    with pytest.raises(ValueError, match="quote"):
        RegionTree.load(path)


def test_load_rejects_unquoted_boolean_parent(tmp_path: Path):
    path = tmp_path / "regions.yaml"
    path.write_text(
        'regions:\n  global: null\n  EU: global\n  PT: NO\n  "NO": EU\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="quote"):
        RegionTree.load(path)


def test_quoted_norway_region_loads(tmp_path: Path):
    path = tmp_path / "regions.yaml"
    path.write_text(
        'regions:\n  global: null\n  EU: global\n  PT: EU\n  "NO": EU\n', encoding="utf-8"
    )
    tree = RegionTree.load(path)
    assert tree.scope_chain("NO") == ["NO", "EU", "global"]
    assert tree.scope_chain("PT") == ["PT", "EU", "global"]

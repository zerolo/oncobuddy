from pathlib import Path

import pytest

from oncobuddy.connectors.guidelines import GuidelineConnector
from oncobuddy.core.options import treatment_options
from oncobuddy.core.profile import parse_profile
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.regions import RegionTree

ENTRY = """\
id: {id}
title: Title
issuer: Demo Society
url: https://example.org/g
region: EU
published: "2024-01-31"
valid_as_of: "2024-01-31"
licence: restricted-link-only
reviewed_by: {reviewer}
{extra}evidence_grade: null
own_words: false
summary: ""
recommendation_ref: "Table 4"
applies_to:
  conditions: ["C50"]
  stages: ["II"]
  required_biomarkers: {{}}
  line_of_therapy: first
  interventions: [trastuzumab]
"""


@pytest.fixture
def tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions.yaml"
    path.write_text(
        "regions:\n  global: null\n  EU: global\n  PT: EU\n  US: global\n", encoding="utf-8"
    )
    return RegionTree.load(path)


def profile():
    return parse_profile({"cancer_type": "C50.9", "stage": "II", "prior_lines": []})


async def test_reviewed_entry_yields_named_option(tree, tmp_path):
    d = tmp_path / "g"
    d.mkdir()
    (d / "r.yaml").write_text(
        ENTRY.format(id="rev", reviewer="dr-demo", extra='reviewed_on: "2026-11-01"\n'),
        encoding="utf-8",
    )
    reconciler = Reconciler([GuidelineConnector(directory=d)], tree)
    result = await treatment_options(profile(), "PT", reconciler)
    assert [o.intervention for o in result.options] == ["trastuzumab"]
    option = result.options[0]
    assert option.guideline.id == "guideline:rev"
    assert "guideline content is link-only; not evaluated" in option.caveats
    assert "cited edition; later versions may exist" in option.caveats
    assert "guideline entry unreviewed (seed)" not in option.caveats


MSI_ENTRY = """\
id: msi
title: MSI demo
issuer: Demo Society
url: https://example.org/msi
region: EU
published: "2024-01-31"
valid_as_of: "2024-01-31"
licence: restricted-link-only
reviewed_by: Dr Demo
reviewed_on: "2026-11-01"
recommendation_ref: Table 4
evidence_grade: null
own_words: false
summary: ""
applies_to:
  conditions: ["C18"]
  stages: ["IV"]
  required_biomarkers:
    MSI-H: positive
  line_of_therapy: first
  interventions: [pembrolizumab]
"""


async def _msi_options(tmp_path, biomarkers):
    from oncobuddy.core.profile import parse_profile
    from oncobuddy.core.reconciler import Reconciler as R

    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "msi.yaml").write_text(MSI_ENTRY, encoding="utf-8")
    tree_path = tmp_path / "regions.yaml"
    tree_path.write_text("regions:\n  global: null\n  EU: global\n  PT: EU\n", encoding="utf-8")
    from oncobuddy.core.regions import RegionTree as T

    profile = parse_profile(
        {"cancer_type": "C18.9", "stage": "IV", "prior_lines": [], "biomarkers": biomarkers}
    )
    return await treatment_options(
        profile, "PT", R([GuidelineConnector(directory=gdir)], T.load(tree_path))
    )


async def test_dmmr_alias_matches_msi_h_entry(tmp_path):
    result = await _msi_options(tmp_path, {"DMMR": "positive"})
    assert [o.intervention for o in result.options] == ["pembrolizumab"]
    assert not any("vocabulary" in n for n in result.notes)


async def test_mss_alias_excludes_msi_h_entry(tmp_path):
    result = await _msi_options(tmp_path, {"MSS": "positive"})
    assert result.options == []
    assert result.excluded and "MSI-H" in result.excluded[0].reason


async def test_mss_negative_does_not_qualify_for_msi_h_entry(tmp_path):
    result = await _msi_options(tmp_path, {"MSS": "negative"})
    assert [o.intervention for o in result.options] == ["pembrolizumab"]  # returned, with caveat
    assert any("MSI-H status unknown" in c for c in result.options[0].caveats)


async def test_unknown_biomarker_key_is_accepted_with_a_count_note_not_the_key(tmp_path):
    result = await _msi_options(tmp_path, {"ZZZ9": "positive", "MSI-H": "positive"})
    assert [o.intervention for o in result.options] == ["pembrolizumab"]
    expected = "1 biomarker key not in the oncoBuddy vocabulary; not used for matching"
    assert expected in result.notes
    assert not any("ZZZ9" in note for note in result.notes)  # profile keys are never echoed

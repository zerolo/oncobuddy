from pathlib import Path

import pytest

from oncobuddy.connectors.guidelines import GuidelineConnector
from oncobuddy.core.access import AGENT_PROTOCOL
from oncobuddy.core.options import BROWSER_NOTE, treatment_options
from oncobuddy.core.profile import parse_profile
from oncobuddy.core.readplan import READ_PLAN_CLOSING, build_read_plan
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.regions import RegionTree

REVIEWED = """\
id: rev
title: Reviewed demo
issuer: Demo Society
url: https://example.org/rev
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
  conditions: ["C50"]
  stages: ["II"]
  required_biomarkers: {}
  line_of_therapy: first
  interventions: [trastuzumab]
"""


@pytest.fixture
def tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions.yaml"
    path.write_text("regions:\n  global: null\n  EU: global\n  PT: EU\n", encoding="utf-8")
    return RegionTree.load(path)


def profile():
    return parse_profile({"cancer_type": "C50.9", "stage": "II", "prior_lines": []})


async def test_seed_guideline_in_scope_carries_browser_access_and_note(tree):
    result = await treatment_options(profile(), "PT", Reconciler([GuidelineConnector()], tree))
    (guideline,) = [g for g in result.guidelines_in_scope if g.id.startswith("guideline:esmo-")]
    assert guideline.payload["access"]["method"] == "browser"
    assert guideline.payload["access"]["entry_url"] == guideline.source_url
    expected = f"{guideline.id}: {BROWSER_NOTE}"
    assert result.notes.count(expected) == 1


async def test_reviewed_entry_with_interventions_has_no_browser_note(tree, tmp_path):
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "rev.yaml").write_text(REVIEWED, encoding="utf-8")
    result = await treatment_options(
        profile(), "PT", Reconciler([GuidelineConnector(directory=gdir)], tree)
    )
    assert [o.intervention for o in result.options] == ["trastuzumab"]
    assert not any(BROWSER_NOTE in note for note in result.notes)


async def test_options_result_carries_agent_protocol(tree):
    result = await treatment_options(profile(), "PT", Reconciler([GuidelineConnector()], tree))
    assert result.agent_protocol == AGENT_PROTOCOL


STALE_NOTE = "registry entry last verified more than 540 days ago"


async def _notes_for(tree, tmp_path, last_verified):
    gdir = tmp_path / "g"
    gdir.mkdir()
    text = REVIEWED.replace("interventions: [trastuzumab]", "interventions: []")
    text += f'last_verified: "{last_verified}"\n'
    (gdir / "rev.yaml").write_text(text, encoding="utf-8")
    result = await treatment_options(
        profile(), "PT", Reconciler([GuidelineConnector(directory=gdir)], tree)
    )
    assert [g.id for g in result.guidelines_in_scope] == ["guideline:rev"]
    return result.notes


async def test_stale_entry_adds_caveat_note(tree, tmp_path):
    notes = await _notes_for(tree, tmp_path, "2020-01-01")
    assert f"guideline:rev: {STALE_NOTE}" in notes


async def test_fresh_entry_has_no_stale_note(tree, tmp_path):
    notes = await _notes_for(tree, tmp_path, "2026-10-01")
    assert not any(STALE_NOTE in n for n in notes)


async def test_read_plan_present_for_browser_guidelines(tree):
    result = await treatment_options(profile(), "PT", Reconciler([GuidelineConnector()], tree))
    assert result.read_plan == build_read_plan(profile())
    assert result.read_plan[-1] == READ_PLAN_CLOSING


async def test_read_plan_empty_without_access(tree, tmp_path):
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "rev.yaml").write_text(REVIEWED, encoding="utf-8")
    result = await treatment_options(
        profile(), "PT", Reconciler([GuidelineConnector(directory=gdir)], tree)
    )
    assert result.options
    assert result.read_plan == []


async def _edition_notes(tree, tmp_path, check):
    gdir = tmp_path / "g"
    gdir.mkdir(exist_ok=True)
    text = REVIEWED.replace("interventions: [trastuzumab]", "interventions: []")
    (gdir / "rev.yaml").write_text(text, encoding="utf-8")

    class Marking(GuidelineConnector):
        async def fetch(self, query):
            records = await super().fetch(query)
            for record in records:
                record.payload["edition_check"] = check
            return records

    result = await treatment_options(profile(), "PT", Reconciler([Marking(directory=gdir)], tree))
    return result.notes


async def test_edition_findings_become_guideline_caveats(tree, tmp_path):
    newer = {"status": "newer_found", "newer": {"doi": "10.1/x", "title": "t", "date": "2026-04-14"}}
    assert "guideline:rev: newer edition found (2026-04-14); read it instead" in (
        await _edition_notes(tree, tmp_path, newer)
    )
    assert "guideline:rev: corrections published; read the notice" in (
        await _edition_notes(tree, tmp_path, {"status": "corrected"})
    )
    assert "guideline:rev: retracted" in await _edition_notes(tree, tmp_path, {"status": "retracted"})
    quiet = await _edition_notes(tree, tmp_path, {"status": "current"})
    assert not any("newer edition" in n or "retracted" in n or "corrections" in n for n in quiet)

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.connectors.guidelines import GuidelineConnector
from oncobuddy.core.cache import DEFAULT_TTLS, Cache
from oncobuddy.core.options import treatment_options
from oncobuddy.core.profile import ClinicalProfile, parse_profile
from oncobuddy.core.reconciler import NOTICE, Reconciler
from oncobuddy.core.records import Entities, Record
from oncobuddy.core.regions import RegionTree

TEMPLATE = """\
id: {id}
title: Title {id}
issuer: Demo Society
url: https://example.org/{id}
region: {region}
published: "2024-01-31"
valid_as_of: "2024-01-31"
licence: {licence}
reviewed_by: {reviewed_by}
{review_extra}evidence_grade: null
own_words: {own_words}
summary: "{summary}"
applies_to:
  conditions: ["C50"]
  stages: {stages}
  required_biomarkers: {biomarkers}
  line_of_therapy: {line}
  interventions: {interventions}
"""


def write_guideline(directory: Path, id: str, **kw) -> None:
    values = {
        "region": "EU",
        "licence": "CC-BY-4.0",
        "reviewed_by": "dr-demo",
        "own_words": "true",
        "summary": "own words",
        "stages": '["I", "II", "III", "IV"]',
        "biomarkers": "{}",
        "line": "first",
        "interventions": "[trastuzumab]",
    }
    values.update(kw)
    # Spec section 4: guidelines naming interventions need review date and locator.
    values["review_extra"] = (
        'reviewed_on: "2026-11-01"\nrecommendation_ref: "Table 4"\n'
        if values["interventions"].strip() != "[]"
        else ""
    )
    (directory / f"{id}.yaml").write_text(TEMPLATE.format(id=id, **values), encoding="utf-8")


class Fake:
    def __init__(self, name, section, records=None, exc=None):
        self.name, self.section = name, section
        self.records, self.exc = list(records or []), exc

    async def fetch(self, query):
        if self.exc:
            raise self.exc
        return list(self.records)


def med(drug: str, region: str = "EU") -> Record:
    return Record(
        id=f"med:{drug}:{region}",
        kind="medicine",
        entities=Entities(drugs=[drug]),
        region=region,
        source="EMA",
        source_url="https://example.org/m",
        valid_as_of=date(2026, 1, 1),
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="CC-BY-4.0",
        payload={},
    )


def guideline_record(id: str, region: str) -> Record:
    return Record(
        id=id,
        kind="guideline",
        entities=Entities(conditions=["C50"], drugs=["trastuzumab"]),
        region=region,
        source="X",
        source_url="https://example.org/g",
        valid_as_of="unknown",
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="CC-BY-4.0",
        payload={
            "applies_to": {
                "conditions": ["C50"],
                "stages": ["I", "II", "III", "IV"],
                "required_biomarkers": {},
                "line_of_therapy": "any",
                "interventions": ["trastuzumab"],
            }
        },
    )


@pytest.fixture
def tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions.yaml"
    path.write_text(
        "regions:\n  global: null\n  EU: global\n  PT: EU\n  US: global\n", encoding="utf-8"
    )
    return RegionTree.load(path)


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    d = tmp_path / "g"
    d.mkdir()
    return d


def profile(**kw) -> ClinicalProfile:
    data = {"cancer_type": "C50.9", "stage": "II", "prior_lines": []}
    data.update(kw)
    return parse_profile(data)


def rec(tree, gdir, auth=None, trials=None, cache=None):
    connectors = [
        GuidelineConnector(directory=gdir),
        auth or Fake("EMA", "authorisation", [med("trastuzumab")]),
        trials or Fake("CT", "trials"),
    ]
    return Reconciler(connectors, tree, cache=cache)


async def test_every_option_has_a_guideline_record(tree, gdir):
    write_guideline(gdir, "g1", interventions="[trastuzumab, pertuzumab]")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert [o.intervention for o in result.options] == ["pertuzumab", "trastuzumab"]
    for option in result.options:
        assert option.guideline.kind == "guideline"
        assert option.intervention in option.guideline.entities.drugs
    assert result.notice == NOTICE
    assert result.scope_chain == ["PT", "EU", "global"]


async def test_scope_only_guidelines_go_to_guidelines_in_scope_not_options(tree, gdir):
    write_guideline(gdir, "scope", interventions="[]")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert result.options == []
    assert [g.id for g in result.guidelines_in_scope] == ["guideline:scope"]
    assert result.no_data is not None and result.no_data.reason == "not_found"
    assert "no guideline names an intervention for this profile in scope" in result.notes


async def test_no_supporting_guideline_returns_no_data_not_empty_success(tree, gdir):
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert result.options == []
    assert result.no_data is not None and result.no_data.reason == "not_found"
    assert result.notes


async def test_real_seed_guidelines_yield_in_scope_links_and_no_options(tree):
    reconciler = Reconciler([GuidelineConnector()], tree)
    result = await treatment_options(profile(), "PT", reconciler)
    assert result.options == []
    esmo = [g.id for g in result.guidelines_in_scope if g.id.startswith("guideline:esmo-")]
    assert esmo == ["guideline:esmo-early-breast-cancer"]
    assert result.no_data is not None and result.no_data.reason == "not_found"


async def test_unknown_stage_gives_caveat_not_exclusion(tree, gdir):
    write_guideline(gdir, "g1", stages='["I", "II"]')
    result = await treatment_options(profile(stage="unknown"), "PT", rec(tree, gdir))
    assert result.excluded == []
    assert "stage not provided; guideline scope is I, II" in result.options[0].caveats


async def test_stage_outside_scope_excluded_with_reason(tree, gdir):
    write_guideline(gdir, "g1", stages='["I", "II"]')
    result = await treatment_options(profile(stage="IV"), "PT", rec(tree, gdir))
    assert result.options == []
    assert [(e.guideline_id, e.reason) for e in result.excluded] == [
        ("guideline:g1", "stage IV outside guideline scope I, II")
    ]


async def test_unknown_biomarker_gives_caveat_not_exclusion(tree, gdir):
    write_guideline(gdir, "g1", biomarkers="{HER2: positive}")
    for biomarkers in ({}, {"HER2": "unknown"}):
        result = await treatment_options(profile(biomarkers=biomarkers), "PT", rec(tree, gdir))
        assert result.excluded == []
        assert (
            "biomarker HER2 status unknown; guideline requires HER2=positive"
            in result.options[0].caveats
        )


async def test_negative_biomarker_excludes_guideline_requiring_positive(tree, gdir):
    write_guideline(gdir, "g1", biomarkers="{HER2: positive}")
    p = profile(biomarkers={"HER2": "negative"})
    result = await treatment_options(p, "PT", rec(tree, gdir))
    assert result.options == []
    assert result.excluded[0].reason == "biomarker HER2 is negative; guideline requires positive"


async def test_matching_biomarker_has_no_caveat(tree, gdir):
    write_guideline(gdir, "g1", biomarkers="{HER2: positive}")
    p = profile(biomarkers={"HER2": "positive"})
    result = await treatment_options(p, "PT", rec(tree, gdir))
    assert not any("HER2" in c for c in result.options[0].caveats)


async def test_opposite_line_of_therapy_excluded(tree, gdir):
    write_guideline(gdir, "first", line="first")
    write_guideline(gdir, "later", line="later")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert [o.guideline.id for o in result.options] == ["guideline:first"]
    assert [e.guideline_id for e in result.excluded] == ["guideline:later"]
    assert "line of therapy" in result.excluded[0].reason
    p = profile(prior_lines=["capecitabine"])
    result = await treatment_options(p, "PT", rec(tree, gdir))
    # prior therapy recorded vs a first-line guideline is a caveat, not an exclusion
    assert [o.guideline.id for o in result.options] == ["guideline:first", "guideline:later"]
    assert result.excluded == []


async def test_us_guideline_never_used_for_pt(tree):
    reconciler = Reconciler(
        [
            Fake("G", "guidelines", [guideline_record("guideline:us", "US")]),
            Fake("EMA", "authorisation"),
        ],
        tree,
    )
    result = await treatment_options(profile(), "PT", reconciler)
    assert result.options == [] and result.guidelines_in_scope == [] and result.excluded == []
    assert result.no_data is not None and result.no_data.reason == "not_found"


async def test_restricted_guideline_noted_link_only(tree, gdir):
    write_guideline(gdir, "g1", licence="restricted-link-only", own_words="false", summary="")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert "guideline content is link-only; not evaluated" in result.options[0].caveats


async def test_unreviewed_and_superseded_caveats_present(tree, gdir):
    # Seed + interventions is rejected by the loader (spec section 4): build the record directly.
    seed = guideline_record("guideline:g1", "EU")
    seed.payload.update(review_status="unreviewed", may_be_superseded=True)
    connectors = [
        Fake("G", "guidelines", [seed]),
        Fake("EMA", "authorisation", [med("trastuzumab")]),
        Fake("CT", "trials"),
    ]
    result = await treatment_options(profile(), "PT", Reconciler(connectors, tree))
    caveats = result.options[0].caveats
    assert "guideline entry unreviewed (seed)" in caveats
    assert "cited edition; later versions may exist" in caveats


async def test_scope_only_caveats_become_notes(tree, gdir):
    write_guideline(gdir, "s", interventions="[]", reviewed_by="unreviewed-seed", stages='["I"]')
    result = await treatment_options(profile(stage="unknown"), "PT", rec(tree, gdir))
    assert "guideline:s: guideline entry unreviewed (seed)" in result.notes
    assert "guideline:s: stage not provided; guideline scope is I" in result.notes


async def test_prior_received_intervention_gets_caveat_not_excluded(tree, gdir):
    write_guideline(gdir, "g1")
    result = await treatment_options(profile(prior_lines=["trastuzumab"]), "PT", rec(tree, gdir))
    assert result.excluded == []
    assert "intervention already received (prior line)" in result.options[0].caveats


async def test_ema_not_found_vs_source_unavailable_wording(tree, gdir):
    write_guideline(gdir, "g1")
    missing = await treatment_options(
        profile(), "PT", rec(tree, gdir, auth=Fake("EMA", "authorisation"))
    )
    assert (
        "not in the EU central register; national authorisations not covered"
        in missing.options[0].caveats
    )
    assert "EMA data unavailable" not in missing.options[0].caveats
    down_auth = Fake("EMA", "authorisation", exc=SourceUnavailable("x"))
    down = await treatment_options(profile(), "PT", rec(tree, gdir, auth=down_auth))
    assert "EMA data unavailable" in down.options[0].caveats
    assert (
        "not in the EU central register; national authorisations not covered"
        not in down.options[0].caveats
    )
    ok = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert not any(c.startswith("EMA") for c in ok.options[0].caveats)
    assert ok.options[0].authorisation.records


async def test_reimbursement_section_without_connector_is_out_of_scope_v1(tree, gdir):
    write_guideline(gdir, "g1")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert result.options[0].reimbursement.no_data.reason == "out_of_scope_v1"


async def test_pointer_caveat_and_reimbursement_pointer(tree, gdir):
    from oncobuddy.connectors.infarmed import InfarmedPointerConnector

    write_guideline(gdir, "g1")
    base = rec(tree, gdir)
    reconciler = Reconciler(
        [
            *base._connectors,
            InfarmedPointerConnector("authorisation"),
            InfarmedPointerConnector("reimbursement"),
        ],
        tree,
    )
    result = await treatment_options(profile(), "PT", reconciler)
    option = result.options[0]
    caveat = (
        "Portuguese national status was not retrieved; read it in the user's browser from the "
        "INFARMED links in the authorisation and reimbursement sections"
    )
    assert caveat in option.caveats
    assert [r.kind for r in option.reimbursement.records] == ["pointer"]
    assert "EU" not in [r.region for r in option.reimbursement.records]
    eu = await treatment_options(profile(), "EU", reconciler)
    assert caveat not in eu.options[0].caveats


async def test_guidelines_source_unavailable_sets_no_data_source_unavailable(tree):
    reconciler = Reconciler([Fake("G", "guidelines", exc=SourceUnavailable("x"))], tree)
    result = await treatment_options(profile(), "PT", reconciler)
    assert result.options == []
    assert result.no_data is not None and result.no_data.reason == "source_unavailable"
    assert result.notes


async def test_option_order_deterministic(tree, gdir):
    write_guideline(gdir, "b", interventions="[zeta, alpha]")
    write_guideline(gdir, "a", interventions="[mid]")
    first = await treatment_options(profile(), "PT", rec(tree, gdir))
    second = await treatment_options(profile(), "PT", rec(tree, gdir))
    order = [(o.guideline.id, o.intervention) for o in first.options]
    assert order == [("guideline:a", "mid"), ("guideline:b", "alpha"), ("guideline:b", "zeta")]
    assert order == [(o.guideline.id, o.intervention) for o in second.options]


async def test_profile_absent_from_cache_file_and_logs(tree, gdir, tmp_path, caplog):
    write_guideline(gdir, "g1", stages='["III"]')
    write_guideline(gdir, "g2", stages='["I"]')
    db = tmp_path / "cache.sqlite3"
    cache = Cache(db, DEFAULT_TTLS)
    p = profile(stage="III", biomarkers={"ZZQXKEY9": "positive"}, prior_lines=["zzqxdrug"], ecog=3)
    with caplog.at_level(logging.DEBUG):
        result = await treatment_options(p, "PT", rec(tree, gdir, cache=cache))
    cache.close()
    assert result.options
    blobs = [db.read_bytes()]
    wal = Path(str(db) + "-wal")
    if wal.exists():
        blobs.append(wal.read_bytes())
    for blob in blobs:
        for needle in (b"ZZQXKEY9", b"zzqxdrug"):
            assert needle not in blob
    texts = [caplog.text, repr(result), result.model_dump_json(), *result.notes]
    texts += [c for o in result.options for c in o.caveats]
    texts += [e.reason for e in result.excluded]
    for text in texts:
        assert "zzqxdrug" not in text.lower() and "zzqxkey9" not in text.lower()


async def test_uses_only_enumerated_values_in_exclusion_reasons(tree, gdir):
    write_guideline(gdir, "g1", stages='["I"]', biomarkers="{HER2: positive}", line="first")
    p = profile(
        stage="IV",
        biomarkers={"HER2": "negative", "ZZQXKEY9": "positive"},
        prior_lines=["zzqxdrug"],
        ecog=3,
    )
    result = await treatment_options(p, "PT", rec(tree, gdir))
    assert len(result.excluded) == 1
    reason = result.excluded[0].reason
    assert reason == (
        "stage IV outside guideline scope I; "
        "biomarker HER2 is negative; guideline requires positive"
    )
    assert "C50" not in reason and "zzq" not in reason.lower()


def profile_no_history(**kw) -> ClinicalProfile:
    data = {"cancer_type": "C50.9", "stage": "II"}
    data.update(kw)
    return parse_profile(data)


async def test_unknown_prior_therapy_never_excludes_later_line_guideline(tree, gdir):
    write_guideline(gdir, "g1", line="later")
    result = await treatment_options(profile_no_history(), "PT", rec(tree, gdir))
    assert result.excluded == []
    assert [o.guideline.id for o in result.options] == ["guideline:g1"]
    assert "prior therapy not provided; guideline is for later line" in result.options[0].caveats


async def test_unknown_prior_therapy_with_first_line_guideline_has_caveat(tree, gdir):
    write_guideline(gdir, "g1", line="first")
    result = await treatment_options(profile_no_history(), "PT", rec(tree, gdir))
    assert result.excluded == []
    assert "prior therapy not provided; guideline is for first line" in result.options[0].caveats


async def test_unknown_prior_therapy_scope_only_caveat_in_notes(tree, gdir):
    write_guideline(gdir, "s", line="later", interventions="[]")
    result = await treatment_options(profile_no_history(), "PT", rec(tree, gdir))
    assert "guideline:s: prior therapy not provided; guideline is for later line" in result.notes


async def test_explicit_empty_prior_lines_excludes_later_line_guideline(tree, gdir):
    write_guideline(gdir, "g1", line="later")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert result.options == []
    assert "line of therapy" in result.excluded[0].reason


async def test_nonempty_prior_lines_with_first_line_guideline_is_a_caveat(tree, gdir):
    write_guideline(gdir, "g1", line="first")
    result = await treatment_options(profile(prior_lines=["capecitabine"]), "PT", rec(tree, gdir))
    assert result.excluded == []
    assert (
        "prior therapy recorded; guideline is for first line - setting of prior therapy unknown"
        in result.options[0].caveats
    )


async def test_empty_stage_scope_never_excludes_and_has_caveat(tree, gdir):
    # Empty stages + interventions is rejected by the loader (spec section 4): direct record.
    empty = guideline_record("guideline:g1", "EU")
    empty.payload["applies_to"]["stages"] = []
    connectors = [
        Fake("G", "guidelines", [empty]),
        Fake("EMA", "authorisation", [med("trastuzumab")]),
        Fake("CT", "trials"),
    ]
    for stage in ("II", "unknown"):
        result = await treatment_options(profile(stage=stage), "PT", Reconciler(connectors, tree))
        assert result.excluded == []
        caveats = result.options[0].caveats
        assert "guideline states no stage scope" in caveats
        assert not any("scope is " in c and c.endswith("scope is ") for c in caveats)
        assert not any("scope is" in c for c in caveats)


async def test_all_guidelines_excluded_note_says_excluded(tree, gdir):
    write_guideline(gdir, "g1", stages='["I"]')
    result = await treatment_options(profile(stage="IV"), "PT", rec(tree, gdir))
    assert "guidelines in scope were excluded for this profile; see excluded" in result.notes
    assert "no guideline in scope" not in result.notes


async def test_non_source_unavailable_error_in_authorisation_is_contained(tree, gdir):
    write_guideline(gdir, "g1")
    auth = Fake("EMA", "authorisation", exc=RuntimeError("secret"))
    result = await treatment_options(profile(), "PT", rec(tree, gdir, auth=auth))
    assert "EMA data unavailable" in result.options[0].caveats
    assert "secret" not in repr(result) + result.model_dump_json()


async def test_trials_source_unavailable_is_reported_on_option(tree, gdir):
    write_guideline(gdir, "g1")
    trials = Fake("CT", "trials", exc=SourceUnavailable("x"))
    result = await treatment_options(profile(), "PT", rec(tree, gdir, trials=trials))
    section = result.options[0].trials
    assert section.no_data is not None and section.no_data.reason == "source_unavailable"


async def test_duplicate_intervention_in_one_guideline_yields_one_option(tree, gdir):
    write_guideline(gdir, "g1", interventions="[trastuzumab, trastuzumab]")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert [o.intervention for o in result.options] == ["trastuzumab"]


def test_options_module_does_not_log():
    source = Path(__file__).parents[2].joinpath("src/oncobuddy/core/options.py").read_text()
    assert "import logging" not in source and "getLogger" not in source
    assert "print(" not in source


async def test_pointer_only_keeps_ema_outcome_caveats(tree, gdir):
    from oncobuddy.connectors.infarmed import InfarmedPointerConnector

    write_guideline(gdir, "g1")
    for ema, expected in (
        (Fake("EMA", "authorisation", exc=SourceUnavailable("x")), "EMA data unavailable"),
        (
            Fake("EMA", "authorisation"),
            "not in the EU central register; national authorisations not covered",
        ),
    ):
        reconciler = Reconciler(
            [GuidelineConnector(gdir), ema, InfarmedPointerConnector("authorisation")], tree
        )
        result = await treatment_options(profile(), "PT", reconciler)
        assert expected in result.options[0].caveats


async def test_caveats_use_structured_no_data_not_note_text(tree, gdir):
    from oncobuddy.core.options import NOT_IN_REGISTER
    from oncobuddy.core.reconciler import PER_PRODUCT_NOTE, NoData, Section

    write_guideline(gdir, "g1")
    pointer = (
        await __import__("oncobuddy.connectors.infarmed", fromlist=["x"])
        .InfarmedPointerConnector("authorisation")
        .fetch(
            __import__("oncobuddy.core.query", fromlist=["x"]).Query(
                drug="trastuzumab", condition=None, region="PT"
            )
        )
    )
    for reason, expected in (
        ("source_unavailable", "EMA data unavailable"),
        ("not_found", NOT_IN_REGISTER),
    ):
        canned = Section(records=pointer, no_data=NoData(reason=reason), notes=["whatever"])

        class Canned(Reconciler):
            async def section(self, name, _canned=canned, **kw):
                if name == "authorisation":
                    return _canned
                return await super().section(name, **kw)

        result = await treatment_options(profile(), "PT", Canned(rec(tree, gdir)._connectors, tree))
        assert expected in result.options[0].caveats
        assert PER_PRODUCT_NOTE not in result.options[0].caveats
        assert any(c.startswith("Portuguese national status") for c in result.options[0].caveats)

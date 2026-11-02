"""Final fix wave: region support, local-source caching, honesty notes, logging."""

import logging
import sqlite3
from pathlib import Path

import httpx
import pytest
import respx
from test_options import Fake, med, profile, rec, write_guideline

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.connectors.guidelines import GuidelineConnector
from oncobuddy.connectors.icd import IcdConnector
from oncobuddy.connectors.trials import CTGOV_URL, TrialsConnector
from oncobuddy.core.cache import DEFAULT_TTLS, Cache
from oncobuddy.core.options import treatment_options
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.regions import RegionTree


@pytest.fixture
def tree(tmp_path: Path) -> RegionTree:
    path = tmp_path / "regions.yaml"
    path.write_text(
        "regions:\n  global: null\n  EU: global\n  PT: EU\n  ES: EU\n  US: global\n",
        encoding="utf-8",
    )
    return RegionTree.load(path)


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    d = tmp_path / "g"
    d.mkdir()
    return d


PER_PRODUCT = "authorisation is per product; indication not checked - see the product page link"
CATEGORY = "matched on ICD site category only; histology and subtype not checked"


class Region(Fake):
    """Fake connector that only supports country regions."""

    region_hint = "trials are searched per country region (e.g. PT, ES)"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = 0

    def supports_region(self, region: str) -> bool:
        return region == "PT"

    async def fetch(self, query):
        self.calls += 1
        return await super().fetch(query)


# -- C1 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("region", ["EU", "global"])
async def test_unsupported_region_is_out_of_scope_with_note_and_no_call(tree, region):
    conn = Region("CT", "trials", [])
    other = Fake("EMA", "authorisation", [med("trastuzumab", "global")])
    r = Reconciler([conn, other], tree)
    status = await r.regional_status("trastuzumab", "C50.9", region)
    section = status.sections["trials"]
    assert section.no_data.reason == "out_of_scope_v1"
    assert (
        f"CT: not searched for region {region}; trials are searched per country region"
        in (section.notes[0])
    )
    assert conn.calls == 0
    assert status.sections["authorisation"].records
    single = await r.section("trials", drug="trastuzumab", condition="C50.9", region=region)
    assert single.no_data.reason == "out_of_scope_v1"
    assert conn.calls == 0


async def test_supported_region_is_searched(tree):
    conn = Region("CT", "trials", [])
    r = Reconciler([conn], tree)
    section = await r.section("trials", drug="trastuzumab", condition="C50.9", region="PT")
    assert conn.calls == 1
    assert section.no_data.reason == "not_found"


async def test_generic_wording_without_region_hint(tree):
    conn = Region("CT", "trials", [])
    del type(conn).region_hint
    r = Reconciler([conn], tree)
    section = await r.section("trials", drug="trastuzumab", condition="C50.9", region="EU")
    assert section.notes == ["CT: not searched for region EU; not available for this region"]


async def test_other_connectors_of_same_section_still_run(tree):
    conn = Region("CT", "trials", [])
    other = Fake("Other", "trials", [med("trastuzumab", "EU")])
    section = await Reconciler([conn, other], tree).section(
        "trials", drug="trastuzumab", condition="C50.9", region="EU"
    )
    assert len(section.records) == 1
    assert any("not searched" in n for n in section.notes)


def test_trials_connector_supports_only_country_regions(tree):
    conn = TrialsConnector(None, tree)  # type: ignore[arg-type]
    assert conn.supports_region("PT") and conn.supports_region("ES")
    assert not conn.supports_region("EU") and not conn.supports_region("global")
    assert not conn.supports_region("XX")
    assert "per country" in conn.region_hint


@respx.mock
async def test_real_trials_connector_makes_no_http_request_outside_country(tree):
    route = respx.get(CTGOV_URL).respond(200, json={"studies": []})
    async with httpx.AsyncClient() as client:
        r = Reconciler([TrialsConnector(client, tree)], tree)
        for region in ("EU", "global"):
            s = await r.section("trials", drug="pembrolizumab", condition="C34.9", region=region)
            assert s.no_data.reason == "out_of_scope_v1"
        assert route.call_count == 0
        await r.section("trials", drug="pembrolizumab", condition="C34.9", region="PT")
        assert route.call_count == 1


# -- C2 ---------------------------------------------------------------------------------


async def test_curated_edits_are_reflected_immediately_and_never_stored(tree, gdir, tmp_path):
    db = tmp_path / "cache.sqlite"
    cache = Cache(db, DEFAULT_TTLS)
    try:
        write_guideline(gdir, "g1")
        first = await Reconciler(
            [GuidelineConnector(gdir), IcdConnector()], tree, cache=cache
        ).regional_status("trastuzumab", "C50.9", "PT")
        assert [r.id for r in first.sections["guidelines"].records] == ["guideline:g1"]
        (gdir / "g1.yaml").unlink()
        write_guideline(gdir, "g2")
        second = await Reconciler(
            [GuidelineConnector(gdir), IcdConnector()], tree, cache=cache
        ).regional_status("trastuzumab", "C50.9", "PT")
        assert [r.id for r in second.sections["guidelines"].records] == ["guideline:g2"]
        assert second.sections["coding"].records
    finally:
        cache.close()
    rows = sqlite3.connect(db).execute("SELECT source FROM entries").fetchall()
    assert rows == []


async def test_closed_cache_does_not_break_local_sources(tree, gdir):
    write_guideline(gdir, "g1")
    cache = Cache(None, DEFAULT_TTLS)
    cache.close()
    r = Reconciler([GuidelineConnector(gdir), IcdConnector()], tree, cache=cache)
    g = await r.section("guidelines", drug=None, condition="C50.9", region="PT")
    c = await r.section("coding", drug=None, condition="C50.9", region="PT")
    assert g.records and c.records


# -- M3 ---------------------------------------------------------------------------------


async def test_connector_failure_logs_name_and_type_only(tree, caplog):
    secret = "SECRET-MESSAGE-xyz"
    conn = Fake("EMA", "authorisation", exc=SourceUnavailable(secret))
    with caplog.at_level(logging.DEBUG):
        s = await Reconciler([conn], tree).section(
            "authorisation", drug="trastuzumab", condition=None, region="PT"
        )
    assert s.no_data.reason == "source_unavailable"
    assert "EMA" in caplog.text and "SourceUnavailable" in caplog.text
    assert secret not in caplog.text
    assert not any(r.exc_info for r in caplog.records)


# -- I7 / I3 reconciler side --------------------------------------------------------------


async def test_regional_status_reimbursement_is_a_section_and_authorisation_note(tree):
    r = Reconciler([Fake("EMA", "authorisation", [med("trastuzumab")])], tree)
    status = await r.regional_status("trastuzumab", "C50.9", "PT")
    assert not hasattr(status, "reimbursement")
    assert status.sections["reimbursement"].no_data.reason == "out_of_scope_v1"
    assert PER_PRODUCT in status.sections["authorisation"].notes
    empty = await Reconciler([Fake("EMA", "authorisation")], tree).regional_status(
        "trastuzumab", "C50.9", "PT"
    )
    assert PER_PRODUCT not in empty.sections["authorisation"].notes


# -- options ------------------------------------------------------------------------------


async def test_unused_profile_fields_are_noted_by_name_only(tree, gdir):
    write_guideline(gdir, "g1")
    p = profile(ecog=2, intent="palliative", comorbidity_flags=["renal_impairment"])
    result = await treatment_options(p, "PT", rec(tree, gdir))
    assert "not used by matching in v1: ecog, intent, comorbidity_flags" in result.notes
    text = " ".join(result.notes)
    assert "palliative" not in text and "renal" not in text
    only = await treatment_options(profile(ecog=0), "PT", rec(tree, gdir))
    assert "not used by matching in v1: ecog" in only.notes
    plain = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert not any("not used by matching" in n for n in plain.notes)


async def test_authorisation_caveats(tree, gdir):
    write_guideline(gdir, "g1")
    none = await treatment_options(
        profile(), "PT", rec(tree, gdir, auth=Fake("EMA", "authorisation"))
    )
    assert "not in the EU central register; national authorisations not covered" in (
        none.options[0].caveats
    )
    assert not any("no EMA authorisation record" in c for c in none.options[0].caveats)

    withdrawn = med("trastuzumab")
    withdrawn.payload["status"] = "Withdrawn"
    ok = med("trastuzumab", "EU")
    ok.payload["status"] = "Authorised"
    bad = await treatment_options(
        profile(), "PT", rec(tree, gdir, auth=Fake("EMA", "authorisation", [withdrawn]))
    )
    assert "EMA status: Withdrawn" in bad.options[0].caveats
    assert PER_PRODUCT in bad.options[0].caveats
    assert PER_PRODUCT in bad.options[0].authorisation.notes
    good = await treatment_options(
        profile(), "PT", rec(tree, gdir, auth=Fake("EMA", "authorisation", [ok]))
    )
    assert not any(c.startswith("EMA status") for c in good.options[0].caveats)
    assert PER_PRODUCT in good.options[0].caveats


async def test_category_only_caveat_on_options_and_scope_entries(tree, gdir):
    write_guideline(gdir, "g1")
    write_guideline(gdir, "scope", interventions="[]")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert CATEGORY in result.options[0].caveats
    assert f"guideline:scope: {CATEGORY}" in result.notes


async def test_reimbursement_is_per_option_section_only(tree, gdir):
    write_guideline(gdir, "g1")
    result = await treatment_options(profile(), "PT", rec(tree, gdir))
    assert not hasattr(result, "reimbursement")
    assert result.options[0].reimbursement.no_data.reason == "out_of_scope_v1"


# -- I9 ---------------------------------------------------------------------------------


@respx.mock
async def test_fixed_default_term_leaves_conditions_empty(tree):
    import json
    from pathlib import Path

    body = json.loads(
        (Path(__file__).parent.parent / "fixtures" / "ctgov_studies.json").read_text()
    )
    respx.get(CTGOV_URL).respond(200, json=body)
    async with httpx.AsyncClient() as client:
        fixed = TrialsConnector(client, tree)
        from oncobuddy.core.query import Query

        recs = await fixed.fetch(Query(drug="pembrolizumab", condition="C34.9", region="PT"))
        assert recs
        assert all(r.entities.conditions == [] for r in recs)
        assert recs[0].payload["search"]["condition_term"] == "cancer OR neoplasm"
        resolved = TrialsConnector(client, tree, condition_terms=lambda c: "lung cancer")
        recs = await resolved.fetch(Query(drug="pembrolizumab", condition="C34.9", region="PT"))
        assert recs[0].entities.conditions == ["C34.9"]

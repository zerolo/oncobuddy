import re
from datetime import date

import pytest
import yaml

from oncobuddy.connectors.icd import IcdConnector, condition_terms, load_rows, lookup_code
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.records import Record
from oncobuddy.core.regions import RegionTree

# ICD-10 neoplasm categories C00-C97 that do not exist (9), so 98 - 9 = 89 C rows.
UNUSED_C = {"C27", "C28", "C29", "C35", "C36", "C42", "C59", "C87", "C89"}
# D00-D09 in situ: D08 does not exist -> 9 rows.
EXPECTED_D = {"D00", "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D09"}


def test_exact_code_lookup():
    recs = lookup_code("C50.9")
    assert len(recs) == 1
    r = recs[0]
    assert r.entities.conditions == ["C50"]
    assert r.payload["queried_code"] == "C50.9"
    assert r.payload["code"] == "C50"
    assert r.region == "global"
    assert r.kind == "code"
    assert r.licence == "oncobuddy-original"
    assert r.valid_as_of == date(2026, 11, 1)
    assert r.reviewed_by == "unreviewed-seed"
    assert r.payload["system"] == "ICD-10 (category)"


def test_lowercase_and_whitespace_code():
    recs = lookup_code("c50.9 ")
    assert [r.payload["code"] for r in recs] == ["C50"]
    assert recs[0].payload["queried_code"] == "C50.9"


@pytest.mark.parametrize("code", ["C99", "Z99", "C27", "", "not a code"])
def test_unknown_category_returns_empty(code):
    assert lookup_code(code) == []


def test_morphology_code_returns_empty():
    assert lookup_code("8500/3") == []


def test_term_lookup_exact_only():
    assert [r.payload["code"] for r in lookup_code("breast")] == ["C50"]
    assert lookup_code("bre") == []
    assert [r.payload["code"] for r in lookup_code("Breast")] == ["C50"]
    assert lookup_code("breast cancer") == []


def test_all_records_region_global_and_valid():
    rows = load_rows()
    assert rows
    for row in rows:
        rec = Record.model_validate(row.to_record().model_dump())
        assert rec.region == "global"
        assert re.fullmatch(r"[a-z0-9 -]+", row.name), row.name
        assert not row.name.startswith("malignant neoplasm")
        assert rec.source_url.startswith("https://icd.who.int/")


def test_bundle_covers_expected_range():
    codes = {r.code for r in load_rows()}
    c_codes = {c for c in codes if c.startswith("C")}
    expected_c = {f"C{i:02d}" for i in range(98)} - UNUSED_C
    assert c_codes == expected_c
    assert len(c_codes) == 89
    assert {c for c in codes if c.startswith("D")} == EXPECTED_D
    assert len(codes) == 98


def test_bundled_file_is_yaml_with_edition_date():
    from importlib.resources import files

    text = (files("oncobuddy.connectors") / "data" / "icd_onc.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(text)["edition_date"] == date(2026, 11, 1)


def test_condition_terms_helper():
    assert condition_terms("C50.9") == "breast"
    assert condition_terms("c34") == "lung"
    assert condition_terms("C99") is None
    assert condition_terms("8500/3") is None


async def test_connector_none_condition_returns_empty():
    c = IcdConnector()
    assert c.name == "ICD index" and c.section == "coding"
    assert await c.fetch(Query(drug="pembrolizumab", condition=None, region="PT")) == []


async def test_connector_fetch_and_invalid_condition():
    c = IcdConnector()
    recs = await c.fetch(Query(drug=None, condition="C50.9", region="PT"))
    assert recs[0].payload["queried_code"] == "C50.9"
    assert await c.fetch(Query(drug=None, condition="8500/3", region="PT")) == []
    with pytest.raises(ValueError):
        await c.fetch(Query(drug=None, condition="breast", region="PT"))


async def test_fetch_via_reconciler_in_coding_section():
    tree = RegionTree.load()
    rec = Reconciler(connectors=[IcdConnector()], tree=tree)
    status = await rec.regional_status("pembrolizumab", "C50.9", "PT")
    sec = status.sections["coding"]
    assert [r.region for r in sec.records] == ["global"]
    assert sec.records[0].payload["queried_code"] == "C50.9"
    assert any("global-level" in n for n in sec.notes)


GOLDEN = {
    "C15": ("oesophagus", "esophageal"),
    "C18": ("colon", "colon"),
    "C20": ("rectum", "rectal"),
    "C34": ("lung", "lung"),
    "C43": ("skin melanoma", "melanoma"),
    "C45": ("mesothelioma", "mesothelioma"),
    "C50": ("breast", "breast"),
    "C61": ("prostate", "prostate"),
    "C77": ("lymph nodes secondary", None),
    "C78": ("respiratory and digestive organs secondary", None),
    "C79": ("other sites secondary", None),
    "C80": ("primary site unknown", None),
    "C90": ("plasma cell neoplasm myeloma", "multiple myeloma"),
    "C91": ("lymphoid leukaemia", "lymphoid leukemia"),
    "C92": ("myeloid leukaemia", "myeloid leukemia"),
}

NULL_SEARCH = {
    "C76", "C80", "C97", "C39", "C26", "C14", "C57", "C63", "C68", "C75", "C96", "C41", "C49",
    "C94", "C95", "C85", "C86", "C77", "C78", "C79", "D01", "D07", "D09", "C06", "C08", "C24",
    "C02", "C44",
}  # fmt: skip


@pytest.mark.parametrize("code", sorted(GOLDEN))
def test_golden_names_and_search_terms(code):
    name, term = GOLDEN[code]
    [rec] = lookup_code(code)
    assert rec.payload["name"] == name
    assert rec.payload["search_term"] == term
    assert condition_terms(code) == term


def test_condition_terms_none_for_null_set():
    rows = {r.code: r for r in load_rows()}
    for code in sorted(NULL_SEARCH):
        assert condition_terms(code) is None, code
        assert len(lookup_code(code)) == 1, code  # identifier record still resolves
        assert rows[code].search_term is None
    assert condition_terms("D09.0") is None


def test_search_terms_are_safe_and_specific():
    banned = {"other", "unspecified", "ill-defined", "secondary", "overlapping"}
    for row in load_rows():
        if row.search_term is None:
            continue
        assert re.fullmatch(r"[a-z0-9 -]+", row.search_term), row.code
        assert not banned & set(row.search_term.split()), row.code


@pytest.mark.parametrize("bad", ["C500", "C50X", "C50.", "C5", "C50.12345"])
def test_strict_code_shape_rejected(bad):
    assert lookup_code(bad) == []
    assert condition_terms(bad) is None


@pytest.mark.parametrize("ok", ["C50", "C50.9", " c50.9 "])
def test_strict_code_shape_accepted(ok):
    [rec] = lookup_code(ok)
    assert rec.payload["code"] == "C50"
    assert rec.payload["queried_code"] == ok.strip().upper()

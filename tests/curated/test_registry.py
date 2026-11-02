import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

import oncobuddy.curated as _curated
from oncobuddy.curated.loader import load_guidelines
from oncobuddy.curated.topics import known_categories

# ESMO registry size after the 2026-11-02 fixes was 39 entries; the floor is 80 percent, rounded down.
MIN_ESMO_ENTRIES = 31


def _esmo_records():
    return [r for r in load_guidelines() if r.source == "ESMO"]


def test_all_guideline_files_load_with_unique_ids():
    records = load_guidelines()
    ids = [r.id for r in records]
    assert len(ids) == len(set(ids))


def test_esmo_entry_count_floor():
    assert len(_esmo_records()) >= MIN_ESMO_ENTRIES


def test_every_esmo_entry_is_scope_only_browser_pointer():
    categories = known_categories()
    for record in _esmo_records():
        payload = record.payload
        assert payload["access"]["method"] == "browser", record.id
        assert payload["applies_to"]["interventions"] == [], record.id
        assert payload["applies_to"]["required_biomarkers"] == {}, record.id
        assert record.licence == "restricted-link-only", record.id
        assert payload.get("last_verified"), record.id
        conditions = payload["applies_to"]["conditions"]
        assert conditions, record.id
        assert set(conditions) <= categories, record.id
        parsed = urlparse(record.source_url)
        assert parsed.scheme == "https" and parsed.hostname == "doi.org", record.id


def test_salivary_gland_guideline_is_routable_by_its_own_icd_categories():
    from oncobuddy.curated.topics import known_categories

    assert {"C07", "C08"} <= known_categories()
    (record,) = [r for r in load_guidelines() if r.id == "guideline:esmo-salivary-gland-cancer"]
    assert record.entities.conditions == ["C07", "C08"]


# --- condition routing pins -------------------------------------------------

_CURATED = Path(_curated.__file__).parent
_TOPICS = yaml.safe_load((_CURATED / "esmo_topics.yaml").read_text())

# Entries whose conditions are the union of two topic lists.
UNION_IDS = {"esmo-gastric-and-oesophageal-cancer": ("gastric", "oesophageal")}

# Entries whose title names one organ or entity: id -> ICD-10 subset.
DECLARED_SUBSETS = {
    "esmo-localised-colon-cancer": ["C18"],  # title: colon
    "esmo-localised-rectal-cancer": ["C19", "C20"],  # title: rectal (rectosigmoid, rectum)
    "esmo-nasopharyngeal-carcinoma": ["C11"],  # title: nasopharynx
    "esmo-sinonasal-malignancy": ["C30", "C31"],  # title: nasal cavity, sinuses
    "esmo-salivary-gland-cancer": ["C07", "C08"],  # title: salivary gland
    "esmo-acute-myeloid-leukaemia-in-adult-patients": ["C92"],  # title: myeloid leukaemia
    "esmo-chronic-lymphocytic-leukaemia": ["C91"],  # title: lymphoid leukaemia
    "esmo-hairy-cell-leukaemia": ["C91"],  # title: hairy cell (C91.4)
    "esmo-peripheral-t-and-natural-killer-cell-lymphomas": ["C84", "C86"],  # title: T/NK lymphomas
}


def _esmo_files():
    files = sorted((_CURATED / "guidelines").glob("esmo-*.yaml"))
    return [(p, yaml.safe_load(p.read_text())) for p in files]


def test_every_esmo_entry_conditions_come_from_the_topic_table():
    assert _esmo_files()
    for path, data in _esmo_files():
        entry_id = data["id"]
        conditions = sorted(data["applies_to"]["conditions"])
        if entry_id in DECLARED_SUBSETS:
            assert conditions == sorted(DECLARED_SUBSETS[entry_id]), entry_id
        elif entry_id in UNION_IDS:
            a, b = UNION_IDS[entry_id]
            assert conditions == sorted(set(_TOPICS[a]) | set(_TOPICS[b])), entry_id
        else:
            assert conditions in [sorted(v) for v in _TOPICS.values()], entry_id


def test_declared_subsets_are_proper_subsets_of_a_topic():
    for entry_id, subset in DECLARED_SUBSETS.items():
        assert any(set(subset) < set(v) for v in _TOPICS.values()), entry_id


def test_esmo_entry_identity_fields():
    seen = set()
    for path, data in _esmo_files():
        assert data["id"] == path.stem
        assert re.fullmatch(r"esmo-[a-z0-9-]+", data["id"])
        assert data["region"] == "EU", data["id"]
        assert data["summary"] == "", data["id"]
        assert data["reviewed_by"] == "unreviewed-seed", data["id"]
        doi = data["url"].lower()
        assert doi not in seen, data["id"]
        seen.add(doi)


# --- DGS (Portugal) pins ----------------------------------------------------

# Floor is 80 percent of the DGS registry size after the 2026-11-02 refresh, rounded down.
MIN_DGS_ENTRIES = 11  # registry had 14 entries

DGS_HOSTS = {"normas.dgs.min-saude.pt", "www.dgs.pt"}

# Entries whose title names one organ or entity: id -> ICD-10 subset .
DGS_DECLARED_SUBSETS: dict[str, list[str]] = {
    "dgs-020-2013": ["C09", "C10"],  # title: oropharynx
    "dgs-021-2013": ["C02", "C03", "C04", "C05", "C06"],  # title: oral cavity
    "dgs-016-2015": ["C13", "C32"],  # title: larynx and hypopharynx
    "dgs-017-2013": ["C73"],  # title: thyroid (equals topic list, declared for clarity)
}


def _dgs_files():
    files = sorted((_CURATED / "guidelines").glob("dgs-*.yaml"))
    return [(p, yaml.safe_load(p.read_text())) for p in files]


def test_dgs_entry_count_floor():
    assert len(_dgs_files()) >= MIN_DGS_ENTRIES


def test_every_dgs_entry_is_scope_only_pointer():
    for path, data in _dgs_files():
        entry_id = data["id"]
        assert entry_id == path.stem
        assert re.fullmatch(r"dgs-[0-9]{3}-[0-9]{4}(-[a-z])?", entry_id), entry_id
        assert data["issuer"] == "DGS", entry_id
        assert data["region"] == "PT", entry_id
        assert not data.get("reference_only"), entry_id
        assert data["access"]["reason"] == "no_api", entry_id
        assert urlparse(data["url"]).hostname in DGS_HOSTS, entry_id
        assert data["applies_to"]["interventions"] == [], entry_id
        assert data["applies_to"]["required_biomarkers"] == {}, entry_id
        assert data["licence"] == "restricted-link-only", entry_id
        assert data["reviewed_by"] == "unreviewed-seed", entry_id
        assert data["summary"] == "", entry_id
        assert data.get("last_verified"), entry_id
        conditions = sorted(data["applies_to"]["conditions"])
        if entry_id in DGS_DECLARED_SUBSETS:
            assert conditions == sorted(DGS_DECLARED_SUBSETS[entry_id]), entry_id
        else:
            assert conditions in [sorted(v) for v in _TOPICS.values()], entry_id


def test_dgs_loaded_records_have_source_dgs():
    records = [r for r in load_guidelines() if r.id.startswith("guideline:dgs-")]
    for record in records:
        assert record.source == "DGS", record.id


# --- NCCN (United States, reference only) pins ------------------------------

# Floor is 80 percent of the NCCN registry size at creation (48 entries), rounded down.
MIN_NCCN_ENTRIES = 38

# Entries whose title names one entity narrower than a topic list: id -> ICD-10 subset.
# Entries whose conditions are the union of two topic lists.
NCCN_UNION_IDS = {"nccn-esophageal-and-esophagogastric-junction-cancers": ("oesophageal", "gastric")}  # junction is C16.0

NCCN_DECLARED_SUBSETS: dict[str, list[str]] = {
    "nccn-acute-lymphoblastic-leukemia": ["C91"],  # title: lymphoid leukaemia
    "nccn-acute-myeloid-leukemia": ["C92"],  # title: myeloid leukaemia
    "nccn-ampullary-adenocarcinoma": ["C24"],  # title: ampulla (biliary topic)
    "nccn-appendiceal-neoplasms-and-cancers": ["C18"],  # title: appendix (colorectal topic)
    "nccn-b-cell-lymphomas": ["C82", "C83", "C85"],  # title: B-cell lymphomas
    "nccn-bone-cancer": ["C40", "C41"],  # title: bone (sarcoma topic)
    "nccn-chronic-lymphocytic-leukemia-small-lymphocytic-lymphoma": ["C91"],  # title: CLL
    "nccn-chronic-myeloid-leukemia": ["C92"],  # title: myeloid leukaemia
    "nccn-colon-cancer": ["C18"],  # title: colon
    "nccn-cutaneous-lymphomas": ["C84"],  # title: cutaneous T-cell lymphomas
    "nccn-hairy-cell-leukemia": ["C91"],  # title: hairy cell (C91.4)
    "nccn-hodgkin-lymphoma": ["C81"],  # title: Hodgkin lymphoma
    "nccn-merkel-cell-carcinoma": ["C44"],  # title: Merkel cell (skin)
    "nccn-rectal-cancer": ["C19", "C20"],  # title: rectal
    "nccn-soft-tissue-sarcoma": ["C49"],  # title: soft tissue
    "nccn-t-cell-lymphomas": ["C84", "C86"],  # title: T-cell lymphomas
    "nccn-vaginal-cancer": ["C52"],  # title: vagina
    "nccn-vulvar-cancer": ["C51"],  # title: vulva
    "nccn-waldenstrom-macroglobulinemia-lymphoplasmacytic-lymphoma": ["C88"],  # title: WM
}


def _nccn_files():
    files = sorted((_CURATED / "guidelines").glob("nccn-*.yaml"))
    return [(p, yaml.safe_load(p.read_text())) for p in files]


def test_nccn_entry_count_floor():
    assert len(_nccn_files()) >= MIN_NCCN_ENTRIES


def test_every_nccn_entry_is_login_gated_reference_pointer():
    assert _nccn_files()
    for path, data in _nccn_files():
        entry_id = data["id"]
        assert entry_id == path.stem
        assert re.fullmatch(r"nccn-[a-z0-9-]+", entry_id) and len(entry_id) <= 80, entry_id
        assert data["issuer"] == "NCCN", entry_id
        assert data["region"] == "US", entry_id
        assert data["reference_only"] is True, entry_id
        assert data["access"]["method"] == "browser", entry_id
        assert data["access"]["reason"] == "login_required", entry_id
        assert urlparse(data["url"]).hostname == "www.nccn.org", entry_id
        assert data["published"] == "unknown" and data["valid_as_of"] == "unknown", entry_id
        recipe = " ".join(data["access"]["read_recipe"]).lower()
        assert "log in" in recipe and "stop" in recipe, entry_id
        assert data["applies_to"]["interventions"] == [], entry_id
        assert data["applies_to"]["required_biomarkers"] == {}, entry_id
        assert data["applies_to"]["stages"] == [], entry_id
        assert data["licence"] == "restricted-link-only", entry_id
        assert data["reviewed_by"] == "unreviewed-seed", entry_id
        assert data["summary"] == "", entry_id
        assert data.get("last_verified"), entry_id
        conditions = sorted(data["applies_to"]["conditions"])
        if entry_id in NCCN_DECLARED_SUBSETS:
            assert conditions == sorted(NCCN_DECLARED_SUBSETS[entry_id]), entry_id
        elif entry_id in NCCN_UNION_IDS:
            a, b = NCCN_UNION_IDS[entry_id]
            assert conditions == sorted(set(_TOPICS[a]) | set(_TOPICS[b])), entry_id
        else:
            assert conditions in [sorted(v) for v in _TOPICS.values()], entry_id


def test_nccn_declared_subsets_exist_and_are_within_a_topic():
    ids = {d["id"] for _, d in _nccn_files()}
    assert set(NCCN_DECLARED_SUBSETS) <= ids
    for entry_id, subset in NCCN_DECLARED_SUBSETS.items():
        assert any(set(subset) <= set(v) for v in _TOPICS.values()), entry_id


def test_nccn_loaded_records_are_reference_only_us():
    records = [r for r in load_guidelines() if r.id.startswith("guideline:nccn-")]
    assert records
    for record in records:
        assert record.source == "NCCN", record.id
        assert record.region == "US", record.id
        assert record.payload["reference_only"] is True, record.id


# --- ASCO (United States, reference only) pins -------------------------------

# Floor is 80 percent of the ASCO registry size at creation (53 entries), rounded down; 52 after dropping a drug-named title.
MIN_ASCO_ENTRIES = 42

# Entries whose conditions are the union of two topic lists.
ASCO_UNION_IDS = {"asco-immunotherapy-and-targeted-therapy-for-advanced-gastroesophageal-cancer": ("gastric", "oesophageal")}  # title: gastroesophageal

# Entries whose title names one organ or entity narrower than a topic list: id -> ICD-10 subset.
ASCO_DECLARED_SUBSETS: dict[str, list[str]] = {
    "asco-management-of-locally-advanced-rectal-cancer": ["C19", "C20"],
    "asco-adjuvant-therapy-for-stage-ii-colon-cancer": ["C18"],
    "asco-therapy-for-diffuse-astrocytic-and-oligodendroglial-tumors-in-adults": ["C71"],
    "asco-transoral-robotic-surgery-in-the-multidisciplinary-care-of-patients-with": ["C09", "C10"],
    "asco-management-of-salivary-gland-malignancy": ["C07", "C08"],
    "asco-chemotherapy-in-combination-with-radiotherapy-for-definitive-intent": ["C11"],
    "asco-management-of-the-neck-in-squamous-cell-carcinoma-of-the-oral-cavity-and": ["C02", "C03", "C04", "C05", "C06", "C09", "C10"],
}


def _asco_files():
    files = sorted((_CURATED / "guidelines").glob("asco-*.yaml"))
    return [(p, yaml.safe_load(p.read_text())) for p in files]


def test_asco_entry_count_floor():
    assert len(_asco_files()) >= MIN_ASCO_ENTRIES


def test_every_asco_entry_is_bot_checked_reference_pointer():
    assert _asco_files()
    for path, data in _asco_files():
        entry_id = data["id"]
        assert entry_id == path.stem
        assert re.fullmatch(r"asco-[a-z0-9-]+", entry_id) and len(entry_id) <= 80, entry_id
        assert data["issuer"] == "ASCO", entry_id
        assert data["region"] == "US", entry_id
        assert data["reference_only"] is True, entry_id
        assert data["access"]["method"] == "browser", entry_id
        assert data["access"]["reason"] == "bot_check", entry_id
        assert data["url"].startswith("https://doi.org/10."), entry_id
        assert data["applies_to"]["interventions"] == [], entry_id
        assert data["applies_to"]["required_biomarkers"] == {}, entry_id
        assert data["licence"] == "restricted-link-only", entry_id
        assert data["reviewed_by"] == "unreviewed-seed", entry_id
        assert data["summary"] == "", entry_id
        assert data.get("last_verified"), entry_id
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["published"]), entry_id
        assert data["published"] == data["valid_as_of"], entry_id
        conditions = sorted(data["applies_to"]["conditions"])
        if entry_id in ASCO_DECLARED_SUBSETS:
            assert conditions == sorted(ASCO_DECLARED_SUBSETS[entry_id]), entry_id
        elif entry_id in ASCO_UNION_IDS:
            a, b = ASCO_UNION_IDS[entry_id]
            assert conditions == sorted(set(_TOPICS[a]) | set(_TOPICS[b])), entry_id
        else:
            assert conditions in [sorted(v) for v in _TOPICS.values()], entry_id


def test_asco_declared_subsets_and_union_ids_exist():
    ids = {d["id"] for _, d in _asco_files()}
    assert set(ASCO_DECLARED_SUBSETS) <= ids
    assert set(ASCO_UNION_IDS) <= ids
    for entry_id, subset in ASCO_DECLARED_SUBSETS.items():
        assert any(set(subset) <= set(v) for v in _TOPICS.values()), entry_id


def test_asco_dois_are_unique():
    dois = [d["url"].lower() for _, d in _asco_files()]
    assert len(dois) == len(set(dois))


def test_asco_loaded_records_are_reference_only_us():
    records = [r for r in load_guidelines() if r.id.startswith("guideline:asco-")]
    assert len(records) >= MIN_ASCO_ENTRIES
    for record in records:
        assert record.source == "ASCO", record.id
        assert record.region == "US", record.id
        assert record.payload["reference_only"] is True, record.id


def test_dgs_read_recipe_is_norm_specific():
    for path, data in _dgs_files():
        steps = data["access"]["read_recipe"]
        for step in steps:
            assert "treatment algorithm" not in step.lower(), path.name
            assert "named drug" not in step.lower(), path.name
        assert any("issue date" in s and "norm" in s for s in steps), path.name
        assert any("replaced or revoked" in s for s in steps), path.name
        assert len(steps) <= 6 and all(len(s) <= 160 for s in steps), path.name


def test_dgs_consultation_pages_say_so_in_recipe():
    pages = [(p, d) for p, d in _dgs_files() if "discussao-publica" in d["url"]]
    assert {d["id"] for _, d in pages} == {"dgs-017-2013", "dgs-023-2013", "dgs-032-2013"}
    for path, data in pages:
        assert any("public-consultation" in s for s in data["access"]["read_recipe"]), path.name


def test_no_registry_entry_names_a_drug_in_id_or_title():
    from oncobuddy.core.access import _DRUGISH

    for path in sorted((_CURATED / "guidelines").glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        for text in (data["id"], data["title"]):
            assert not _DRUGISH.search(text), (path.name, text)

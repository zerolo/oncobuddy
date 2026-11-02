import copy
from pathlib import Path

import pytest
import yaml

from oncobuddy.curated.loader import default_directory, load_guidelines

DENY = ("trastuzumab", "pertuzumab", "pembrolizumab", "cetuximab", "panitumumab", "bevacizumab",
        "osimertinib", "alectinib")
SEED = "esmo-early-breast-cancer.yaml"
ACCESS = {
    "method": "browser",
    "reason": "bot_check",
    "read_recipe": ["open the entry link", "read the recommendations", "report names as printed"],
}


def write(tmp_path: Path, **over) -> Path:
    data = yaml.safe_load((default_directory() / SEED).read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data.update(over)
    (tmp_path / "g.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def test_access_block_loads_and_fills_entry_url(tmp_path):
    records = load_guidelines(write(tmp_path, access=ACCESS))
    access = records[0].payload["access"]
    assert access["method"] == "browser"
    assert access["read_recipe"] == ACCESS["read_recipe"]
    assert access["entry_url"] == records[0].source_url


def test_explicit_entry_url_is_kept(tmp_path):
    block = {**ACCESS, "entry_url": "https://www.esmo.org/guidelines"}
    records = load_guidelines(write(tmp_path, access=block))
    assert records[0].payload["access"]["entry_url"] == "https://www.esmo.org/guidelines"


def test_entry_without_access_has_no_access_key(tmp_path):
    records = load_guidelines(write(tmp_path, access=None))
    assert "access" not in records[0].payload


def test_browser_on_open_licence_rejected(tmp_path):
    with pytest.raises(ValueError, match="licence") as err:
        load_guidelines(
            write(tmp_path, access=ACCESS, licence="CC-BY-4.0", own_words=True, summary="x")
        )
    assert "g.yaml" in str(err.value)


def test_dose_in_recipe_rejected_without_echo(tmp_path):
    block = {**ACCESS, "read_recipe": ["SECRET-STEP give 10 mg"]}
    with pytest.raises(ValueError, match="dose") as err:
        load_guidelines(write(tmp_path, access=block))
    assert "SECRET-STEP" not in str(err.value)


def test_unknown_access_key_rejected(tmp_path):
    with pytest.raises(ValueError, match="access"):
        load_guidelines(write(tmp_path, access={**ACCESS, "fetch": True}))


def test_every_seed_uses_browser_handoff():
    for record in load_guidelines():
        access = record.payload["access"]
        assert access["method"] == "browser"
        if record.id.startswith("guideline:dgs-"):
            expected = "no_api"
        elif record.id.startswith("guideline:nccn-"):
            expected = "login_required"
        else:
            expected = "bot_check"
        assert access["reason"] == expected
        assert 3 <= len(access["read_recipe"]) <= 6
        text = " ".join(access["read_recipe"]).casefold()
        assert not any(name in text for name in DENY)


def test_entry_url_on_unlisted_host_rejected(tmp_path):
    block = {**ACCESS, "entry_url": "https://evil.example/?p="}
    with pytest.raises(ValueError, match="entry_url"):
        load_guidelines(write(tmp_path, access=block))


@pytest.mark.parametrize("key", ["DMMR", "ZZZ9", "erbb2"])
def test_non_canonical_required_biomarker_rejected(tmp_path, key):
    data = yaml.safe_load((default_directory() / SEED).read_text(encoding="utf-8"))
    data["applies_to"]["required_biomarkers"] = {key: "positive"}
    (tmp_path / "g.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="biomarker"):
        load_guidelines(tmp_path)


def test_canonical_required_biomarker_accepted(tmp_path):
    data = yaml.safe_load((default_directory() / SEED).read_text(encoding="utf-8"))
    data["applies_to"]["required_biomarkers"] = {"MSI-H": "positive"}
    (tmp_path / "g.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert load_guidelines(tmp_path)[0].payload["applies_to"]["required_biomarkers"] == {
        "MSI-H": "positive"
    }

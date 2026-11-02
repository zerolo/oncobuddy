from pathlib import Path

import pytest

from oncobuddy.curated.loader import load_guidelines

VALID = """\
id: demo-guideline
title: Demo guideline
issuer: Demo Society
url: https://example.org/demo
region: EU
published: "2024-01-31"
valid_as_of: "2024-01-31"
licence: restricted-link-only
reviewed_by: unreviewed-seed
evidence_grade: null
own_words: false
summary: ""
applies_to:
  conditions: ["C50"]
  stages: ["I", "II"]
  required_biomarkers: {}
  line_of_therapy: any
  interventions: []
"""


def write(tmp_path: Path, text: str, name: str = "demo.yaml") -> Path:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def error_for(tmp_path: Path, text: str) -> str:
    write(tmp_path, text)
    with pytest.raises(ValueError) as info:
        load_guidelines(tmp_path)
    return str(info.value)


def test_valid_entry_maps_to_record(tmp_path):
    (record,) = load_guidelines(write(tmp_path, VALID))
    assert record.id == "guideline:demo-guideline"
    assert record.kind == "guideline"
    assert record.region == "EU"
    assert record.entities.conditions == ["C50"]
    assert record.payload["review_status"] == "unreviewed"
    assert "summary" not in record.payload


def test_missing_licence_fails_with_filename(tmp_path):
    text = VALID.replace("licence: restricted-link-only\n", "")
    message = error_for(tmp_path, text)
    assert "demo.yaml" in message
    assert "licence" in message
    assert "Demo Society" not in message and "Demo guideline" not in message


def test_unknown_key_rejected(tmp_path):
    assert "surprise" in error_for(tmp_path, VALID + "surprise: 1\n")


def test_restricted_with_summary_rejected(tmp_path):
    text = VALID.replace('summary: ""', "summary: some text").replace(
        "own_words: false", "own_words: true"
    )
    assert "summary" in error_for(tmp_path, text)


def test_non_restricted_summary_requires_own_words_and_length_limit(tmp_path):
    open_ok = VALID.replace("restricted-link-only", "CC-BY-4.0").replace(
        'summary: ""', "summary: Short own-words note."
    )
    assert "own_words" in error_for(tmp_path, open_ok)  # own_words is false
    good = open_ok.replace("own_words: false", "own_words: true")
    (record,) = load_guidelines(write(tmp_path, good))
    assert record.payload["summary"] == "Short own-words note."
    too_long = good.replace("Short own-words note.", "x" * 401)
    assert "400" in error_for(tmp_path, too_long)


@pytest.mark.parametrize("bad", ['reviewed_by: ""', 'reviewed_by: "  "'])
def test_reviewed_by_required_and_nonempty(tmp_path, bad):
    assert "reviewed_by" in error_for(tmp_path, VALID.replace("reviewed_by: unreviewed-seed", bad))
    missing = VALID.replace("reviewed_by: unreviewed-seed\n", "")
    assert "reviewed_by" in error_for(tmp_path, missing)


def test_bad_region_rejected(tmp_path):
    message = error_for(tmp_path, VALID.replace("region: EU", "region: Atlantis"))
    assert "demo.yaml" in message and "region" in message


def test_biomarker_key_pattern(tmp_path):
    bad = VALID.replace("required_biomarkers: {}", "required_biomarkers: {her2: positive}")
    assert "biomarker" in error_for(tmp_path, bad)
    good = VALID.replace("required_biomarkers: {}", "required_biomarkers: {HER2: positive}")
    (record,) = load_guidelines(write(tmp_path, good))
    assert record.payload["applies_to"]["required_biomarkers"] == {"HER2": "positive"}
    wrong_value = VALID.replace("required_biomarkers: {}", "required_biomarkers: {HER2: maybe}")
    assert "required_biomarkers" in error_for(tmp_path, wrong_value)


def test_intervention_inn_pattern(tmp_path):
    bad = VALID.replace("interventions: []", "interventions: [Trastuzumab]")
    assert "interventions" in error_for(tmp_path, bad)
    good = (
        VALID.replace("interventions: []", "interventions: [trastuzumab]")
        .replace(
            "reviewed_by: unreviewed-seed\n",
            'reviewed_by: Dr Example\nreviewed_on: "2026-11-01"\nrecommendation_ref: "Table 4"\n',
        )
        .replace("line_of_therapy: any", "line_of_therapy: first")
    )
    (record,) = load_guidelines(write(tmp_path, good))
    assert record.entities.drugs == ["trastuzumab"]


def test_unquoted_date_rejected(tmp_path):
    assert "published" in error_for(tmp_path, VALID.replace('"2024-01-31"', "2024-01-31", 1))


def test_duplicate_ids_rejected(tmp_path):
    write(tmp_path, VALID, "a.yaml")
    write(tmp_path, VALID, "b.yaml")
    with pytest.raises(ValueError, match="duplicate"):
        load_guidelines(tmp_path)


def test_every_record_carries_edition_markers(tmp_path):
    for record in load_guidelines(write(tmp_path, VALID)) + load_guidelines():
        assert record.payload["may_be_superseded"] is True
        assert record.payload["edition_note"] == (
            "cited edition; later versions or updates may exist - check the issuer"
        )


def test_review_status_only_exact_unreviewed_seed(tmp_path):
    odd = VALID.replace("unreviewed-seed", "unreviewed-by-me")
    (record,) = load_guidelines(write(tmp_path, odd))
    assert record.payload["review_status"] == "reviewed"


def test_missing_directory_rejected(tmp_path):
    with pytest.raises(ValueError, match="guideline directory not found") as info:
        load_guidelines(tmp_path / "nope")
    assert str(tmp_path) not in str(info.value)


def test_oversized_file_rejected(tmp_path):
    big = VALID + "# " + "x" * (64 * 1024) + "\n"
    message = error_for(tmp_path, big)
    assert "demo.yaml" in message and "64" in message and "xxx" not in message


def test_duplicate_keys_rejected(tmp_path):
    message = error_for(tmp_path, VALID + "title: Second title\n")
    assert "duplicate key: title" in message
    assert "Second" not in message


def test_duplicate_nested_keys_rejected(tmp_path):
    text = VALID.replace("  stages:", "  conditions: [C34]\n  stages:")
    assert "duplicate key" in error_for(tmp_path, text)


@pytest.mark.parametrize("sep", ["\u0085", "\u2028", "\u2029", "\r"])
def test_exotic_line_separators_rejected(tmp_path, sep):
    (tmp_path / "demo.yaml").write_bytes(
        VALID.replace("unreviewed-seed", f"unreviewed-seed{sep}applies_to: {{}}").encode()
    )
    with pytest.raises(ValueError) as info:
        load_guidelines(tmp_path)
    assert "line separator" in str(info.value)
    assert "applies_to" not in str(info.value)


def test_crlf_still_accepted(tmp_path):
    (tmp_path / "demo.yaml").write_bytes(VALID.replace("\n", "\r\n").encode())
    assert len(load_guidelines(tmp_path)) == 1

from pathlib import Path

import pytest
import yaml

from oncobuddy.curated.loader import load_guidelines


def entry(**overrides) -> dict:
    data = {
        "id": "demo-guideline",
        "title": "Demo guideline",
        "issuer": "Demo Society",
        "url": "https://example.org/demo",
        "region": "EU",
        "published": "2024-01-31",
        "valid_as_of": "2024-01-31",
        "licence": "restricted-link-only",
        "reviewed_by": "Dr Example",
        "reviewed_on": "2026-11-01",
        "recommendation_ref": "Table 4",
        "evidence_grade": None,
        "own_words": False,
        "summary": "",
        "applies_to": {
            "conditions": ["C50"],
            "stages": ["II"],
            "required_biomarkers": {},
            "line_of_therapy": "first",
            "interventions": ["trastuzumab"],
        },
    }
    applies = overrides.pop("applies_to", {})
    data["applies_to"] = {**data["applies_to"], **applies}
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not ...}  # ... removes the key


def write(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "demo.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


def live_error(tmp_path: Path, **overrides) -> str:
    write(tmp_path, entry(**overrides))
    with pytest.raises(ValueError) as info:
        load_guidelines(tmp_path)
    return str(info.value)


def test_reviewed_with_interventions_loads(tmp_path):
    (record,) = load_guidelines(write(tmp_path, entry()))
    assert record.payload["applies_to"]["interventions"] == ["trastuzumab"]
    assert record.payload["review_status"] == "reviewed"
    assert record.payload["reviewed_on"] == "2026-11-01"
    assert record.payload["reviewed_by"] == "Dr Example"
    assert record.payload["recommendation_ref"] == "Table 4"


def test_live_rejects_seed_reviewer_with_interventions(tmp_path):
    assert "reviewed_by" in live_error(tmp_path, reviewed_by="unreviewed-seed")


def test_seed_with_interventions_rejected(tmp_path):
    msg = live_error(
        tmp_path, reviewed_by="unreviewed-seed", reviewed_on=..., recommendation_ref=...
    )
    assert "reviewed_by" in msg


def test_live_requires_reviewed_on_and_ref(tmp_path):
    assert "reviewed_on" in live_error(tmp_path, reviewed_on=...)
    assert "recommendation_ref" in live_error(tmp_path, recommendation_ref=...)


def test_live_rejects_any_line_and_empty_stages_with_interventions(tmp_path):
    assert "line_of_therapy" in live_error(tmp_path, applies_to={"line_of_therapy": "any"})
    assert "stages" in live_error(tmp_path, applies_to={"stages": []})


def test_reviewed_on_future_rejected(tmp_path):
    assert "reviewed_on" in live_error(tmp_path, reviewed_on="2999-01-01")


def test_reviewed_on_before_valid_as_of_rejected(tmp_path):
    msg = live_error(tmp_path, valid_as_of="2026-12-01", reviewed_on="2026-11-01")
    assert "reviewed_on" in msg


def test_recommendation_ref_length_and_date_format(tmp_path):
    assert "recommendation_ref" in live_error(tmp_path, recommendation_ref="x" * 81)
    assert "reviewed_on" in live_error(tmp_path, reviewed_on="01/11/2026")


def test_error_does_not_echo_content(tmp_path):
    msg = live_error(tmp_path, reviewed_on="SECRET-MARKER")
    assert "SECRET-MARKER" not in msg
    assert "reviewed_on" in msg

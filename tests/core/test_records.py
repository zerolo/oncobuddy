from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from oncobuddy.core.records import Entities, Record

RESTRICTED_KEYS = ["text", "abstract", "full_text", "summary_text"]


def _base(**overrides):
    data = {
        "id": "rec-1",
        "kind": "medicine",
        "entities": Entities(drugs=["trastuzumab"]),
        "region": "PT",
        "source": "infarmed",
        "source_url": "https://example.test/rec-1",
        "valid_as_of": date(2026, 10, 1),
        "retrieved_at": datetime(2026, 11, 1, tzinfo=UTC),
        "licence": "open",
        "payload": {"name": "Herceptin"},
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("field", ["region", "source", "valid_as_of", "licence"])
def test_record_requires_field(field):
    data = _base()
    del data[field]
    with pytest.raises(ValidationError, match=field):
        Record(**data)


def test_unknown_date_is_kept_as_unknown():
    record = Record(**_base(valid_as_of="unknown"))
    assert record.valid_as_of == "unknown"


def test_invalid_date_string_is_rejected():
    with pytest.raises(ValidationError):
        Record(**_base(valid_as_of="maybe"))


def test_pointer_kind_is_accepted():
    assert Record(**_base(kind="pointer")).kind == "pointer"


def test_invalid_kind_is_rejected():
    with pytest.raises(ValidationError):
        Record(**_base(kind="podcast"))


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        Record(**_base(surprise="x"))


@pytest.mark.parametrize("key", RESTRICTED_KEYS)
def test_restricted_record_payload_has_no_text_field(key):
    with pytest.raises(ValidationError, match=key):
        Record(**_base(licence="restricted-link-only", payload={"title": "x", key: "copied"}))


@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"text": "copied"}},
        {"items": [{"abstract": "copied"}]},
        {"outer": {"inner": [{"deep": {"full_text": "copied"}}]}},
    ],
)
def test_restricted_record_rejects_nested_text_fields(payload):
    with pytest.raises(ValidationError, match="text|abstract|full_text"):
        Record(**_base(licence="restricted-link-only", payload=payload))


def test_restricted_record_without_text_fields_is_allowed():
    record = Record(**_base(licence="restricted-link-only", payload={"title": "x"}))
    assert record.licence == "restricted-link-only"


def test_text_key_allowed_for_non_restricted_licence():
    record = Record(**_base(licence="open", payload={"text": "fine here"}))
    assert record.payload == {"text": "fine here"}

"""Record schema: the single shape every connector result is normalised into."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RESTRICTED_LICENCE = "restricted-link-only"
RESTRICTED_TEXT_KEYS = frozenset({"text", "abstract", "full_text", "summary_text"})


def _restricted_keys_in(value: object) -> set[str]:
    """Collect restricted text keys at any depth of nested dicts and lists."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in RESTRICTED_TEXT_KEYS:
                found.add(key)
            found |= _restricted_keys_in(child)
    elif isinstance(value, list):
        for child in value:
            found |= _restricted_keys_in(child)
    return found


class Entities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drugs: list[str] = Field(default_factory=list)
    atc: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    biomarkers: list[str] = Field(default_factory=list)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["medicine", "trial", "guideline", "code", "publication", "pointer", "edition"]
    entities: Entities
    region: str
    source: str
    source_url: str
    valid_as_of: date | Literal["unknown"]
    retrieved_at: datetime
    licence: str
    payload: dict
    reviewed_by: str | None = None

    @model_validator(mode="after")
    def _restricted_payload_has_no_text(self) -> "Record":
        if self.licence == RESTRICTED_LICENCE:
            found = sorted(_restricted_keys_in(self.payload))
            if found:
                raise ValueError(
                    f"restricted-link-only record must not carry text fields: {found}"
                )
        return self

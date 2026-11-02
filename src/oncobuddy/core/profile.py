"""Closed-schema clinical profile: the only patient-adjacent shape the server accepts.

This is a privacy boundary. Unknown fields are rejected, and free text cannot be
represented: cancer type and drugs pass the shape-checked normalisers, biomarker keys
match a fixed pattern, and every other field is an enum or a bounded integer. Errors
name field paths and error types only, never input values, and the profile never
prints its contents.

Use parse_profile as the only entry point for untrusted input. Direct construction,
ClinicalProfile(**d), raises a pydantic ValidationError whose message includes the input
values, so it must not reach logs or clients.
"""

import re
from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from oncobuddy.core.biomarkers import normalise_biomarkers
from oncobuddy.core.normalise import normalise_condition, normalise_drug

_BIOMARKER_KEY = re.compile(r"[A-Z0-9_-]{1,20}")
_MAX_PRIOR_LINES = 20
_MAX_BIOMARKERS = 30
# Bound on the raw list, checked before de-duplication. Only four distinct flags exist.
_MAX_COMORBIDITY_FLAGS_RAW = 8

Biomarker = Literal["positive", "negative", "unknown"]
ComorbidityFlag = Literal["renal_impairment", "hepatic_impairment", "cardiac_disease", "pregnancy"]


class ProfileError(ValueError):
    """Raised for an invalid profile. The message names field paths and error types only."""


class ClinicalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cancer_type: str
    stage: Literal["I", "II", "III", "IV", "unknown"] = "unknown"
    biomarkers: dict[str, Biomarker] = Field(default_factory=dict, max_length=_MAX_BIOMARKERS)
    ecog: Annotated[int, Field(ge=0, le=4, strict=True)] | None = None
    # None = not provided; [] = explicitly no prior therapy.
    prior_lines: list[str] | None = Field(default=None, max_length=_MAX_PRIOR_LINES)
    intent: Literal["curative", "palliative", "unknown"] = "unknown"
    comorbidity_flags: list[ComorbidityFlag] = Field(
        default_factory=list, max_length=_MAX_COMORBIDITY_FLAGS_RAW
    )

    @field_validator("cancer_type")
    @classmethod
    def _normalise_cancer_type(cls, value: str) -> str:
        code = normalise_condition(value)
        if code is None:
            raise ValueError("cancer_type must be an ICD-10 or ICD-O-3 code")
        return code

    @field_validator("prior_lines")
    @classmethod
    def _normalise_prior_lines(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for name in value:
            drug = normalise_drug(name)
            if drug is None:
                raise ValueError("prior_lines entries must be INN drug names")
            if drug not in result:
                result.append(drug)
        return result

    @field_validator("comorbidity_flags")
    @classmethod
    def _dedupe_comorbidity_flags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @field_validator("biomarkers", mode="before")
    @classmethod
    def _upper_biomarker_keys(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        result: dict[object, object] = {}
        for key, status in value.items():
            name = key.upper() if isinstance(key, str) else key
            if name in result and result[name] != status:
                raise ValueError("biomarker keys differ only by case and disagree")
            result[name] = status
        return result

    @field_validator("biomarkers")
    @classmethod
    def _check_biomarker_keys(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not _BIOMARKER_KEY.fullmatch(key):
                raise ValueError("biomarker keys must match ^[A-Z0-9_-]{1,20}$")
        return normalise_biomarkers(value)

    def __repr__(self) -> str:
        return "ClinicalProfile(<redacted>)"

    __str__ = __repr__

    # pydantic's rich and pretty hooks expose field values; yield nothing instead.
    def __rich_repr__(self) -> Iterator[tuple[str | None, object]]:
        return iter(())

    def __repr_args__(self) -> list[tuple[str | None, object]]:
        return []


_FIELDS = frozenset(ClinicalProfile.model_fields)


def _location(error: dict) -> str:
    """Field path for one pydantic error. User-supplied keys are replaced by placeholders."""
    loc = error["loc"]
    if not loc:
        return "<root>"
    head = loc[0]
    if head in _FIELDS:
        parts = [head]
    elif error["type"] == "extra_forbidden":
        parts = ["extra"]
    else:
        parts = ["<key>"]
    for part in loc[1:]:
        parts.append(str(part) if isinstance(part, int) else "<key>")
    return ".".join(parts)


def parse_profile(data: object) -> ClinicalProfile:
    """Validate untrusted input into a ClinicalProfile.

    Raises ProfileError. Its message lists field paths and pydantic error types only. The
    original ValidationError is dropped with `from None`, since it carries the input values.
    """
    if not isinstance(data, dict):
        raise ProfileError("profile must be an object")
    try:
        return ClinicalProfile.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{_location(error)}: {error['type']}"
            for error in exc.errors(include_url=False, include_input=False, include_context=False)
        )
        raise ProfileError(f"invalid profile: {problems}") from None

"""Loader for the curated guideline index (one guideline per YAML file).

Format, rules and review process: CONTRIBUTING.md. Source checks: docs/sources/guidelines.md.

Every file is validated with pydantic (unknown keys rejected). An invalid file raises
ValueError naming the file and the offending field, never the file content.

``valid_as_of`` is the date of the CITED EDITION (online publication date). It is NOT a
statement that the guideline is still current: every record carries ``may_be_superseded: true``
and an ``edition_note``. ``applies_to.stages`` in seeds are inferred from the guideline title
("early" => I-III, "metastatic" => IV), not statements made by the guideline.
"""

import re
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from oncobuddy.core.access import Access, check_entry_url, check_licence_for_access
from oncobuddy.core.biomarkers import is_canonical
from oncobuddy.core.records import RESTRICTED_LICENCE, Entities, Record
from oncobuddy.core.regions import RegionTree

SUMMARY_MAX_CHARS = 400
MAX_FILE_BYTES = 64 * 1024
EDITION_NOTE = "cited edition; later versions or updates may exist - check the issuer"
STALE_DAYS = 540
_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"


_LINE_BREAKS = re.compile("[\u0085\u2028\u2029]|\r(?!\n)")


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (YAML would silently keep the last)."""


def _construct_unique(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in seen
        except TypeError:
            raise yaml.YAMLError("unhashable key") from None
        if duplicate:
            name = key if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key) else "?"
            raise _DuplicateKeyError(name)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


class _DuplicateKeyError(yaml.YAMLError):
    pass


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique)


def _check_date(value: str) -> str:
    if value == "unknown":
        return value
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("must be YYYY-MM-DD or 'unknown'")
    date.fromisoformat(value)
    return value


class Applicability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conditions: list[str] = Field(min_length=1)
    stages: list[Literal["I", "II", "III", "IV"]]
    required_biomarkers: dict[str, Literal["positive", "negative"]] = Field(default_factory=dict)
    line_of_therapy: Literal["first", "later", "any"]
    interventions: list[str] = Field(default_factory=list)  # empty = scope-only entry

    @field_validator("conditions")
    @classmethod
    def _conditions(cls, value: list[str]) -> list[str]:
        for code in value:
            if not re.fullmatch(r"[A-Z][0-9]{2}", code):
                raise ValueError("conditions must be ICD-10 3-character categories like 'C50'")
        return value

    @field_validator("required_biomarkers")
    @classmethod
    def _biomarkers(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not re.fullmatch(r"[A-Z0-9_-]{1,20}", key):
                raise ValueError("biomarker keys must match ^[A-Z0-9_-]{1,20}$")
            if not is_canonical(key):
                raise ValueError("biomarker keys must be canonical oncoBuddy vocabulary keys")
        return value

    @field_validator("interventions")
    @classmethod
    def _interventions(cls, value: list[str]) -> list[str]:
        for name in value:
            if not re.fullmatch(r"[a-z][a-z-]{1,39}", name):
                raise ValueError("interventions must be lowercase INN-shaped names")
        return value


class GuidelineFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_SLUG)
    title: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    url: str = Field(min_length=1)
    region: str = Field(min_length=1)
    published: str
    valid_as_of: str
    licence: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    reviewed_on: str | None = None
    recommendation_ref: str | None = Field(default=None, min_length=1, max_length=80)
    evidence_grade: str | None
    own_words: bool
    summary: str
    applies_to: Applicability
    access: Access | None = None
    last_verified: str | None = None
    reference_only: bool = False

    @field_validator("last_verified")
    @classmethod
    def _last_verified(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value == "unknown":
            raise ValueError("must be YYYY-MM-DD")
        _check_date(value)
        if date.fromisoformat(value) > datetime.now(UTC).date():
            raise ValueError("must not be in the future")
        return value

    @field_validator("published", "valid_as_of")
    @classmethod
    def _dates(cls, value: str) -> str:
        return _check_date(value)

    @field_validator("reviewed_on")
    @classmethod
    def _reviewed_on(cls, value: str | None) -> str | None:
        if value is not None and value == "unknown":
            raise ValueError("must be YYYY-MM-DD")
        return value if value is None else _check_date(value)

    @field_validator("reviewed_by")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewed_by must be non-empty")
        return value

    @model_validator(mode="after")
    def _summary_rules(self) -> "GuidelineFile":
        if self.licence == RESTRICTED_LICENCE:
            if self.summary != "":
                raise ValueError("summary must be empty when licence is restricted-link-only")
        else:
            if not self.own_words:
                raise ValueError("a non-restricted summary must have own_words: true")
            if len(self.summary) > SUMMARY_MAX_CHARS:
                raise ValueError(f"summary must be at most {SUMMARY_MAX_CHARS} characters")
        return self


def default_directory() -> Path:
    return Path(str(files("oncobuddy.curated") / "guidelines"))


def _check_review(spec: GuidelineFile) -> None:
    """Rules for hand-curated entries that name interventions. Messages name fields only."""
    reviewer = spec.reviewed_by.strip().casefold()
    applies = spec.applies_to
    if not applies.interventions:
        return
    if reviewer == "unreviewed-seed":
        raise ValueError("reviewed_by: seed entries may not list interventions")
    if not applies.stages:
        raise ValueError("applies_to.stages: must be non-empty when interventions are listed")
    if applies.line_of_therapy == "any":
        raise ValueError(
            "applies_to.line_of_therapy: must be stated (not 'any') when interventions are listed"
        )
    if spec.recommendation_ref is None:
        raise ValueError("recommendation_ref: required when interventions are listed")
    if spec.reviewed_on is None:
        raise ValueError("reviewed_on: required when interventions are listed")
    reviewed = date.fromisoformat(spec.reviewed_on)
    if reviewed > datetime.now(UTC).date():
        raise ValueError("reviewed_on: must not be in the future")
    if spec.valid_as_of != "unknown" and reviewed < date.fromisoformat(spec.valid_as_of):
        raise ValueError("reviewed_on: must not precede valid_as_of")


def parse_yaml_text(raw: bytes, name: str) -> object:
    """Strict parse: UTF-8, no exotic line breaks, no duplicate keys. Errors name only ``name``."""
    try:
        text = raw.decode("utf-8")
        if _LINE_BREAKS.search(text):
            raise ValueError(f"{name}: unsupported line separator in file")
        return yaml.load(text, Loader=_StrictLoader)
    except _DuplicateKeyError as err:
        raise ValueError(f"{name}: duplicate key: {err}") from None
    except (yaml.YAMLError, UnicodeDecodeError):
        raise ValueError(f"{name}: not valid YAML") from None


def _describe(error: ValidationError) -> str:
    # loc + message only: pydantic's "input" values would leak file content.
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<file>'}: {e['msg']}" for e in error.errors()
    )


def _to_record(spec: GuidelineFile, retrieved_at: datetime) -> Record:
    payload: dict = {
        "title": spec.title,
        "issuer": spec.issuer,
        "own_words": spec.own_words,
        "evidence_grade": spec.evidence_grade,
        "applies_to": spec.applies_to.model_dump(),
        "published": spec.published,
        "review_status": "unreviewed" if spec.reviewed_by.strip().casefold() == "unreviewed-seed" else "reviewed",
        "edition_note": EDITION_NOTE,
        "may_be_superseded": True,
        "reviewed_by": spec.reviewed_by,
    }
    if spec.reviewed_on is not None:
        payload["reviewed_on"] = spec.reviewed_on
    if spec.recommendation_ref is not None:
        payload["recommendation_ref"] = spec.recommendation_ref
    if spec.licence != RESTRICTED_LICENCE:
        payload["summary"] = spec.summary
    if spec.last_verified is not None:
        payload["last_verified"] = spec.last_verified
        age = retrieved_at.date() - date.fromisoformat(spec.last_verified)
        payload["verification_stale"] = age > timedelta(days=STALE_DAYS)
    if spec.reference_only:
        payload["reference_only"] = True
    if spec.access is not None:
        access = spec.access.model_dump()
        access["entry_url"] = access["entry_url"] or spec.url
        payload["access"] = access
    return Record(
        id=f"guideline:{spec.id}",
        kind="guideline",
        entities=Entities(
            conditions=list(spec.applies_to.conditions),
            drugs=list(spec.applies_to.interventions),
        ),
        region=spec.region,
        source=spec.issuer,
        source_url=spec.url,
        valid_as_of=(
            "unknown" if spec.valid_as_of == "unknown" else date.fromisoformat(spec.valid_as_of)
        ),
        retrieved_at=retrieved_at,
        licence=spec.licence,
        payload=payload,
        reviewed_by=spec.reviewed_by,
    )


def load_guidelines(directory: Path | None = None) -> list[Record]:
    """Load and validate every ``*.yaml`` file in the directory (default: packaged seeds)."""
    root = default_directory() if directory is None else Path(directory)
    if not root.is_dir():
        raise ValueError("guideline directory not found")
    tree = RegionTree.load()
    now = datetime.now(UTC)
    records: list[Record] = []
    seen: dict[str, str] = {}
    for path in sorted(root.glob("*.yaml")):
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"{path.name}: file larger than {MAX_FILE_BYTES // 1024} KiB")
        data = parse_yaml_text(path.read_bytes(), path.name)
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: expected a mapping at top level")  # noqa: TRY004
        try:
            spec = GuidelineFile.model_validate(data)
        except ValidationError as err:
            raise ValueError(f"{path.name}: invalid guideline: {_describe(err)}") from None
        try:
            _check_review(spec)
        except ValueError as err:
            raise ValueError(f"{path.name}: invalid guideline: {err}") from None
        if spec.access is not None:
            try:
                check_licence_for_access(spec.access, spec.licence)
                check_entry_url(spec.access, spec.url)
            except ValueError as err:
                raise ValueError(f"{path.name}: invalid guideline: access: {err}") from None
        try:
            chain = tree.scope_chain(spec.region)
        except ValueError:
            raise ValueError(f"{path.name}: region: not in the region tree") from None
        if spec.reference_only and ("EU" in chain or spec.region == "global"):
            raise ValueError(
                f"{path.name}: invalid guideline: reference_only: not allowed for EU-scope or global regions"
            )
        if spec.id in seen:
            raise ValueError(f"{path.name}: duplicate id also used in {seen[spec.id]}")
        seen[spec.id] = path.name
        try:
            records.append(_to_record(spec, now))
        except ValidationError as err:
            raise ValueError(f"{path.name}: invalid record: {_describe(err)}") from None
    return records

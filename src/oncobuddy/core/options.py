"""Treatment options matcher: options come only from guideline records, never model knowledge.

Deterministic. Uncertainty (unknown stage or biomarker, unreviewed or superseded guideline,
missing EMA record) surfaces as caveats; only a definite mismatch excludes a guideline, and
every exclusion is listed. The profile is patient-derived: it is never logged, stored or
echoed. Notes, caveats and exclusion reasons use only stage, biomarker keys and the enumerated
statuses, line-of-therapy labels and guideline-supplied text.
"""

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from oncobuddy.core.access import AGENT_PROTOCOL
from oncobuddy.core.biomarkers import is_canonical
from oncobuddy.core.profile import ClinicalProfile
from oncobuddy.core.readplan import build_read_plan
from oncobuddy.core.reconciler import NOTICE, PER_PRODUCT_NOTE, NoData, Reconciler, Section
from oncobuddy.core.records import RESTRICTED_LICENCE, Record


class Exclusion(BaseModel):
    guideline_id: str
    reason: str


class TreatmentOption(BaseModel):
    intervention: str
    line: Literal["first", "later", "any"]
    guideline: Record
    authorisation: Section
    trials: Section
    reimbursement: Section
    caveats: list[str] = Field(default_factory=list)


class OptionsResult(BaseModel):
    query_region: str
    scope_chain: list[str]
    options: list[TreatmentOption] = Field(default_factory=list)
    guidelines_in_scope: list[Record] = Field(default_factory=list)
    # Guidelines issued outside the query region: listed only, never matched or used.
    reference_elsewhere: list[Record] = Field(default_factory=list)
    excluded: list[Exclusion] = Field(default_factory=list)
    no_data: NoData | None = None
    read_plan: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    notice: str = NOTICE
    agent_protocol: str = AGENT_PROTOCOL


CATEGORY_CAVEAT = "matched on ICD site category only; histology and subtype not checked"
POINTER_CAVEAT = (
    "Portuguese national status was not retrieved; read it in the user's browser from the "
    "INFARMED links in the authorisation and reimbursement sections"
)
BROWSER_NOTE = "source requires browser reading: ask the user"
NOT_IN_REGISTER = "not in the EU central register; national authorisations not covered"


def _unused_fields(profile: ClinicalProfile) -> list[str]:
    """Names (never values) of provided profile fields that matching does not use in v1."""
    names = []
    if profile.ecog is not None:
        names.append("ecog")
    if profile.intent != "unknown":
        names.append("intent")
    if profile.comorbidity_flags:
        names.append("comorbidity_flags")
    return names


def _applicability(
    profile: ClinicalProfile, profile_line: str, guideline: Record
) -> tuple[list[str], list[str]]:
    """Return (exclusion reasons, caveats) for one guideline against the profile."""
    applies = guideline.payload.get("applies_to", {})
    stages: list[str] = list(applies.get("stages", []))
    scope = ", ".join(stages)
    reasons: list[str] = []
    caveats: list[str] = []

    if not stages:
        caveats.append("guideline states no stage scope")
    elif profile.stage == "unknown":
        caveats.append(f"stage not provided; guideline scope is {scope}")
    elif profile.stage not in stages:
        reasons.append(f"stage {profile.stage} outside guideline scope {scope}")

    for key, wanted in sorted(applies.get("required_biomarkers", {}).items()):
        have = profile.biomarkers.get(key, "unknown")
        if have == wanted:
            continue
        if have == "unknown":
            caveats.append(f"biomarker {key} status unknown; guideline requires {key}={wanted}")
        else:
            reasons.append(f"biomarker {key} is {have}; guideline requires {wanted}")

    line = applies.get("line_of_therapy", "any")
    if line != "any" and profile_line == "unknown":
        caveats.append(f"prior therapy not provided; guideline is for {line} line")
    elif line == "first" and profile_line == "later":
        caveats.append(
            "prior therapy recorded; guideline is for first line - setting of prior therapy unknown"
        )
    elif line != "any" and line != profile_line:
        reasons.append(f"line of therapy: guideline is {line}, profile is {profile_line}")
    return reasons, caveats


def _has_browser_access(records: list[Record]) -> bool:
    return any(r.payload.get("access", {}).get("method") == "browser" for r in records)


def _guideline_caveats(guideline: Record) -> list[str]:
    caveats: list[str] = [CATEGORY_CAVEAT]
    if guideline.payload.get("review_status") == "unreviewed":
        caveats.append("guideline entry unreviewed (seed)")
    if guideline.payload.get("may_be_superseded"):
        caveats.append("cited edition; later versions may exist")
    if guideline.payload.get("verification_stale"):
        caveats.append("registry entry last verified more than 540 days ago")
    if guideline.licence == RESTRICTED_LICENCE:
        caveats.append("guideline content is link-only; not evaluated")
    check = guideline.payload.get("edition_check")
    status = check.get("status") if isinstance(check, dict) else None
    if status == "newer_found":
        when = (check.get("newer") or {}).get("date", "date unknown")
        caveats.append(f"newer edition found ({when}); read it instead")
    elif status == "corrected":
        caveats.append("corrections published; read the notice")
    elif status == "retracted":
        caveats.append("retracted")
    return caveats


async def treatment_options(
    profile: ClinicalProfile, region: str, reconciler: Reconciler
) -> OptionsResult:
    chain = reconciler.tree.scope_chain(region)  # UnknownRegion propagates
    result = OptionsResult(query_region=region, scope_chain=chain)
    # Profile-supplied keys are never echoed (they come from the patient's record): count only.
    outside = sum(1 for key in profile.biomarkers if not is_canonical(key))
    if outside:
        noun = "key" if outside == 1 else "keys"
        result.notes.append(
            f"{outside} biomarker {noun} not in the oncoBuddy vocabulary; "
            "not used for matching"
        )
    unused = _unused_fields(profile)
    if unused:
        result.notes.append(f"not used by matching in v1: {', '.join(unused)}")

    guidelines = await reconciler.section(
        "guidelines", drug=None, condition=profile.cancer_type, region=region
    )
    result.notes.extend(guidelines.notes)
    result.reference_elsewhere = list(guidelines.reference_elsewhere)
    reference_browser = _has_browser_access(result.reference_elsewhere)
    if guidelines.no_data is not None:
        result.no_data = guidelines.no_data
        if guidelines.no_data.reason == "source_unavailable":
            result.notes.append("guideline source unavailable; no options can be derived")
        else:
            result.notes.append("no guideline records for this condition in scope")
        if reference_browser:
            result.read_plan = build_read_plan(profile)
        return result

    prior = profile.prior_lines
    profile_line = "unknown" if prior is None else ("later" if prior else "first")
    pending: list[tuple[Record, str, list[str]]] = []  # (guideline, intervention, caveats)
    seen: set[tuple[str, str]] = set()
    in_scope_ids: set[str] = set()

    for guideline in guidelines.records:
        reasons, caveats = _applicability(profile, profile_line, guideline)
        if reasons:
            result.excluded.append(Exclusion(guideline_id=guideline.id, reason="; ".join(reasons)))
            continue
        caveats = caveats + _guideline_caveats(guideline)
        interventions = guideline.payload.get("applies_to", {}).get("interventions", [])
        if not interventions:
            if guideline.id not in in_scope_ids:
                in_scope_ids.add(guideline.id)
                result.guidelines_in_scope.append(guideline)
                if guideline.payload.get("access", {}).get("method") == "browser":
                    caveats = [*caveats, BROWSER_NOTE]
                result.notes.extend(f"{guideline.id}: {c}" for c in caveats)
            continue
        for intervention in interventions:
            if (intervention, guideline.id) in seen:
                continue
            seen.add((intervention, guideline.id))
            pending.append((guideline, intervention, caveats))

    async def lookup(intervention: str) -> tuple[Section, Section, Section]:
        return await asyncio.gather(
            reconciler.section(
                "authorisation",
                drug=intervention,
                condition=profile.cancer_type,
                region=region,
            ),
            reconciler.section(
                "trials", drug=intervention, condition=profile.cancer_type, region=region
            ),
            reconciler.section(
                "reimbursement",
                drug=intervention,
                condition=profile.cancer_type,
                region=region,
            ),
        )

    sections = await asyncio.gather(*(lookup(intervention) for _, intervention, _ in pending))

    options: list[TreatmentOption] = []
    for (guideline, intervention, caveats), (authorisation, trials, reimbursement) in zip(
        pending, sections
    ):
        extra: list[str] = []
        if prior is not None and intervention in prior:
            extra.append("intervention already received (prior line)")
        real = [r for r in authorisation.records if r.kind != "pointer"]
        if authorisation.no_data is not None:
            if authorisation.no_data.reason == "not_found":
                extra.append(NOT_IN_REGISTER)
            elif authorisation.no_data.reason == "source_unavailable":
                extra.append("EMA data unavailable")
        if real:
            extra.append(PER_PRODUCT_NOTE)
            for medicine in real:
                status = medicine.payload.get("status")
                if isinstance(status, str) and status != "Authorised":
                    extra.append(f"EMA status: {status}")
        if any(r.kind == "pointer" for r in authorisation.records):
            extra.append(POINTER_CAVEAT)
        options.append(
            TreatmentOption(
                intervention=intervention,
                line=guideline.payload["applies_to"].get("line_of_therapy", "any"),
                guideline=guideline,
                authorisation=authorisation,
                trials=trials,
                reimbursement=reimbursement,
                caveats=[*caveats, *extra],
            )
        )
    # Stable sort keeps guideline order (records arrive in section order); then name.
    index = {g.id: i for i, g in enumerate(guidelines.records)}
    options.sort(key=lambda o: (index[o.guideline.id], o.intervention))
    result.options = options
    if reference_browser or _has_browser_access(
        [*result.guidelines_in_scope, *(o.guideline for o in options)]
    ):
        result.read_plan = build_read_plan(profile)

    if not options:
        if result.no_data is None:
            result.no_data = NoData(reason="not_found")
        if result.guidelines_in_scope:
            note = "no guideline names an intervention for this profile in scope"
        elif result.excluded:
            note = "guidelines in scope were excluded for this profile; see excluded"
        else:
            note = "no guideline in scope"
        result.notes.append(note)
    return result

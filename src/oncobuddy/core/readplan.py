"""Deterministic read plan: what to look for when the user reads a guideline in the browser.

Privacy: built only from the stage enum, canonical biomarker keys with positive/negative
status, the first/later-line distinction and the intent enum. Profile-supplied free values
(unknown biomarker keys, prior drug names, the ICD code) are never read into it.
"""

from oncobuddy.core.biomarkers import is_canonical
from oncobuddy.core.profile import ClinicalProfile

READ_PLAN_CLOSING = "This plan is a reading instruction, not advice"
MAX_LINES = 4
MAX_CHARS = 200

_PREFIX = "Find the recommendations for "
_FALLBACK = "the condition in the entry"
_DRUGS_LINE = "Report each named drug with its printed evidence level and the setting it applies to"


def _descriptor(profile: ClinicalProfile) -> str:
    head: list[str] = []
    if profile.stage != "unknown":
        head.append(f"stage {profile.stage}")
    markers = [
        f"{key} {status}"
        for key, status in sorted(profile.biomarkers.items())
        if is_canonical(key) and status in ("positive", "negative")
    ]
    tail: list[str] = []
    if profile.prior_lines is not None:
        tail.append("later-line treatment" if profile.prior_lines else "first-line treatment")
    if profile.intent in ("curative", "palliative"):
        tail.append(f"{profile.intent} intent")
    while True:
        text = ", ".join([*head, *markers, *tail])
        if len(_PREFIX) + len(text) <= MAX_CHARS or not markers:
            return text or _FALLBACK
        markers.pop()


def build_read_plan(profile: ClinicalProfile) -> list[str]:
    return [_PREFIX + _descriptor(profile), _DRUGS_LINE, READ_PLAN_CLOSING][:MAX_LINES]

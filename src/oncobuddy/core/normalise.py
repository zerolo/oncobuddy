"""Input normalisation: exact, shape-checked forms of drug and condition inputs.

These checks double as the privacy filter for queries sent to external sources: only
plain ASCII drug names and ICD/ICD-O codes pass. No brand or fuzzy mapping happens here.
Exact matching against sources is the job of the connectors, so unmatched names come back
as not_found there.
"""

import re

_DRUG = re.compile(r"[a-z][a-z-]{1,39}")
# ICD-10 / ICD-O-3 topography, e.g. C50, C50.9, D05.1.
_TOPOGRAPHY = re.compile(r"[A-Z][0-9]{2}[0-9A-Z]?(?:\.[0-9A-Z]{1,4})?")
# ICD-O-3 morphology, e.g. 8500/3 (the behaviour digit is required).
_MORPHOLOGY = re.compile(r"[0-9]{4}/[0-9]")


def normalise_drug(name: str) -> str | None:
    if not name.isascii():
        return None  # reject before case-folding: e.g. KELVIN SIGN lowercases to ASCII "k"
    value = name.strip().lower()
    return value if _DRUG.fullmatch(value) else None


def normalise_condition(value: str) -> str | None:
    if not value.isascii():
        return None  # reject before case-folding: e.g. dotless i uppercases to ASCII "I"
    code = value.strip().upper()
    if _TOPOGRAPHY.fullmatch(code) or _MORPHOLOGY.fullmatch(code):
        return code
    return None

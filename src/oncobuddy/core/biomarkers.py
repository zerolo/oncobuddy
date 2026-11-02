"""Canonical biomarker vocabulary: one key per marker, with aliases normalised on input.

Profiles and curated guideline entries must name the same marker the same way or matching
silently fails. Curated entries use canonical keys only; profile input may use aliases, which
are mapped to the canonical key (some aliases invert the status, e.g. MSS -> MSI-H negative).
A key outside the vocabulary is still accepted in a profile but is not used for matching (no
curated entry can require it) and the matcher says how many such keys there were; it can never
appear in a curated entry.

"positive" means the marker is present as the meaning below states; the cited source defines
the exact test and thresholds. Error messages never echo keys or values.
"""

CANONICAL: dict[str, str] = {
    "HER2": "overexpressed or amplified, as the source defines it",
    "ER": "oestrogen receptor expressed, as the source defines it",
    "PR": "progesterone receptor expressed, as the source defines it",
    "MSI-H": "mismatch-repair deficient or microsatellite-instability high",
    "RAS": "KRAS or NRAS mutation present; negative means wild-type for both",
    "KRAS": "KRAS mutation present",
    "NRAS": "NRAS mutation present",
    "BRAF-V600E": "BRAF V600E mutation present",
    "EGFR": "activating EGFR mutation present",
    "ALK": "ALK gene fusion present",
    "ROS1": "ROS1 gene fusion present",
    "NTRK": "NTRK gene fusion present",
    "BRCA": "pathogenic BRCA1 or BRCA2 variant present",
}

# alias -> (canonical key, invert status)
ALIASES: dict[str, tuple[str, bool]] = {
    "ERBB2": ("HER2", False),
    "HER-2": ("HER2", False),
    "HER2_NEU": ("HER2", False),
    "DMMR": ("MSI-H", False),
    "MSIH": ("MSI-H", False),
    "MSI_H": ("MSI-H", False),
    "MSI-HIGH": ("MSI-H", False),
    "MSS": ("MSI-H", True),
    "PMMR": ("MSI-H", True),
}

_FLIP = {"positive": "negative", "negative": "positive", "unknown": "unknown"}
# MSS only negates: "not MSS" includes MSI-low, which is not MSI-H, so it never asserts MSI-H.
_NEGATE_ONLY = frozenset({"MSS"})


def canonical_key(key: str) -> tuple[str, bool]:
    """Return (canonical key, invert); an unknown key is returned unchanged."""
    if key in ALIASES:
        return ALIASES[key]
    return key, False


def is_canonical(key: str) -> bool:
    return key in CANONICAL


def normalise_biomarkers(raw: dict[str, str]) -> dict[str, str]:
    """Map aliases to canonical keys. A known status beats unknown; known disagreement raises."""
    result: dict[str, str] = {}
    for key, status in raw.items():
        name, invert = canonical_key(key)
        if not invert:
            value = status
        elif key in _NEGATE_ONLY:
            value = "negative" if status == "positive" else "unknown"
        else:
            value = _FLIP.get(status, status)
        have = result.get(name)
        if have is None or have == "unknown":
            result[name] = value
        elif value not in ("unknown", have):
            raise ValueError("biomarker aliases conflict")
    return result


def vocabulary_description() -> str:
    """Plain-ASCII description of the vocabulary for the tool schema and docs."""
    keys = ", ".join(CANONICAL)
    pos = [a for a, (_, inv) in ALIASES.items() if not inv]
    neg = [a for a, (_, inv) in ALIASES.items() if inv]
    return (
        f"Biomarker status by key. Canonical keys: {keys}. Aliases are mapped on input "
        f"({', '.join(pos)}; {', '.join(neg)} invert the status, e.g. MSS positive means MSI-H "
        "negative, while MSS negative stays unknown). Keys are case-insensitive. positive means "
        "the marker is present (RAS negative = wild-type); unlisted keys are accepted but not "
        "used for matching."
    )

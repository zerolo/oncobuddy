import pytest

from oncobuddy.core.biomarkers import (
    CANONICAL,
    canonical_key,
    is_canonical,
    normalise_biomarkers,
    vocabulary_description,
)


def test_canonical_keys_map_to_themselves():
    for key in CANONICAL:
        assert canonical_key(key) == (key, False)
        assert is_canonical(key)


@pytest.mark.parametrize("alias", ["DMMR", "MSIH", "MSI_H", "MSI-HIGH"])
def test_msi_aliases(alias):
    assert canonical_key(alias) == ("MSI-H", False)
    assert not is_canonical(alias)


def test_bare_msi_is_not_an_alias():
    assert canonical_key("MSI") == ("MSI", False)  # ambiguous: could be MSI-low


def test_pmmr_flips_status_both_ways():
    assert canonical_key("PMMR") == ("MSI-H", True)
    assert normalise_biomarkers({"PMMR": "positive"}) == {"MSI-H": "negative"}
    assert normalise_biomarkers({"PMMR": "negative"}) == {"MSI-H": "positive"}
    assert normalise_biomarkers({"PMMR": "unknown"}) == {"MSI-H": "unknown"}


def test_mss_only_negates_never_asserts_msi_h():
    # "not MSS" includes MSI-low, which is not MSI-H, so MSS negative must stay unknown.
    assert canonical_key("MSS") == ("MSI-H", True)
    assert normalise_biomarkers({"MSS": "positive"}) == {"MSI-H": "negative"}
    assert normalise_biomarkers({"MSS": "negative"}) == {"MSI-H": "unknown"}
    assert normalise_biomarkers({"MSS": "unknown"}) == {"MSI-H": "unknown"}


def test_her2_aliases():
    for alias in ("ERBB2", "HER-2", "HER2_NEU"):
        assert normalise_biomarkers({alias: "positive"}) == {"HER2": "positive"}


def test_unknown_key_passes_through_unchanged():
    assert canonical_key("ZZZ9") == ("ZZZ9", False)
    assert not is_canonical("ZZZ9")
    assert normalise_biomarkers({"ZZZ9": "positive"}) == {"ZZZ9": "positive"}


def test_kras_does_not_alias_ras():
    assert canonical_key("KRAS") == ("KRAS", False)
    assert is_canonical("KRAS") and is_canonical("RAS")


def test_agreeing_duplicates_collapse():
    assert normalise_biomarkers({"DMMR": "positive", "MSI-H": "positive"}) == {
        "MSI-H": "positive"
    }
    assert normalise_biomarkers({"PMMR": "negative", "DMMR": "positive"}) == {"MSI-H": "positive"}
    assert normalise_biomarkers({"MSS": "negative", "DMMR": "positive"}) == {"MSI-H": "positive"}


def test_known_status_beats_unknown():
    assert normalise_biomarkers({"DMMR": "unknown", "MSI-H": "positive"}) == {"MSI-H": "positive"}


def test_conflicting_duplicates_raise_without_echo():
    with pytest.raises(ValueError, match="conflict") as err:
        normalise_biomarkers({"DMMR": "positive", "MSS": "positive"})
    assert "positive" not in str(err.value) and "DMMR" not in str(err.value)


def test_vocabulary_description_lists_keys_and_aliases():
    text = vocabulary_description()
    for key in CANONICAL:
        assert key in text
    assert "DMMR" in text and "MSS" in text
    assert text.isascii()

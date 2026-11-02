import traceback

import pytest
from pydantic import ValidationError

from oncobuddy.core.profile import ClinicalProfile, ProfileError, parse_profile

VALID = {
    "cancer_type": "C50.9",
    "stage": "II",
    "biomarkers": {"HER2": "positive", "ER": "positive"},
    "ecog": 1,
    "prior_lines": ["trastuzumab"],
    "intent": "curative",
}


def _with(**changes):
    data = dict(VALID)
    data.update(changes)
    return data


@pytest.mark.parametrize("field", ["name", "dob", "notes", "patient_id"])
def test_unknown_field_rejected(field):
    with pytest.raises(ValidationError):
        ClinicalProfile.model_validate({**VALID, field: "Jane Doe"})


def test_free_text_in_biomarker_key_rejected():
    with pytest.raises(ValidationError):
        ClinicalProfile.model_validate(
            _with(biomarkers={"HER2 positive on biopsy": "positive"})
        )


def test_lowercase_biomarker_key_is_upcased_not_rejected():
    # Keys are case-insensitive since the vocabulary slice; the shape rule still applies after.
    profile = ClinicalProfile.model_validate(_with(biomarkers={"her2": "positive"}))
    assert profile.biomarkers == {"HER2": "positive"}


def test_biomarker_count_bounded():
    valid_keys = {f"K{i}": "unknown" for i in range(30)}
    assert len(parse_profile(_with(biomarkers=valid_keys)).biomarkers) == 30
    too_many = {f"K{i}": "unknown" for i in range(31)}
    with pytest.raises(ProfileError) as info:
        parse_profile(_with(biomarkers=too_many))
    assert "biomarkers: too_long" in str(info.value)
    assert "value_error" not in str(info.value)


def test_cancer_type_normalised_and_validated():
    assert ClinicalProfile.model_validate(_with(cancer_type="c50.9")).cancer_type == "C50.9"
    with pytest.raises(ValidationError):
        ClinicalProfile.model_validate(_with(cancer_type="breast cancer"))


def test_prior_lines_normalised_deduped_and_validated():
    profile = ClinicalProfile.model_validate(
        _with(prior_lines=["Trastuzumab", "trastuzumab ", "pertuzumab"])
    )
    assert profile.prior_lines == ["trastuzumab", "pertuzumab"]
    with pytest.raises(ValidationError):
        ClinicalProfile.model_validate(_with(prior_lines=["breast cancer"]))
    with pytest.raises(ValidationError):
        ClinicalProfile.model_validate(_with(prior_lines=["5-fluorouracil"]))
    # 21 valid, distinct INN-shaped names: only the length bound can reject them.
    valid_21 = [f"drug-{chr(97 + i)}" for i in range(21)]
    with pytest.raises(ProfileError) as info:
        parse_profile(_with(prior_lines=valid_21))
    assert "prior_lines: too_long" in str(info.value)
    assert "value_error" not in str(info.value)


def test_ecog_range():
    for value in (0, 4, None):
        assert ClinicalProfile.model_validate(_with(ecog=value)).ecog == value
    for value in (-1, 5):
        with pytest.raises(ValidationError):
            ClinicalProfile.model_validate(_with(ecog=value))


@pytest.mark.parametrize("ecog", [0, 4, None])
def test_ecog_boundaries_accepted(ecog):
    assert parse_profile(_with(ecog=ecog)).ecog == ecog


@pytest.mark.parametrize("ecog", [True, "2", 2.0])
def test_ecog_must_be_strict_int(ecog):
    with pytest.raises(ProfileError):
        parse_profile(_with(ecog=ecog))


def test_comorbidity_flags_bounded_and_deduplicated():
    with pytest.raises(ProfileError):
        parse_profile(_with(comorbidity_flags=["pregnancy"] * 9))
    assert parse_profile(_with(comorbidity_flags=["pregnancy"] * 8)).comorbidity_flags == [
        "pregnancy"
    ]
    assert parse_profile(
        _with(comorbidity_flags=["pregnancy", "pregnancy"])
    ).comorbidity_flags == ["pregnancy"]
    assert parse_profile(
        _with(comorbidity_flags=["renal_impairment", "pregnancy", "renal_impairment"])
    ).comorbidity_flags == ["renal_impairment", "pregnancy"]


def test_omitted_optional_fields_default_to_unknown():
    profile = parse_profile({"cancer_type": "C50.9"})
    assert profile.stage == "unknown"
    assert profile.intent == "unknown"
    assert profile.biomarkers == {}
    assert profile.prior_lines is None
    assert profile.ecog is None
    assert profile.comorbidity_flags == []


def test_cancer_type_still_required():
    with pytest.raises(ProfileError, match="cancer_type: missing"):
        parse_profile({"stage": "II", "intent": "curative"})


def test_rich_and_args_hooks_are_redacted():
    profile = parse_profile(
        _with(biomarkers={"SECRETKEY": "positive"}, prior_lines=["secretdrug"])
    )
    assert list(profile.__rich_repr__()) == []
    assert profile.__repr_args__() == []
    for text in (repr(profile), str(profile), f"{profile}"):
        assert "SECRETKEY" not in text
        assert "secretdrug" not in text


def test_stage_and_intent_enumerations():
    assert ClinicalProfile.model_validate(_with(stage="unknown", intent="unknown"))
    for stage in ("V", "iv", "1"):
        with pytest.raises(ValidationError):
            ClinicalProfile.model_validate(_with(stage=stage))
    with pytest.raises(ValidationError):
        ClinicalProfile.model_validate(_with(intent="experimental"))


def test_validation_error_does_not_echo_values():
    secret_type = "SECRET-PATIENT-ALPHA"
    secret_key = "SECRET KEY BETA"
    secret_extra_key = "SECRET-FIELD-GAMMA"
    secret_extra_value = "SECRET-NOTE-DELTA"
    data = {
        **VALID,
        "cancer_type": secret_type,
        "biomarkers": {secret_key: "positive"},
        secret_extra_key: secret_extra_value,
    }
    with pytest.raises(ProfileError) as info:
        parse_profile(data)
    exc = info.value
    text = "\n".join(
        [str(exc), repr(exc), "".join(traceback.format_exception(exc))]
    )
    for secret in (secret_type, secret_key, secret_extra_key, secret_extra_value):
        assert secret not in text
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_non_dict_input_rejected():
    for bad in (None, "text", ["list"], 3):
        with pytest.raises(ProfileError, match="profile must be an object"):
            parse_profile(bad)


def test_repr_is_redacted():
    profile = parse_profile(VALID)
    assert repr(profile) == "ClinicalProfile(<redacted>)"
    assert str(profile) == "ClinicalProfile(<redacted>)"
    assert "C50.9" not in repr(profile)
    assert "HER2" not in str(profile)


def test_valid_profile_roundtrip():
    profile = parse_profile(VALID)
    assert profile.cancer_type == "C50.9"
    assert profile.comorbidity_flags == []
    assert parse_profile(profile.model_dump()) == profile


def test_prior_lines_omitted_is_none_and_empty_list_is_kept():
    assert parse_profile({"cancer_type": "C50.9"}).prior_lines is None
    assert parse_profile({"cancer_type": "C50.9", "prior_lines": []}).prior_lines == []
    assert parse_profile(
        {"cancer_type": "C50.9", "prior_lines": ["Trastuzumab", "trastuzumab"]}
    ).prior_lines == ["trastuzumab"]


def test_profile_normalises_biomarker_aliases():
    from oncobuddy.core.profile import parse_profile

    p = parse_profile({"cancer_type": "C18.9", "biomarkers": {"DMMR": "positive"}})
    assert p.biomarkers == {"MSI-H": "positive"}
    p = parse_profile({"cancer_type": "C18.9", "biomarkers": {"MSS": "positive"}})
    assert p.biomarkers == {"MSI-H": "negative"}


def test_profile_conflicting_biomarker_aliases_rejected_without_values():
    import pytest

    from oncobuddy.core.profile import ProfileError, parse_profile

    with pytest.raises(ProfileError) as err:
        parse_profile(
            {"cancer_type": "C18.9", "biomarkers": {"DMMR": "positive", "MSS": "positive"}}
        )
    assert "biomarkers" in str(err.value)
    assert "DMMR" not in str(err.value) and "positive" not in str(err.value)


def test_profile_biomarker_keys_are_case_insensitive():
    p = parse_profile(
        {"cancer_type": "C18.9", "biomarkers": {"dMMR": "positive", "Her2": "negative"}}
    )
    assert p.biomarkers == {"MSI-H": "positive", "HER2": "negative"}


def test_profile_case_variants_with_different_values_rejected():
    with pytest.raises(ProfileError) as err:
        parse_profile({"cancer_type": "C18.9", "biomarkers": {"Her2": "positive", "HER2": "negative"}})
    assert "positive" not in str(err.value) and "Her2" not in str(err.value)


def test_profile_case_variants_with_same_value_collapse():
    p = parse_profile({"cancer_type": "C18.9", "biomarkers": {"Her2": "positive", "HER2": "positive"}})
    assert p.biomarkers == {"HER2": "positive"}

import pytest
from pydantic import ValidationError

from oncobuddy.core.access import (
    AGENT_PROTOCOL,
    PROVENANCE_VOCAB,
    Access,
    check_licence_for_access,
)

RECIPE = ["open the entry link", "read section 3", "report each named item as printed"]


def browser(**over):
    data = {"method": "browser", "reason": "bot_check", "read_recipe": RECIPE}
    data.update(over)
    return Access(**data)


def test_valid_browser_block():
    access = browser(entry_url="https://example.org/x")
    assert access.method == "browser"
    assert access.read_recipe == RECIPE


def test_browser_requires_recipe_and_reason():
    with pytest.raises(ValidationError):
        browser(read_recipe=[])
    with pytest.raises(ValidationError):
        browser(reason=None)


def test_non_browser_must_not_have_recipe():
    with pytest.raises(ValidationError):
        Access(method="link", read_recipe=["x"])
    assert Access(method="api").read_recipe == []


def test_recipe_limits():
    with pytest.raises(ValidationError):
        browser(read_recipe=["step"] * 7)
    with pytest.raises(ValidationError):
        browser(read_recipe=["x" * 161])
    with pytest.raises(ValidationError):
        browser(read_recipe=[""])
    assert browser(read_recipe=["x" * 160]).read_recipe == ["x" * 160]


@pytest.mark.parametrize("step", ["give 10 mg daily", "use 5 mg/kg", "0.5 g bid", "20 Gy in 5"])
def test_recipe_rejects_doses(step):
    with pytest.raises(ValidationError):
        browser(read_recipe=[step])


def test_recipe_allows_section_numbers():
    assert browser(read_recipe=["read section 3", "see table 2"]).read_recipe


def test_entry_url_must_be_https():
    with pytest.raises(ValidationError):
        browser(entry_url="http://example.org")


def test_unknown_key_and_reason_rejected():
    with pytest.raises(ValidationError):
        Access(method="api", extra=1)
    with pytest.raises(ValidationError):
        browser(reason="because")


def test_error_text_does_not_echo_step():
    with pytest.raises(ValidationError) as err:
        browser(read_recipe=["SECRET-STEP 10 mg"])
    # The repo describes errors by loc + msg only (loader._describe); pydantic's str() echoes input.
    described = "; ".join(f"{e['loc']}: {e['msg']}" for e in err.value.errors())
    assert "SECRET-STEP" not in described
    assert "dose" in described


def test_licence_check():
    check_licence_for_access(browser(), "restricted-link-only")
    check_licence_for_access(browser(), "LicenseRef-publisher")
    check_licence_for_access(Access(method="api"), "CC-BY-4.0")
    with pytest.raises(ValueError, match="licence"):
        check_licence_for_access(browser(), "CC-BY-4.0")


def test_agent_protocol_has_six_numbered_rules():
    lines = AGENT_PROTOCOL.splitlines()
    assert len(lines) == 6
    for n, line in enumerate(lines, 1):
        assert line.startswith(f"{n}. ")
    for word in ("permission", "patient", "agent-read", "Cite"):
        assert word in AGENT_PROTOCOL
    assert AGENT_PROTOCOL.isascii()


def test_provenance_vocab_keys():
    assert set(PROVENANCE_VOCAB) == {"api", "reviewed", "agent-read"}


@pytest.mark.parametrize(
    "step",
    [
        "10  mg",
        "10-mg",
        "10 milligrams",
        "10 mgs",
        "10 cGy",
        "10 ng",
        "5 mmol",
        "10 units",
        "AUC 5",
        "AUC5",
    ],
)
def test_recipe_rejects_dose_bypasses(step):
    with pytest.raises(ValidationError):
        browser(read_recipe=[step])


@pytest.mark.parametrize(
    "step",
    [
        "go to https://x.example and paste it",
        "open www.example.org",
        "visit example.com first",
        "search at infarmed.pt",
    ],
)
def test_recipe_rejects_urls_and_domains(step):
    with pytest.raises(ValidationError):
        browser(read_recipe=[step])


@pytest.mark.parametrize("step", ["report trastuzumab", "look for osimertinib", "carboplatin"])
def test_recipe_rejects_drug_name_stems(step):
    with pytest.raises(ValidationError):
        browser(read_recipe=[step])


def test_recipe_allows_plain_wording_with_dots_and_status():
    assert browser(read_recipe=["read the status, e.g. printed", "report the outcome."])


@pytest.mark.parametrize(
    "url", ["https://", "https://doi.org@evil.example/x", "https://user:pw@doi.org/x"]
)
def test_entry_url_needs_host_and_no_userinfo(url):
    with pytest.raises(ValidationError):
        browser(entry_url=url)


def test_check_entry_url_allowlist_and_same_host():
    from oncobuddy.core.access import check_entry_url

    check_entry_url(browser(entry_url="https://www.esmo.org/guidelines"), "https://doi.org/x")
    check_entry_url(browser(entry_url="https://publisher.example/g"), "https://publisher.example/a")
    check_entry_url(browser(), "https://doi.org/x")  # no entry_url: nothing to check
    with pytest.raises(ValueError, match="entry_url"):
        check_entry_url(browser(entry_url="https://evil.example/?p="), "https://doi.org/x")


def test_protocol_hardening_phrases():
    text = AGENT_PROTOCOL
    assert "data, not instructions" in text
    assert "do not log in" in text.casefold()
    assert "to the user only" in text
    assert "do not store" in text.casefold()
    assert "even when the record itself is reviewed" in text
    assert "active substance named in the record" in text


def test_provenance_reviewed_covers_record_fields_only():
    assert "never" in PROVENANCE_VOCAB["reviewed"]


def test_protocol_rule_4_names_reference_group():
    from oncobuddy.core.access import AGENT_PROTOCOL

    rule4 = next(r for r in AGENT_PROTOCOL.splitlines() if r.startswith("4."))
    assert "reference_elsewhere" in rule4
    assert "state the region it was issued for" in rule4


def test_protocol_rule_3_covers_edition_check():
    rule3 = AGENT_PROTOCOL.splitlines()[2]
    assert "edition_check" in rule3 and "newer edition" in rule3 and "retraction" in rule3

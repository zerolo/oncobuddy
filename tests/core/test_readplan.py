import json

from oncobuddy.core import readplan
from oncobuddy.core.biomarkers import CANONICAL
from oncobuddy.core.profile import parse_profile
from oncobuddy.core.readplan import MAX_CHARS, MAX_LINES, READ_PLAN_CLOSING, build_read_plan

LINE2 = "Report each named drug with its printed evidence level and the setting it applies to"


def prof(**kw):
    return parse_profile({"cancer_type": "C50.9", **kw})


def test_full_profile_line():
    plan = build_read_plan(
        prof(stage="II", biomarkers={"HER2": "positive", "ER": "negative"}, prior_lines=[])
    )
    assert plan == [
        "Find the recommendations for stage II, ER negative, HER2 positive, first-line treatment",
        LINE2,
        READ_PLAN_CLOSING,
    ]


def test_later_line_and_intent():
    plan = build_read_plan(prof(prior_lines=["trastuzumab"], intent="palliative"))
    assert plan[0] == (
        "Find the recommendations for later-line treatment, palliative intent"
    )
    assert "curative intent" in build_read_plan(prof(intent="curative"))[0]


def test_unknowns_omitted():
    plan = build_read_plan(prof(biomarkers={"HER2": "unknown"}))
    assert plan[0] == "Find the recommendations for the condition in the entry"


def test_empty_profile_fallback():
    assert build_read_plan(prof())[0] == "Find the recommendations for the condition in the entry"


def test_unknown_values_never_appear():
    p = prof(biomarkers={"ZZQXKEY9": "positive", "HER2": "positive"}, prior_lines=["zzqxdrug"])
    plan = build_read_plan(p)
    for text in (*plan, repr(plan), json.dumps(plan)):
        assert "zzqxkey9" not in text.lower() and "zzqxdrug" not in text.lower()
        assert "C50" not in text
    assert "HER2 positive" in plan[0]


def test_closing_is_last_and_bounds():
    p = prof(
        stage="IV",
        biomarkers={k: "positive" for k in CANONICAL},
        prior_lines=["trastuzumab"],
        intent="curative",
    )
    plan = build_read_plan(p)
    assert plan[-1] == READ_PLAN_CLOSING
    assert len(plan) <= MAX_LINES
    assert all(len(line) <= MAX_CHARS for line in plan)


def test_descriptor_cut_drops_trailing_biomarkers(monkeypatch):
    monkeypatch.setattr(readplan, "MAX_CHARS", 90)
    p = prof(
        stage="IV",
        biomarkers={k: "positive" for k in CANONICAL},
        prior_lines=[],
    )
    line = build_read_plan(p)[0]
    assert len(line) <= 90
    assert line.startswith("Find the recommendations for stage IV, ALK positive")
    assert "NTRK" not in line

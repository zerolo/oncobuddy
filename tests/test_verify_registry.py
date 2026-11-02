import importlib.util
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_registry.py"


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("verify_registry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(httpx, "get", no_network)
    return module


def test_doi_of(mod):
    assert mod.doi_of("https://doi.org/10.1016/j.annonc.2022.12.009") == "10.1016/j.annonc.2022.12.009"
    assert mod.doi_of("https://example.org/10.1016/x") is None
    assert mod.doi_of("https://doi.org/") is None
    assert mod.doi_of("not a url") is None


def _good(doi):
    return {
        "title": ["Some Guideline: ESMO"],
        "type": "journal-article",
        "container-title": ["Annals of Oncology"],
        "page": "129-142",
    }


def _records(mod, monkeypatch, urls, source="ESMO"):
    class R:
        def __init__(self, i, u):
            self.id, self.source_url, self.payload = i, u, {"title": "Some Guideline: ESMO"}
            self.source = source

    monkeypatch.setattr(mod, "load_guidelines", lambda d=None: [R(f"rec-{i}", u) for i, u in enumerate(urls)])


def test_verify_flags_missing_doi(mod, monkeypatch):
    _records(mod, monkeypatch, ["https://doi.org/10.1/a", "https://doi.org/10.1/b"])
    problems = mod.verify(Path("."), lambda doi: 404 if doi.endswith("b") else 200, _good, lambda s: None)
    assert problems == ["rec-1: DOI not found (status 404)"]


def test_verify_flags_non_doi_url(mod, monkeypatch):
    _records(mod, monkeypatch, ["https://example.org/x"])
    assert mod.verify(Path("."), lambda doi: 200, _good, lambda s: None) == ["rec-0: url is not a doi.org link"]


def test_main_exit_codes(mod, monkeypatch, capsys):
    _records(mod, monkeypatch, ["https://doi.org/10.1/a"])
    monkeypatch.setattr(mod, "_lookup", lambda doi: 200)
    monkeypatch.setattr(mod, "_crossref", lambda doi: _good(doi))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    assert mod.main([]) == 0
    assert "ok: 1 entries" in capsys.readouterr().out
    monkeypatch.setattr(mod, "_lookup", lambda doi: 404)
    assert mod.main([]) == 1
    assert "DOI not found (status 404)" in capsys.readouterr().out
    monkeypatch.setattr(mod, "_lookup", lambda doi: 200)
    monkeypatch.setattr(mod, "_crossref", lambda doi: {**_good(doi), "page": "561"})
    assert mod.main([]) == 1
    assert "too few pages (1)" in capsys.readouterr().out


def _run(mod, monkeypatch, message, sleeps=None):
    _records(mod, monkeypatch, ["https://doi.org/10.1/a"])
    sl = [] if sleeps is None else sleeps
    return mod.verify(Path("."), lambda doi: 200, lambda doi: message, sl.append)


def test_matching_crossref_record_passes(mod, monkeypatch):
    assert _run(mod, monkeypatch, _good("x")) == []


def test_title_normalisation_ignores_marks_case_and_spacing(mod, monkeypatch):
    msg = {**_good("x"), "title": ["  some   GUIDELINE:\tESMO \u2606 "]}
    assert _run(mod, monkeypatch, msg) == []
    msg = {**_good("x"), "title": ["Some Guideline: ESMO*\u2020"]}
    assert _run(mod, monkeypatch, msg) == []


def test_title_difference_reported(mod, monkeypatch):
    msg = {**_good("x"), "title": ["Another thing"]}
    assert _run(mod, monkeypatch, msg) == ["rec-0: Crossref title differs"]


def test_not_a_journal_article(mod, monkeypatch):
    msg = {**_good("x"), "type": "posted-content"}
    assert _run(mod, monkeypatch, msg) == ["rec-0: not a journal article"]


def test_unexpected_journal(mod, monkeypatch):
    msg = {**_good("x"), "container-title": ["Some Other Journal"]}
    assert _run(mod, monkeypatch, msg) == ["rec-0: unexpected journal"]
    assert _run(mod, monkeypatch, {**_good("x"), "container-title": ["ESMO Open"]}) == []


def test_page_span_rules(mod, monkeypatch):
    assert _run(mod, monkeypatch, {**_good("x"), "page": "561"}) == ["rec-0: too few pages (1)"]
    assert _run(mod, monkeypatch, {**_good("x"), "page": "10-11"}) == ["rec-0: too few pages (2)"]
    assert _run(mod, monkeypatch, {**_good("x"), "page": "10-12"}) == []
    msg = {**_good("x"), "volume": "34"}
    del msg["page"]
    assert _run(mod, monkeypatch, msg) == ["rec-0: no page range"]


def test_identical_title_correspondence_rejected(mod, monkeypatch):
    msg = {**_good("x"), "type": "journal-article", "page": "561"}
    assert _run(mod, monkeypatch, msg) == ["rec-0: too few pages (1)"]


def test_sleep_between_crossref_calls(mod, monkeypatch):
    _records(mod, monkeypatch, ["https://doi.org/10.1/a", "https://doi.org/10.1/b"])
    sleeps = []
    mod.verify(Path("."), lambda doi: 200, _good, sleeps.append)
    assert sleeps == [1]


def test_default_crossref_uses_header_and_no_email(mod, monkeypatch):
    seen = {}

    def fake_get(url, **k):
        seen["url"], seen["headers"] = url, k.get("headers")
        return httpx.Response(200, json={"message": {"title": ["t"]}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert mod._crossref("10.1/a") == {"title": ["t"]}
    assert seen["url"] == "https://api.crossref.org/works/10.1/a"
    assert seen["headers"] == {"User-Agent": "oncoBuddy-registry/0.1"}


def test_article_number_and_ahead_of_print_are_not_page_problems(mod, monkeypatch):
    open_msg = {**_good("x"), "container-title": ["ESMO Open"], "page": "108364", "article-number": "108364"}
    assert _run(mod, monkeypatch, open_msg) == []
    ahead = _good("x")
    del ahead["page"]
    assert _run(mod, monkeypatch, ahead) == []
    # a bare single page in a numbered issue (correspondence) is still rejected
    assert _run(mod, monkeypatch, {**_good("x"), "page": "561", "article-number": "9", "volume": "34"}) == [
        "rec-0: too few pages (1)"
    ]


def _run_source(mod, monkeypatch, source, journal):
    _records(mod, monkeypatch, ["https://doi.org/10.1/a"], source=source)
    msg = {**_good("x"), "container-title": [journal]}
    return mod.verify(Path("."), lambda doi: 200, lambda doi: msg, lambda s: None)


def test_journal_set_depends_on_source(mod, monkeypatch):
    assert _run_source(mod, monkeypatch, "ASCO", "Journal of Clinical Oncology") == []
    assert _run_source(mod, monkeypatch, "ASCO", "Annals of Oncology") == ["rec-0: unexpected journal"]
    assert _run_source(mod, monkeypatch, "ESMO", "Journal of Clinical Oncology") == ["rec-0: unexpected journal"]


def test_non_doi_source_on_allowed_host_is_not_checkable(mod, monkeypatch):
    _records(mod, monkeypatch, ["https://www.nice.org.uk/guidance/ng1"], source="NICE")
    assert mod.verify(Path("."), lambda doi: 1 / 0, lambda doi: 1 / 0, lambda s: None) == []
    assert mod.unchecked_count(Path(".")) == 1


def test_non_doi_source_on_unknown_host_is_a_problem(mod, monkeypatch):
    _records(mod, monkeypatch, ["https://evil.example/x"], source="NICE")
    assert mod.verify(Path("."), lambda doi: 200, _good, lambda s: None) == ["rec-0: url host is not allowed"]
    _records(mod, monkeypatch, ["http://www.nice.org.uk/x"], source="NICE")
    assert mod.verify(Path("."), lambda doi: 200, _good, lambda s: None) == ["rec-0: url host is not allowed"]


def test_main_final_line_counts_unchecked(mod, monkeypatch, capsys):
    _records(mod, monkeypatch, ["https://www.nice.org.uk/guidance/ng1"], source="NICE")
    assert mod.main([]) == 0
    assert "ok: 1 entries (1 not machine-checkable)" in capsys.readouterr().out


def test_dois_alias_runs_main(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    sys.modules.pop("verify_registry", None)
    spec = importlib.util.spec_from_file_location("verify_dois", SCRIPT.parent / "verify_dois.py")
    alias = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(alias)
    import verify_registry

    monkeypatch.setattr(verify_registry, "main", lambda argv=None: 7)
    assert alias.main([]) == 7

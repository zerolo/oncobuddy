import asyncio
import copy
import json
import traceback
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.connectors.pubmed import ESEARCH_URL, ESUMMARY_URL
from oncobuddy.connectors.pubmed import PubMedConnector as _RealPubMedConnector
from oncobuddy.core.cache import DEFAULT_TTLS, Cache
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.regions import RegionTree

FIX = Path(__file__).parent.parent / "fixtures"
SECRET = "SECRETKEY123"
TODAY = date(2026, 11, 1)
BASE_TERM = '"pembrolizumab"[Title/Abstract] AND neoplasms[MeSH Terms]'


def _search() -> dict:
    return json.loads((FIX / "pubmed_esearch.json").read_text(encoding="utf-8"))


def _summary() -> dict:
    return json.loads((FIX / "pubmed_esummary.json").read_text(encoding="utf-8"))


def _q(drug: str | None, condition: str | None = None) -> Query:
    return Query(drug=drug, condition=condition, region="EU")


async def _no_sleep(_seconds: float) -> None:
    """Tests that do not assert on rate limiting must not wait for it."""


class PubMedConnector(_RealPubMedConnector):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("sleep", _no_sleep)
        super().__init__(*args, **kwargs)


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


class _Time:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def _mock_ok():
    es = respx.get(ESEARCH_URL).respond(200, json=_search())
    su = respx.get(ESUMMARY_URL).respond(200, json=_summary())
    return es, su


@respx.mock
async def test_builds_query_from_inn(client):
    es, su = _mock_ok()
    await PubMedConnector(client, email="me@example.org", max_results=3).fetch(
        _q("pembrolizumab")
    )
    p = es.calls.last.request.url.params
    assert p["db"] == "pubmed"
    assert p["term"] == BASE_TERM
    assert p["tool"] == "oncobuddy-mcp"
    assert p["email"] == "me@example.org"
    assert p["retmode"] == "json"
    assert p["retmax"] == "3"
    assert p["sort"] == "pub_date"
    assert "api_key" not in p
    sp = su.calls.last.request.url.params
    assert sp["db"] == "pubmed"
    assert sp["id"] == "42126144,41732954,42885310"
    assert sp["retmode"] == "json"
    assert sp["tool"] == "oncobuddy-mcp"


@respx.mock
async def test_condition_terms_resolver_adds_validated_label(client):
    es, _ = _mock_ok()
    c = PubMedConnector(client, condition_terms=lambda cond: "non-small cell lung cancer")
    await c.fetch(_q("pembrolizumab", "nsclc"))
    assert es.calls.last.request.url.params["term"] == (
        BASE_TERM + ' AND "non-small cell lung cancer"[Title/Abstract]'
    )


@respx.mock
@pytest.mark.parametrize("label", ['x" OR 1', "a[b]", "", "  ", "lung (cancer)", "a\nb", "é"])
async def test_unsafe_label_is_ignored(client, label):
    es, _ = _mock_ok()
    await PubMedConnector(client, condition_terms=lambda cond: label).fetch(
        _q("pembrolizumab", "nsclc")
    )
    assert es.calls.last.request.url.params["term"] == BASE_TERM


@respx.mock
async def test_resolver_not_called_without_condition_and_none_ignored(client):
    es, _ = _mock_ok()
    called = []

    def resolver(cond: str) -> str | None:
        called.append(cond)
        return None

    c = PubMedConnector(client, condition_terms=resolver)
    await c.fetch(_q("pembrolizumab", None))
    assert called == []
    await c.fetch(_q("pembrolizumab", "nsclc"))
    assert called == ["nsclc"]
    assert es.calls.last.request.url.params["term"] == BASE_TERM


@respx.mock
@pytest.mark.parametrize(
    "drug", ["Pembrolizumab", 'x" OR "1', "a", "pembro lizumab", "1abc", "x]", "a" * 41, ""]
)
async def test_non_inn_drug_raises_value_error(client, drug):
    es, _ = _mock_ok()
    with pytest.raises(ValueError):
        await PubMedConnector(client).fetch(_q(drug))
    assert es.call_count == 0


async def test_none_drug_returns_empty(client):
    assert await PubMedConnector(client).fetch(_q(None)) == []


@respx.mock
async def test_record_fields(client):
    _mock_ok()
    recs = await PubMedConnector(client, today=lambda: TODAY).fetch(_q("pembrolizumab"))
    assert [r.id for r in recs] == ["pubmed:42126144", "pubmed:41732954", "pubmed:42885310"]
    r = recs[0]
    assert r.kind == "publication"
    assert r.region == "global"
    assert r.source == "PubMed"
    assert r.source_url == "https://pubmed.ncbi.nlm.nih.gov/42126144/"
    assert r.valid_as_of == date(2026, 5, 13)  # earliest non-future; Dec 31 issue date is future
    assert r.licence == "NLM-PubMed-metadata"
    assert r.entities.drugs == ["pembrolizumab"]
    assert r.payload == {
        "pmid": "42126144",
        "title": _summary()["result"]["42126144"]["title"],
        "journal": "Oncoimmunology",
        "pubdate": "2026 Dec 31",
        "epubdate": "2026 May 13",
        "pubtypes": ["Journal Article", "Clinical Trial, Phase I", "Clinical Trial, Phase II"],
        "doi": "10.1080/2162402X.2026.2661444",
        "search": {
            "sort": "pub_date_desc",
            "returned": 3,
            "total": 8704,
            "filter": "neoplasms[MeSH Terms]",
            "condition_label": None,
        },
    }
    for rec in recs:
        assert "abstract" not in json.dumps(rec.payload).lower()


@respx.mock
async def test_month_only_pubdate_uses_full_epubdate_else_unknown(client):
    _mock_ok()
    recs = await PubMedConnector(client, today=lambda: TODAY).fetch(_q("pembrolizumab"))
    assert recs[2].payload["pubdate"] == "2026 Dec"
    assert recs[2].valid_as_of == date(2026, 10, 16)  # from epubdate, not sortpubdate 12/01
    body = _summary()
    del body["result"]["42885310"]["epubdate"]
    respx.get(ESUMMARY_URL).respond(200, json=body)
    recs = await PubMedConnector(client, today=lambda: TODAY).fetch(_q("pembrolizumab"))
    assert recs[2].valid_as_of == "unknown"
    assert "epubdate" not in recs[2].payload


@respx.mock
@pytest.mark.parametrize(
    "sort_date, pubdate",
    [
        ("", "2026 Dec 31"),
        ("garbage", "2026 Dec 31"),
        ("2026/13/45 00:00", "2026 Dec 31"),
        ("2026", "2026"),
        ("2026/01/01 00:00", "2026"),
        ("2026/01/01 00:00", "2026 Winter"),
    ],
)
async def test_unparseable_or_imprecise_date_is_unknown(client, sort_date, pubdate):
    body = _summary()
    body["result"]["42126144"]["sortpubdate"] = sort_date
    body["result"]["42126144"]["pubdate"] = pubdate
    del body["result"]["42126144"]["epubdate"]
    respx.get(ESEARCH_URL).respond(200, json=_search())
    respx.get(ESUMMARY_URL).respond(200, json=body)
    recs = await PubMedConnector(client).fetch(_q("pembrolizumab"))
    assert recs[0].valid_as_of == "unknown"


def _dated(pubdate, sortpubdate, epubdate):
    body = _summary()
    row = body["result"]["42126144"]
    row["pubdate"], row["sortpubdate"] = pubdate, sortpubdate
    if epubdate is None:
        row.pop("epubdate")
    else:
        row["epubdate"] = epubdate
    return body


@respx.mock
@pytest.mark.parametrize(
    "pubdate, sortpub, epub, expected",
    [
        ("2026 Dec 31", "2026/12/31 00:00", "2026 May 13", date(2026, 5, 13)),
        ("2026 Dec 31", "2026/12/31 00:00", None, "unknown"),  # only future
        ("2026 Dec 31", "2026/12/31 00:00", "2027 Jan 2", "unknown"),
        ("2026 Dec", "2026/12/01 00:00", None, "unknown"),
        ("2024 Mar 5", "2024/03/05 00:00", None, date(2024, 3, 5)),
        ("2024 Mar 5", "2024/03/05 00:00", "2024 Feb 20", date(2024, 2, 20)),
        ("2024 Mar 5", "2024/03/09 00:00", None, date(2024, 3, 9)),  # both parse -> accepted
        ("2024 Mar 5", "garbage", None, "unknown"),
        ("2024 Mar 5", "2024/03/05 00:00", "2024 Feb", date(2024, 3, 5)),  # epub month-only
        ("2024 Feb 31", "2024/02/31 00:00", None, "unknown"),
        ("2024 Mar 5", "2024/03/05 00:00", "junk", date(2024, 3, 5)),
        ("2024 Mar 5-9", "2024/03/05 00:00", None, date(2024, 3, 5)),
    ],
)
async def test_valid_as_of_is_earliest_non_future_candidate(
    client, pubdate, sortpub, epub, expected
):
    respx.get(ESEARCH_URL).respond(200, json=_search())
    respx.get(ESUMMARY_URL).respond(200, json=_dated(pubdate, sortpub, epub))
    recs = await PubMedConnector(client, today=lambda: TODAY).fetch(_q("pembrolizumab"))
    assert recs[0].valid_as_of == expected
    assert recs[0].payload["pubdate"] == pubdate
    if epub is not None:
        assert recs[0].payload["epubdate"] == epub


@respx.mock
async def test_search_marker_with_label_and_total(client):
    _mock_ok()
    c = PubMedConnector(client, condition_terms=lambda cond: " lung cancer ")
    recs = await c.fetch(_q("pembrolizumab", "C34.9"))
    for r in recs:
        s = r.payload["search"]
        assert s == {
            "sort": "pub_date_desc",
            "returned": 3,
            "total": int(_search()["esearchresult"]["count"]),
            "filter": "neoplasms[MeSH Terms]",
            "condition_label": "lung cancer",
        }


@respx.mock
@pytest.mark.parametrize("count", [None, "abc", "", 5, "-3"])
async def test_total_is_none_when_count_missing_or_unparseable(client, count):
    body = _search()
    if count is None:
        del body["esearchresult"]["count"]
    else:
        body["esearchresult"]["count"] = count
    respx.get(ESEARCH_URL).respond(200, json=body)
    respx.get(ESUMMARY_URL).respond(200, json=_summary())
    recs = await PubMedConnector(client).fetch(_q("pembrolizumab"))
    expected = 5 if count == 5 else None
    assert recs[0].payload["search"]["total"] == expected


@respx.mock
async def test_warnings_from_warninglist_and_errorlist(client):
    body = _search()
    body["esearchresult"]["warninglist"] = {
        "quotedphrasesnotfound": ["zzzdrug"],
        "phrasesnotfound": ["x" * 200],
    }
    body["esearchresult"]["errorlist"] = {
        "phrasesnotfound": ["a", "b", "c", "caf\u00e9"],
        "fieldsnotfound": ["ignored"],
    }
    respx.get(ESEARCH_URL).respond(200, json=body)
    respx.get(ESUMMARY_URL).respond(200, json=_summary())
    recs = await PubMedConnector(client).fetch(_q("pembrolizumab"))
    w = recs[0].payload["search"]["warnings"]
    assert 1 <= len(w) <= 5
    assert all(isinstance(x, str) and x.isascii() and len(x) <= 80 for x in w)
    assert any("zzzdrug" in x for x in w)
    assert recs[0].payload["search"]["returned"] == 3
    # absent when no warnings
    respx.get(ESEARCH_URL).respond(200, json=_search())
    recs = await PubMedConnector(client).fetch(_q("pembrolizumab"))
    assert "warnings" not in recs[0].payload["search"]


@respx.mock
async def test_trailing_newline_drug_raises_value_error(client):
    _mock_ok()
    with pytest.raises(ValueError):
        await PubMedConnector(client).fetch(_q("abc\n"))


@respx.mock
@pytest.mark.parametrize("kind", ["http", "network", "json_s", "json_e"])
async def test_no_cause_or_secret_leak(client, kind):
    url = ESEARCH_URL + "?api_key=" + SECRET
    if kind == "http":
        respx.get(ESEARCH_URL).respond(500, text=SECRET)
    elif kind == "network":
        req = httpx.Request("GET", url)
        respx.get(ESEARCH_URL).mock(side_effect=httpx.ConnectError(url, request=req))
    elif kind == "json_e":
        respx.get(ESEARCH_URL).respond(200, text=SECRET)
    else:
        respx.get(ESEARCH_URL).respond(200, json=_search())
        respx.get(ESUMMARY_URL).respond(200, text=SECRET)
    with pytest.raises(SourceUnavailable) as ei:
        await PubMedConnector(client, api_key=SECRET).fetch(_q("pembrolizumab"))
    exc = ei.value
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    assert SECRET not in str(exc)
    assert SECRET not in repr(exc)
    assert SECRET not in "".join(traceback.format_exception(exc))


@respx.mock
async def test_duplicate_ids_give_one_record_in_order(client):
    body = _search()
    body["esearchresult"]["idlist"] = ["42126144", "41732954", "42126144"]
    es = respx.get(ESEARCH_URL).respond(200, json=body)
    su = respx.get(ESUMMARY_URL).respond(200, json=_summary())
    recs = await PubMedConnector(client).fetch(_q("pembrolizumab"))
    assert [r.id for r in recs] == ["pubmed:42126144", "pubmed:41732954"]
    assert su.calls.last.request.url.params["id"] == "42126144,41732954"
    assert recs[0].payload["search"]["returned"] == 2
    assert es.call_count == 1


@pytest.mark.parametrize("bad", [0, -1, 101, True, 1.5, "10", None])
def test_max_results_validated(bad):
    with pytest.raises(ValueError):
        PubMedConnector(None, max_results=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("ok", [1, 10, 100])
def test_max_results_bounds_accepted(ok):
    PubMedConnector(None, max_results=ok)  # type: ignore[arg-type]


@respx.mock
async def test_missing_sortpubdate_and_doi_are_tolerated(client):
    body = _summary()
    del body["result"]["42126144"]["sortpubdate"]
    del body["result"]["42126144"]["epubdate"]
    body["result"]["42126144"]["articleids"] = [
        {"idtype": "pubmed", "idtypen": 1, "value": "42126144"}
    ]
    respx.get(ESEARCH_URL).respond(200, json=_search())
    respx.get(ESUMMARY_URL).respond(200, json=body)
    r = (await PubMedConnector(client).fetch(_q("pembrolizumab")))[0]
    assert r.valid_as_of == "unknown"
    assert "doi" not in r.payload


@respx.mock
async def test_empty_idlist_returns_empty_list(client):
    body = _search()
    body["esearchresult"]["idlist"] = []
    body["esearchresult"]["count"] = "0"
    respx.get(ESEARCH_URL).respond(200, json=body)
    su = respx.get(ESUMMARY_URL).respond(200, json=_summary())
    assert await PubMedConnector(client).fetch(_q("pembrolizumab")) == []
    assert su.call_count == 0


@respx.mock
async def test_esearch_error_key_raises_source_unavailable(client):
    body = _search()
    body["esearchresult"]["ERROR"] = "Invalid query"
    respx.get(ESEARCH_URL).respond(200, json=body)
    with pytest.raises(SourceUnavailable) as exc:
        await PubMedConnector(client).fetch(_q("pembrolizumab"))
    assert "Invalid query" not in str(exc.value)


@respx.mock
@pytest.mark.parametrize("stage", ["esearch", "esummary"])
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_http_errors_raise_source_unavailable(client, stage, status):
    if stage == "esearch":
        respx.get(ESEARCH_URL).respond(status, text="secret body")
    else:
        respx.get(ESEARCH_URL).respond(200, json=_search())
        respx.get(ESUMMARY_URL).respond(status, text="secret body")
    with pytest.raises(SourceUnavailable) as exc:
        await PubMedConnector(client, api_key=SECRET).fetch(_q("pembrolizumab"))
    msg = str(exc.value)
    assert "secret body" not in msg
    assert SECRET not in msg


@respx.mock
@pytest.mark.parametrize("stage", ["esearch", "esummary"])
async def test_malformed_json_raises_source_unavailable(client, stage):
    if stage == "esearch":
        respx.get(ESEARCH_URL).respond(200, text="<html>nope")
    else:
        respx.get(ESEARCH_URL).respond(200, json=_search())
        respx.get(ESUMMARY_URL).respond(200, text="<html>nope")
    with pytest.raises(SourceUnavailable) as exc:
        await PubMedConnector(client, api_key=SECRET).fetch(_q("pembrolizumab"))
    assert SECRET not in str(exc.value)


@respx.mock
@pytest.mark.parametrize("exc_type", [httpx.ConnectTimeout, httpx.ConnectError])
async def test_network_errors_raise_source_unavailable(client, exc_type):
    respx.get(ESEARCH_URL).mock(side_effect=exc_type("boom"))
    with pytest.raises(SourceUnavailable) as exc:
        await PubMedConnector(client, api_key=SECRET).fetch(_q("pembrolizumab"))
    assert SECRET not in str(exc.value)


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"error": "API rate limit exceeded"},
        {"esearchresult": []},
        {"esearchresult": {}},
        {"esearchresult": {"idlist": "x"}},
        {"esearchresult": {"idlist": [1]}},
        {"esearchresult": {"idlist": ["abc"]}},
    ],
)
async def test_wrong_esearch_shape_raises_source_unavailable(client, body):
    respx.get(ESEARCH_URL).respond(200, json=body)
    with pytest.raises(SourceUnavailable):
        await PubMedConnector(client).fetch(_q("pembrolizumab"))


@respx.mock
@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["result"]["42126144"].pop("title"),
        lambda r: r["result"]["42126144"].update(title=""),
        lambda r: r["result"]["42126144"].update(title=None),
        lambda r: r["result"]["42126144"].update(source=""),
        lambda r: r["result"]["42126144"].update(source=5),
        lambda r: r["result"]["42126144"].update(pubdate=" "),
        lambda r: r["result"]["42126144"].update(uid="999"),
        lambda r: r["result"]["42126144"].update(pubtype="Journal Article"),
        lambda r: r["result"].pop("42126144"),
        lambda r: r["result"]["41732954"].update(error="cannot get document summary"),
        lambda r: r.pop("result"),
        lambda r: r.update(result=[]),
    ],
)
async def test_missing_required_summary_fields_raise_source_unavailable(client, mutate):
    body = copy.deepcopy(_summary())
    mutate(body)
    respx.get(ESEARCH_URL).respond(200, json=_search())
    respx.get(ESUMMARY_URL).respond(200, json=body)
    with pytest.raises(SourceUnavailable):
        await PubMedConnector(client).fetch(_q("pembrolizumab"))


@respx.mock
async def test_api_key_only_in_params_and_not_in_records(client):
    es, su = _mock_ok()
    recs = await PubMedConnector(client, api_key=SECRET, email="a@b.org").fetch(
        _q("pembrolizumab")
    )
    assert es.calls.last.request.url.params["api_key"] == SECRET
    assert su.calls.last.request.url.params["api_key"] == SECRET
    for r in recs:
        dumped = r.model_dump_json()
        assert SECRET not in dumped
        assert "a@b.org" not in dumped


@respx.mock
async def test_request_carries_timeout_and_user_agent_without_mutating_client(client):
    es, su = _mock_ok()
    before = dict(client.headers)
    await PubMedConnector(client).fetch(_q("pembrolizumab"))
    for route in (es, su):
        req = route.calls.last.request
        assert req.headers["user-agent"].startswith("oncobuddy-mcp/")
        assert req.extensions["timeout"]["read"] == 30.0
    assert dict(client.headers) == before


@respx.mock
async def test_rate_limit_spacing(client):
    es, su = _mock_ok()
    t = _Time()
    c = PubMedConnector(client, clock=t.clock, sleep=t.sleep)
    await c.fetch(_q("pembrolizumab"))
    # first request immediate; esummary must wait the full interval (clock does not advance)
    assert t.sleeps == [pytest.approx(0.34)]
    t.now += 10
    await c.fetch(_q("pembrolizumab"))
    # long gap: no wait before esearch, then one interval before esummary
    assert t.sleeps == [pytest.approx(0.34), pytest.approx(0.34)]
    assert es.call_count == 2 and su.call_count == 2


@respx.mock
async def test_rate_limit_interval_shorter_with_api_key(client):
    _mock_ok()
    t = _Time()
    await PubMedConnector(client, api_key=SECRET, clock=t.clock, sleep=t.sleep).fetch(
        _q("pembrolizumab")
    )
    assert t.sleeps == [pytest.approx(0.1)]


@respx.mock
async def test_concurrent_fetches_are_serialised_and_spaced(client):
    _mock_ok()
    t = _Time()
    c = PubMedConnector(client, clock=t.clock, sleep=t.sleep)
    await asyncio.gather(c.fetch(_q("pembrolizumab")), c.fetch(_q("pembrolizumab")))
    assert len(t.sleeps) == 3  # 4 requests, the first one immediate
    assert all(s == pytest.approx(0.34) for s in t.sleeps)


async def test_search_term_never_stored_in_cache(tmp_path: Path, client):
    tree_path = tmp_path / "regions.yaml"
    tree_path.write_text("regions:\n  global: null\n  EU: global\n", encoding="utf-8")
    tree = RegionTree.load(tree_path)
    cache = Cache(tmp_path / "cache.sqlite", DEFAULT_TTLS)
    try:
        with respx.mock:
            _mock_ok()
            rec = Reconciler(
                [PubMedConnector(client, condition_terms=lambda c: "lung cancer")],
                tree,
                cache=cache,
            )
            status = await rec.regional_status("pembrolizumab", "C34.9", "EU")
        assert status.sections["literature"].records
    finally:
        cache.close()
    data = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("cache"))
    assert b"pubmed:42126144" in data  # records were cached...
    assert b"[Title/Abstract]" not in data  # ...but never the search string
    assert b'"pembrolizumab"[' not in data  # no query string; only the filter name appears
    assert b"AND neoplasms" not in data

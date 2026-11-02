import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.connectors.trials import CTGOV_URL, TrialsConnector
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.regions import RegionTree

FIX = Path(__file__).parent.parent / "fixtures"
TODAY = date(2026, 11, 1)
MULTI = "NCT04613596"  # real: Portugal + Spain + many other countries
US_ONLY = "NCT07853690"  # real: United States only
NO_LOCS = "NCT05077215"  # real: contactsLocationsModule is empty


def _body() -> dict:
    return json.loads((FIX / "ctgov_studies.json").read_text(encoding="utf-8"))


def _only(body: dict, nct: str) -> dict:
    """One real study from the fixture; pagination markers removed so the page is complete."""
    body = copy.deepcopy(body)
    body["studies"] = [
        s for s in body["studies"] if s["protocolSection"]["identificationModule"]["nctId"] == nct
    ]
    body.pop("nextPageToken", None)
    body["totalCount"] = len(body["studies"])
    return body


def _q(
    drug: str | None = "pembrolizumab", condition: str | None = "C34.9", region: str = "PT"
) -> Query:
    return Query(drug=drug, condition=condition, region=region)


@pytest.fixture
def tree() -> RegionTree:
    return RegionTree.load()


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


def _conn(client, tree, **kw) -> TrialsConnector:
    kw.setdefault("today", lambda: TODAY)
    return TrialsConnector(client, tree, **kw)


@respx.mock
async def test_us_only_trial_emits_nothing(client, tree):
    respx.get(CTGOV_URL).respond(200, json=_only(_body(), US_ONLY))
    assert await _conn(client, tree).fetch(_q()) == []


@respx.mock
async def test_trial_without_locations_emits_nothing(client, tree):
    respx.get(CTGOV_URL).respond(200, json=_only(_body(), NO_LOCS))
    assert await _conn(client, tree).fetch(_q()) == []


@respx.mock
async def test_multi_country_trial_tagged_per_site_country(client, tree):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    pt_recs = await _conn(client, tree).fetch(_q(region="PT"))
    es_recs = await _conn(client, tree).fetch(_q(region="ES"))
    assert [r.region for r in pt_recs] == ["PT"]  # only the target region's country is emitted
    assert [r.region for r in es_recs] == ["ES"]
    assert route.calls[0].request.url.params["query.locn"] == "Portugal"
    assert route.calls[1].request.url.params["query.locn"] == "Spain"
    pt, es = pt_recs[0], es_recs[0]
    assert pt.id != es.id
    assert pt.id == f"nct:{MULTI}:PT"
    assert es.id == f"nct:{MULTI}:ES"
    for r in (pt, es):
        assert r.kind == "trial"
        assert r.source == "ClinicalTrials.gov"
        assert r.source_url == f"https://clinicaltrials.gov/study/{MULTI}"
        assert r.entities.drugs == ["pembrolizumab"]
        assert r.entities.conditions == []  # fixed neoplasm term: no condition claimed
        assert r.payload["nct_id"] == MULTI
        assert r.payload["title"].startswith("Phase 2 Trial of Adagrasib")
        assert r.payload["phase"] == ["PHASE2", "PHASE3"]
        assert r.payload["overall_status"] == "RECRUITING"
        assert r.payload["sponsor"] == "Mirati Therapeutics Inc."
    # real, mixed site statuses are reported (counts from the real fixture response)
    assert pt.payload["sites_in_region"] == 9
    assert pt.payload["sites_without_status"] == 0
    assert pt.payload["recruiting_in_region"] is True
    assert pt.payload["site_statuses_in_region"] == [
        "NOT_YET_RECRUITING",
        "RECRUITING",
        "WITHDRAWN",
    ]
    assert es.payload["sites_in_region"] == 25
    assert "ACTIVE_NOT_RECRUITING" in es.payload["site_statuses_in_region"]
    assert "countries_in_region_site_count" not in pt.payload


@respx.mock
async def test_target_region_without_site_emits_nothing_even_if_other_tree_country_has(
    client, tree
):
    body = _only(_body(), MULTI)
    cl = body["studies"][0]["protocolSection"]["contactsLocationsModule"]
    cl["locations"] = [x for x in cl["locations"] if x["country"] != "Portugal"]
    respx.get(CTGOV_URL).respond(200, json=body)
    assert await _conn(client, tree).fetch(_q(region="PT")) == []


@respx.mock
@pytest.mark.parametrize("region", ["EU", "global", "XX"])
async def test_non_country_region_makes_no_call_and_returns_empty(client, tree, region):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    assert await _conn(client, tree).fetch(_q(region=region)) == []
    assert route.call_count == 0


def _site_mutated(statuses: list) -> dict:
    """Mutated copy of the real fixture: PT sites replaced by one site per given status
    (None = status key missing). Edited test data, not a real response."""
    body = _only(_body(), MULTI)
    cl = body["studies"][0]["protocolSection"]["contactsLocationsModule"]
    cl["locations"] = [x for x in cl["locations"] if x["country"] != "Portugal"]
    for s in statuses:
        loc = {"country": "Portugal"}
        if s is not None:
            loc["status"] = s
        cl["locations"].append(loc)
    return body


@respx.mock
@pytest.mark.parametrize(
    "statuses,recruiting,no_status",
    [
        (["WITHDRAWN"], False, 0),
        (["WITHDRAWN", "COMPLETED"], False, 0),
        (["RECRUITING"], True, 0),
        (["ENROLLING_BY_INVITATION", "WITHDRAWN"], True, 0),
        ([None], False, 1),
        ([None, "WITHDRAWN"], False, 1),
        ([None, "NOT_YET_RECRUITING"], True, 1),
    ],
)
async def test_recruiting_in_region_flag(client, tree, statuses, recruiting, no_status):
    respx.get(CTGOV_URL).respond(200, json=_site_mutated(statuses))
    (rec,) = await _conn(client, tree).fetch(_q(region="PT"))
    assert rec.payload["recruiting_in_region"] is recruiting
    assert rec.payload["sites_in_region"] == len(statuses)
    assert rec.payload["sites_without_status"] == no_status
    assert rec.payload["site_statuses_in_region"] == sorted({s for s in statuses if s})


def _many_without_pt(next_token: bool) -> dict:
    """Mutated: five copies of the real US-only study (edited test data)."""
    body = _body()
    body["studies"] = [copy.deepcopy(body["studies"][1]) for _ in range(5)]
    if not next_token:
        body.pop("nextPageToken", None)
        body["totalCount"] = 5
    return body


@respx.mock
async def test_truncated_with_zero_survivors_raises(client, tree):
    respx.get(CTGOV_URL).respond(200, json=_many_without_pt(True))
    with pytest.raises(SourceUnavailable, match="truncated") as ei:
        await _conn(client, tree).fetch(_q())
    assert ei.value.__cause__ is None


@respx.mock
async def test_truncated_by_total_greater_than_returned_raises(client, tree):
    body = _many_without_pt(False)
    body["totalCount"] = 847
    respx.get(CTGOV_URL).respond(200, json=body)
    with pytest.raises(SourceUnavailable, match="truncated"):
        await _conn(client, tree).fetch(_q())


@respx.mock
async def test_not_truncated_with_zero_survivors_returns_empty(client, tree):
    respx.get(CTGOV_URL).respond(200, json=_many_without_pt(False))
    assert await _conn(client, tree).fetch(_q()) == []


@respx.mock
async def test_pt_query_through_reconciler_returns_only_pt_and_eu_scope(tree):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    async with httpx.AsyncClient() as c:
        rec = Reconciler([_conn(c, tree)], tree, cache=None)
        pt = await rec.regional_status("pembrolizumab", "C34.9", "PT")
        eu = await rec.regional_status("pembrolizumab", "C34.9", "EU")
    records = pt.sections["trials"].records
    assert [r.region for r in records] == ["PT"]
    assert all(MULTI in r.id for r in records)
    assert not any(US_ONLY in r.id or NO_LOCS in r.id for r in records)
    # Design consequence: trials exist per country region only; EU/global queries make no call.
    assert route.call_count == 1
    assert eu.sections["trials"].records == []
    assert eu.sections["trials"].no_data is not None


@respx.mock
async def test_default_status_filter_in_request_params(client, tree):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    await _conn(client, tree, page_size=25).fetch(_q())
    req = route.calls.last.request
    p = req.url.params
    assert p["filter.overallStatus"] == "RECRUITING|NOT_YET_RECRUITING|ENROLLING_BY_INVITATION"
    assert p["query.intr"] == "pembrolizumab"
    assert p["query.cond"] == "cancer OR neoplasm"
    assert p["query.locn"] == "Portugal"
    assert p["pageSize"] == "25"
    assert p["countTotal"] == "true"
    assert "LocationCountry" in p["fields"] and "NCTId" in p["fields"]
    assert "pageToken" not in p
    assert "User-Agent" in req.headers and "oncobuddy" in req.headers["User-Agent"]


@respx.mock
async def test_payload_search_marker(client, tree):
    respx.get(CTGOV_URL).respond(200, json=_body())
    recs = await _conn(client, tree).fetch(_q())
    s = recs[0].payload["search"]
    assert s["status_filter"] == ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"]
    assert s["returned"] == 3
    assert s["total"] == 847
    assert s["location_filter"] == "Portugal"
    assert s["truncated"] is True  # fixture carries a real nextPageToken; only one page is read
    assert s["condition_term"] == "cancer OR neoplasm"
    assert s["condition_source"] == "fixed-neoplasm-default"


@respx.mock
async def test_condition_resolver_label_used(client, tree):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    recs = await _conn(client, tree, condition_terms=lambda c: "lung cancer").fetch(_q())
    assert route.calls.last.request.url.params["query.cond"] == "lung cancer"
    assert recs[0].payload["search"]["condition_term"] == "lung cancer"
    assert recs[0].payload["search"]["condition_source"] == "resolver"


@respx.mock
@pytest.mark.parametrize("label", ['x" OR 1', "a(b)", "", "   ", "lung|cancer", None, 5])
async def test_unsafe_label_ignored(client, tree, label):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    await _conn(client, tree, condition_terms=lambda c: label).fetch(_q())
    assert route.calls.last.request.url.params["query.cond"] == "cancer OR neoplasm"


@respx.mock
@pytest.mark.parametrize("drug", ["Pembrolizumab", "pembro lizumab", "a", "x;y", "1abc", "drug\n"])
async def test_non_inn_drug_value_error(client, tree, drug):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    with pytest.raises(ValueError):
        await _conn(client, tree).fetch(_q(drug))
    assert route.call_count == 0


@respx.mock
async def test_none_drug_returns_empty(client, tree):
    route = respx.get(CTGOV_URL).respond(200, json=_body())
    assert await _conn(client, tree).fetch(_q(None)) == []
    assert route.call_count == 0


@respx.mock
@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
async def test_http_errors_raise_source_unavailable(client, tree, status):
    respx.get(CTGOV_URL).respond(status, text="SECRET-BODY-LEAK")
    with pytest.raises(SourceUnavailable) as ei:
        await _conn(client, tree).fetch(_q())
    assert "SECRET-BODY-LEAK" not in str(ei.value)
    assert ei.value.__cause__ is None


@respx.mock
async def test_transport_errors_and_timeouts(client, tree):
    for exc in (httpx.ConnectError("boom secret"), httpx.ReadTimeout("slow secret")):
        respx.get(CTGOV_URL).mock(side_effect=exc)
        with pytest.raises(SourceUnavailable) as ei:
            await _conn(client, tree).fetch(_q())
        assert "secret" not in str(ei.value)
        assert ei.value.__cause__ is None


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        '{"studies": "x"}',
        '{"nope": 1}',
        '{"studies": [1]}',
        '{"studies": [{"protocolSection": []}]}',
    ],
)
async def test_malformed_json_raises_source_unavailable(client, tree, body):
    respx.get(CTGOV_URL).respond(200, text=body)
    with pytest.raises(SourceUnavailable) as ei:
        await _conn(client, tree).fetch(_q())
    assert ei.value.__cause__ is None


def _mutated(path: list, value) -> dict:
    body = _only(_body(), MULTI)
    node = body["studies"][0]
    for k in path[:-1]:
        node = node[k]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return body


@respx.mock
@pytest.mark.parametrize(
    "path,value",
    [
        (["protocolSection", "identificationModule", "nctId"], KeyError),
        (["protocolSection", "identificationModule", "nctId"], ""),
        (["protocolSection", "identificationModule", "nctId"], "NCT1234"),
        (["protocolSection", "identificationModule", "nctId"], "NCT04613596\n"),
        (["protocolSection", "identificationModule", "briefTitle"], KeyError),
        (["protocolSection", "identificationModule", "briefTitle"], "  "),
        (["protocolSection", "statusModule", "overallStatus"], ""),
        (["protocolSection", "contactsLocationsModule", "locations"], "x"),
        (["protocolSection", "designModule", "phases"], "PHASE2"),
    ],
)
async def test_missing_nct_or_title_raises_source_unavailable(client, tree, path, value):
    respx.get(CTGOV_URL).respond(200, json=_mutated(path, value))
    with pytest.raises(SourceUnavailable) as ei:
        await _conn(client, tree).fetch(_q())
    assert ei.value.__cause__ is None


@respx.mock
@pytest.mark.parametrize(
    "stamp,expected",
    [
        ({"date": "2026-10-30"}, date(2026, 10, 30)),
        ({"date": "2026-11-01"}, date(2026, 11, 1)),
        ({"date": "2026-11-02"}, "unknown"),  # future
        ({"date": "2026-10"}, "unknown"),  # month-only is not a date
        ({"date": "garbage"}, "unknown"),
        ({"date": ""}, "unknown"),
        ({}, "unknown"),
        (None, "unknown"),
    ],
)
async def test_valid_as_of_from_update_date_and_unknown_when_missing_and_never_future(
    client, tree, stamp, expected
):
    body = _only(_body(), MULTI)
    sm = body["studies"][0]["protocolSection"]["statusModule"]
    if stamp is None:
        del sm["lastUpdatePostDateStruct"]
    else:
        sm["lastUpdatePostDateStruct"] = stamp
    respx.get(CTGOV_URL).respond(200, json=body)
    recs = await _conn(client, tree).fetch(_q())
    assert recs and all(r.valid_as_of == expected for r in recs)


@respx.mock
async def test_region_only_emitted_if_in_tree(client):
    from oncobuddy.core.regions import RegionTree as RT

    small = RT({"global": None, "EU": "global", "PT": "EU"})  # no ES
    async with httpx.AsyncClient() as c:
        respx.get(CTGOV_URL).respond(200, json=_body())
        recs = await TrialsConnector(c, small, today=lambda: TODAY).fetch(_q())
    assert [r.region for r in recs] == ["PT"]


def test_page_size_validated(tree):
    for bad in (0, -1, 1001, True, "5"):
        with pytest.raises(ValueError):
            TrialsConnector(httpx.AsyncClient(), tree, page_size=bad)  # type: ignore[arg-type]


@respx.mock
async def test_does_not_mutate_callers_client(client, tree):
    respx.get(CTGOV_URL).respond(200, json=_body())
    before = dict(client.headers)
    await _conn(client, tree).fetch(_q())
    assert dict(client.headers) == before

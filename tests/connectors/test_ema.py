import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.connectors.ema import EMA_MEDICINES_JSON_URL, EmaConnector
from oncobuddy.core.query import Query

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ema_medicines.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _q(drug: str | None) -> Query:
    return Query(drug=drug, condition=None, region="EU")


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


@respx.mock
async def test_returns_eu_region_record_for_authorised_inn(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    recs = await EmaConnector(client).fetch(_q("pembrolizumab"))
    assert len(recs) == 1
    r = recs[0]
    assert r.region == "EU"
    assert r.kind == "medicine"
    assert r.source == "EMA"
    assert r.source_url == "https://www.ema.europa.eu/en/medicines/human/EPAR/keytruda"
    assert r.valid_as_of == date(2026, 10, 30)
    assert r.licence == "EMA-reuse-notice"
    assert r.entities.drugs == ["pembrolizumab"]
    assert r.entities.atc == ["L01FF02"]
    assert r.payload["product"] == "Keytruda"
    assert r.payload["status"] == "Authorised"
    assert r.payload["authorisation_date"] == "2015-07-17"
    assert r.payload["orphan"] is False
    assert "therapeutic_indication" not in r.payload


@respx.mock
async def test_unknown_inn_returns_empty_list(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    assert await EmaConnector(client).fetch(_q("notadrug")) == []


@respx.mock
async def test_withdrawn_medicine_returned_with_its_status(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    recs = await EmaConnector(client).fetch(_q("indacaterol"))
    assert [r.payload["status"] for r in recs] == ["Withdrawn"]
    # status is not inferred from presence: application-withdrawn trastuzumab too
    recs = await EmaConnector(client).fetch(_q("trastuzumab"))
    assert {r.payload["status"] for r in recs} == {"Authorised", "Application withdrawn"}
    hervelous = next(r for r in recs if r.payload["product"] == "Hervelous")
    assert "authorisation_date" not in hervelous.payload


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_429_and_500_raise_source_unavailable(client, status):
    respx.get(EMA_MEDICINES_JSON_URL).respond(status, text="secret body")
    with pytest.raises(SourceUnavailable) as exc:
        await EmaConnector(client).fetch(_q("pembrolizumab"))
    assert "secret body" not in str(exc.value)


@respx.mock
async def test_network_error_and_timeout_raise_source_unavailable(client):
    respx.get(EMA_MEDICINES_JSON_URL).mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(SourceUnavailable):
        await EmaConnector(client).fetch(_q("pembrolizumab"))
    respx.get(EMA_MEDICINES_JSON_URL).mock(side_effect=httpx.ConnectError("c"))
    with pytest.raises(SourceUnavailable):
        await EmaConnector(client).fetch(_q("pembrolizumab"))


@respx.mock
async def test_malformed_json_raises_source_unavailable(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, text="<html>not json")
    with pytest.raises(SourceUnavailable):
        await EmaConnector(client).fetch(_q("pembrolizumab"))


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        [],
        {"meta": {}},
        {"data": {"a": 1}},
        {"data": ["x"]},
        {"data": [{"name_of_medicine": "X"}]},
    ],
)
async def test_wrong_shape_raises_source_unavailable(client, body):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=body)
    with pytest.raises(SourceUnavailable):
        await EmaConnector(client).fetch(_q("pembrolizumab"))


@respx.mock
async def test_missing_revision_date_gives_unknown(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    recs = await EmaConnector(client).fetch(_q("enzalutamide"))
    assert len(recs) == 1
    assert recs[0].valid_as_of == "unknown"
    assert "authorisation_date" not in recs[0].payload
    assert recs[0].payload["status"] == "Authorised"


@respx.mock
async def test_exact_match_not_substring(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    c = EmaConnector(client)
    assert await c.fetch(_q("trastuz")) == []
    assert await c.fetch(_q("trastuzumab emtansine")) == []
    assert await c.fetch(_q("pembro")) == []


@respx.mock
async def test_multi_substance_field_matches_any_one(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    c = EmaConnector(client)
    assert len(await c.fetch(_q("levodopa"))) == 1
    assert len(await c.fetch(_q("carbidopa"))) == 1


@respx.mock
async def test_index_downloaded_once_per_instance(client):
    route = respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    c = EmaConnector(client)
    await c.fetch(_q("pembrolizumab"))
    await c.fetch(_q("trastuzumab"))
    assert route.call_count == 1


@respx.mock
async def test_failed_download_is_not_cached(client):
    route = respx.get(EMA_MEDICINES_JSON_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=_payload())]
    )
    c = EmaConnector(client)
    with pytest.raises(SourceUnavailable):
        await c.fetch(_q("pembrolizumab"))
    assert len(await c.fetch(_q("pembrolizumab"))) == 1
    assert route.call_count == 2


async def test_none_drug_returns_empty(client):
    assert await EmaConnector(client).fetch(_q(None)) == []


# ---- fix round 1 ----


@respx.mock
async def test_every_record_has_scope_marker_and_dataset_timestamp(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    recs = await EmaConnector(client).fetch(_q("trastuzumab"))
    assert recs
    for r in recs:
        assert r.payload["procedure"] == "EU centrally authorised"
        assert r.payload["dataset_timestamp"] == "2026-11-01T06:08:29Z"


@respx.mock
async def test_dataset_timestamp_omitted_when_absent(client):
    body = _payload()
    del body["meta"]
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=body)
    recs = await EmaConnector(client).fetch(_q("pembrolizumab"))
    assert "dataset_timestamp" not in recs[0].payload


@respx.mock
async def test_entities_drugs_are_row_inns(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    c = EmaConnector(client)
    assert (await c.fetch(_q("pembrolizumab")))[0].entities.drugs == ["pembrolizumab"]
    combo = (await c.fetch(_q("levodopa")))[0]
    assert combo.entities.drugs == ["levodopa", "carbidopa"]


@respx.mock
async def test_salt_name_match_emits_inn_not_salt(client):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    recs = await EmaConnector(client).fetch(_q("indacaterol maleate"))
    assert len(recs) == 1
    assert "indacaterol" in recs[0].entities.drugs
    assert "indacaterol maleate" not in recs[0].entities.drugs


def _with_row(**overrides) -> dict:
    body = _payload()
    row = next(x for x in body["data"] if x["name_of_medicine"] == "Keytruda")
    row.update(overrides)
    return body


@respx.mock
@pytest.mark.parametrize(
    "overrides",
    [
        {"medicine_url": ""},
        {"medicine_url": "   "},
        {"medicine_url": 5},
        {"medicine_url": None},
        {"ema_product_number": ""},
        {"ema_product_number": 12},
        {"medicine_status": ""},
        {"medicine_status": None},
        {"medicine_status": 3},
    ],
)
async def test_required_fields_must_be_non_empty_strings(client, overrides):
    respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_with_row(**overrides))
    with pytest.raises(SourceUnavailable):
        await EmaConnector(client).fetch(_q("pembrolizumab"))


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@respx.mock
async def test_index_reused_within_max_age_and_refreshed_after(client):
    route = respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    clock = _Clock()
    c = EmaConnector(client, max_age_seconds=100, clock=clock)
    await c.fetch(_q("pembrolizumab"))
    clock.now += 99
    await c.fetch(_q("pembrolizumab"))
    assert route.call_count == 1
    clock.now += 2
    await c.fetch(_q("pembrolizumab"))
    assert route.call_count == 2


@respx.mock
async def test_failed_refresh_raises_not_stale(client):
    route = respx.get(EMA_MEDICINES_JSON_URL).mock(
        side_effect=[httpx.Response(200, json=_payload()), httpx.Response(500)]
    )
    clock = _Clock()
    c = EmaConnector(client, max_age_seconds=100, clock=clock)
    assert len(await c.fetch(_q("pembrolizumab"))) == 1
    clock.now += 101
    with pytest.raises(SourceUnavailable):
        await c.fetch(_q("pembrolizumab"))
    assert route.call_count == 2


@respx.mock
async def test_concurrent_first_fetches_download_once(client):
    route = respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    c = EmaConnector(client)
    a, b = await asyncio.gather(c.fetch(_q("pembrolizumab")), c.fetch(_q("trastuzumab")))
    assert a and b
    assert route.call_count == 1


@respx.mock
async def test_request_carries_timeout_and_user_agent_without_mutating_client(client):
    route = respx.get(EMA_MEDICINES_JSON_URL).respond(200, json=_payload())
    before = dict(client.headers)
    await EmaConnector(client).fetch(_q("pembrolizumab"))
    req = route.calls.last.request
    assert req.headers["user-agent"].startswith("oncobuddy-mcp/")
    assert req.extensions["timeout"]["read"] == 30.0
    assert dict(client.headers) == before

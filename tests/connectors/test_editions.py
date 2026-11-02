import asyncio
import json
from datetime import UTC, date, datetime

import httpx

from oncobuddy.connectors.editions import EditionChecker, classify
from oncobuddy.core.cache import DEFAULT_TTLS, Cache
from oncobuddy.core.records import Entities, Record

DOI = "10.1016/j.annonc.2022.10.003"
TITLE = "Metastatic colorectal cancer: ESMO Clinical Practice Guideline"
ENTRY_DATE = date(2022, 10, 25)
JOURNALS = {"Annals of Oncology", "ESMO Open"}


def parts(y, m, d):
    return {"date-parts": [[y, m, d]]}


def notice(kind, doi=DOI):
    return {"DOI": "10.1016/notice", "created": parts(2023, 1, 5),
            "update-to": [{"DOI": doi, "type": kind, "updated": parts(2023, 1, 5)}]}


def cand(title=TITLE, doi="10.1016/j.annonc.2026.03.005", journal="Annals of Oncology",
         page="1-20", created=(2026, 4, 14), type_="journal-article", article=None):
    item = {"DOI": doi, "title": [title], "container-title": [journal], "type": type_,
            "created": parts(*created)}
    if page:
        item["page"] = page
    if article:
        item["article-number"] = article
    return item


def run(notices=(), cands=()):
    return classify(DOI, TITLE, ENTRY_DATE, JOURNALS, list(notices), list(cands))


def test_current_when_nothing_found():
    assert run()["status"] == "current"


def test_statuses_and_precedence():
    assert run(notices=[notice("correction")])["status"] == "corrected"
    assert run(notices=[notice("expression_of_concern")])["status"] == "corrected"
    assert run(notices=[notice("retraction")])["status"] == "retracted"
    newer = run(cands=[cand()])
    assert newer["status"] == "newer_found"
    assert newer["newer"] == {"doi": "10.1016/j.annonc.2026.03.005", "title": TITLE, "date": "2026-04-14"}
    both = run(notices=[notice("correction")], cands=[cand()])
    assert both["status"] == "newer_found" and both["notices"][0]["type"] == "correction"
    assert run(notices=[notice("retraction")], cands=[cand()])["status"] == "retracted"


def test_notices_for_other_dois_are_ignored():
    assert run(notices=[notice("retraction", doi="10.1016/other")])["status"] == "current"


def test_rejected_candidates():
    bad = [
        cand(page="561"),  # single page, no article number
        cand(journal="Lancet Oncology"),
        cand(title="Gastric cancer: ESMO Clinical Practice Guideline"),
        cand(doi=DOI),
        cand(created=(2022, 10, 1)),
        cand(type_="posted-content"),
    ]
    assert run(cands=bad)["status"] == "current"


def test_article_number_counts_as_a_full_article():
    assert run(cands=[cand(page=None, article="100500")])["status"] == "newer_found"


def make_record(source="ESMO", url=f"https://doi.org/{DOI}", rid="guideline:esmo-mcrc"):
    return Record(
        id=rid, kind="guideline", entities=Entities(conditions=["C18"]), region="EU",
        source=source, source_url=url, valid_as_of=ENTRY_DATE,
        retrieved_at=datetime(2026, 11, 2, tzinfo=UTC), licence="restricted-link-only",
        payload={"title": TITLE},
    )


class Fake:
    def __init__(self, notices=(), cands=(), fail=None, delay=0.0):
        self.requests = []
        self.notices, self.cands, self.fail, self.delay = list(notices), list(cands), fail, delay

    def client(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                return httpx.Response(self.fail)
            items = self.notices if "updates:" in request.url.params.get("filter", "") else self.cands
            return httpx.Response(200, json={"message": {"items": items}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def annotate(fake, records, **kw):
    cache = kw.pop("cache", Cache(None, DEFAULT_TTLS))
    kw.setdefault("spacing", 0.0)
    checker = EditionChecker(fake.client(), cache, **kw)
    await checker.annotate(records)
    return cache


async def test_checker_sets_payload_and_sends_only_registry_values():
    fake = Fake(cands=[cand()])
    (record,) = [make_record()]
    await annotate(fake, [record], mailto=None)
    check = record.payload["edition_check"]
    assert check["status"] == "newer_found" and check["checked_at"]
    assert len(fake.requests) == 2
    for request in fake.requests:
        assert request.url.host == "api.crossref.org"
        assert set(request.url.params) <= {"filter", "rows", "select", "query.bibliographic"}
        assert "mailto" not in request.url.params
    assert fake.requests[1].url.params["query.bibliographic"] == "Metastatic colorectal cancer"


async def test_mailto_only_when_configured():
    fake = Fake()
    await annotate(fake, [make_record()], mailto="me@example.org")
    assert all(r.url.params["mailto"] == "me@example.org" for r in fake.requests)


async def test_cache_hit_makes_no_request():
    cache = Cache(None, DEFAULT_TTLS)
    await annotate(Fake(cands=[cand()]), [make_record()], cache=cache)
    second = Fake()
    record = make_record()
    await annotate(second, [record], cache=cache)
    assert second.requests == []
    assert record.payload["edition_check"]["status"] == "newer_found"


async def test_failures_are_unknown_never_current_and_not_cached():
    cache = Cache(None, DEFAULT_TTLS)
    record = make_record()
    await annotate(Fake(fail=500), [record], cache=cache)
    assert record.payload["edition_check"]["status"] == "unknown"
    again = make_record()
    fake = Fake()
    await annotate(fake, [again], cache=cache)
    assert fake.requests and again.payload["edition_check"]["status"] == "current"


async def test_http_429_is_unknown():
    record = make_record()
    await annotate(Fake(fail=429), [record])
    assert record.payload["edition_check"]["status"] == "unknown"


async def test_spacing_holds_with_email_concurrency():
    starts = []

    async def handler(request):
        starts.append(asyncio.get_running_loop().time())
        return httpx.Response(200, json={"message": {"items": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    records = [make_record(rid=f"guideline:g{i}", url=f"https://doi.org/10.1016/x{i}") for i in range(3)]
    await EditionChecker(client, Cache(None, DEFAULT_TTLS), mailto="a@b.org", spacing=0.03).annotate(records)
    gaps = [b - a for a, b in zip(sorted(starts), sorted(starts)[1:])]
    assert min(gaps) >= 0.025


async def test_bad_json_is_unknown():
    def handler(request):
        return httpx.Response(200, content=b"not json")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    record = make_record()
    await EditionChecker(client, Cache(None, DEFAULT_TTLS)).annotate([record])
    assert record.payload["edition_check"]["status"] == "unknown"


async def test_timeout_gives_unknown():
    record = make_record()
    await annotate(Fake(delay=0.5), [record], timeout=0.05)
    assert record.payload["edition_check"]["status"] == "unknown"


async def test_cap_of_six_and_not_checked_cases():
    records = [make_record(rid=f"guideline:g{i}", url=f"https://doi.org/10.1016/x{i}") for i in range(7)]
    nccn = make_record(source="NCCN", url="https://www.nccn.org/x", rid="guideline:nccn")
    fake = Fake()
    await annotate(fake, [*records, nccn])
    statuses = [r.payload["edition_check"]["status"] for r in records]
    assert statuses.count("current") == 6 and statuses[-1] == "not_checked"
    assert nccn.payload["edition_check"] == {"status": "not_checked"}
    assert len(fake.requests) == 12


async def test_disabled_checker_marks_not_checked_without_requests():
    fake = Fake()
    record = make_record()
    await annotate(fake, [record], enabled=False)
    assert record.payload["edition_check"] == {"status": "not_checked"}
    assert fake.requests == []


async def test_requests_are_serialised_without_email_and_limited_with_it():
    for mailto, limit in ((None, 1), ("me@example.org", 3)):
        live = peak = 0

        async def handler(request, ):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return httpx.Response(200, json={"message": {"items": []}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        records = [make_record(rid=f"guideline:g{i}", url=f"https://doi.org/10.1016/x{i}") for i in range(4)]
        await EditionChecker(client, Cache(None, DEFAULT_TTLS), mailto=mailto, spacing=0.0).annotate(records)
        assert peak == limit


async def test_spacing_between_request_starts():
    starts = []

    async def handler(request):
        starts.append(asyncio.get_running_loop().time())
        return httpx.Response(200, json={"message": {"items": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await EditionChecker(client, Cache(None, DEFAULT_TTLS), spacing=0.05).annotate([make_record()])
    assert len(starts) == 2 and starts[1] - starts[0] >= 0.045


async def test_deadline_keeps_finished_records_and_marks_rest_unknown():
    cache = Cache(None, DEFAULT_TTLS)
    records = [make_record(rid=f"guideline:g{i}", url=f"https://doi.org/10.1016/x{i}") for i in range(4)]
    await annotate(Fake(delay=0.05), records, cache=cache, deadline=0.25)
    statuses = [r.payload["edition_check"]["status"] for r in records]
    assert statuses[0] == "current" and statuses[-1] == "unknown"
    # finished ones were cached
    again = make_record(rid="guideline:g0", url="https://doi.org/10.1016/x0")
    quiet = Fake()
    await annotate(quiet, [again], cache=cache)
    assert quiet.requests == []


def test_payload_is_json_serialisable():
    json.dumps(run(notices=[notice("correction")], cands=[cand()]))

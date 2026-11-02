import json
import logging
from datetime import UTC, date, datetime

import pytest
from mcp.client import Client

from oncobuddy.connectors.base import SourceUnavailable
from oncobuddy.core.cache import Cache, cache_key
from oncobuddy.core.query import Query
from oncobuddy.core.reconciler import NOTICE, Reconciler
from oncobuddy.core.records import Entities, Record
from oncobuddy.core.regions import RegionTree
from oncobuddy.server import (
    RECORD_LRU_SIZE,
    OncoTools,
    clear_cache_main,
    create_server,
    profile_input_schema,
)

TOOLS = {
    "regional_status",
    "guidelines_for",
    "find_trials",
    "treatment_options",
    "get_medicine",
    "search_literature",
    "lookup_code",
    "get_record",
}
SECTIONS = ("authorisation", "reimbursement", "guidelines", "trials", "literature", "coding")
SECRET = "SECRET-PATIENT-JOHN-DOE-1234"


def record(record_id: str, region: str = "EU", kind: str = "medicine") -> Record:
    return Record(
        id=record_id,
        kind=kind,
        entities=Entities(drugs=["trastuzumab"], conditions=["C50"]),
        region=region,
        source="Fake",
        source_url="https://example.org/" + record_id,
        valid_as_of=date(2026, 1, 1),
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="CC-BY-4.0",
        payload={"name": record_id},
    )


class Fake:
    def __init__(self, section: str, records=None, exc=None) -> None:
        self.name = f"fake-{section}"
        self.section = section
        self.records = records or []
        self.exc = exc

    async def fetch(self, query: Query) -> list[Record]:
        if self.exc:
            raise self.exc
        return list(self.records)


def make_tools(exc=None) -> OncoTools:
    sections = SECTIONS
    connectors = [
        Fake(s, [record(f"{s}-1", "EU"), record(f"{s}-pt", "PT")], exc=exc) for s in sections
    ]
    return OncoTools(Reconciler(connectors, RegionTree.load(), cache=None))


@pytest.fixture
def tools() -> OncoTools:
    return make_tools()


def assert_envelope(result: dict, region: str) -> None:
    assert result["notice"] == NOTICE
    assert result["query_region"] == region
    assert result["scope_chain"][0] == region
    assert result["scope_chain"][-1] == "global"


async def call_all(tools: OncoTools, region: str) -> dict[str, dict]:
    return {
        "regional_status": await tools.regional_status("trastuzumab", "C50.9", region),
        "guidelines_for": await tools.guidelines_for("C50.9", region),
        "find_trials": await tools.find_trials("trastuzumab", "C50.9", region),
        "treatment_options": await tools.treatment_options({"cancer_type": "C50.9"}, region),
        "get_medicine": await tools.get_medicine("trastuzumab", region),
        "search_literature": await tools.search_literature("trastuzumab", "C50.9", region),
    }


async def test_every_tool_response_has_notice_and_scope_chain(tools):
    for name, result in (await call_all(tools, "PT")).items():
        assert_envelope(result, "PT")
        assert result["scope_chain"] == ["PT", "EU", "global"], name
    code = await tools.lookup_code("C50.9")
    assert_envelope(code, "global")
    assert code["section"]["records"]
    first = code["section"]["records"][0]["id"]
    got = await tools.get_record(first)
    assert_envelope(got, "global")
    assert got["record"]["id"] == first


async def test_lookup_code_not_found_has_reason(tools):
    result = await tools.lookup_code("zzz nothing")
    assert_envelope(result, "global")
    assert result["section"]["no_data"]["reason"] == "not_found"


async def test_unknown_region_returns_error_object_not_data(tools):
    for result in (await call_all(tools, "XX")).values():
        assert result["error"]["code"] == "unknown_region"
        assert set(result) == {"error", "notice"}
        assert sorted(result["error"]["valid_regions"]) == ["ES", "EU", "PT", "UK", "US", "global"]
        assert result["notice"] == NOTICE


async def test_bad_inputs_give_not_found_not_exceptions(tools):
    result = await tools.get_medicine("Not A Drug!!", "PT")
    assert_envelope(result, "PT")
    assert result["section"]["no_data"]["reason"] == "not_found"
    result = await tools.regional_status("x", "not a code", "PT")
    assert all(s["no_data"]["reason"] == "not_found" for s in result["sections"].values())


async def test_all_connectors_failing_yields_only_source_unavailable():
    tools = make_tools(exc=SourceUnavailable("boom secret-host"))
    result = await tools.regional_status("trastuzumab", "C50.9", "PT")
    for name in SECTIONS:
        section = result["sections"][name]
        assert section["records"] == []
        assert section["no_data"]["reason"] == "source_unavailable"
    assert "secret-host" not in json.dumps(result)


async def test_get_record_round_trip_and_unknown_id(tools):
    status = await tools.regional_status("trastuzumab", "C50.9", "PT")
    rec = status["sections"]["authorisation"]["records"][0]
    got = await tools.get_record(rec["id"])
    assert got["record"] == rec
    assert_envelope(got, "PT")
    unknown = await tools.get_record("nope")
    assert unknown == {"error": {"code": "unknown_record"}, "notice": NOTICE}


async def test_get_record_lru_is_bounded(tools):
    recs = [record(f"r{i}") for i in range(RECORD_LRU_SIZE + 5)]
    tools._remember(recs, "EU", ["EU", "global"])
    assert (await tools.get_record("r0"))["error"]["code"] == "unknown_record"
    assert "record" in await tools.get_record(f"r{RECORD_LRU_SIZE + 4}")


def walk_strings(schema: dict, path: str = "profile"):
    """Yield (path, node) for every string-typed schema node."""
    if isinstance(schema, dict):
        if schema.get("type") == "string":
            yield path, schema
        for key, child in schema.items():
            if key == "propertyNames":
                yield from walk_strings({**child, "type": "string"}, f"{path}.{key}")
            else:
                yield from walk_strings(child, f"{path}.{key}")
    elif isinstance(schema, list):
        for i, child in enumerate(schema):
            yield from walk_strings(child, f"{path}[{i}]")


async def test_treatment_options_tool_schema_has_no_free_text_field():
    async with Client(create_server(make_tools())) as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}
    assert set(listed) == TOOLS
    schema = listed["treatment_options"].input_schema
    profile = schema["properties"]["profile"]
    assert profile["additionalProperties"] is False
    assert set(profile["properties"]) == {
        "cancer_type", "stage", "biomarkers", "ecog", "prior_lines", "intent",
        "comorbidity_flags",
    }  # fmt: skip
    strings = list(walk_strings(profile))
    assert strings
    for path, node in strings:
        assert "enum" in node or "const" in node or "pattern" in node, path
    # No tool exposes any patient identifier-like or free-text parameter name.
    async with Client(create_server(make_tools())) as client:
        for tool in (await client.list_tools()).tools:
            names = set(tool.input_schema.get("properties", {}))
            assert not names & {"name", "patient", "notes", "free_text", "dob", "mrn"}


def test_profile_schema_matches_model_fields():
    from oncobuddy.core.profile import ClinicalProfile

    assert set(profile_input_schema()["properties"]) == set(ClinicalProfile.model_fields)


async def test_invalid_profile_response_leaks_nothing(caplog, capfd):
    tools = make_tools()
    bad = {"cancer_type": SECRET, "stage": SECRET, "name": SECRET, "prior_lines": [SECRET]}
    with caplog.at_level(logging.DEBUG):
        direct = await tools.treatment_options(bad, "PT")
        async with Client(create_server(make_tools())) as client:
            via = await client.call_tool("treatment_options", {"profile": bad, "region": "PT"})
            not_dict = await client.call_tool(
                "treatment_options", {"profile": SECRET, "region": "PT"}
            )
    assert direct["error"]["code"] == "invalid_profile"
    assert direct["notice"] == NOTICE
    blob = json.dumps(direct) + repr(via) + repr(not_dict)
    err = capfd.readouterr()
    assert SECRET not in blob
    assert SECRET not in caplog.text
    assert SECRET not in err.out + err.err
    assert "invalid_profile" in repr(via)
    assert "cancer_type" in direct["error"]["detail"]


async def test_valid_profile_does_not_reach_logs(caplog):
    tools = make_tools()
    with caplog.at_level(logging.DEBUG):
        result = await tools.treatment_options(
            {
                "cancer_type": "C50.9",
                "prior_lines": ["tamoxifen"],
                "biomarkers": {"HER2": "positive"},
            },
            "PT",
        )
    assert_envelope(result, "PT")
    assert "tamoxifen" not in caplog.text
    assert "HER2" not in caplog.text


async def test_server_starts_and_lists_tools():
    async with Client(create_server(make_tools())) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == TOOLS
        result = await client.call_tool(
            "regional_status", {"drug": "trastuzumab", "indication": "C50.9", "region": "PT"}
        )
    data = result.structured_content
    assert data["notice"] == NOTICE
    assert data["scope_chain"] == ["PT", "EU", "global"]


def test_build_reconciler_wires_all_sections(monkeypatch):
    from oncobuddy.server import build_reconciler

    monkeypatch.setenv("ONCOBUDDY_CACHE", "memory")
    reconciler = build_reconciler({"ONCOBUDDY_EMAIL": "x@example.org", "NCBI_API_KEY": "k"})
    sections = [c.section for c in reconciler._connectors]
    assert set(sections) == {
        "authorisation",
        "reimbursement",
        "guidelines",
        "trials",
        "literature",
        "coding",
    }
    pointers = [c for c in reconciler._connectors if c.name == "INFARMED (pointer)"]
    assert sorted(c.section for c in pointers) == ["authorisation", "reimbursement"]


def test_clear_cache_memory_mode(monkeypatch, capsys):
    monkeypatch.setenv("ONCOBUDDY_CACHE", "memory")
    clear_cache_main()
    assert "nothing" in capsys.readouterr().out


def test_clear_cache_disk(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ONCOBUDDY_CACHE", raising=False)
    monkeypatch.setenv("ONCOBUDDY_CACHE_DIR", str(tmp_path))
    cache = Cache.from_env()
    key = cache_key(drug="trastuzumab")
    cache.put("EMA", key, [record("a")])
    assert cache.get("EMA", key)
    cache.close()
    clear_cache_main()
    assert "cleared" in capsys.readouterr().out
    cache = Cache.from_env()
    assert cache.get("EMA", key) is None
    cache.close()


# -- fix round 1: SDK-boundary scrubbing --------------------------------------------------

LEAKS = ("SECRETDRUG", "SECRETX", "input_value", "pydantic.dev", "Traceback")


async def sdk_call(name: str, arguments: dict) -> tuple[object, str]:
    async with Client(create_server(make_tools())) as client:
        result = await client.call_tool(name, arguments)
    return result.structured_content, repr(result)


@pytest.mark.parametrize(
    ("name", "arguments", "code", "detail"),
    [
        (
            "treatment_options",
            {"profile": {"cancer_type": "C50.9", "prior_lines": ["SECRETDRUG"]}},
            "invalid_arguments",
            "region: missing",
        ),
        (
            "guidelines_for",
            {"condition": "C50.9", "region": ["SECRETX"]},
            "invalid_arguments",
            "region: string_type",
        ),
        (
            "guidelines_for",
            {"condition": 5, "region": "PT"},
            "invalid_arguments",
            "condition: string_type",
        ),
        ("get_record", {"id": 5}, "invalid_arguments", "id: string_type"),
        ("get_record", {}, "invalid_arguments", "id: missing"),
        ("no_such_tool_SECRETX", {"a": "SECRETX"}, "unknown_tool", "unknown tool"),
    ],
)
async def test_sdk_validation_errors_are_scrubbed(name, arguments, code, detail, caplog, capfd):
    with caplog.at_level(logging.DEBUG):
        data, text = await sdk_call(name, arguments)
    assert data == {"error": {"code": code, "detail": detail}, "notice": NOTICE}
    err = capfd.readouterr()
    for leak in LEAKS:
        assert leak not in text, leak
        assert leak not in caplog.text, leak
        assert leak not in err.out + err.err, leak


async def test_extra_argument_is_ignored():
    data, _ = await sdk_call("get_record", {"id": "x", "extra": "SECRETX"})
    assert data["error"]["code"] == "unknown_record"


async def test_unexpected_exception_is_scrubbed(caplog):
    tools = make_tools()

    async def boom(*a, **k):
        raise RuntimeError("SECRETX boom")

    tools.reconciler().section = boom  # type: ignore[method-assign]
    async with Client(create_server(tools)) as client:
        with caplog.at_level(logging.DEBUG):
            result = await client.call_tool(
                "guidelines_for", {"condition": "C50.9", "region": "PT"}
            )
    assert result.structured_content == {
        "error": {"code": "internal_error", "detail": "unexpected error"},
        "notice": NOTICE,
    }
    assert "SECRETX" not in repr(result) + caplog.text
    assert "RuntimeError" in caplog.text


# -- server-level mixed-region test --------------------------------------------------------

MIXED_TREE = """\
regions:
  global: null
  EU: global
  PT: EU
  ES: EU
  US: global
"""


async def test_server_level_mixed_region_filtering(tmp_path):
    path = tmp_path / "regions.yaml"
    path.write_text(MIXED_TREE, encoding="utf-8")
    tree = RegionTree.load(path)
    regions = ["PT", "ES", "EU", "US", "global"]
    connectors = [
        Fake(s, [record(f"{s}-{r}", r) for r in regions])
        for s in SECTIONS
    ]
    tools = OncoTools(Reconciler(connectors, tree))
    async with Client(create_server(tools)) as client:
        status = (
            await client.call_tool(
                "regional_status",
                {"drug": "trastuzumab", "indication": "C50.9", "region": "PT"},
            )
        ).structured_content
        med = (
            await client.call_tool("get_medicine", {"drug": "trastuzumab", "region": "PT"})
        ).structured_content
        blocked = [
            (await client.call_tool("get_record", {"id": f"authorisation-{r}"})).structured_content
            for r in ("ES", "US")
        ]
    returned = [r for s in status["sections"].values() for r in s["records"]]
    returned += med["section"]["records"]
    assert returned
    assert {r["region"] for r in returned} == {"PT", "EU", "global"}
    for b in blocked:
        assert b["error"]["code"] == "unknown_record"


# -- build_reconciler cleanup, clear-cache directory hygiene -----------------------------


async def test_build_reconciler_aclose_closes_client(monkeypatch):
    from oncobuddy.server import build_reconciler

    monkeypatch.setenv("ONCOBUDDY_CACHE", "memory")
    reconciler = build_reconciler({})
    assert not reconciler.client.is_closed
    await reconciler.aclose()
    assert reconciler.client.is_closed


def test_clear_cache_does_not_create_directory(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ONCOBUDDY_CACHE", raising=False)
    target = tmp_path / "never" / "created"
    monkeypatch.setenv("ONCOBUDDY_CACHE_DIR", str(target))
    clear_cache_main()
    assert not target.exists()
    assert "no cache found at" in capsys.readouterr().out


def test_schema_override_fails_loudly_if_sdk_internals_change():
    server = create_server(make_tools())
    tool = server._tool_manager.get_tool("treatment_options")
    assert tool.parameters["properties"]["profile"]["additionalProperties"] is False


# -- final wave: server wiring ------------------------------------------------------------


async def test_find_trials_for_eu_reports_out_of_scope_without_http(monkeypatch):
    import httpx
    import respx

    from oncobuddy.connectors.trials import CTGOV_URL, TrialsConnector

    tree = RegionTree.load()
    with respx.mock:
        route = respx.get(CTGOV_URL).respond(200, json={"studies": []})
        async with httpx.AsyncClient() as http:
            tools = OncoTools(Reconciler([TrialsConnector(http, tree)], tree))
            async with Client(create_server(tools)) as client:
                result = await client.call_tool(
                    "find_trials", {"drug": "pembrolizumab", "condition": "C34.9", "region": "EU"}
                )
        assert route.call_count == 0
    section = result.structured_content["section"]
    assert section["no_data"]["reason"] == "out_of_scope_v1"
    assert "not searched for region EU" in section["notes"][0]


async def test_build_reconciler_quiets_http_loggers_and_sets_timeout(monkeypatch):
    from oncobuddy.server import build_reconciler

    monkeypatch.setenv("ONCOBUDDY_CACHE", "memory")
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    reconciler = build_reconciler({})
    try:
        for name in ("httpx", "httpcore"):
            assert logging.getLogger(name).level == logging.WARNING
        assert reconciler._timeout_s == 45.0
    finally:
        await reconciler.aclose()


async def test_treatment_options_remembers_reimbursement_pointer(tmp_path):
    from oncobuddy.connectors.guidelines import GuidelineConnector
    from oncobuddy.connectors.infarmed import InfarmedPointerConnector

    path = tmp_path / "regions.yaml"
    path.write_text(MIXED_TREE, encoding="utf-8")
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "g1.yaml").write_text(
        "id: g1\ntitle: T\nissuer: I\nurl: https://example.org/g1\nregion: EU\n"
        'published: "2024-01-31"\nvalid_as_of: "2024-01-31"\nlicence: CC-BY-4.0\n'
        "reviewed_by: dr\nreviewed_on: '2026-11-01'\nrecommendation_ref: Table 4\nevidence_grade: null\nown_words: true\nsummary: s\n"
        "applies_to:\n  conditions: [C50]\n  stages: [I, II, III, IV]\n"
        "  required_biomarkers: {}\n  line_of_therapy: first\n  interventions: [trastuzumab]\n",
        encoding="utf-8",
    )
    tools = OncoTools(
        Reconciler(
            [
                GuidelineConnector(gdir),
                InfarmedPointerConnector("authorisation"),
                InfarmedPointerConnector("reimbursement"),
            ],
            RegionTree.load(path),
        )
    )
    out = await tools.treatment_options({"cancer_type": "C50.9"}, "PT")
    pointer = out["options"][0]["reimbursement"]["records"][0]
    got = await tools.get_record(pointer["id"])
    assert got["record"]["id"] == pointer["id"]


EXPECTED_INPUT_PROPERTIES = {
    "regional_status": ["drug", "indication", "region"],
    "guidelines_for": ["condition", "region"],
    "find_trials": ["condition", "drug", "region"],
    "treatment_options": ["profile", "region"],
    "get_medicine": ["drug", "region"],
    "search_literature": ["condition", "drug", "region"],
    "lookup_code": ["code_or_term"],
    "get_record": ["id"],
}


async def test_every_successful_response_carries_agent_protocol(tools):
    from oncobuddy.core.access import AGENT_PROTOCOL

    results = dict(await call_all(tools, "PT"))
    code = await tools.lookup_code("C50.9")
    results["lookup_code"] = code
    results["get_record"] = await tools.get_record(code["section"]["records"][0]["id"])
    assert set(results) == TOOLS
    for name, result in results.items():
        assert result["agent_protocol"] == AGENT_PROTOCOL, name


async def test_error_responses_are_unchanged_by_the_protocol(tools):
    assert await tools.get_record("nope") == {"error": {"code": "unknown_record"}, "notice": NOTICE}


async def test_tool_input_properties_have_not_grown():
    async with Client(create_server(make_tools())) as client:
        listed = (await client.list_tools()).tools
    actual = {t.name: sorted(t.input_schema["properties"]) for t in listed}
    assert actual == EXPECTED_INPUT_PROPERTIES


async def test_browser_sources_are_never_contacted(monkeypatch):
    import httpx

    from oncobuddy.connectors.guidelines import GuidelineConnector
    from oncobuddy.connectors.infarmed import InfarmedPointerConnector

    def boom(*args, **kwargs):
        raise AssertionError("network call to a browser source")

    monkeypatch.setattr(httpx.AsyncClient, "send", boom)
    tools = OncoTools(
        Reconciler(
            [
                GuidelineConnector(),
                InfarmedPointerConnector("authorisation"),
                InfarmedPointerConnector("reimbursement"),
            ],
            RegionTree.load(),
        )
    )
    guidelines = await tools.guidelines_for("C50.9", "PT")
    assert guidelines["section"]["records"][0]["payload"]["access"]["method"] == "browser"
    status = await tools.regional_status("trastuzumab", "C50.9", "PT")
    pointer = status["sections"]["authorisation"]["records"][0]
    assert pointer["payload"]["access"]["method"] == "browser"
    assert status["agent_protocol"]


def test_profile_schema_describes_the_biomarker_vocabulary():
    from oncobuddy.core.biomarkers import CANONICAL

    text = profile_input_schema()["properties"]["biomarkers"]["description"]
    for key in CANONICAL:
        assert key in text
    assert "DMMR" in text


async def test_reference_records_are_remembered_for_get_record():
    ref = record("us-ref", "US", kind="guideline")
    ref = ref.model_copy(update={"payload": {"reference_only": True}})
    connectors = [Fake("guidelines", [record("g-eu", "EU", kind="guideline"), ref])]
    tools = OncoTools(Reconciler(connectors, RegionTree.load(), cache=None))
    result = await tools.guidelines_for("C50.9", "PT")
    assert [r["id"] for r in result["section"]["reference_elsewhere"]] == ["us-ref"]
    got = await tools.get_record("us-ref")
    assert got["record"]["id"] == "us-ref"
    assert got["query_region"] == "PT"
    assert got["scope_chain"] == ["PT", "EU", "global"]

    tools2 = OncoTools(Reconciler(connectors, RegionTree.load(), cache=None))
    await tools2.regional_status("trastuzumab", "C50.9", "PT")
    assert (await tools2.get_record("us-ref"))["query_region"] == "PT"

    tools3 = OncoTools(Reconciler(connectors, RegionTree.load(), cache=None))
    opts = await tools3.treatment_options({"cancer_type": "C50.9"}, "PT")
    assert [r["id"] for r in opts["reference_elsewhere"]] == ["us-ref"]
    assert (await tools3.get_record("us-ref"))["record"]["id"] == "us-ref"


def test_build_reconciler_wires_the_edition_checker_and_its_switch(monkeypatch):
    from oncobuddy.connectors.guidelines import GuidelineConnector
    from oncobuddy.server import build_reconciler

    monkeypatch.setenv("ONCOBUDDY_CACHE", "memory")

    def checker(env):
        rec = build_reconciler(env)
        return next(c for c in rec._connectors if isinstance(c, GuidelineConnector))._checker

    assert checker({})._enabled is True  # on by default
    assert checker({"ONCOBUDDY_EDITION_CHECK": "on"})._enabled is True
    assert checker({"ONCOBUDDY_EDITION_CHECK": "off"})._enabled is False
    assert checker({"ONCOBUDDY_EMAIL": "x@example.org"})._mailto == "x@example.org"

"""MCP server (stdio): thin tool wrappers over the Reconciler.

Privacy rules enforced here:
- No tool takes free text about a patient. ``treatment_options`` takes the closed
  ClinicalProfile shape; its exposed input schema is that shape with explicit patterns, but
  validation goes through ``parse_profile`` so errors never echo input values.
- Nothing in this module logs request arguments, profiles or env values, and no exception
  text is placed in a response.
- Every successful response carries NOTICE, the query region and its scope chain.
"""

import copy
import json
import logging
import os
import sys
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent
from pydantic import ValidationError

from oncobuddy.connectors import icd
from oncobuddy.connectors.editions import EditionChecker
from oncobuddy.connectors.ema import EmaConnector
from oncobuddy.connectors.guidelines import GuidelineConnector
from oncobuddy.connectors.icd import IcdConnector
from oncobuddy.connectors.infarmed import InfarmedPointerConnector
from oncobuddy.connectors.pubmed import PubMedConnector
from oncobuddy.connectors.trials import TrialsConnector
from oncobuddy.core.access import AGENT_PROTOCOL
from oncobuddy.core.biomarkers import vocabulary_description
from oncobuddy.core.cache import Cache, default_cache_path
from oncobuddy.core.options import treatment_options
from oncobuddy.core.profile import ClinicalProfile, ProfileError, parse_profile
from oncobuddy.core.reconciler import NOTICE, NoData, Reconciler, Section
from oncobuddy.core.records import Record
from oncobuddy.core.regions import RegionTree, UnknownRegion

RECORD_LRU_SIZE = 500
_HTTP_TIMEOUT_S = 20.0
# EMA's first download is about 7 MB; connectors enforce their own 30 s HTTP timeouts.
_RECONCILER_TIMEOUT_S = 45.0


def quiet_http_loggers() -> None:
    """httpx logs request URLs (which can carry api_key/email) at INFO."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class ManagedReconciler(Reconciler):
    """Reconciler that owns the shared HTTP client and cache; ``aclose`` releases both."""

    client: httpx.AsyncClient
    cache: Cache

    async def aclose(self) -> None:
        await self.client.aclose()
        self.cache.close()


def build_reconciler(env: Mapping[str, str] | None = None) -> ManagedReconciler:
    """Wire connectors, cache and region tree. Call ``await result.aclose()`` when done."""
    env = os.environ if env is None else env
    quiet_http_loggers()
    client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=False)
    tree = RegionTree.load()
    cache = Cache.from_env()
    checker = EditionChecker(
        client,
        cache,
        mailto=env.get("ONCOBUDDY_EMAIL"),
        enabled=env.get("ONCOBUDDY_EDITION_CHECK", "on").strip().lower() != "off",
    )
    connectors = [
        EmaConnector(client),
        PubMedConnector(
            client,
            email=env.get("ONCOBUDDY_EMAIL"),
            api_key=env.get("NCBI_API_KEY"),
            condition_terms=icd.condition_terms,
        ),
        TrialsConnector(client, tree, condition_terms=icd.condition_terms),
        IcdConnector(),
        GuidelineConnector(checker=checker),
        InfarmedPointerConnector("authorisation"),
        InfarmedPointerConnector("reimbursement"),
    ]
    reconciler = ManagedReconciler(connectors, tree, cache=cache, timeout_s=_RECONCILER_TIMEOUT_S)
    reconciler.client = client
    reconciler.cache = cache
    return reconciler


def profile_input_schema() -> dict[str, Any]:
    """Closed ClinicalProfile schema with explicit patterns on every string-typed field.

    The model validators enforce these shapes (normalise.py, profile.py); the patterns make
    them visible to agents so no property reads as free text.
    """
    schema = copy.deepcopy(ClinicalProfile.model_json_schema())
    props = schema["properties"]
    props["cancer_type"]["pattern"] = (
        r"^\s*(?:[A-Za-z][0-9]{2}[0-9A-Za-z]?(?:\.[0-9A-Za-z]{1,4})?|[0-9]{4}/[0-9])\s*$"
    )
    props["cancer_type"]["description"] = "ICD-10 or ICD-O-3 code, e.g. C50.9"
    for option in props["prior_lines"]["anyOf"]:
        if option.get("type") == "array":
            option["items"]["pattern"] = r"^\s*[A-Za-z][A-Za-z-]{1,39}\s*$"
    props["prior_lines"]["description"] = (
        "INN drug names of earlier therapy; [] means none, omit if unknown"
    )
    props["biomarkers"]["propertyNames"] = {"pattern": r"^[A-Z0-9_-]{1,20}$"}
    props["biomarkers"]["description"] = vocabulary_description()
    return schema


class OncoTools:
    """Tool implementations. The reconciler is built lazily so tests can inject fakes."""

    def __init__(self, reconciler: Reconciler | None = None) -> None:
        self._reconciler = reconciler
        self._records: OrderedDict[str, tuple[Record, str, list[str]]] = OrderedDict()

    def reconciler(self) -> Reconciler:
        if self._reconciler is None:
            self._reconciler = build_reconciler()
        return self._reconciler

    async def aclose(self) -> None:
        if isinstance(self._reconciler, ManagedReconciler):
            await self._reconciler.aclose()
            self._reconciler = None

    # -- helpers ---------------------------------------------------------------------

    def _remember(self, records: list[Record], region: str, chain: list[str]) -> None:
        for record in records:
            self._records[record.id] = (record, region, list(chain))
            self._records.move_to_end(record.id)
        while len(self._records) > RECORD_LRU_SIZE:
            self._records.popitem(last=False)

    def _unknown_region(self) -> dict[str, Any]:
        return {
            "error": {
                "code": "unknown_region",
                "valid_regions": self.reconciler().tree.regions,
            },
            "notice": NOTICE,
        }

    def _chain(self, region: object) -> list[str] | None:
        if not isinstance(region, str):
            return None
        try:
            return self.reconciler().tree.scope_chain(region)
        except UnknownRegion:
            return None

    @staticmethod
    def _envelope(region: str, chain: list[str], **payload: Any) -> dict[str, Any]:
        return {
            "notice": NOTICE,
            "agent_protocol": AGENT_PROTOCOL,
            "query_region": region,
            "scope_chain": chain,
            **payload,
        }

    async def _section_tool(
        self, name: str, region: str, *, drug: str | None, condition: str | None
    ) -> dict[str, Any]:
        chain = self._chain(region)
        if chain is None:
            return self._unknown_region()
        section: Section = await self.reconciler().section(
            name, drug=drug, condition=condition, region=region
        )
        self._remember([*section.records, *section.reference_elsewhere], region, chain)
        return self._envelope(region, chain, section=section.model_dump(mode="json"))

    # -- tools -----------------------------------------------------------------------

    async def regional_status(self, drug: str, indication: str, region: str) -> dict[str, Any]:
        """Per-section status (authorisation, reimbursement, guidelines, trials, literature,
        coding) of a drug (INN name) for an indication (ICD-10/ICD-O-3 code), limited to the
        region's scope chain (e.g. PT, EU, global). Each section carries records with
        provenance, or an explicit no_data reason. Portugal results include a link-only
        INFARMED pointer, not a retrieved status; no_data can be set alongside it."""
        chain = self._chain(region)
        if chain is None:
            return self._unknown_region()
        status = await self.reconciler().regional_status(drug, indication, region)
        for section in status.sections.values():
            self._remember([*section.records, *section.reference_elsewhere], region, chain)
        return self._envelope(
            region,
            chain,
            sections={k: v.model_dump(mode="json") for k, v in status.sections.items()},
        )

    async def guidelines_for(self, condition: str, region: str) -> dict[str, Any]:
        """Curated guideline links for a condition (ICD-10/ICD-O-3 code) in a region's
        scope chain. Seed guidelines are scope-only links, not recommendations."""
        return await self._section_tool("guidelines", region, drug=None, condition=condition)

    async def find_trials(self, drug: str, condition: str, region: str) -> dict[str, Any]:
        """Recruiting clinical trials for a drug (INN name, required) and condition
        (ICD-10/ICD-O-3 code) in a country region such as PT. EU-level queries return no
        trials because trials are registered per country."""
        return await self._section_tool("trials", region, drug=drug, condition=condition)

    async def treatment_options(self, profile: Any, region: str) -> dict[str, Any]:
        """Evidence-grounded options for a structured, non-identifying clinical profile
        (closed schema; no free text). Every option cites a guideline record. In v1 the seed
        guidelines are scope-only, so results are guideline links, not named interventions."""
        try:
            parsed = parse_profile(profile)
        except ProfileError as exc:
            # ProfileError text holds field paths and error types only.
            return {"error": {"code": "invalid_profile", "detail": str(exc)}, "notice": NOTICE}
        chain = self._chain(region)
        if chain is None:
            return self._unknown_region()
        result = await treatment_options(parsed, region, self.reconciler())
        records: list[Record] = [*result.guidelines_in_scope, *result.reference_elsewhere]
        for option in result.options:
            records.append(option.guideline)
            records.extend(option.authorisation.records)
            records.extend(option.trials.records)
            records.extend(option.reimbursement.records)
        self._remember(records, region, chain)
        return result.model_dump(mode="json")

    async def get_medicine(self, drug: str, region: str) -> dict[str, Any]:
        """Marketing authorisation records for a drug (INN name) in a region's scope chain.
        EMA covers centrally authorised products only. Portugal results include a link-only
        INFARMED pointer, not a retrieved status."""
        return await self._section_tool("authorisation", region, drug=drug, condition=None)

    async def search_literature(
        self, drug: str, condition: str | None = None, region: str = "global"
    ) -> dict[str, Any]:
        """PubMed publication links (no abstracts) for a drug (INN name), optionally
        narrowed by a condition code."""
        return await self._section_tool("literature", region, drug=drug, condition=condition)

    async def lookup_code(self, code_or_term: str) -> dict[str, Any]:
        """Resolve an ICD-10/ICD-O-3 code or exact site name to a coding record."""
        chain = self._chain("global")
        assert chain is not None
        records = icd.lookup_code(code_or_term)
        self._remember(records, "global", chain)
        section = Section(records=records, no_data=None if records else NoData(reason="not_found"))
        return self._envelope("global", chain, section=section.model_dump(mode="json"))

    async def get_record(self, id: str) -> dict[str, Any]:
        """Return a record returned earlier in this session by its id. Never fetches."""
        hit = self._records.get(id) if isinstance(id, str) else None
        if hit is None:
            return {"error": {"code": "unknown_record"}, "notice": NOTICE}
        self._records.move_to_end(id)
        record, region, chain = hit
        return self._envelope(region, chain, record=record.model_dump(mode="json"))


def _scrubbed(code: str, detail: str) -> CallToolResult:
    payload = {"error": {"code": code, "detail": detail}, "notice": NOTICE}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
        structured_content=payload,
        is_error=True,
    )


def _validation_detail(exc: ValidationError) -> str:
    """Argument names and pydantic error types only: never input, msg, ctx or url."""
    parts = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = error["loc"]
        parts.append(f"{loc[0] if loc else '<root>'}: {error['type']}")
    return "; ".join(dict.fromkeys(parts))


def _install_scrubbing(server: MCPServer) -> None:
    """Wrap the single dispatch point every tools/call passes through."""
    manager = server._tool_manager
    original = manager.call_tool

    async def call_tool(name, arguments, context, convert_result=False):
        if manager.get_tool(name) is None:
            return _scrubbed("unknown_tool", "unknown tool")
        try:
            return await original(name, arguments, context, convert_result=convert_result)
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001 - scrubbed catch-all at the SDK boundary
            cause = exc if isinstance(exc, ValidationError) else exc.__cause__
            if isinstance(cause, ValidationError):
                return _scrubbed("invalid_arguments", _validation_detail(cause))
            logging.getLogger("oncobuddy").warning("tool failed: %s", type(cause or exc).__name__)
            return _scrubbed("internal_error", "unexpected error")

    manager.call_tool = call_tool  # type: ignore[method-assign]


def create_server(tools: OncoTools | None = None) -> MCPServer:
    tools = tools or OncoTools()

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await tools.aclose()

    server = MCPServer(
        "oncobuddy",
        instructions="Region-tagged, provenance-stamped oncology data for clinicians. " + NOTICE,
        lifespan=lifespan,
        log_level="WARNING",
    )
    for fn in (
        tools.regional_status,
        tools.guidelines_for,
        tools.find_trials,
        tools.treatment_options,
        tools.get_medicine,
        tools.search_literature,
        tools.lookup_code,
        tools.get_record,
    ):
        server.add_tool(fn)
    # Expose the closed profile schema. The function takes ``Any`` so the SDK never
    # validates (and never echoes) the profile itself; parse_profile does.
    tool = server._tool_manager.get_tool("treatment_options")
    tool.parameters["properties"]["profile"] = profile_input_schema()
    tool.parameters["required"] = ["profile", "region"]
    _install_scrubbing(server)
    return server


def _configure_logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    quiet_http_loggers()


def main() -> None:
    _configure_logging()
    create_server().run("stdio")


def clear_cache_main() -> None:
    if os.environ.get("ONCOBUDDY_CACHE") == "memory":
        print("Cache is in memory mode; nothing is stored on disk.")
        return
    path = default_cache_path()
    if not path.exists():
        print(f"no cache found at {path}")
        return
    cache = Cache.from_env()
    try:
        cache.clear()
    finally:
        cache.close()
    print("oncoBuddy cache cleared.")

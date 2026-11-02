"""Reconciler: fan out to connectors, filter to the scope chain, and report no-data reasons.

Every section in V1_SECTIONS always appears in the result. An empty section always carries
an explicit NoData reason; a source failure is never reported as not_found.
"""

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field

from oncobuddy.connectors.base import Connector
from oncobuddy.core.cache import Cache, cache_key
from oncobuddy.core.normalise import normalise_condition, normalise_drug
from oncobuddy.core.query import Query
from oncobuddy.core.records import Record
from oncobuddy.core.regions import RegionTree

NOTICE = (
    "Decision support for a qualified clinician; not a substitute for clinical judgement. "
    "Verify against the cited source before acting."
)
logger = logging.getLogger("oncobuddy.reconciler")

POINTER_ONLY_NOTE = (
    "INFARMED: not retrieved by oncoBuddy; read the status in the user's browser (see access)"
)
PER_PRODUCT_NOTE = (
    "authorisation is per product; indication not checked - see the product page link"
)
V1_SECTIONS = ("authorisation", "reimbursement", "guidelines", "trials", "literature", "coding")


class NoData(BaseModel):
    reason: Literal["not_found", "source_unavailable", "out_of_scope_v1"]


class Section(BaseModel):
    """Records plus an optional no_data reason.

    ``no_data`` describes the real (retrieved) sources only: records may be link-only
    pointers even when no_data is set.
    """

    records: list[Record] = Field(default_factory=list)
    no_data: NoData | None = None
    notes: list[str] = Field(default_factory=list)
    reference_elsewhere: list[Record] = Field(default_factory=list)


class RegionalStatus(BaseModel):
    query_region: str
    scope_chain: list[str]
    sections: dict[str, Section]
    notice: str


def _specificity(record: Record) -> int:
    """Guidelines with fewer condition categories sort first; other kinds are unranked."""
    return len(record.entities.conditions) if record.kind == "guideline" else 0


def reference_note(count: int, regions: list[str], query_region: str) -> str:
    return (
        f"{count} reference guideline(s) issued outside {query_region}'s scope "
        f"({', '.join(regions)}) are listed separately; "
        "national approval, reimbursement and practice may differ"
    )


def _edition_status(record: Record) -> str | None:
    check = record.payload.get("edition_check")
    return check.get("status") if isinstance(check, dict) else None


def _edition_notes(records: list[Record]) -> list[str]:
    counts = {"newer_found": 0, "corrected": 0, "retracted": 0, "unknown": 0}
    for record in records:
        status = _edition_status(record)
        if status in counts:
            counts[status] += 1
    notes: list[str] = []
    if counts["newer_found"] or counts["corrected"] or counts["retracted"]:
        notes.append(
            f"edition check: {counts['newer_found']} newer edition(s), "
            f"{counts['corrected']} corrected, {counts['retracted']} retracted"
        )
    if counts["unknown"]:
        notes.append(f"edition check unavailable for {counts['unknown']} guideline(s)")
    return notes


def _is_reference(record: Record) -> bool:
    return record.kind == "guideline" and record.payload.get("reference_only") is True


class _Skipped:
    """Outcome marker for a connector that does not serve the queried region."""

    def __init__(self, note: str) -> None:
        self.note = note


class Reconciler:
    def __init__(
        self,
        connectors: list[Connector],
        tree: RegionTree,
        cache: Cache | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        for connector in connectors:
            if connector.section not in V1_SECTIONS:
                raise ValueError(
                    f"connector {connector.name!r} has unknown section {connector.section!r}"
                )
        self._connectors = list(connectors)
        self._tree = tree
        self._cache = cache
        self._timeout_s = timeout_s

    async def regional_status(self, drug: str, indication: str, region: str) -> RegionalStatus:
        chain = self._tree.scope_chain(region)  # UnknownRegion propagates to the caller
        drug_key = normalise_drug(drug)
        condition_key = normalise_condition(indication)
        if drug_key is None or condition_key is None:
            sections = {name: Section(no_data=NoData(reason="not_found")) for name in V1_SECTIONS}
            return RegionalStatus(
                query_region=region, scope_chain=chain, sections=sections, notice=NOTICE
            )

        outcomes = await self._run(self._connectors, drug_key, condition_key, region)

        sections: dict[str, Section] = {}
        for name in V1_SECTIONS:
            members = [
                (connector, outcome)
                for connector, outcome in zip(self._connectors, outcomes)
                if connector.section == name
            ]
            if not members:
                sections[name] = Section(no_data=NoData(reason="out_of_scope_v1"))
            else:
                sections[name] = self._build_section(members, chain, region)
        return RegionalStatus(
            query_region=region, scope_chain=chain, sections=sections, notice=NOTICE
        )

    @property
    def tree(self) -> RegionTree:
        return self._tree

    async def section(
        self, name: str, *, drug: str | None, condition: str | None, region: str
    ) -> Section:
        """Fetch a single section; drug and condition are optional but must normalise if given."""
        chain = self._tree.scope_chain(region)  # UnknownRegion propagates to the caller
        if name not in V1_SECTIONS:
            raise ValueError(f"unknown section {name!r}")
        drug_key = None if drug is None else normalise_drug(drug)
        condition_key = None if condition is None else normalise_condition(condition)
        if (drug is not None and drug_key is None) or (
            condition is not None and condition_key is None
        ):
            return Section(no_data=NoData(reason="not_found"))
        members_c = [c for c in self._connectors if c.section == name]
        if not members_c:
            return Section(no_data=NoData(reason="out_of_scope_v1"))
        outcomes = await self._run(members_c, drug_key, condition_key, region)
        return self._build_section(list(zip(members_c, outcomes)), chain, region)

    async def _run(
        self, connectors: list[Connector], drug: str | None, condition: str | None, region: str
    ) -> list[object]:
        """One outcome per connector; connectors that do not serve the region are not called."""
        outcomes: list[object] = [None] * len(connectors)
        tasks: list[asyncio.Task] = []
        slots: list[int] = []
        for index, connector in enumerate(connectors):
            supports = getattr(connector, "supports_region", None)
            if supports is not None and not supports(region):
                hint = getattr(connector, "region_hint", None) or "not available for this region"
                outcomes[index] = _Skipped(
                    f"{connector.name}: not searched for region {region}; {hint}"
                )
                continue
            slots.append(index)
            tasks.append(asyncio.create_task(self._fetch(connector, drug, condition, region)))
        for index, outcome in zip(slots, await self._collect(tasks)):
            outcomes[index] = outcome
        return outcomes

    async def _fetch(
        self, connector: Connector, drug: str | None, condition: str | None, region: str
    ) -> list[Record]:
        query = Query(drug=drug, condition=condition, region=region)
        key = (
            self._cache_key(drug, condition, region)
            if getattr(connector, "cacheable", True)
            else None
        )
        if key is not None:
            cached = self._cache_get(connector.name, key)
            if cached is not None:
                return cached
        records = await connector.fetch(query)
        if key is not None:
            self._cache_put(connector.name, key, records)
        return records

    async def _collect(self, tasks: list[asyncio.Task]) -> list[object]:
        """Return one outcome per task: a result, an exception, or a timeout marker.

        Tasks still pending after timeout_s are cancelled but not awaited, so a connector
        that ignores cancellation cannot stall the response.
        """
        if not tasks:
            return []
        try:
            done, _ = await asyncio.wait(tasks, timeout=self._timeout_s)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
        outcomes: list[object] = []
        for task in tasks:
            if task not in done:
                task.cancel()
                outcomes.append(TimeoutError(f"connector exceeded {self._timeout_s}s"))
            elif task.cancelled():
                outcomes.append(asyncio.CancelledError())
            else:
                error = task.exception()
                outcomes.append(error if error is not None else task.result())
        return outcomes

    def _build_section(
        self,
        members: list[tuple[Connector, object]],
        chain: list[str],
        region: str,
    ) -> Section:
        records: list[Record] = []
        reference: list[Record] = []
        notes: list[str] = []
        failed = False
        skipped = 0
        for connector, outcome in members:
            if isinstance(outcome, _Skipped):
                skipped += 1
                notes.append(outcome.note)
                continue
            if isinstance(outcome, BaseException):
                failed = True
                notes.append(f"{connector.name} unavailable")
                # Name and exception type only: messages may carry request details.
                logger.warning("connector %s failed: %s", connector.name, type(outcome).__name__)
                continue
            for r in outcome:
                if self._tree.eligible(r.region, region):
                    records.append(r)
                elif connector.section == "guidelines" and _is_reference(r):
                    reference.append(r)

        pointers = [r for r in records if r.kind == "pointer"]
        real = [r for r in records if r.kind != "pointer"]
        position = {region_id: index for index, region_id in enumerate(chain)}
        real.sort(
            key=lambda r: (
                position[r.region],
                r.kind == "guideline" and _edition_status(r) == "retracted",
                _specificity(r),
            )
        )
        pointers.sort(key=lambda r: position[r.region])
        no_data: NoData | None = None
        if not real:
            reason: Literal["not_found", "source_unavailable", "out_of_scope_v1"]
            if failed:
                reason = "source_unavailable"
            elif skipped == len(members):
                reason = "out_of_scope_v1"
            else:
                reason = "not_found"
            no_data = NoData(reason=reason)
            if pointers:
                notes.append(POINTER_ONLY_NOTE)
        else:
            # Pointers carry no data: they never count as a record for the target region.
            if real[0].region != region:
                notes.insert(0, f"no {region} record; showing {real[0].region}-level")
            if members[0][0].section == "authorisation":
                notes.append(PER_PRODUCT_NOTE)
        if members and members[0][0].section == "guidelines":
            notes.extend(_edition_notes(real))
        # sorted() is stable: ties keep original order
        reference.sort(key=lambda r: (r.region, len(r.entities.conditions)))
        if reference:
            regions = sorted({r.region for r in reference})
            notes.append(reference_note(len(reference), regions, region))
        return Section(
            records=[*real, *pointers],
            no_data=no_data,
            notes=notes,
            reference_elsewhere=reference,
        )

    def _cache_key(self, drug: str | None, condition: str | None, region: str) -> str | None:
        if self._cache is None:
            return None
        try:
            return cache_key(drug=drug, condition=condition, region=region)
        except ValueError:
            return None

    def _cache_get(self, source: str, key: str) -> list[Record] | None:
        try:
            return self._cache.get(source, key)
        except ValueError:
            return None

    def _cache_put(self, source: str, key: str, records: list[Record]) -> None:
        try:
            self._cache.put(source, key, records)
        except ValueError:
            pass

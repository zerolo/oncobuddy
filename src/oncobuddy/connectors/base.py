"""Connector protocol shared by all source adapters."""

from typing import Protocol

from oncobuddy.core.query import Query
from oncobuddy.core.records import Record


class SourceUnavailable(Exception):
    """Raised by a connector when its source cannot be reached or answered."""


class Connector(Protocol):
    name: str
    section: str

    async def fetch(self, query: Query) -> list[Record]: ...

    # Optional members, read with getattr by the reconciler (absent means the default):
    #   supports_region(region: str) -> bool   default True; when False the connector is not
    #       called and its section reports out_of_scope_v1 with a note.
    #   region_hint: str   short wording for that note ("not available for this region").
    #   cacheable: bool    default True; False for local curated sources, which must always
    #       be read fresh and never stored in the disk cache.

"""Query passed from the reconciler to each connector."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Query:
    drug: str | None
    condition: str | None
    region: str
    filters: dict[str, str] = field(default_factory=dict)

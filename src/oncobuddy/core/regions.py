"""Region tree: parent-pointer hierarchy and the scope chain used to filter records."""

from importlib.resources import files
from pathlib import Path

import yaml


class UnknownRegion(ValueError):
    """Raised when a region ID is not present in the tree."""


def _id_type_message(value: object, what: str) -> str:
    return (
        f"{what} {value!r} must be a non-empty string; quote region IDs in the YAML "
        f'(e.g. "NO": EU), because unquoted NO/ON/YES/OFF/Y/N are read as booleans'
    )


class RegionTree:
    def __init__(self, parents: dict[str, str | None]) -> None:
        self._parents = parents

    @classmethod
    def load(cls, path: Path | None = None) -> "RegionTree":
        if path is None:
            text = files("oncobuddy").joinpath("regions.yaml").read_text(encoding="utf-8")
        else:
            text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict) or not isinstance(data.get("regions"), dict):
            raise TypeError("regions file must contain a top-level 'regions' mapping")
        parents = dict(data["regions"])
        cls._validate(parents)
        return cls(parents)

    @staticmethod
    def _validate(parents: dict[str, str | None]) -> None:
        for region, parent in parents.items():
            if not isinstance(region, str) or not region:
                raise ValueError(_id_type_message(region, "region ID"))
            if parent is not None and (not isinstance(parent, str) or not parent):
                raise ValueError(_id_type_message(parent, f"parent of region {region!r}"))
        for region, parent in parents.items():
            if parent is not None and parent not in parents:
                raise ValueError(f"region {region!r} has unknown parent {parent!r}")
        roots = [region for region, parent in parents.items() if parent is None]
        if len(roots) != 1:
            raise ValueError(f"expected exactly one root region, found {len(roots)}: {roots}")
        for region in parents:
            seen: set[str] = set()
            current: str | None = region
            while current is not None:
                if current in seen:
                    raise ValueError(f"cycle in region tree involving {region!r}")
                seen.add(current)
                current = parents[current]

    @property
    def regions(self) -> list[str]:
        """All known region IDs, sorted."""
        return sorted(self._parents)

    def scope_chain(self, region: str) -> list[str]:
        """Return the region followed by its ancestors up to the root."""
        if region not in self._parents:
            raise UnknownRegion(
                f"unknown region {region!r}; valid regions: {', '.join(sorted(self._parents))}"
            )
        chain: list[str] = []
        current: str | None = region
        while current is not None:
            chain.append(current)
            current = self._parents[current]
        return chain

    def eligible(self, record_region: str, target: str) -> bool:
        """A record is eligible for a target only if its region is in the target's chain."""
        return record_region in self.scope_chain(target)

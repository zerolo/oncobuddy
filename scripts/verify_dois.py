"""Alias kept for the old name: runs the registry verifier."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_registry


def main(argv: list[str] | None = None) -> int:
    return verify_registry.main(argv)


if __name__ == "__main__":
    sys.exit(main())

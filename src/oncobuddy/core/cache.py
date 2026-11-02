"""Privacy-conscious SQLite cache for API responses.

Only API responses are cached, never an ingested copy of a source. Cache keys are built
from normalised entities (INN, ICD code, region) and never from free text.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter

from oncobuddy.core.records import Record

DEFAULT_TTLS: dict[str, int] = {
    "EMA": 86400,
    "PubMed": 86400,
    "ClinicalTrials.gov": 3600,
    "Crossref-edition": 7 * 86400,
}

_VALUE = r"[A-Za-z0-9._:/+-]{1,64}"
_KEY_VALUE = re.compile(_VALUE)
_KEY = re.compile(
    r"(drug|condition|region)=" + _VALUE + r"(\|(drug|condition|region)=" + _VALUE + r")*"
)
_SOURCE = re.compile(r"[A-Za-z0-9._ -]{1,40}")
_RECORDS = TypeAdapter(list[Record])
_CACHE_FILE = "cache.sqlite3"
_SIDECARS = ("-wal", "-shm", "-journal")


def _checked(name: str, value: str) -> str:
    if not isinstance(value, str) or not _KEY_VALUE.fullmatch(value):
        raise ValueError(f"invalid cache key {name} value: {value!r}")
    return value


def _check_key(key: str) -> None:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError(f"invalid cache key: {key!r}")


def _check_source(source: str) -> None:
    if not isinstance(source, str) or not _SOURCE.fullmatch(source):
        raise ValueError(f"invalid cache source: {source!r}")


def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
    """Only real corruption justifies deleting the file; locks and misuse do not."""
    return not isinstance(exc, (sqlite3.OperationalError, sqlite3.ProgrammingError))


def cache_key(
    *,
    drug: str | None = None,
    condition: str | None = None,
    region: str | None = None,
) -> str:
    """Build a cache key from normalised entities only; free text is rejected."""
    parts: list[str] = []
    if drug is not None:
        parts.append(f"drug={_checked('drug', drug).lower()}")
    if condition is not None:
        parts.append(f"condition={_checked('condition', condition)}")
    if region is not None:
        parts.append(f"region={_checked('region', region)}")
    if not parts:
        raise ValueError("cache_key needs at least one of drug, condition, region")
    return "|".join(parts)


def default_cache_path() -> Path:
    """Return the cache file path in the OS user cache directory."""
    override = os.environ.get("ONCOBUDDY_CACHE_DIR")
    if override:
        directory = Path(override)
    elif sys.platform == "darwin":
        directory = Path.home() / "Library" / "Caches" / "oncobuddy"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        directory = base / "oncobuddy" / "Cache"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".cache"
        directory = base / "oncobuddy"
    return directory / _CACHE_FILE


class Cache:
    """Cache of record lists keyed by (source, key). ``path=None`` means in-memory."""

    def __init__(
        self,
        path: Path | None,
        ttl_seconds: dict[str, int],
        clock: Callable[[], float] = time.time,
        default_ttl_seconds: int = 3600,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._path = path
        self._ttls = dict(ttl_seconds)
        self._clock = clock
        self._default_ttl = default_ttl_seconds
        self._max_bytes = max_bytes
        self._conn: sqlite3.Connection | None = None
        self._closed = False
        if path is not None:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self._conn = self._connect()
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc):
                raise
            self._reset()

    def _connect(self) -> sqlite3.Connection:
        target = ":memory:" if self._path is None else str(self._path)
        conn = sqlite3.connect(target, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            if self._path is not None:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                " source TEXT NOT NULL,"
                " key TEXT NOT NULL,"
                " stored_at REAL NOT NULL,"
                " expires_at REAL NOT NULL,"
                " records_json TEXT NOT NULL,"
                " PRIMARY KEY (source, key))"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS entries_stored_at ON entries(stored_at)")
        except sqlite3.DatabaseError:
            conn.close()
            raise
        if self._path is not None and os.name == "posix":
            for name in (str(self._path), *(f"{self._path}{s}" for s in _SIDECARS)):
                if os.path.exists(name):
                    os.chmod(name, 0o600)
        return conn

    def _reset(self) -> None:
        """Drop a corrupt cache file (or memory contents) and open a fresh one."""
        if self._conn is not None:
            self._conn.close()
        if self._path is not None:
            for suffix in ("", *_SIDECARS):
                Path(f"{self._path}{suffix}").unlink(missing_ok=True)
        self._conn = self._connect()

    def _run(self, operation: Callable[[], object]) -> object:
        """Run a database operation; on real corruption, reset once and retry."""
        try:
            return operation()
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc):
                raise
            self._reset()
            return operation()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("cache is closed")

    def get(self, source: str, key: str) -> list[Record] | None:
        self._ensure_open()
        _check_source(source)
        _check_key(key)

        def read() -> list[Record] | None:
            row = self._conn.execute(
                "SELECT expires_at, records_json FROM entries WHERE source = ? AND key = ?",
                (source, key),
            ).fetchone()
            if row is None:
                return None
            expires_at, payload = row
            if self._clock() >= expires_at:
                self._delete(source, key)
                return None
            try:
                return _RECORDS.validate_json(payload)
            except ValueError:
                self._delete(source, key)
                return None

        return self._run(read)  # type: ignore[return-value]

    def put(self, source: str, key: str, records: list[Record]) -> None:
        self._ensure_open()
        _check_source(source)
        _check_key(key)
        now = self._clock()
        ttl = self._ttls.get(source, self._default_ttl)
        raw = _RECORDS.dump_json(records)
        if len(raw) > self._max_bytes:
            # Best-effort cache: skip the oversized entry, evict nothing. Drop any older
            # value under this key so a stale answer is not served after a refresh.
            self._run(lambda: self._delete(source, key))
            return
        payload = raw.decode("utf-8")

        def write() -> None:
            self._conn.execute(
                "INSERT OR REPLACE INTO entries"
                " (source, key, stored_at, expires_at, records_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (source, key, now, now + ttl, payload),
            )
            self._evict_to_cap()

        self._run(write)

    def clear(self) -> None:
        self._ensure_open()
        self._run(lambda: self._conn.execute("DELETE FROM entries"))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._conn is not None:
            self._conn.close()

    @classmethod
    def from_env(cls) -> Cache:
        if os.environ.get("ONCOBUDDY_CACHE") == "memory":
            return cls(None, DEFAULT_TTLS)
        return cls(default_cache_path(), DEFAULT_TTLS)

    def _delete(self, source: str, key: str) -> None:
        self._conn.execute("DELETE FROM entries WHERE source = ? AND key = ?", (source, key))

    def _live_bytes(self) -> int:
        # Free pages left by deletes are reused by later inserts, so count only live pages.
        page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
        return (page_count - freelist) * page_size

    def _evict_to_cap(self) -> None:
        while self._live_bytes() > self._max_bytes:
            cursor = self._conn.execute(
                "DELETE FROM entries WHERE rowid IN"
                " (SELECT rowid FROM entries ORDER BY stored_at ASC LIMIT 1)"
            )
            if cursor.rowcount == 0:
                break

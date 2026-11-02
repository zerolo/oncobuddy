import os
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, date, datetime

import pytest

from oncobuddy.core.cache import Cache, cache_key, default_cache_path
from oncobuddy.core.records import Entities, Record


def _record(record_id: str = "rec-1") -> Record:
    return Record(
        id=record_id,
        kind="medicine",
        entities=Entities(drugs=["trastuzumab"]),
        region="PT",
        source="infarmed",
        source_url=f"https://example.test/{record_id}",
        valid_as_of=date(2026, 10, 1),
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        licence="open",
        payload={"name": "Herceptin"},
    )


class FakeClock:
    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


TTLS = {"EMA": 100}


def test_roundtrip(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3", TTLS)
    try:
        cache.put("EMA", "drug=trastuzumab", [_record()])
        assert cache.get("EMA", "drug=trastuzumab") == [_record()]
        assert cache.get("EMA", "drug=other") is None
    finally:
        cache.close()


def test_expired_entry_returns_none(tmp_path):
    clock = FakeClock()
    cache = Cache(tmp_path / "cache.sqlite3", TTLS, clock=clock)
    try:
        cache.put("EMA", "drug=trastuzumab", [_record()])
        clock.now += 99
        assert cache.get("EMA", "drug=trastuzumab") is not None
        clock.now += 1  # now == expires_at, which counts as expired
        assert cache.get("EMA", "drug=trastuzumab") is None
    finally:
        cache.close()


def test_free_text_key_rejected():
    with pytest.raises(ValueError):
        cache_key(condition="breast cancer her2 patient john")


def test_memory_mode_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = Cache(None, TTLS)
    try:
        cache.put("EMA", "drug=trastuzumab", [_record()])
        assert cache.get("EMA", "drug=trastuzumab") == [_record()]
    finally:
        cache.close()
    assert list(tmp_path.iterdir()) == []


def test_clear(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3", TTLS)
    try:
        cache.put("EMA", "drug=trastuzumab", [_record()])
        cache.clear()
        assert cache.get("EMA", "drug=trastuzumab") is None
    finally:
        cache.close()


def test_two_connections_same_file_wal(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = Cache(path, TTLS)
    second = Cache(path, TTLS)
    try:
        first.put("EMA", "drug=trastuzumab", [_record("a")])
        second.put("EMA", "drug=pertuzumab", [_record("b")])  # no "database is locked"
        assert first.get("EMA", "drug=pertuzumab") == [_record("b")]
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        first.close()
        second.close()


def test_corrupt_file_is_recreated(tmp_path):
    path = tmp_path / "cache.sqlite3"
    path.write_bytes(b"this is not a sqlite database" * 200)
    cache = Cache(path, TTLS)
    try:
        cache.put("EMA", "drug=trastuzumab", [_record()])
        assert cache.get("EMA", "drug=trastuzumab") == [_record()]
    finally:
        cache.close()


def test_size_cap_drops_oldest_rows(tmp_path):
    clock = FakeClock()
    cache = Cache(tmp_path / "cache.sqlite3", TTLS, clock=clock, max_bytes=64 * 1024)
    try:
        for i in range(200):
            clock.now += 1
            cache.put("EMA", f"drug=d{i}", [_record(f"r{i}")])
        assert cache.get("EMA", "drug=d0") is None  # oldest evicted
        assert cache.get("EMA", "drug=d199") is not None  # newest kept
    finally:
        cache.close()


def test_cache_key_fixed_order_and_lowercases_drug_only():
    key = cache_key(region="PT-LVT", condition="C50.9", drug="Trastuzumab")
    assert key == "drug=trastuzumab|condition=C50.9|region=PT-LVT"
    assert cache_key(region="PT") == "region=PT"


def test_cache_key_requires_a_part():
    with pytest.raises(ValueError):
        cache_key()


def test_cache_key_rejects_long_value():
    with pytest.raises(ValueError):
        cache_key(drug="a" * 65)


def test_default_cache_path_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOBUDDY_CACHE_DIR", str(tmp_path))
    assert default_cache_path() == tmp_path / "cache.sqlite3"


def test_from_env_memory_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOBUDDY_CACHE", "memory")
    monkeypatch.setenv("ONCOBUDDY_CACHE_DIR", str(tmp_path / "unused"))
    cache = Cache.from_env()
    try:
        cache.put("EMA", "drug=trastuzumab", [_record()])
        assert cache.get("EMA", "drug=trastuzumab") == [_record()]
    finally:
        cache.close()
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize(
    "bad_key",
    [
        "breast cancer her2 patient john",
        "drug=trastuzumab\n",
        "drug=trastuzumаb",  # Cyrillic a lookalike
        "drug=",
        "drug=trastuzumab|",
        "region=PT ",
    ],
)
def test_put_and_get_reject_non_key_strings(tmp_path, bad_key):
    cache = Cache(tmp_path / "cache.sqlite3", TTLS)
    try:
        with pytest.raises(ValueError):
            cache.put("EMA", bad_key, [_record()])
        with pytest.raises(ValueError):
            cache.get("EMA", bad_key)
    finally:
        cache.close()


@pytest.mark.parametrize("source", ["", "EMA;DROP", "x" * 41, "EMA\n"])
def test_put_and_get_reject_bad_source(tmp_path, source):
    cache = Cache(tmp_path / "cache.sqlite3", TTLS)
    try:
        with pytest.raises(ValueError):
            cache.put(source, "drug=trastuzumab", [_record()])
        with pytest.raises(ValueError):
            cache.get(source, "drug=trastuzumab")
    finally:
        cache.close()


def test_cache_key_output_roundtrips_through_put_get(tmp_path):
    key = cache_key(drug="Trastuzumab", condition="C50.9", region="PT-LVT")
    cache = Cache(tmp_path / "cache.sqlite3", TTLS)
    try:
        cache.put("ClinicalTrials.gov", key, [_record()])
        assert cache.get("ClinicalTrials.gov", key) == [_record()]
    finally:
        cache.close()


def test_use_after_close_does_not_delete_shared_file(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = Cache(path, TTLS)
    second = Cache(path, TTLS)
    try:
        first.put("EMA", "drug=trastuzumab", [_record()])
        second.close()
        with pytest.raises(RuntimeError, match="cache is closed"):
            second.get("EMA", "drug=trastuzumab")
        with pytest.raises(RuntimeError, match="cache is closed"):
            second.put("EMA", "drug=trastuzumab", [_record()])
        with pytest.raises(RuntimeError, match="cache is closed"):
            second.clear()
        assert path.exists()
        assert first.get("EMA", "drug=trastuzumab") == [_record()]
    finally:
        first.close()


def test_oversized_record_is_skipped_and_others_survive(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3", TTLS, max_bytes=64 * 1024)
    try:
        cache.put("EMA", "drug=small1", [_record("s1")])
        cache.put("EMA", "drug=small2", [_record("s2")])
        huge = _record("huge").model_copy(update={"payload": {"blob": "x" * 100_000}})
        cache.put("EMA", "drug=huge", [huge])
        assert cache.get("EMA", "drug=huge") is None
        assert cache.get("EMA", "drug=small1") == [_record("s1")]
        assert cache.get("EMA", "drug=small2") == [_record("s2")]
    finally:
        cache.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_cache_dir_and_file_are_private(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOBUDDY_CACHE_DIR", str(tmp_path / "fresh"))
    path = default_cache_path()
    cache = Cache(path, TTLS)
    try:
        cache.put("EMA", "drug=trastuzumab", [_record()])
    finally:
        cache.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_every_v1_connector_name_has_a_ttl():
    from oncobuddy.core.cache import DEFAULT_TTLS

    for name in ("EMA", "PubMed", "ClinicalTrials.gov"):
        assert name in DEFAULT_TTLS
    # Local curated connectors are never cached, so they carry no TTL.
    # The edition checker caches Crossref answers; curated sources still have no entry here.
    assert set(DEFAULT_TTLS) == {"EMA", "PubMed", "ClinicalTrials.gov", "Crossref-edition"}

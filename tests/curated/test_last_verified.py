import copy
from pathlib import Path

import pytest
import yaml

from oncobuddy.curated.loader import STALE_DAYS, default_directory, load_guidelines

SEED = "esmo-early-breast-cancer.yaml"


def write(tmp_path: Path, **over) -> Path:
    data = yaml.safe_load((default_directory() / SEED).read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data.pop("last_verified", None)
    data.update(over)
    (tmp_path / "g.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def test_stale_days_constant():
    assert STALE_DAYS == 540


def test_fresh_date_not_stale(tmp_path):
    (record,) = load_guidelines(write(tmp_path, last_verified="2026-10-01"))
    assert record.payload["last_verified"] == "2026-10-01"
    assert record.payload["verification_stale"] is False


def test_old_date_is_stale(tmp_path):
    (record,) = load_guidelines(write(tmp_path, last_verified="2020-01-01"))
    assert record.payload["last_verified"] == "2020-01-01"
    assert record.payload["verification_stale"] is True


def test_absent_field_adds_no_keys(tmp_path):
    (record,) = load_guidelines(write(tmp_path))
    assert "last_verified" not in record.payload
    assert "verification_stale" not in record.payload


@pytest.mark.parametrize("bad", ["2999-01-01", "01/11/2026", "unknown", "2026-13-45"])
def test_bad_dates_rejected_naming_field_only(tmp_path, bad):
    with pytest.raises(ValueError, match="last_verified") as err:
        load_guidelines(write(tmp_path, last_verified=bad))
    assert bad not in str(err.value)

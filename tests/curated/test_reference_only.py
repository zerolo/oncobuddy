import copy
from pathlib import Path

import pytest
import yaml

from oncobuddy.core.access import ALLOWED_HOSTS
from oncobuddy.curated.loader import default_directory, load_guidelines

SEED = "esmo-early-breast-cancer.yaml"


def write(tmp_path: Path, drop=(), **over) -> Path:
    data = yaml.safe_load((default_directory() / SEED).read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data.update(over)
    for key in drop:
        data.pop(key, None)
    (tmp_path / "g.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("region", ["US", "UK"])
def test_reference_only_loads_for_us_and_uk(tmp_path, region):
    (record,) = load_guidelines(write(tmp_path, region=region, reference_only=True))
    assert record.region == region
    assert record.payload["reference_only"] is True


@pytest.mark.parametrize("region", ["EU", "PT", "ES", "global"])
def test_reference_only_rejected_for_eu_scope_and_global(tmp_path, region):
    write(tmp_path, region=region, reference_only=True)
    with pytest.raises(ValueError) as info:
        load_guidelines(tmp_path)
    assert "reference_only" in str(info.value)


def test_reference_only_unknown_region_rejected(tmp_path):
    write(tmp_path, region="FR", reference_only=True)
    with pytest.raises(ValueError) as info:
        load_guidelines(tmp_path)
    assert "region" in str(info.value)


def test_no_flag_means_no_payload_key(tmp_path):
    (record,) = load_guidelines(write(tmp_path))
    assert "reference_only" not in record.payload
    (record,) = load_guidelines(write(tmp_path, region="US", reference_only=False))
    assert "reference_only" not in record.payload


def test_new_hosts_allowed():
    for host in ("www.nccn.org", "www.asco.org", "ascopubs.org", "www.nice.org.uk",
                 "normas.dgs.min-saude.pt", "www.dgs.pt"):
        assert host in ALLOWED_HOSTS

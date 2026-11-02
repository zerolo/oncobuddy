from datetime import date
from urllib.parse import urlparse

from oncobuddy.core.access import ALLOWED_HOSTS as ENTRY_HOSTS
from oncobuddy.core.records import RESTRICTED_LICENCE
from oncobuddy.core.regions import RegionTree
from oncobuddy.curated.loader import load_guidelines

# Hosts curated guideline links may point to; extend it (and CONTRIBUTING.md) when adding a source.
ALLOWED_HOSTS = {"doi.org", "www.esmo.org", "esmo.org", "www.dgs.pt", "normas.dgs.min-saude.pt", "www.nccn.org"}


def test_all_seed_files_validate():
    records = load_guidelines()  # packaged directory
    assert len(records) >= 1
    ids = [r.id for r in records]
    assert len(ids) == len(set(ids))
    tree = RegionTree.load()
    for record in records:
        tree.scope_chain(record.region)  # raises if not in the tree
        assert record.source_url.startswith("https://")
        assert urlparse(record.source_url).hostname in ALLOWED_HOSTS
        published = record.payload["published"]
        assert published == "unknown" or date.fromisoformat(published)
        assert record.valid_as_of == "unknown" or record.valid_as_of <= date.today()
        assert record.reviewed_by
        if record.licence == RESTRICTED_LICENCE:
            assert "summary" not in record.payload


def test_every_entry_url_host_is_allowed():
    for record in load_guidelines():
        host = urlparse(record.payload["access"]["entry_url"]).hostname
        assert host in ENTRY_HOSTS, record.id

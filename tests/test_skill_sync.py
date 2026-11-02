import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml
from mcp.client import Client

from oncobuddy.core.access import AGENT_PROTOCOL, PROVENANCE_VOCAB
from oncobuddy.core.readplan import READ_PLAN_CLOSING
from oncobuddy.core.reconciler import Reconciler
from oncobuddy.core.regions import RegionTree
from oncobuddy.server import OncoTools, create_server

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugin" / "skills" / "oncobuddy" / "SKILL.md"
START, END = "<!-- protocol:start -->", "<!-- protocol:end -->"


def _build_sdist(out: Path) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(out)], cwd=ROOT, check=True, capture_output=True
    )
    [artifact] = [p for p in out.iterdir() if p.name.endswith(".tar.gz")]
    return artifact


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_protocol_in_skill_equals_server_constant():
    text = _text()
    between = text.split(START, 1)[1].split(END, 1)[0].strip("\n")
    assert between == AGENT_PROTOCOL


def test_front_matter_parses():
    text = _text()
    assert text.startswith("---\n")
    front = yaml.safe_load(text.split("---\n", 2)[1])
    assert front["name"] == "oncobuddy"
    assert isinstance(front["description"], str) and front["description"]


def test_skill_names_every_tool_and_provenance_label():
    import asyncio

    async def names() -> set[str]:
        server = create_server(OncoTools(Reconciler([], RegionTree.load())))
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}

    text = _text()
    for name in asyncio.run(names()):
        assert name in text, name
    for label in PROVENANCE_VOCAB:
        assert label in text, label


def test_sdist_includes_the_skill(tmp_path):
    sdist = _build_sdist(tmp_path)
    with tarfile.open(sdist) as tf:
        names = tf.getnames()
    assert [n for n in names if n.endswith("plugin/skills/oncobuddy/SKILL.md")]


def test_skill_describes_read_plan():
    text = _text()
    assert "read_plan" in text
    assert READ_PLAN_CLOSING in text


def test_skill_and_readme_list_every_biomarker_key_and_alias():
    from oncobuddy.core.biomarkers import ALIASES, CANONICAL

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (_text(), readme):
        for key in CANONICAL:
            assert f"`{key}`" in text, key
    for alias in ALIASES:
        assert f"`{alias}`" in _text(), alias


def test_skill_names_reference_group_and_every_registry_source():
    text = _text()
    assert "reference_elsewhere" in text
    for source in ("ESMO", "DGS", "NCCN", "ASCO", "NICE"):
        assert source in text, source

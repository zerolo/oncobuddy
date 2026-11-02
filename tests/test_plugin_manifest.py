import json
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_plugin_manifest_matches_project():
    manifest = load(PLUGIN / ".claude-plugin" / "plugin.json")
    assert manifest["name"] == "onco-medbuddy"
    assert manifest["version"] == PYPROJECT["project"]["version"]
    assert manifest["description"]
    assert "not a medical device" in manifest["description"].lower()


def test_marketplace_lists_the_plugin():
    market = load(ROOT / ".claude-plugin" / "marketplace.json")
    assert market["name"] == "onco-medbuddy"
    [entry] = market["plugins"]
    assert entry["name"] == "onco-medbuddy" and entry["source"] == "./plugin"


def test_mcp_config_launches_the_entry_point():
    server = load(PLUGIN / ".mcp.json")["mcpServers"]["onco-medbuddy"]
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", "git+https://github.com/zerolo/onco-medbuddy", "onco-medbuddy"]
    assert "onco-medbuddy" in PYPROJECT["project"]["scripts"]
    assert set(server["env"]) == {"ONCOBUDDY_EMAIL"}


def test_skill_front_matter():
    text = (PLUGIN / "skills" / "oncobuddy" / "SKILL.md").read_text()
    front = yaml.safe_load(text.split("---")[1])
    assert front["name"] == "oncobuddy" and front["description"]

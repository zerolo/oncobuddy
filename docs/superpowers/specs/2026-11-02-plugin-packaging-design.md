# oncoBuddy plugin packaging - Design and plan (one document)

Approved in chat 2026-11-02. Goal: one install step gives an agent the skill and the MCP server.

## Decisions
- `plugin/` is the plugin root: `plugin/.claude-plugin/plugin.json`, `plugin/.mcp.json`, `plugin/skills/oncobuddy/`.
- `.claude-plugin/marketplace.json` at the repo root lists the plugin (source `./plugin`). Install:
  `/plugin marketplace add <repo path>` then `/plugin install oncobuddy@oncobuddy`.
- Server source: local path for now (owner decision; nothing is published). `.mcp.json` runs
  `uvx --from ${ONCOBUDDY_SRC} oncobuddy-mcp`; `ONCOBUDDY_SRC` is set once by the user (a marketplace
  install copies `plugin/`, so a relative path cannot work, and no user path is committed). Switching to
  PyPI later means replacing the args with `["oncobuddy-mcp"]`.
- `ONCOBUDDY_EMAIL` is passed through (empty by default).
- Out of scope: publishing, LICENSE, other clients' manifests, server code changes.

## Tests (offline, `tests/test_plugin_manifest.py`)
JSON files parse; plugin name/version equal `pyproject.toml`; marketplace lists the plugin with source
`./plugin`; `.mcp.json` launches the `oncobuddy-mcp` entry point and that entry point exists in
`[project.scripts]`; SKILL.md front matter has name `oncobuddy` and a description; the manifest carries
the "not a medical device" wording.

## Steps
1. Write manifests and test (TDD: test first). 2. README section + SKILL.md missing-server note.
3. `uv run pytest -q`, `uv run ruff check .`, commit.

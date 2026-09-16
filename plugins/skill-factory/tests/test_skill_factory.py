"""Tests for the Skill Factory plugin.

Run with the Hermes venv python (system python3 lacks PyYAML and the PEP-604
type syntax this codebase uses):

    ~/.hermes/hermes-agent/venv/bin/python -m pytest plugins/skill-factory/tests -q

These cover the invariants that were silently broken before:
  * the generated SKILL.md frontmatter must PARSE (it was indented 8 spaces)
  * the generated plugin package must compile and register
  * the propose -> capture -> generate pipeline must be reachable
  * persistence must go through PluginState.set (which has no __setitem__)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import py_compile
import re
import sys
from pathlib import Path

import pytest

PLUGIN_INIT = Path(__file__).resolve().parents[1] / "__init__.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("sf_under_test", PLUGIN_INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeState:
    """Mirrors PluginState: get/set and NO item assignment."""

    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    # deliberately no __setitem__ — that absence is the point


class FakeCtx:
    def __init__(self):
        self.commands = {}
        self.tools = {}
        self.hooks = {}
        self.state = FakeState()

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = handler

    def register_hook(self, name, cb):
        self.hooks[name] = cb


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    mod = _load_plugin()
    ctx = FakeCtx()
    mod.register(ctx)
    return mod, ctx


def _call(handler, *args):
    out = handler(*args) if args else handler()
    if asyncio.iscoroutine(out):
        out = asyncio.new_event_loop().run_until_complete(out)
    return out


# --------------------------------------------------------------------------- #
# Registration surface
# --------------------------------------------------------------------------- #

def test_registers_the_documented_surface(plugin):
    _, ctx = plugin
    assert set(ctx.commands) == {
        "skill-factory-propose", "skill-factory-list", "skill-factory-status",
        "skill-factory-queue", "skill-factory-save", "skill-factory-clear",
    }
    assert set(ctx.tools) == {"skill_factory_status", "skill_factory_capture"}
    assert set(ctx.hooks) == {"post_tool_call", "pre_llm_call"}


def test_command_names_are_flat_and_hyphenated(plugin):
    """Spaced names ("skill-factory propose") can never register."""
    _, ctx = plugin
    for name in ctx.commands:
        assert " " not in name
        assert re.fullmatch(r"[a-z0-9-]+", name), name


# --------------------------------------------------------------------------- #
# The generated SKILL.md must be a valid Hermes skill
# --------------------------------------------------------------------------- #

def test_generated_skill_md_frontmatter_parses(plugin):
    mod, _ = plugin
    path, content = mod.generate_skill_md(
        "demo-flow", "software-development", "A demo workflow",
        ["one", "two"], ["an example"], ["demo"],
    )
    # Hermes anchors frontmatter at the very start of the file.
    assert content.startswith("---\n")
    assert not content.startswith(" "), "frontmatter must not be indented"
    import yaml
    fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", content, re.S).group(1))
    assert fm["name"] == "Demo Flow"
    assert fm["description"] == "A demo workflow"
    assert fm["category"] == "software-development"
    assert fm["tags"] == ["demo"]
    # and the step list must not be mis-indented
    assert "1. one\n2. two" in content


def test_generated_skill_md_loads_with_hermes_own_parser(plugin):
    mod, _ = plugin
    path, _ = mod.generate_skill_md(
        "demo-flow", "custom", "desc", ["step"], [], ["t"],
    )
    from tools.skills_tool import _parse_frontmatter
    fm, body = _parse_frontmatter(path.read_text())
    assert fm.get("name") == "Demo Flow", "Hermes' parser must see the metadata"
    assert fm.get("description") == "desc"


# --------------------------------------------------------------------------- #
# The generated plugin package must be installable
# --------------------------------------------------------------------------- #

def test_generated_plugin_package_compiles_and_registers(plugin, tmp_path):
    mod, _ = plugin
    pkg_dir, files = mod.generate_plugin_package(
        "demo-flow", "A demo workflow", ["one", "two"],
    )
    assert (pkg_dir / "plugin.yaml").exists()
    assert (pkg_dir / "__init__.py").exists()
    py_compile.compile(str(pkg_dir / "__init__.py"), doraise=True)

    spec = importlib.util.spec_from_file_location("gen_demo", pkg_dir / "__init__.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    ctx = FakeCtx()
    gen.register(ctx)
    assert "demo-flow" in ctx.commands
    assert "demo_flow_run" in ctx.tools
    # the generated handler must actually return the steps
    out = ctx.commands["demo-flow"]()
    assert "1. one" in out and "2. two" in out


def test_generated_plugin_yaml_declares_its_tool(plugin, tmp_path):
    import yaml
    mod, _ = plugin
    pkg_dir, _ = mod.generate_plugin_package("demo-flow", "d", ["s"])
    manifest = yaml.safe_load((pkg_dir / "plugin.yaml").read_text())
    assert manifest["name"] == "demo-flow"
    assert manifest["provides_tools"] == ["demo_flow_run"]


# --------------------------------------------------------------------------- #
# The propose -> capture -> generate pipeline
# --------------------------------------------------------------------------- #

def test_propose_queues_context_that_reaches_the_model(plugin):
    """A command return value is user-only; the model is reached via pre_llm_call."""
    _, ctx = plugin
    out = _call(ctx.commands["skill-factory-propose"], "")
    assert isinstance(out, str)
    payload = ctx.hooks["pre_llm_call"]()
    assert payload and "context" in payload
    assert "skill_factory_capture" in payload["context"]
    # one-shot: must not repeat on the next turn
    assert ctx.hooks["pre_llm_call"]() is None


def test_capture_writes_both_artifacts_and_is_reachable(plugin):
    """Nothing called add_to_queue before, so `save` could never work."""
    _, ctx = plugin
    res = json.loads(ctx.tools["skill_factory_capture"]({
        "name": "git-pr-workflow",
        "description": "Raise a PR end to end",
        "category": "software-development",
        "steps": ["branch", "commit", "push", "open PR"],
    }))
    assert res["ok"] is True
    # SKILL.md + the plugin package's plugin.yaml + __init__.py
    assert len(res["files"]) == 3
    names = sorted(Path(f).name for f in res["files"])
    assert names == ["SKILL.md", "__init__.py", "plugin.yaml"]
    for f in res["files"]:
        assert Path(f).exists()
    # capture must make the proposal visible to queue
    assert "git-pr-workflow" in _call(ctx.commands["skill-factory-queue"], "")
    # `save` re-uses the captured proposal under a NEW name — it must report
    # that new name and its files, not the original.
    saved = _call(ctx.commands["skill-factory-save"], "git-pr-v2")
    assert "git-pr-v2" in saved
    assert "plugin.yaml" in saved


def test_capture_rejects_bad_input(plugin):
    _, ctx = plugin
    cap = ctx.tools["skill_factory_capture"]
    assert "error" in json.loads(cap({}))
    assert "error" in json.loads(cap({"name": "x"}))            # no steps
    assert "error" in json.loads(cap({"name": "!!!", "steps": ["a"]}))  # unusable name


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def test_state_persists_through_ctx_state_get_set(plugin):
    """PluginState has no __setitem__; item assignment silently never persists."""
    _, ctx = plugin
    ctx.hooks["post_tool_call"](tool_name="read_file", args={"path": "x"}, status="ok")
    ctx.tools["skill_factory_capture"]({"name": "demo", "steps": ["a"]})
    stored = ctx.state.get("tracker")
    assert isinstance(stored, dict)
    assert stored["generated_skills"], "generated skills must reach ctx.state"
    assert stored["proposal_queue"], "the proposal queue must reach ctx.state"


def test_tracker_survives_an_item_assignment_free_store(plugin):
    """Regression: a dict-style store raised TypeError and lost everything."""
    mod, ctx = plugin
    # FakeState has no __setitem__ — any code doing store[k] = v would raise.
    ctx.hooks["post_tool_call"](tool_name="terminal", args={"command": "ls"}, status="ok")
    assert mod._tracker.events, "events must be recorded"


# --------------------------------------------------------------------------- #
# Event recording cost / bounds
# --------------------------------------------------------------------------- #

def test_events_are_bounded(plugin):
    mod, ctx = plugin
    for _ in range(600):
        ctx.hooks["post_tool_call"](tool_name="t", args={}, status="ok")
    assert len(mod._tracker.events) <= mod.SessionTracker._MAX_EVENTS


def test_repeated_tools_ranks_by_frequency(plugin):
    mod, ctx = plugin
    for tool, n in (("read_file", 4), ("patch", 2), ("terminal", 5)):
        for _ in range(n):
            ctx.hooks["post_tool_call"](tool_name=tool, args={}, status="ok")
    ranked = mod._tracker.repeated_tools(min_count=3)
    assert ranked[0] == ("terminal", 5)
    assert ("read_file", 4) in ranked
    assert all(n >= 3 for _, n in ranked)

"""Skill Factory — Hermes plugin package.

Watches your workflows and generates reusable skills automatically.

The plugin is the command interface for the Skill Factory meta-skill; the
observation/analysis behaviour lives in skills/skill-factory/SKILL.md.

Install (see install.sh):
    cp -r plugins/skill-factory  ~/.hermes/plugins/skill-factory
    cp -r skills/skill-factory   ~/.hermes/skills/meta/skill-factory
    hermes plugins enable skill-factory --no-allow-tool-override

Note on command names: the Hermes plugin API registers flat, hyphenated
commands, so the documented `/skill-factory propose` is invoked as
`/skill-factory-propose`. See README.md.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

PLUGIN_NAME = "skill-factory"
PLUGIN_VERSION = "1.1.0"
PLUGIN_DESCRIPTION = "Meta-skill that watches workflows and generates reusable Hermes skills"

REPO_URL = "https://github.com/Romanescu11/hermes-skill-factory"
FORK_URL = "https://github.com/prismatic7/hermes-skill-factory"

# --------------------------------------------------------------------------- #
# Paths — always resolve through HERMES_HOME so the plugin works under any
# profile. Hardcoding Path.home()/".hermes" silently writes into the default
# profile from every other profile.
# --------------------------------------------------------------------------- #


def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    try:  # the canonical helper, when the framework is importable
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _skills_dir() -> Path:
    return _hermes_home() / "skills"


def _plugins_dir() -> Path:
    return _hermes_home() / "plugins"


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #

_SCHEMA_VERSION = 1


class SessionTracker:
    """Tracks workflow patterns within the current Hermes session.

    Persisted through ``ctx.state`` when available (profile-scoped, survives
    process restarts); falls back to process memory otherwise.

    ``PluginState`` exposes ``get``/``set`` — it is NOT a dict (no
    ``__setitem__``), so attribute-free item assignment raises TypeError. The
    in-memory cache also matters for cost: the observation hook fires on every
    tool call, and going through ``ctx.state`` for each one would read and
    atomically rewrite the whole JSON blob every time.
    """

    #: flush the in-memory cache to ctx.state at most once per N records
    _FLUSH_EVERY = 20
    #: hard cap on retained events
    _MAX_EVENTS = 500

    def __init__(self, store: Optional[Any] = None):
        self._store = store
        self.session_start = datetime.now()
        self._cache: Optional[dict] = None
        self._dirty = 0

    # -- storage ---------------------------------------------------------- #

    def _blank(self) -> dict:
        return {
            "schema_version": _SCHEMA_VERSION,
            "events": [],
            "proposal_queue": [],
            "generated_skills": [],
            "last_proposal": None,
        }

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        data = None
        if self._store is not None:
            try:
                data = self._store.get("tracker")
            except Exception:
                data = None
        if not isinstance(data, dict):
            data = self._blank()
        self._cache = data
        return data

    def _flush(self) -> None:
        if self._store is None or self._cache is None:
            return
        try:  # PluginState.set(key, value) — not item assignment
            self._store.set("tracker", self._cache)
            self._dirty = 0
        except Exception:
            pass  # persistence is best-effort; never break a turn

    def _mutate(self, fn, *, immediate: bool = False) -> None:
        data = self._load()
        fn(data)
        data["schema_version"] = _SCHEMA_VERSION
        self._dirty += 1
        if immediate or self._dirty >= self._FLUSH_EVERY:
            self._flush()

    # -- API -------------------------------------------------------------- #

    @property
    def events(self) -> list:
        return list(self._load().get("events", []))

    @property
    def generated_skills(self) -> list:
        return list(self._load().get("generated_skills", []))

    @property
    def proposal_queue(self) -> list:
        return list(self._load().get("proposal_queue", []))

    @property
    def last_proposal(self) -> Optional[dict]:
        return self._load().get("last_proposal")

    def record_event(self, event_type: str, data: dict) -> None:
        def _do(d: dict) -> None:
            events = d.setdefault("events", [])
            events.append(
                {"type": event_type, "data": data, "timestamp": datetime.now().isoformat()}
            )
            del events[: -self._MAX_EVENTS]

        self._mutate(_do)

    def add_to_queue(self, proposal: dict) -> None:
        def _do(d: dict) -> None:
            d.setdefault("proposal_queue", []).append(proposal)
            d["last_proposal"] = proposal

        self._mutate(_do, immediate=True)

    def mark_generated(self, skill_name: str, files: list) -> None:
        def _do(d: dict) -> None:
            d.setdefault("generated_skills", []).append(
                {
                    "name": skill_name,
                    "files": files,
                    "generated_at": datetime.now().isoformat(),
                }
            )

        self._mutate(_do, immediate=True)

    def clear(self) -> None:
        self._cache = self._blank()
        self._flush()

    # -- pattern detection ------------------------------------------------ #

    def repeated_tools(self, min_count: int = 3) -> list:
        """Tools seen at least ``min_count`` times, most frequent first.

        This is the deterministic half of "detect a workflow": the plugin can
        see tool frequency but not intent, so it surfaces the candidates and
        lets the model decide which is worth capturing.
        """
        counts: dict = {}
        for ev in self.events:
            if ev.get("type") != "tool_call":
                continue
            tool = (ev.get("data") or {}).get("tool")
            if tool:
                counts[tool] = counts.get(tool, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(t, n) for t, n in ranked if n >= min_count]



_tracker = SessionTracker()


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def _sanitize_name(name: str) -> str:
    """Convert any string to a valid kebab-case skill name."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name


def _skill_dir(category: str, skill_name: str) -> Path:
    category = _sanitize_name(category) or "custom"
    return _skills_dir() / category / skill_name


def generate_skill_md(
    skill_name: str,
    category: str,
    description: str,
    workflow_steps: list,
    examples: list,
    tags: list,
) -> tuple:
    """Generate a SKILL.md file and return (path, content)."""
    steps_md = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(workflow_steps))
    examples_md = ""
    for i, example in enumerate(examples, 1):
        examples_md += f"\n### Example {i}\n\n{example}\n"

    tags_str = ", ".join(tags)
    display_name = skill_name.replace("-", " ").title()

    # NB: textwrap.dedent() strips only the COMMON leading whitespace, and an
    # f-string's interpolated values (description, steps, ...) start at column 0.
    # A multi-line interpolation therefore flattens the common prefix to "" and
    # NOTHING is stripped — which left every line indented 8 spaces and made the
    # YAML frontmatter unparseable (Hermes' own parser returned {}). Build the
    # text unindented instead, so no dedent is needed at all.
    frontmatter = "\n".join([
        "---",
        f"name: {display_name}",
        "version: 1.0.0",
        f"category: {category}",
        f"description: {description}",
        f"tags: [{tags_str}]",
        "generated_by: skill-factory",
        f"generated_at: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
    ])

    content = "\n".join([
        frontmatter,
        "",
        f"# {display_name}",
        "",
        description,
        "",
        "## When to Activate",
        "",
        f"Activate this skill when you need to perform the {display_name} workflow.",
        "This skill was auto-generated from a live session by Skill Factory.",
        "",
        "## Workflow",
        "",
        "### Steps",
        "",
        steps_md,
        "",
        "## Quality Checklist",
        "",
        "Before completing this workflow:",
        "- [ ] All steps completed in order",
        "- [ ] Output verified against expected result",
        "- [ ] No side effects left behind",
        "",
        "## Examples",
        examples_md.rstrip(),
        "",
        "## Integration",
        "",
        f"This skill was generated by the [Skill Factory]({REPO_URL})",
        "meta-skill. Edit this file to refine the workflow steps and examples.",
        "",
    ])

    target_dir = _skill_dir(category, skill_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "SKILL.md"
    target_path.write_text(content, encoding="utf-8")
    return target_path, content


def generate_plugin_package(
    skill_name: str,
    description: str,
    workflow_steps: list,
) -> tuple:
    """Generate a plugin *package* and return (dir, [paths]).

    Emits a directory with plugin.yaml + __init__.py. A loose `.py` in
    ~/.hermes/plugins/ is never loaded: plugin discovery skips anything that
    is not a directory (hermes_cli/plugins_discovery.py::scan_directory).
    """
    target_dir = _plugins_dir() / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)

    fn_name = skill_name.replace("-", "_")
    display_name = skill_name.replace("-", " ").title()

    # Build the manifest as plain lines: same textwrap.dedent trap as
    # generate_skill_md (interpolated values sit at column 0, so the common
    # prefix collapses and nothing is stripped).
    manifest = "\n".join([
        f"name: {skill_name}",
        "version: 1.0.0",
        f"description: {json.dumps(description)}",
        "author: skill-factory",
        "provides_tools:",
        f"- {fn_name}_run",
        "",
    ])
    (target_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    steps_literal = "\n".join(f'        {s!r},' for s in (workflow_steps or ["implement me"]))

    body = "\n".join([
        f'"""{display_name} — Auto-generated by Skill Factory.',
        "",
        description,
        "",
        "Generated from a live session. Edit the handler below to implement the",
        f"steps. Invoke with /{skill_name}.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import json",
        "",
        f"PLUGIN_NAME = {skill_name!r}",
        'PLUGIN_VERSION = "1.0.0"',
        f"PLUGIN_DESCRIPTION = {description!r}",
        "",
        "_CTX = None",
        "",
        "",
        "def _steps():",
        "    return [",
        steps_literal,
        "    ]",
        "",
        "",
        "def _run(args=None, **kwargs):",
        '    """Command/tool handler. Returns a string; never raises."""',
        "    try:",
        "        steps = _steps()",
        f'        head = "**{display_name}** — {{n}} step(s):".format(n=len(steps))',
        '        listed = "\\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))',
        f'        tail = ("\\n\\nEdit `~/.hermes/plugins/{skill_name}/__init__.py` "',
        '                "to implement the steps.")',
        "        return head + \"\\n\" + listed + tail",
        "    except Exception as exc:",
        '        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})',
        "",
        "",
        "def register(ctx) -> None:",
        '    """Wire the generated command and tool."""',
        "    global _CTX",
        "    _CTX = ctx",
        "    ctx.register_command(",
        f"        {skill_name!r},",
        "        _run,",
        f"        description={description!r},",
        "    )",
        "    ctx.register_tool(",
        f"        name={fn_name + '_run'!r},",
        f"        toolset={fn_name!r},",
        '        schema={',
        '            "type": "object",',
        '            "properties": {',
        '                "input": {"type": "string", "description": "optional input"},',
        "            },",
        '            "additionalProperties": False,',
        "        },",
        "        handler=_run,",
        f"        description={(description or skill_name)!r},",
        '        emoji="\\U0001f527",',
        "    )",
        "",
    ])
    (target_dir / "__init__.py").write_text(body, encoding="utf-8")

    return target_dir, [str(target_dir / "plugin.yaml"), str(target_dir / "__init__.py")]


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def _as_lines(rows: list) -> str:
    return "\n".join(rows)


def _msg(ctx, text: str) -> str:
    """Deliver a message where possible, and always return it for the caller.

    ``ctx.inject_message`` is CLI-only in the current core (it needs a live CLI
    ref) and returns False in the gateway, in ``hermes chat -q`` and in
    kanban-spawned sessions. Returning the text is the portable path — the
    command handler's return value is rendered on every surface.
    """
    try:
        if ctx is not None and hasattr(ctx, "inject_message"):
            ctx.inject_message(text, role="user")
    except Exception:
        pass
    return text


def register(ctx) -> None:
    """Register Skill Factory commands, tool and hook."""
    global _tracker
    _store = None
    try:
        _store = ctx.state
    except Exception:
        _store = None
    _tracker = SessionTracker(store=_store)

    # One-shot context bridge. A plugin command's return value is rendered to
    # the USER only (cli.py `_cprint`s it and never queues it as a turn), so
    # returning "go analyse the session" would never reach the model. A
    # `pre_llm_call` callback can return {"context": ...}, which the core
    # appends to the NEXT user message — that is the only portable way to hand
    # the model an instruction from a command.
    pending_context: list = []

    def on_pre_llm_call(**kwargs):
        if not pending_context:
            return None
        text = pending_context.pop(0)
        return {"context": text}

    # -- propose ---------------------------------------------------------- #
    async def cmd_propose(args: str = "", **kwargs):
        """Ask the model to analyse the session and propose a capture."""
        # Deterministic candidates the plugin CAN see (tool frequency).
        top = _tracker.repeated_tools()[:8]
        if top:
            observed = "Tools used repeatedly this session:\n" + "\n".join(
                f"  - {t} x{n}" for t, n in top
            )
        else:
            observed = (
                "No tool has been used 3+ times yet this session, so there is "
                f"nothing obviously repeated. Events recorded: {len(_tracker.events)}."
            )

        pending_context.append(
            "The user ran /skill-factory-propose. Act as the Skill Factory "
            "meta-skill (see your active SKILL.md).\n\n"
            f"{observed}\n\n"
            "Pick the single most valuable repeatable workflow from THIS "
            "session's conversation history. Then capture it by calling the "
            "`skill_factory_capture` tool with:\n"
            "  name        - kebab-case, e.g. git-pr-workflow\n"
            "  description - one line\n"
            "  category    - e.g. software-development, sysadmin, research\n"
            "  steps       - ordered list of concrete steps\n"
            "  examples    - optional concrete examples from this session\n"
            "  tags        - optional short tags\n"
            "Do not just describe the skill — call the tool so it is written to "
            "disk. If nothing in this session is worth capturing, say so and "
            "stop."
        )
        return (
            "Skill Factory: analysing this session. The proposal request has "
            "been queued and will reach the model on your next message — send "
            "any message (e.g. \"go\") to complete the proposal."
        )

    # -- capture ---------------------------------------------------------- #
    def tool_capture(args=None, **kwargs):
        """Persist a proposal and generate the skill + plugin package.

        This is the write path the whole plugin exists for. It is a TOOL rather
        than only a slash command so the model can complete the
        propose -> capture -> generate loop in a single turn: the model calls
        it after analyse, and the files land immediately.
        """
        args = args or {}
        try:
            raw_name = str(args.get("name") or "").strip()
            if not raw_name:
                return json.dumps({"error": "name is required"})
            skill_name = _sanitize_name(raw_name)
            if not skill_name:
                return json.dumps({"error": f"name {raw_name!r} has no usable characters"})

            description = str(args.get("description") or f"Auto-generated skill: {skill_name}")
            category = _sanitize_name(str(args.get("category") or "custom")) or "custom"
            steps = args.get("steps") or []
            if isinstance(steps, str):
                steps = [s.strip() for s in steps.splitlines() if s.strip()]
            if not isinstance(steps, list) or not steps:
                return json.dumps({"error": "steps must be a non-empty list"})
            examples = args.get("examples") or []
            if isinstance(examples, str):
                examples = [examples]
            tags = args.get("tags") or ["generated", "skill-factory"]
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]

            proposal = {
                "name": skill_name,
                "description": description,
                "category": category,
                "steps": [str(s) for s in steps],
                "examples": [str(e) for e in examples],
                "tags": [str(t) for t in tags],
            }
            # Record it so /skill-factory-queue and /skill-factory-save agree
            # with what was generated. Previously NOTHING called add_to_queue,
            # which made `save` unreachable ("No proposal active" forever).
            _tracker.add_to_queue(proposal)

            md_path, _ = generate_skill_md(
                skill_name, category, description, proposal["steps"],
                proposal["examples"], proposal["tags"],
            )
            pkg_dir, pkg_files = generate_plugin_package(
                skill_name, description, proposal["steps"],
            )
            files = [str(md_path)] + pkg_files
            _tracker.mark_generated(skill_name, files)

            return json.dumps(
                {
                    "ok": True,
                    "skill": skill_name,
                    "files": files,
                    "next": (
                        f"hermes plugins enable {skill_name} --no-allow-tool-override"
                        " && hermes gateway restart"
                    ),
                },
                indent=2,
            )
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    # -- list ------------------------------------------------------------- #
    async def cmd_list(args: str = "", **kwargs):
        if not _tracker.generated_skills:
            return "No skills generated yet this session. Try `/skill-factory-propose`."
        rows = ["Skills generated this session:", ""]
        for skill in _tracker.generated_skills:
            rows.append(f"- {skill['name']} — {', '.join(skill['files'])}")
            rows.append(f"  generated at {skill['generated_at'][:16]}")
        return _as_lines(rows)

    # -- status ----------------------------------------------------------- #
    async def cmd_status(args: str = "", **kwargs):
        minutes = int((datetime.now() - _tracker.session_start).total_seconds() / 60)
        top = _tracker.repeated_tools()[:5]
        observed = ", ".join(f"{t} x{n}" for t, n in top) or "none yet"
        return (
            f"Skill Factory status\n\n"
            f"- session duration: {minutes} min\n"
            f"- events tracked: {len(_tracker.events)}\n"
            f"- proposals queued: {len(_tracker.proposal_queue)}\n"
            f"- skills generated: {len(_tracker.generated_skills)}\n"
            f"- repeated tools: {observed}\n\n"
            f"Run `/skill-factory-propose` to surface a proposal now."
        )

    # -- queue ------------------------------------------------------------ #
    async def cmd_queue(args: str = "", **kwargs):
        q = _tracker.proposal_queue
        if not q:
            return (
                "No patterns queued yet. Keep working — Skill Factory detects "
                "repeatable workflows automatically."
            )
        rows = [f"Proposal queue ({len(q)} pending)", ""]
        for i, proposal in enumerate(q, 1):
            rows.append(
                f"{i}. {proposal.get('name', 'unnamed')} — {proposal.get('description', '')}"
            )
        return _as_lines(rows)

    # -- save ------------------------------------------------------------- #
    async def cmd_save(args: str = "", **kwargs):
        """Re-generate the last captured proposal under a different name."""
        skill_name = _sanitize_name(args.strip()) if args.strip() else None
        if not skill_name:
            return "Provide a skill name. Example:\n`/skill-factory-save my-workflow-name`"
        proposal = _tracker.last_proposal
        if not proposal:
            return (
                "No proposal active. Run `/skill-factory-propose` first, then "
                "`/skill-factory-save <name>`."
            )
        try:
            md_path, _ = generate_skill_md(
                skill_name, proposal.get("category", "custom"),
                proposal.get("description", f"Auto-generated skill: {skill_name}"),
                proposal.get("steps", ["implement me"]),
                proposal.get("examples", []),
                proposal.get("tags", ["generated", "skill-factory"]),
            )
            pkg_dir, pkg_files = generate_plugin_package(
                skill_name,
                proposal.get("description", f"Auto-generated skill: {skill_name}"),
                proposal.get("steps", ["implement me"]),
            )
            files = [str(md_path)] + pkg_files
            _tracker.mark_generated(skill_name, files)
            return (
                f"Skill '{skill_name}' saved.\n\nFiles written:\n"
                + "\n".join(f"- `{f}`" for f in files)
                + "\n\nEnable it with: "
                f"`hermes plugins enable {skill_name} --no-allow-tool-override`"
            )
        except Exception as e:
            return f"Failed to save skill: {type(e).__name__}: {e}"

    # -- clear ------------------------------------------------------------ #
    async def cmd_clear(args: str = "", **kwargs):
        _tracker.clear()
        return "Session log cleared. Skill Factory is watching fresh."

    # -- tool: status as JSON (model-callable) ---------------------------- #
    def tool_status(args=None, **kwargs):
        try:
            return json.dumps(
                {
                    "session_started": _tracker.session_start.isoformat(),
                    "events_tracked": len(_tracker.events),
                    "proposals_queued": len(_tracker.proposal_queue),
                    "skills_generated": len(_tracker.generated_skills),
                    "repeated_tools": _tracker.repeated_tools()[:10],
                    "skills_dir": str(_skills_dir()),
                    "plugins_dir": str(_plugins_dir()),
                },
                indent=2,
            )
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    # -- hook: record tool calls ------------------------------------------ #
    def on_post_tool_call(**kwargs):
        """Record tool calls for pattern analysis.

        The core emits ``post_tool_call`` with tool_name / args / result (plus
        ids and duration_ms), NOT tool_args / tool_result.
        """
        try:
            tool_args = kwargs.get("args")
            _tracker.record_event(
                "tool_call",
                {
                    "tool": kwargs.get("tool_name"),
                    "args_keys": sorted(tool_args) if isinstance(tool_args, dict) else [],
                    "status": kwargs.get("status"),
                },
            )
        except Exception:
            pass  # observation must never break a turn

    ctx.register_command("skill-factory-propose", cmd_propose,
                         description="Analyse the session and propose the top workflow as a skill")
    ctx.register_command("skill-factory-list", cmd_list,
                         description="List skills generated this session")
    ctx.register_command("skill-factory-status", cmd_status,
                         description="Show what patterns Skill Factory is tracking")
    ctx.register_command("skill-factory-queue", cmd_queue,
                         description="Show all detected patterns queued for proposals")
    ctx.register_command("skill-factory-save", cmd_save, args_hint="<skill-name>",
                         description="Save the last proposed skill under a custom name")
    ctx.register_command("skill-factory-clear", cmd_clear,
                         description="Clear the current session tracking log")

    ctx.register_tool(
        name="skill_factory_status",
        toolset="skill_factory",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=tool_status,
        description="Report Skill Factory tracking state as JSON (events, queue, generated skills, paths)",
        emoji="\U0001f3ed",
    )

    # The write path — model-callable so propose -> capture -> generate
    # completes inside one turn.
    ctx.register_tool(
        name="skill_factory_capture",
        toolset="skill_factory",
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case skill name, e.g. git-pr-workflow"},
                "description": {"type": "string", "description": "one-line description of the workflow"},
                "category": {"type": "string", "description": "skill category directory, e.g. software-development"},
                "steps": {"type": "array", "items": {"type": "string"},
                          "description": "ordered concrete steps of the workflow"},
                "examples": {"type": "array", "items": {"type": "string"},
                             "description": "optional concrete examples"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "optional short tags"},
            },
            "required": ["name", "steps"],
            "additionalProperties": False,
        },
        handler=tool_capture,
        description=(
            "Write a captured workflow to disk as a SKILL.md plus an installable Hermes "
            "plugin package. Call this to complete a Skill Factory proposal. Returns the "
            "written file paths."
        ),
        emoji="\U0001f3ed",
    )

    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)


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
    process restarts); falls back to process memory on older cores.
    """

    def __init__(self, store: Optional[Any] = None):
        self._store = store
        self.session_start = datetime.now()

    # -- storage ---------------------------------------------------------- #

    def _load(self) -> dict:
        if self._store is not None:
            try:
                data = self._store.get("tracker")
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        if not hasattr(self, "_mem"):
            self._mem = {}
        return self._mem

    def _save(self, data: dict) -> None:
        if self._store is not None:
            try:
                self._store["tracker"] = data
                return
            except Exception:
                pass
        self._mem = data

    def _mutate(self, fn) -> None:
        data = self._load()
        fn(data)
        data["schema_version"] = _SCHEMA_VERSION
        self._save(data)

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
            # bound the log: a long session must not grow this without limit
            del events[:-500]

        self._mutate(_do)

    def add_to_queue(self, proposal: dict) -> None:
        def _do(d: dict) -> None:
            d.setdefault("proposal_queue", []).append(proposal)
            d["last_proposal"] = proposal

        self._mutate(_do)

    def mark_generated(self, skill_name: str, files: list) -> None:
        def _do(d: dict) -> None:
            d.setdefault("generated_skills", []).append(
                {
                    "name": skill_name,
                    "files": files,
                    "generated_at": datetime.now().isoformat(),
                }
            )

        self._mutate(_do)

    def clear(self) -> None:
        self._save({"schema_version": _SCHEMA_VERSION, "events": [],
                    "proposal_queue": [], "generated_skills": [],
                    "last_proposal": None})


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

    content = textwrap.dedent(
        f"""\
        ---
        name: {display_name}
        version: 1.0.0
        category: {category}
        description: {description}
        tags: [{tags_str}]
        generated_by: skill-factory
        generated_at: {datetime.now().strftime("%Y-%m-%d")}
        ---

        # {display_name}

        {description}

        ## When to Activate

        Activate this skill when you need to perform the {display_name} workflow.
        This skill was auto-generated from a live session by Skill Factory.

        ## Workflow

        ### Steps

        {steps_md}

        ## Quality Checklist

        Before completing this workflow:
        - [ ] All steps completed in order
        - [ ] Output verified against expected result
        - [ ] No side effects left behind

        ## Examples
        {examples_md}

        ## Integration

        This skill was generated by the [Skill Factory]({REPO_URL})
        meta-skill. Edit this file to refine the workflow steps and examples.
        """
    )

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
    steps_comments = "\n        ".join(
        f"# Step {i + 1}: {s}" for i, s in enumerate(workflow_steps)
    )

    manifest = textwrap.dedent(
        f"""\
        name: {skill_name}
        version: 1.0.0
        description: {json.dumps(description)}
        author: skill-factory
        provides_tools:
        - {fn_name}_run
        """
    )
    (target_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    body = textwrap.dedent(
        f'''\
        """{display_name} — Auto-generated by Skill Factory.

        {description}

        Generated from a live session. Edit the handler below to implement the
        steps. Invoke with /{skill_name} (or the registered tool
        {{tool}}).
        """

        from __future__ import annotations

        import json

        PLUGIN_NAME = "{skill_name}"
        PLUGIN_VERSION = "1.0.0"
        PLUGIN_DESCRIPTION = {json.dumps(description)}

        _CTX = None


        def _steps():
            return [
        {chr(10).join(f'        "{s}",' for s in workflow_steps) if workflow_steps else '        "implement me",'}
            ]


        def _run(args=None, **kwargs):
            """Command/tool handler. Returns a string; never raises."""
            try:
                steps = _steps()
                return "**{display_name}** — {{n}} step(s):\\n".format(n=len(steps)) + "\\n".join(
                    f"{{i}}. {{s}}" for i, s in enumerate(steps, 1)
                ) + "\\n\\nEdit `~/.hermes/plugins/{skill_name}/__init__.py` to implement."
            except Exception as exc:
                return json.dumps({{"error": f"{{type(exc).__name__}}: {{exc}}"}})


        def register(ctx) -> None:
            """Wire the generated command and tool."""
            global _CTX
            _CTX = ctx
            ctx.register_command(
                "{skill_name}",
                _run,
                description={json.dumps(description)},
            )
            ctx.register_tool(
                name="{fn_name}_run",
                toolset="{fn_name}",
                schema={{
                    "type": "object",
                    "properties": {{
                        "input": {{"type": "string", "description": "optional input"}},
                    }},
                    "additionalProperties": False,
                }},
                handler=_run,
                description={json.dumps(description or skill_name)},
                emoji="\\U0001f527",
            )
        '''
    )
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

    # -- propose ---------------------------------------------------------- #
    async def cmd_propose(args: str = "", **kwargs):
        header = (
            "Skill Factory — analysing this session.\n\n"
            "The skill-factory meta-skill is active. Review the workflows from "
            "this session and propose the most reusable one.\n\n"
            "_Tip: the meta-skill must be installed at "
            "`~/.hermes/skills/meta/skill-factory/SKILL.md` for AI-driven "
            "proposals._"
        )
        # The pattern analysis itself is the AI's job via SKILL.md; returning the
        # text puts the instruction in front of the model on every surface.
        return header

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
        return (
            f"Skill Factory status\n\n"
            f"- session duration: {minutes} min\n"
            f"- events tracked: {len(_tracker.events)}\n"
            f"- proposals queued: {len(_tracker.proposal_queue)}\n"
            f"- skills generated: {len(_tracker.generated_skills)}\n\n"
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
        skill_name = _sanitize_name(args.strip()) if args.strip() else None
        if not skill_name:
            return "Provide a skill name. Example:\n`/skill-factory-save my-workflow-name`"
        if not _tracker.last_proposal:
            return (
                "No proposal active. Run `/skill-factory-propose` first, then "
                "`/skill-factory-save <name>`."
            )
        proposal = _tracker.last_proposal
        category = proposal.get("category", "custom")
        description = proposal.get("description", f"Auto-generated skill: {skill_name}")
        steps = proposal.get("steps", ["Step 1: implement me"])
        examples = proposal.get("examples", [])
        tags = proposal.get("tags", ["generated", "skill-factory"])
        try:
            md_path, _ = generate_skill_md(skill_name, category, description, steps, examples, tags)
            pkg_dir, pkg_files = generate_plugin_package(skill_name, description, steps)
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

    ctx.register_hook("post_tool_call", on_post_tool_call)

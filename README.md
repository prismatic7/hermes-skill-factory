# 🏭 Skill Factory

> **The meta-skill that creates skills.**
> Your AI watches your workflows and turns them into reusable Hermes skills — automatically.

Built for [Nous Research's Hermes Agent](https://github.com/NousResearch/hermes-agent) (v2026.3+).

---

## What It Does

Every time you solve a problem with Hermes, you're performing a workflow worth repeating. Skill Factory watches silently, detects patterns, and at the right moment asks:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏭 SKILL FACTORY — New Skill Detected
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

I noticed you repeatedly set up a Python environment,
installed dependencies, and ran tests in the same order.

Proposed Skill:   python-env-setup
Category:         software-development
Description:      Reproducible Python project setup workflow

What it captures:
  1. Create venv and activate
  2. Upgrade pip and install dependencies
  3. Run pytest to verify environment

Generate: [A] SKILL.md  [B] plugin.py  [C] Both  [D] Skip
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Say **C** and it writes both files immediately:

- `~/.hermes/skills/<category>/<name>/SKILL.md` — AI instructions for the workflow
- `~/.hermes/plugins/<name>/` — a plugin package (plugin.yaml + __init__.py) exposing a slash command

---

## Installation

**Requirements:** Hermes Agent v2026.3+

```bash
git clone https://github.com/Romanescu11/hermes-skill-factory
cd hermes-skill-factory
bash install.sh
```

Or manually:

```bash
# Install the meta-skill
mkdir -p ~/.hermes/skills/meta/skill-factory
cp skills/skill-factory/SKILL.md ~/.hermes/skills/meta/skill-factory/

# Install the plugin as a PACKAGE (a directory, not a loose .py)
mkdir -p ~/.hermes/plugins/skill-factory
cp plugins/skill-factory/plugin.yaml  ~/.hermes/plugins/skill-factory/
cp plugins/skill-factory/__init__.py ~/.hermes/plugins/skill-factory/
```

Then enable the plugin:

```bash
hermes plugins enable skill-factory --no-allow-tool-override
hermes gateway restart          # so a running gateway picks it up
```

> **Why a directory, not a `.py` file?** Hermes plugin discovery skips anything
> under `~/.hermes/plugins/` that is not a directory
> (`hermes_cli/plugins_discovery.py::scan_directory`), so a loose
> `skill_factory.py` is never imported and none of its commands ever register.
> `--no-allow-tool-override` is required because `plugins enable` otherwise
> blocks on an interactive capability prompt, which hangs any non-TTY context.


---

## Usage

Once installed, Skill Factory runs in the background during every session.

| Command | What it does |
|---|---|
| `/skill-factory-propose` | Analyze the session and propose the top detected skill now |
| `/skill-factory-list` | List all skills generated this session |
| `/skill-factory-status` | Show how many patterns are being tracked |
| `/skill-factory-queue` | Show all detected patterns queued for proposal |
| `/skill-factory-save <name>` | Save the last proposal with a custom name |
| `/skill-factory-clear` | Clear the current session log |

**Tip:** You can also just tell Hermes naturally:
- *"Save this as a skill"*
- *"Remember how to do this"*
- *"Turn this workflow into a reusable skill"*

---

## What Gets Generated

### SKILL.md

A complete skill definition following Hermes' native skill format:

```markdown
---
name: Python Env Setup
category: software-development
description: Reproducible Python project setup
tags: [python, venv, testing]
---

# Python Env Setup

## When to Activate
...

## Workflow
### Phase 1: Environment
1. python -m venv .venv
2. source .venv/bin/activate
...

## Examples
...
```

### plugin.py

A scaffolded Hermes plugin with a slash command:

```python
from __future__ import annotations
import json

_CTX = None

def _run(args=None, **kwargs):
    try:
        # Step 1: Create venv
        # Step 2: Install deps
        # Step 3: Run tests
        return "Running **Python Env Setup** workflow."
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

def register(ctx) -> None:
    global _CTX
    _CTX = ctx
    ctx.register_command("python-env-setup", _run, description="Reproducible Python project setup")
```

---

## Repo Structure

```
hermes-skill-factory/
├── skills/
│   └── skill-factory/
│       └── SKILL.md          # The meta-skill (core AI instructions)
├── plugins/
│   └── skill-factory/        # Plugin package: /skill-factory-* commands
├── templates/
│   ├── SKILL_TEMPLATE.md     # Template for generated skills
│   └── PLUGIN_TEMPLATE.py    # Template for generated plugins
├── examples/
│   └── generated/
│       └── git-pr-workflow/  # Example of a Skill Factory output
│           └── SKILL.md
├── docs/
│   └── how-it-works.md       # Architecture deep-dive
└── install.sh                # One-command installer
```

---

## How It Works

See [docs/how-it-works.md](docs/how-it-works.md) for a full breakdown.

**TL;DR:**
1. `SKILL.md` teaches the Hermes AI *how* to observe, detect, and propose skills
2. `plugins/skill-factory/` provides the `/skill-factory-*` commands and file generation
3. You work normally — Skill Factory watches silently and proposes at the right moment

---

## Examples

The `examples/generated/` directory contains real example outputs from Skill Factory:

- [`git-pr-workflow`](examples/generated/git-pr-workflow/SKILL.md) — End-to-end PR creation workflow

---

## Contributing

PRs welcome. If you've generated a great skill using Skill Factory, consider adding it to `examples/generated/` with a PR.

---

## License

MIT

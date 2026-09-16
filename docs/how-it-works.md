# How Skill Factory Works

## The Core Idea

Every time you solve a problem with Hermes, you're teaching it something. But without Skill Factory, that knowledge evaporates at the end of the session.

Skill Factory makes Hermes **learn from your workflows automatically** — turning live sessions into reusable procedural memory.

---

## Architecture

Skill Factory has three components that work together:

```
┌─────────────────────────────────────────────────────────────┐
│                        Your Session                         │
│  "write a test → run it → fix the failure → commit"        │
└──────────────────────────┬──────────────────────────────────┘
                           │ observed silently
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              SKILL.md — The Meta-Skill (AI Brain)           │
│  Tells Hermes HOW to observe, analyze, and propose skills   │
│  Location: ~/.hermes/skills/meta/skill-factory/SKILL.md     │
└──────────────────────────┬──────────────────────────────────┘
                           │ proposes + generates
                           ▼
┌─────────────────────────────────────────────────────────────┐
│           plugin.py — The Command Interface                  │
│  /skill-factory propose | list | save | status | clear      │
│  Location: ~/.hermes/plugins/skill-factory/ (package)       │
└──────────────────────────┬──────────────────────────────────┘
                           │ writes files to disk
                           ▼
┌──────────────────────────────────────────────────┐
│  Generated Skill Package                         │
│  ~/.hermes/skills/<category>/<name>/SKILL.md     │
│  ~/.hermes/plugins/<name>/  (package)            │
└──────────────────────────────────────────────────┘
```

---

## The Three Stages

### Stage 1: Passive Observation

Once installed, Skill Factory watches every tool call, command, and workflow in your session. It doesn't interrupt you. It builds a mental map of what you're doing.

The Hermes AI (guided by `SKILL.md`) tracks:
- Repeated patterns (same sequence of steps 2+ times)
- Multi-step workflows (3+ steps toward a coherent goal)
- Tool combinations (tools used together consistently)
- Verbal hints ("I always do this...", "let me set this up again...")

### Stage 2: Proposal

When a trigger fires (session ends, you run `/skill-factory propose`, or a pattern repeats), Skill Factory surfaces a proposal:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏭 SKILL FACTORY — New Skill Detected
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

I noticed you repeatedly set up a Python virtual environment,
installed dependencies, and ran tests in the same order.

Proposed Skill:   python-env-setup
Category:         software-development
Description:      Reproducible Python project environment setup

What it captures:
  1. Create venv with python -m venv .venv
  2. Activate and upgrade pip
  3. Install from requirements.txt or pyproject.toml
  4. Run pytest to verify environment

Generate: [A] SKILL.md  [B] plugin.py  [C] Both  [D] Skip
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Stage 3: Generation

When you approve, Skill Factory writes two files:

**`SKILL.md`** — AI instructions for the workflow, written in the same format as Hermes built-in skills. The next time you need this workflow, just activate the skill and Hermes walks you through it with full context.

**`plugin.py`** — A slash command (`/python-env-setup`) that triggers the workflow directly. Includes hooks and tool registrations scaffolded from the detected pattern.

---

## File Locations

| File | Purpose | Location |
|---|---|---|
| `SKILL.md` (meta-skill) | Teaches Hermes to be a Skill Factory | `~/.hermes/skills/meta/skill-factory/SKILL.md` |
| `skill-factory/` (plugin) | Provides `/skill-factory-*` commands | `~/.hermes/plugins/skill-factory/` |
| Generated skills | Your captured workflows | `~/.hermes/skills/<category>/<name>/SKILL.md` |
| Generated plugins | Your captured commands | `~/.hermes/plugins/<name>/` |

---

## Editing Generated Skills

Generated skills are a starting point, not a final product. After generation:

1. Open the SKILL.md in your editor
2. Refine the workflow steps to match your exact process
3. Add more examples from real sessions
4. Add quality checklists specific to your domain
5. Enable the generated plugin: `hermes plugins enable <name> --no-allow-tool-override`, then `hermes gateway restart`

The generated `plugin.py` contains TODO comments where you should fill in the actual implementation logic.

---

## Design Decisions

**Why SKILL.md + plugin.py together?**

`SKILL.md` gives Hermes *understanding* — it shapes how the AI reasons through the workflow. `plugin.py` gives users *access* — a direct command to trigger the workflow without needing to explain it every time.

**Why passive observation instead of always-on recording?**

Recording everything is noisy. The Skill Factory approach is to have the AI *understand* patterns rather than log raw events — the same way a good mentor watches you work and offers tips rather than filming every keystroke.

**Why propose instead of auto-save?**

Auto-saving everything would create junk skills. The proposal step ensures a human reviews what's being captured, names it meaningfully, and decides if it's worth keeping.

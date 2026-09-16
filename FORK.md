# FORK.md — why this fork exists, and how it differs

This is a fork of **[Romanescu11/hermes-skill-factory](https://github.com/Romanescu11/hermes-skill-factory)**,
maintained at **[prismatic7/hermes-skill-factory](https://github.com/prismatic7/hermes-skill-factory)**.

## Why fork rather than wait

Upstream has had a **single commit** since it was created (`ca38242`, 2026-03-18),
no tags, no releases, and one branch. Its last push was 2026-03-18T13:13:38Z. With
552 stars and 70 forks, people are still installing it — and the install has never
produced a working plugin.

Two bug reports were already open upstream and neither had been actioned:

- **#3** — *Skill Factory plugin does not load on current Hermes versions* (2026-08-10).
  It correctly identifies the loose-`.py` install defect and includes a PowerShell
  workaround. **The workaround still does not work**, because it does not address the
  second defect below.
- **#2** — *Install script literally says "your-username"* (2026-06-02).

This fork fixes those and everything else found in a full audit.

## What was broken

Two independent faults meant no version of this plugin has ever loaded.

**1. The plugin was installed as a loose `.py` file.**

`install.sh` and the README both ran `cp skill_factory.py ~/.hermes/plugins/`.
Hermes plugin discovery skips anything that is not a directory:

```python
# hermes_cli/plugins_discovery.py::scan_directory
for child in sorted(path.iterdir()):
    if not child.is_dir() or (depth == 0 and skip_names and child.name in skip_names):
        continue
```

Verified on a clean `HERMES_HOME` with the file installed the documented way —
`discovered user plugins: NONE`.

**2. The registration API does not exist.**

Even in a directory, the plugin cannot load:

| Used by upstream | Status on current `PluginContext` |
|---|---|
| `@hermes.command` / `@hermes.tool` / `@hermes.on` | **do not exist** |
| `ctx.reply` | **does not exist** |
| `ctx.inject_system_message` | **does not exist** |
| `def register(hermes)` | the signature is `def register(ctx)` |

The real surface is `ctx.register_command` / `register_tool` / `register_hook`, and
`ctx.inject_message` — which is CLI-only and returns `False` in the gateway and in
cron, so returning the string from a handler is the portable path.

**3. The commands could never register anyway.**

All six were declared with spaces in their names (`"skill-factory propose"`). Plugin
commands are flat and hyphenated, so these were unregistrable regardless of the
decorator API.

## Full list of changes in this fork

| # | Defect | Fix |
|---|---|---|
| 1 | Loose-`.py` install never loads | Installed as a package (`plugin.yaml` + `__init__.py`) |
| 2 | `@hermes.command` / `.tool` / `.on` don't exist | `ctx.register_command` / `register_tool` / `register_hook` |
| 3 | `ctx.reply` / `ctx.inject_system_message` don't exist | Return the string from the handler — works on every surface |
| 4 | Command names contain spaces | Flat names: `/skill-factory-propose` … |
| 5 | Hook read `tool_args` / `tool_result`; the core emits `args` / `result` | Correct keys — the hook was recording nothing useful |
| 6 | Hardcoded `Path.home()/".hermes"` | Resolves via `HERMES_HOME`, falling back to `hermes_constants.get_hermes_home()` — it was writing into the *default* profile from every other profile |
| 7 | The generator emitted loose `.py` files | Emits packages — otherwise it reproduced defect 1 for everything it created |
| 8 | `install.sh` had no enable step and no verification | Enables, then self-verifies with `hermes plugins doctor --ci` |
| 9 | `your-username` placeholder URL | Real URL — in 4 files, not just the README |
| 10 | `hermes skills reload` / `hermes skills enable` don't exist | `hermes plugins enable … --no-allow-tool-override` + `hermes gateway restart` |
| 11 | `SKILL.md` taught the dead API in its Phase-4 template | Rewritten — this is where every generated file inherited it from |
| 12 | No `.gitignore`; bytecode committed | Added, and the tracked `.pyc` removed |

Two further improvements beyond the defect list:

- **`ctx.state`-backed persistence** for the session tracker, with an in-memory
  fallback for older cores, and a 500-event bound so a long session cannot grow the
  log without limit.
- **A `skill_factory_status` tool** — model-callable JSON, alongside the slash commands.

## ⚠️ Breaking change

Command names changed from `/skill-factory propose` (with a space — never
registerable) to **`/skill-factory-propose`**. Same pattern for `-list`, `-status`,
`-queue`, `-save`, `-clear`.

If you had a local workaround, re-map your invocations.

## Installing this fork

```bash
git clone https://github.com/prismatic7/hermes-skill-factory
cd hermes-skill-factory
bash install.sh
```

Or manually:

```bash
# Meta-skill
mkdir -p ~/.hermes/skills/meta/skill-factory
cp skills/skill-factory/SKILL.md ~/.hermes/skills/meta/skill-factory/

# Plugin — as a PACKAGE (a directory, not a loose .py)
mkdir -p ~/.hermes/plugins/skill-factory
cp plugins/skill-factory/plugin.yaml  ~/.hermes/plugins/skill-factory/
cp plugins/skill-factory/__init__.py ~/.hermes/plugins/skill-factory/

hermes plugins enable skill-factory --no-allow-tool-override
hermes gateway restart
```

**`--no-allow-tool-override` is required.** Without it `plugins enable` blocks on an
interactive capability prompt, which hangs any non-TTY caller — a script, cron, or an
agent tool call. This plugin never overrides built-in tools.

`install.sh` respects `HERMES_DIR` / `HERMES_HOME`, so you can target a profile:

```bash
HERMES_HOME=~/.hermes/profiles/<profile> bash install.sh
```

## Verifying

```bash
hermes plugins doctor plugins/skill-factory --ci
#   manifest: skill-factory 1.1.0 (standalone)
#   OK: runtime discovery, manifest parsing, import, and registration passed
#   registrations: 1 tool(s), 1 hook(s)

hermes --help | grep skill-factory
```

After a gateway restart, expect six commands plus a tool and a hook.

## Syncing with upstream

Upstream is added as a remote for exactly this purpose:

```bash
git fetch upstream
git log --oneline upstream/master..master      # what this fork adds
git diff upstream/master..master               # full divergence
```

To take an upstream change:

```bash
git checkout -b upstream-sync
git cherry-pick <sha>        # or: git merge upstream/master
```

Given upstream's activity, conflicts are unlikely.

## Upstream contribution

The same fixes are offered upstream as
**[PR #10](https://github.com/Romanescu11/hermes-skill-factory/pull/10)**
(`Fixes #3`, `Fixes #2`), and note in that PR that issue #3's PowerShell workaround
is incomplete — it fixes the install path but leaves an `__init__.py` that raises
`AttributeError` inside `register()`.

If upstream ever revives and merges it, this fork can be retired.

## Layout

```
plugins/skill-factory/          the plugin package (plugin.yaml + __init__.py)
skills/skill-factory/SKILL.md   the meta-skill (observation/analysis behaviour)
templates/                      scaffolding used when generating new plugins/skills
docs/how-it-works.md            architecture notes
install.sh                      installer, with post-install verification
legacy/                         the original broken plugin, kept for reference
```

`legacy/skill_factory.py.broken-original` is the upstream file exactly as it shipped.
It is retained so the diff is auditable — it is **not** installed by anything and
should not be copied into `~/.hermes/plugins/`.

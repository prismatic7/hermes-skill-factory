#!/usr/bin/env bash
# Skill Factory — Hermes Installation Script
# ==========================================
# Installs the Skill Factory meta-skill and plugin into your Hermes config.
#
# The plugin is installed as a PACKAGE (a directory containing plugin.yaml and
# __init__.py), not as a loose .py file. Hermes plugin discovery skips anything
# that is not a directory — see hermes_cli/plugins_discovery.py::scan_directory
# (`if not child.is_dir(): continue`). A loose .py in ~/.hermes/plugins/ is
# never imported and its commands never register.

set -euo pipefail

HERMES_DIR="${HERMES_DIR:-${HERMES_HOME:-$HOME/.hermes}}"
SKILLS_DIR="$HERMES_DIR/skills"
PLUGINS_DIR="$HERMES_DIR/plugins"
PLUGIN_NAME="skill-factory"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[skill-factory]${NC} $*"; }
warn()  { echo -e "${YELLOW}[skill-factory]${NC} $*"; }
error() { echo -e "${RED}[skill-factory]${NC} $*" >&2; exit 1; }

# ------------------------------------------------------------------
# Pre-flight checks
# ------------------------------------------------------------------

if [ ! -d "$HERMES_DIR" ]; then
  error "Hermes config directory not found at $HERMES_DIR. Is Hermes installed?"
fi

SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ------------------------------------------------------------------
# Install the meta-skill (SKILL.md)
# ------------------------------------------------------------------

SKILL_DEST="$SKILLS_DIR/meta/$PLUGIN_NAME"
mkdir -p "$SKILL_DEST"

if [ -f "$SKILL_DEST/SKILL.md" ]; then
  warn "SKILL.md already exists at $SKILL_DEST/SKILL.md — overwriting."
fi

cp "$SRC_ROOT/skills/$PLUGIN_NAME/SKILL.md" "$SKILL_DEST/SKILL.md"
info "Installed skill: $SKILL_DEST/SKILL.md"

# ------------------------------------------------------------------
# Install the plugin as a package
# ------------------------------------------------------------------

PLUGIN_DEST="$PLUGINS_DIR/$PLUGIN_NAME"

if [ -e "$PLUGIN_DEST" ]; then
  warn "Plugin already exists at $PLUGIN_DEST — replacing."
  rm -rf "$PLUGIN_DEST"
fi

# A legacy loose .py from an older install never loaded either; remove it so the
# install state is unambiguous.
if [ -f "$PLUGINS_DIR/${PLUGIN_NAME}.py" ]; then
  warn "Removing legacy loose plugin file $PLUGINS_DIR/${PLUGIN_NAME}.py (never loaded)."
  rm -f "$PLUGINS_DIR/${PLUGIN_NAME}.py"
fi

mkdir -p "$PLUGIN_DEST"
cp "$SRC_ROOT/plugins/$PLUGIN_NAME/plugin.yaml" "$PLUGIN_DEST/plugin.yaml"
cp "$SRC_ROOT/plugins/$PLUGIN_NAME/__init__.py" "$PLUGIN_DEST/__init__.py"
info "Installed plugin package: $PLUGIN_DEST/"

# ------------------------------------------------------------------
# Enable the plugin
# ------------------------------------------------------------------
#
# --no-allow-tool-override is required: without it `plugins enable` blocks on an
# interactive capability prompt, which hangs any non-TTY context (cron, a
# script, an agent tool call). This plugin never overrides built-in tools.

if command -v hermes >/dev/null 2>&1; then
  if HERMES_HOME="$HERMES_DIR" hermes plugins enable "$PLUGIN_NAME" \
       --no-allow-tool-override </dev/null >/dev/null 2>&1; then
    info "Enabled plugin: $PLUGIN_NAME"
  else
    warn "Could not auto-enable. Run:"
    warn "  HERMES_HOME=$HERMES_DIR hermes plugins enable $PLUGIN_NAME --no-allow-tool-override"
  fi
else
  warn "The 'hermes' CLI was not found on PATH — enable the plugin manually:"
  warn "  hermes plugins enable $PLUGIN_NAME --no-allow-tool-override"
fi

# ------------------------------------------------------------------
# Verification
# ------------------------------------------------------------------

if command -v hermes >/dev/null 2>&1; then
  if HERMES_HOME="$HERMES_DIR" hermes plugins doctor "$PLUGIN_DEST" --ci >/dev/null 2>&1; then
    info "Verified: plugin loads and registers."
  else
    warn "Validation reported a problem. Inspect with:"
    warn "  hermes plugins doctor $PLUGIN_DEST"
  fi
fi

# ------------------------------------------------------------------
# Done
# ------------------------------------------------------------------

echo ""
info "✅ Skill Factory installed successfully!"
echo ""
echo "  Skill:   $SKILL_DEST/SKILL.md"
echo "  Plugin:  $PLUGIN_DEST/  (plugin.yaml + __init__.py)"
echo ""
echo "  Next steps:"
echo "    1. Restart the gateway so a running process picks it up:"
echo "         hermes gateway restart"
echo "    2. Confirm the commands are registered:"
echo "         hermes --help | grep skill-factory"
echo "    3. In a session, use the flat command names:"
echo "         /skill-factory-propose       /skill-factory-status"
echo "         /skill-factory-list          /skill-factory-queue"
echo "         /skill-factory-save <name>   /skill-factory-clear"
echo ""
echo "  Note: Hermes plugin commands are flat, so '/skill-factory propose'"
echo "  is invoked as '/skill-factory-propose'."
echo ""

#!/usr/bin/env bash
# setup-graphify.sh — idempotent bootstrap of the merged-corpus parent dir
# and graphify install for gru.
#
# What this does:
#   1. Creates ~/payments-graph/ if missing.
#   2. Symlinks the 4 product repos from ~/IdeaProjects/ as siblings.
#   3. Installs graphify (pip + graphify install) if missing.
#   4. Prints next-steps for running /graphify in Claude Code.
#
# Idempotent: safe to re-run. Skips work that's already done.
#
# Prerequisites:
#   - The 4 product repos are already cloned at ~/IdeaProjects/<repo>/.
#   - Python 3.10+ available on PATH (for the pip step).
#   - Claude Code installed (verified separately; this script doesn't check).
#
# Usage:
#   ./scripts/setup-graphify.sh                  # full setup (pipx preferred)
#   ./scripts/setup-graphify.sh --no-pip         # skip install (you'll install graphify yourself)
#   ./scripts/setup-graphify.sh --pip-fallback   # force pip3 install with --break-system-packages
#                                                # (last resort; pipx is recommended on macOS / PEP 668)
#   ./scripts/setup-graphify.sh --check          # verify state, make no changes

set -euo pipefail

# ---- config (matches gru/.agents/jira-conventions.md repo list) ----
PARENT="${HOME}/payments-graph"
SOURCE_PARENT="${HOME}/IdeaProjects"
REPOS=(payment-platform walletapi infrastructure spring-boot-starters)

# ---- flags ----
DO_PIP=1
CHECK_ONLY=0
PIP_FALLBACK=0
for arg in "$@"; do
  case "$arg" in
    --no-pip) DO_PIP=0 ;;
    --pip-fallback) PIP_FALLBACK=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

# ---- helpers ----
log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32mOK\033[0m  %s\n' "$*"; }
warn(){ printf '\033[1;33mWARN\033[0m %s\n' "$*" >&2; }
err() { printf '\033[1;31mERR\033[0m %s\n' "$*" >&2; }

# ---- 1. parent dir ----
log "Parent dir: $PARENT"
if [[ -d "$PARENT" ]]; then
  ok "exists"
else
  if [[ "$CHECK_ONLY" == "1" ]]; then
    warn "missing (would create)"
  else
    mkdir -p "$PARENT"
    ok "created"
  fi
fi

# ---- 2. symlink repos ----
log "Symlinking 4 product repos as siblings"
for r in "${REPOS[@]}"; do
  src="$SOURCE_PARENT/$r"
  dst="$PARENT/$r"

  if [[ ! -d "$src/.git" ]]; then
    err "source missing or not a git repo: $src"
    err "clone the 4 product repos under $SOURCE_PARENT/ first."
    exit 1
  fi

  if [[ -L "$dst" ]]; then
    actual=$(readlink "$dst")
    if [[ "$actual" == "$src" ]]; then
      ok "$r -> $src (already linked)"
    else
      warn "$r symlink points to $actual (expected $src)"
    fi
  elif [[ -e "$dst" ]]; then
    warn "$r exists but is not a symlink — skipping (manual review needed)"
  else
    if [[ "$CHECK_ONLY" == "1" ]]; then
      warn "$r missing (would symlink to $src)"
    else
      ln -s "$src" "$dst"
      ok "$r -> $src (linked)"
    fi
  fi
done

# ---- 3. graphify install ----
# Prefer pipx (handles PEP 668 / externally-managed Python on macOS Homebrew cleanly).
# Fall back to pip3 with --break-system-packages only if the user explicitly opts in.
if [[ "$DO_PIP" == "1" ]]; then
  log "Checking graphify CLI"
  if command -v graphify &>/dev/null; then
    ok "graphify already installed: $(command -v graphify)"
  elif [[ "$CHECK_ONLY" == "1" ]]; then
    warn "graphify CLI missing"
  elif [[ "$PIP_FALLBACK" == "1" ]]; then
    PIP=""
    if command -v pip3 &>/dev/null; then PIP=pip3; elif command -v pip &>/dev/null; then PIP=pip; fi
    if [[ -z "$PIP" ]]; then
      err "pip / pip3 not found. Install Python 3.10+ first, then re-run."
      exit 1
    fi
    log "Installing graphify via $PIP install --user --break-system-packages graphifyy (fallback mode)"
    warn "fallback mode bypasses PEP 668. pipx is the recommended path on macOS."
    "$PIP" install --user --break-system-packages graphifyy
    log "Registering graphify Claude Code skill"
    graphify install
    ok "installed"
  elif command -v pipx &>/dev/null; then
    log "Installing graphify via pipx install graphifyy"
    pipx install graphifyy
    log "Registering graphify Claude Code skill"
    graphify install
    ok "installed"
  elif command -v brew &>/dev/null; then
    err "pipx not installed and pipx is required (macOS Homebrew Python is externally-managed; PEP 668)."
    err "Install pipx, then re-run this script:"
    err "  brew install pipx && pipx ensurepath"
    err ""
    err "Alternatively, run with --pip-fallback to force pip --break-system-packages, or"
    err "with --no-pip to install graphify manually."
    exit 1
  else
    err "Neither pipx nor a usable pip path is set up cleanly."
    err "Install pipx (https://pipx.pypa.io/stable/installation/) or run with --pip-fallback."
    exit 1
  fi
else
  warn "skipping graphify install (--no-pip). The graphify CLI must be installed manually."
fi

# ---- 4. summary + next steps ----
echo
log "Summary"
echo "  Parent dir: $PARENT"
ls -la "$PARENT" 2>/dev/null | sed 's/^/    /' || true
echo

if [[ "$CHECK_ONLY" == "1" ]]; then
  log "Check mode — no changes made."
  exit 0
fi

cat <<NEXT

==== Next steps ====

1. Open Claude Code in the merged-corpus parent dir:
     cd $PARENT
     claude

2. Inside Claude Code, build the knowledge graph:
     /graphify . --wiki --mcp

   --wiki   produces agent-crawlable wiki articles under graphify-out/wiki/
            (used by gru skills as a markdown navigation surface).
   --mcp    starts the MCP stdio server that gru skills query directly.

3. Once the run completes, review:
     $PARENT/graphify-out/GRAPH_REPORT.md
   for god-nodes summary, surprising connections, and false-positive edges to prune.

4. For ongoing maintenance, pick one:

   a) Foreground watcher (simple, ephemeral, leave running during gru work):
        cd $PARENT && graphify --watch .

   b) Per-repo post-commit hooks (durable, runs on every commit):
        for r in ${REPOS[*]}; do
          (cd $SOURCE_PARENT/\$r && graphify hook install)
        done
      (Then verify the hook plays well with the merged-corpus layout — see
       gru/knowledge/narrative/README.md for the open question.)

5. Document the chosen maintenance ritual in:
     gru/knowledge/narrative/README.md

NEXT

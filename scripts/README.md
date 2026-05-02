# scripts/

Helper scripts for one-time setup and (eventually) the **stage 3 minion dispatcher**.

## Current scripts

### `setup-graphify.sh`

Idempotent bootstrap of the merged-corpus parent directory at `~/payments-graph/` with the 4 product repos symlinked as siblings, plus the graphify install. Run once per machine.

```bash
./scripts/setup-graphify.sh                # full setup (pipx preferred)
./scripts/setup-graphify.sh --check        # verify state, make no changes
./scripts/setup-graphify.sh --no-pip       # skip install (you'll install graphify yourself)
./scripts/setup-graphify.sh --pip-fallback # force pip3 --user --break-system-packages
```

**Install path on macOS** (PEP 668 / externally-managed Homebrew Python):

The script prefers `pipx` because Homebrew Python rejects `pip install --user` per [PEP 668](https://peps.python.org/pep-0668/). If pipx is missing, the script will tell you to install it:

```bash
brew install pipx && pipx ensurepath
```

Then re-run. `--pip-fallback` exists as an escape hatch but is discouraged on Homebrew Python.

The script also prints next steps for running `/graphify .` inside Claude Code and choosing a maintenance ritual (`--watch` daemon vs. per-repo post-commit hook). See [`../knowledge/narrative/README.md`](../knowledge/narrative/README.md) for the maintenance trade-offs.

## Reserved for stage 3

The PoC is scoped to stages 1–2 (idea → groomed tickets). When stage 3 lands, the minion dispatcher will live here:

- `dispatch.sh` (or `dispatch.py`) — JQL pickup, worktree create, claude-code launch, Jira transition + comment.
- Any helpers shared across multiple skills (Atlassian MCP wrappers, graphify MCP wrappers, etc.).

See [`../docs/plan.md`](../docs/plan.md) → "Path forward to stage 3 — minions" for the full plan.

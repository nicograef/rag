#!/usr/bin/env bash
# setup-dev-tools.sh – install project-specific dev tools (idempotent)
#
# Called by devcontainer.json postCreateCommand, or run manually.
# Each block checks before installing — safe to re-run anytime.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$1"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }
fatal() { error "$1"; exit 1; }

info "Project root: $PROJECT_ROOT"
cd "$PROJECT_ROOT"

# ── uv (Python toolchain: venv, deps, interpreter) ──────────────────────────

info "Ensuring uv..."
if command -v uv >/dev/null 2>&1; then
  info "uv already installed: $(uv --version)"
else
  command -v curl >/dev/null 2>&1 || fatal "Missing 'curl' — needed to bootstrap uv."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# ── Python dependencies ─────────────────────────────────────────────────────

info "Syncing Python dependencies (uv sync)..."
uv sync

# ── Docker (needed for 'make db') ───────────────────────────────────────────

if command -v docker >/dev/null 2>&1; then
  info "docker available: $(docker --version)"
else
  warn "docker not found — 'make db' (Postgres + pgvector) will not work here."
fi

# ── Summary ──────────────────────────────────────────────────────────────────
info "Setup complete."

echo "  uv:      $(uv --version)"
echo "  python:  $(uv run python --version)"

info "Next step: make check"

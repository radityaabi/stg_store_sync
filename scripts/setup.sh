#!/usr/bin/env bash
# ============================================================================
# STG Store Sync — One-command environment setup (Linux / macOS / WSL)
# ============================================================================
# Prepares this repo to run, and to run headlessly on its per-job schedule:
#   1. venv        — create .venv if missing (reuse it if present)
#   2. deps        — pip install -e ".[dev]" into the venv
#   3. .env        — copy .env.example and fill STG_* if missing (never overwrite)
#   4. verify      — run the test suite in the venv (gates step 5)
#   5. schedule    — register one OS task per job (crontab) via `schedule sync`
#
# Idempotent: safe to re-run. An existing .env is never modified.
#
# Usage:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh                       # interactive
#   ./scripts/setup.sh --non-interactive \
#       --stg-server https://host/api --stg-user u --stg-password p
#   ./scripts/setup.sh --skip-tests --skip-schedule
#
# Prerequisites: bash, Python 3.12+, crontab (for step 5)
# ============================================================================
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'

log_info()  { echo -e "${BLUE}ℹ${NC}  $1"; }
log_ok()    { echo -e "${GREEN}✓${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}⚠${NC}  $1"; }
log_error() { echo -e "${RED}✗${NC}  $1"; }
log_step()  { echo -e "\n${CYAN}${BOLD}━━ $1 ━━${NC}"; }

# ── Defaults + arg parsing ──────────────────────────────────────────────────
STG_SERVER=""; STG_USER=""; STG_PASSWORD=""
INTERACTIVE=true; SKIP_TESTS=false; SKIP_SCHEDULE=false

usage() {
    cat <<'EOF'
Usage: scripts/setup.sh [options]

Options:
  --stg-server URL       STG REST base URL (enables non-interactive)
  --stg-user USER        STG Studio username
  --stg-password PASS    STG Studio password
  --non-interactive      Never prompt; use the values above
  --skip-tests           Do not run the test suite (step 4)
  --skip-schedule        Do not register OS tasks (step 5)
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stg-server)      STG_SERVER="$2"; INTERACTIVE=false; shift 2 ;;
        --stg-user)        STG_USER="$2"; INTERACTIVE=false; shift 2 ;;
        --stg-password)    STG_PASSWORD="$2"; INTERACTIVE=false; shift 2 ;;
        --non-interactive) INTERACTIVE=false; shift ;;
        --skip-tests)      SKIP_TESTS=true; shift ;;
        --skip-schedule)   SKIP_SCHEDULE=true; shift ;;
        -h|--help)         usage; exit 0 ;;
        *) log_error "unknown option: $1"; usage; exit 1 ;;
    esac
done

# ── Locate the project root from this script's own path (never CWD) ─────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
log_info "Project dir: $PROJECT_DIR"

# ── 1. Find Python 3.12+ ────────────────────────────────────────────────────
log_step "1/5  Python"
PYTHON=""
for cand in python3.13 python3.12 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
            PYTHON="$cand"; break
        fi
    fi
done
if [[ -z "$PYTHON" ]]; then
    log_error "Python 3.12+ not found on PATH (tried python3.13/3.12/3/python)."
    exit 1
fi
log_ok "Using $PYTHON ($("$PYTHON" --version 2>&1))"

# ── 2. venv ─────────────────────────────────────────────────────────────────
log_step "2/5  Virtual environment"
if [[ -x ".venv/bin/python" ]]; then
    log_ok ".venv already exists — reusing"
else
    "$PYTHON" -m venv .venv
    log_ok "Created .venv"
fi
VENV_PY=".venv/bin/python"

# ── 3. Dependencies ─────────────────────────────────────────────────────────
log_step "3/5  Dependencies"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -e ".[dev]"
log_ok "Installed the package + dev extras"

# ── 4. .env ─────────────────────────────────────────────────────────────────
log_step "4/5  Credentials (.env)"
if [[ -f ".env" ]]; then
    log_ok ".env exists — leaving it untouched"
else
    cp .env.example .env
    log_info "Created .env from .env.example"
    if [[ "$INTERACTIVE" == true ]]; then
        read -r -p "STG_SERVER (e.g. https://host/api): " STG_SERVER
        read -r -p "STG_USER: " STG_USER
        read -r -s -p "STG_PASSWORD: " STG_PASSWORD; echo
    fi
    if [[ -n "$STG_SERVER" && -n "$STG_USER" && -n "$STG_PASSWORD" ]]; then
        # Rewrite only the three credential lines; keep the file's comments.
        "$VENV_PY" - "$STG_SERVER" "$STG_USER" "$STG_PASSWORD" <<'PY'
import re, sys
server, user, password = sys.argv[1:4]
path = ".env"
with open(path) as f:
    text = f.read()
for key, val in (("STG_SERVER", server), ("STG_USER", user), ("STG_PASSWORD", password)):
    text = re.sub(rf"(?m)^{key}=.*$", f"{key}={val}", text)
with open(path, "w") as f:
    f.write(text)
PY
        log_ok "Wrote credentials into .env"
    else
        log_warn ".env created but STG_* left blank — edit it before a live run"
    fi
fi

# ── 5. Verify (gates step 6) ────────────────────────────────────────────────
log_step "5/5  Verify"
if [[ "$SKIP_TESTS" == true ]]; then
    log_warn "Tests skipped (--skip-tests); OS tasks will NOT be registered"
    SKIP_SCHEDULE=true
else
    if "$VENV_PY" -m pytest -q; then
        log_ok "Test suite passed"
    else
        log_error "Test suite failed — not registering OS tasks"
        exit 1
    fi
fi

# ── 6. OS schedule (best-effort) ────────────────────────────────────────────
log_step "6  OS schedule"
if [[ "$SKIP_SCHEDULE" == true ]]; then
    log_info "Skipped. Register later with: ./.venv/bin/python -m store_sync.cli schedule sync"
else
    if "$VENV_PY" -m store_sync.cli schedule sync; then
        log_ok "OS tasks reconciled"
    else
        log_warn "Could not register OS tasks (crontab unavailable?)."
        log_info "Register later with: ./.venv/bin/python -m store_sync.cli schedule sync"
    fi
fi

echo
log_ok "Setup complete."
log_info "Run the GUI:      ./.venv/bin/python -m store_sync.gui"
log_info "Run headlessly:   ./.venv/bin/python -m store_sync.cli list"

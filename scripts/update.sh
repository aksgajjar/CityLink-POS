#!/bin/bash
# CityLink POS — one-click update + launch.
#
# What it does:
#   1. cd to project root (this script's parent dir)
#   2. git fetch + reset to origin/main (latest dev branch)
#   3. ensure .venv exists; create if missing
#   4. pip install -r requirements.txt (quiet)
#   5. launch the app
#
# Safety:
#   - Local DB (data/store.db) is never touched.
#   - All errors logged to errors.log; readable summary printed to terminal.
#   - If git fails, we still try to launch the existing checkout so the
#     tester is never stuck.

set -u

# ─── Resolve project root from this script's location ───────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}" || {
  echo "[update] could not cd to project root: ${PROJECT_ROOT}"
  exit 1
}

LOG="${PROJECT_ROOT}/errors.log"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
log()  { echo "$(ts) [update] $*" | tee -a "${LOG}"; }
warn() { echo "$(ts) [update] WARN: $*" | tee -a "${LOG}"; }

log "=== CityLink POS update started ==="
log "project root: ${PROJECT_ROOT}"

# ─── Step 1: git pull (best effort) ─────────────────────────────────────────
if [ -d ".git" ]; then
  log "fetching latest from origin/main…"
  if git fetch origin >>"${LOG}" 2>&1; then
    if git reset --hard origin/main >>"${LOG}" 2>&1; then
      head_short="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
      log "updated to commit ${head_short}"
    else
      warn "git reset failed — continuing with current checkout"
      echo
      echo "Update failed at git reset step."
      echo "App will still open with the version already installed."
      echo "See errors.log for details."
      echo
    fi
  else
    warn "git fetch failed — no internet? continuing with current checkout"
    echo
    echo "Could not reach GitHub (offline?)."
    echo "App will still open with the version already installed."
    echo
  fi
else
  warn "not a git checkout — skipping update step"
fi

# ─── Step 2: venv ───────────────────────────────────────────────────────────
VENV_DIR="${PROJECT_ROOT}/.venv"
if [ ! -d "${VENV_DIR}" ]; then
  log "creating .venv (first run)…"
  # Try python3.11 / 3.10 / 3.9 in order; fall back to whatever python3 is.
  PY_BIN=""
  for cand in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      PY_BIN="$cand"; break
    fi
  done
  if [ -z "${PY_BIN}" ]; then
    warn "no python3 found in PATH"
    echo
    echo "ERROR: Python 3 is not installed."
    echo "Install Python 3.9+ from https://www.python.org/downloads/macos/"
    echo
    read -n 1 -s -r -p "Press any key to close this window…"
    exit 1
  fi
  if ! "${PY_BIN}" -m venv "${VENV_DIR}" >>"${LOG}" 2>&1; then
    warn "venv create failed via ${PY_BIN}"
    echo
    echo "ERROR: could not create virtual environment."
    echo "See errors.log for details."
    echo
    read -n 1 -s -r -p "Press any key to close this window…"
    exit 1
  fi
  log "venv created with ${PY_BIN}"
fi

# ─── Step 3: pip install (quiet, best effort) ───────────────────────────────
if [ -f "requirements.txt" ]; then
  log "installing/updating requirements…"
  if ! "${VENV_DIR}/bin/pip" install -q --disable-pip-version-check \
       -r requirements.txt >>"${LOG}" 2>&1; then
    warn "pip install failed — launching anyway with currently-installed packages"
    echo
    echo "Some Python packages could not be updated (offline or version conflict)."
    echo "App will still try to launch with what's already installed."
    echo
  fi
fi

# ─── Step 4: launch ─────────────────────────────────────────────────────────
log "launching app…"
echo
echo "Starting CityLink POS…"
echo

# exec replaces the shell process so the app inherits the terminal output.
exec "${VENV_DIR}/bin/python" main.py

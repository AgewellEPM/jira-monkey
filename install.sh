#!/usr/bin/env bash
# 🍌 Monkey — one-line installer, straight from GitHub.
#
#   curl -fsSL https://raw.githubusercontent.com/AgewellEPM/jira-monkey/main/install.sh | bash
#
# Installs the `Monkey` (and `jira-monkey`) command onto your machine.
# Prefers pipx (isolated), falls back to pip --user. Needs Python 3.11+.
set -euo pipefail

REPO="git+https://github.com/AgewellEPM/jira-monkey.git"
NEED_MAJOR=3
NEED_MINOR=11

say()  { printf '\033[38;5;220m🍌 %s\033[0m\n' "$*"; }
err()  { printf '\033[38;5;203m✗  %s\033[0m\n' "$*" >&2; }
ok()   { printf '\033[38;5;79m✔  %s\033[0m\n' "$*"; }

# --- find a Python 3.11+ interpreter -----------------------------------------
find_python() {
  for c in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($NEED_MAJOR,$NEED_MINOR) else 1)" 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}

say "Monkey installer"
PY="$(find_python || true)"
if [ -z "${PY:-}" ]; then
  err "Python ${NEED_MAJOR}.${NEED_MINOR}+ not found."
  err "Install it (macOS: 'brew install python@3.12'; Debian/Ubuntu: 'sudo apt install python3.12') and re-run."
  exit 1
fi
ok "Using $($PY --version) at $(command -v "$PY")"

# --- prefer pipx (clean, isolated), else pip --user --------------------------
if command -v pipx >/dev/null 2>&1; then
  say "Installing with pipx…"
  pipx install --force "$REPO"
elif "$PY" -m pipx --version >/dev/null 2>&1; then
  say "Installing with pipx (module)…"
  "$PY" -m pipx install --force "$REPO"
else
  say "pipx not found — installing with pip --user…"
  "$PY" -m pip install --user --upgrade "$REPO"
  BIN="$("$PY" -c 'import site,sys,os; print(os.path.join(site.getuserbase(),"bin"))')"
  case ":$PATH:" in
    *":$BIN:"*) : ;;
    *) err "Add this to your PATH so the 'Monkey' command is found:"; echo "    export PATH=\"$BIN:\$PATH\"";;
  esac
fi

echo
ok "Installed. Start it with:"
printf '\033[38;5;220m    Monkey\033[0m   (or: jira-monkey)\n'
echo
say "Docs: https://github.com/AgewellEPM/jira-monkey   ·   run 'Monkey --help'"

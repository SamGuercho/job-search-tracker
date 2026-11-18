#!/usr/bin/env bash
# Start the Job Search Tracker on http://127.0.0.1:8000 (PORT=xxxx ./run.sh for another port).
# First run: creates .venv and installs dependencies. After a `git pull` that changes requirements.txt,
# dependencies are reinstalled automatically.
#
#   ./run.sh --tailscale   also publish it privately on your Tailscale network over https, to use it
#                          from your phone (requires JOBTRACKER_PASSWORD; keeps the Mac awake while running)
set -euo pipefail
cd "$(dirname "$0")"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11 or newer is required (found $(python3 --version 2>&1))."; exit 1; }

if [ ! -d .venv ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
fi
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "Installing dependencies…"
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  touch .venv/.installed
fi
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example (add your OpenAI key there to enable AI features)."; }

PORT="${PORT:-8000}"
SERVE=(.venv/bin/python -m jobtracker serve --port "$PORT")

if [ "${1:-}" = "--tailscale" ]; then
  command -v tailscale >/dev/null \
    || { echo "Tailscale is not installed (https://tailscale.com/download)."; exit 1; }
  .venv/bin/python -c 'import sys; from jobtracker import config; sys.exit(0 if config.PASSWORD else 1)' \
    || { echo "Set JOBTRACKER_PASSWORD in .env first: the app becomes reachable from your other devices."; exit 1; }
  # The app stays on 127.0.0.1; Tailscale terminates https and forwards only your tailnet's traffic to it.
  tailscale serve --bg "$PORT" >/dev/null \
    || { echo "tailscale serve failed: enable HTTPS certificates at https://login.tailscale.com/admin/dns"; exit 1; }
  URL="https://$(tailscale status --json | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  echo "Job Search Tracker → http://127.0.0.1:$PORT  ·  on your devices: $URL"
  echo "(stop sharing with: tailscale serve --https=443 off)"
  # Keep the Mac awake while the app runs, so the phone can reach it
  command -v caffeinate >/dev/null && SERVE=(caffeinate -is "${SERVE[@]}")
else
  echo "Job Search Tracker → http://127.0.0.1:$PORT"
fi
exec "${SERVE[@]}"

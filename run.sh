#!/usr/bin/env bash
# Start the Job Search Tracker on http://127.0.0.1:8000 (PORT=xxxx ./run.sh for another port).
# First run: creates .venv and installs dependencies. After a `git pull` that changes requirements.txt,
# dependencies are reinstalled automatically.
set -euo pipefail
cd "$(dirname "$0")"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10 or newer is required (found $(python3 --version 2>&1))."; exit 1; }

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

echo "Job Search Tracker → http://127.0.0.1:${PORT:-8000}"
exec .venv/bin/python -m jobtracker serve --port "${PORT:-8000}"

"""CLI over the action registry.

  python -m jobtracker serve [--port 8000]
  python -m jobtracker actions                      # list actions + schemas
  python -m jobtracker <action> '<json payload>'    # run one action
  python -m jobtracker <action> @payload.json
  echo '{...}' | python -m jobtracker <action> -

Output is always JSON on stdout, so agents can parse it.
"""
from __future__ import annotations

import json
import sys

from . import actions  # noqa: F401  (registers the actions)
from . import registry


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        print("Actions:\n" + "\n".join(f"  {a.name:28} {a.description.splitlines()[0]}" for a in registry.ACTIONS.values()))
        return 0
    if argv[0] == "serve":
        import uvicorn
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8000
        uvicorn.run("jobtracker.api:app", host="127.0.0.1", port=port)
        return 0
    if argv[0] == "actions":
        print(json.dumps(registry.manifest(), indent=2))
        return 0

    name, raw = argv[0], (argv[1] if len(argv) > 1 else "{}")
    if raw == "-":
        raw = sys.stdin.read()
    elif raw.startswith("@"):
        with open(raw[1:], encoding="utf-8") as f:
            raw = f.read()
    try:
        result = registry.run(name, json.loads(raw))
    except Exception as e:  # report every failure as JSON for agents
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, indent=2))
        return 1
    print(json.dumps({"ok": True, "result": result}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

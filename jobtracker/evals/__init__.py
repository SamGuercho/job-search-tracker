"""Eval cases built from flagged turns, and the runner that replays them (the fixing process, see
.claude/skills/fix-flagged/SKILL.md).

A case is a frozen snapshot of a turn that went wrong, with what "fixed" means:
    {"id", "title", "flag_ids", "status": "draft" | "frozen" | "archived",
     "snapshot": {"message", "history": [{"role", "text"}], "fixture": {"companies.json": …},
                  "recorded": [{"action", "args", "result" | "error"}], "source": {turn_id, cost_usd, reply, tools…}},
     "checks": [{"type": …}, …]     code checks (checks.py)
     "rubric": ["Did the assistant …?", …]}   yes/no questions, graded by Claude Code
Cases live on the server (telemetry DB, through the monitor API), never in git: they hold real conversations.
Local copies and run results go to evals/local/ (git-ignored).
"""

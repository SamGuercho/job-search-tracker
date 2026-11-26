---
name: fix-flagged
description: Work through the assistant's review queue (turns flagged in the monitor, thumbs down from the chat) - find the gaps in prompts, tools and skills, freeze eval cases, fix, re-run once, and mark the flags fixed. Use when the user types /fix-flagged or asks to fix the flagged turns.
---

# Fix the flagged turns

The assistant (Ezri's chat, `jobtracker/services/assistant.py`) records every turn; problems get flagged in the
monitor's Review page. This process turns those flags into a frozen eval set, fixes the gaps, and proves the fix
without breaking earlier cases. **Stop at each ⏸ and wait for the user's answer.**

Tools: `.venv/bin/python -m jobtracker eval <command>` (see `eval --help`); it talks to the monitor at
`JOBTRACKER_MONITOR_URL` (in `.env`; the server's monitor over Tailscale, e.g. `https://<pc>.<tailnet>.ts.net:8443`).
Check it answers first: `curl -s "$JOBTRACKER_MONITOR_URL/health"`. Everything local lands in `evals/local/`
(git-ignored). **Cases hold real conversations: never copy them into git, a commit message, an issue or a PR.**

Where behavior comes from:
- the system prompt: `INSTRUCTIONS` in `jobtracker/services/assistant.py` (and `_instructions()`);
- each tool = an action in `jobtracker/actions.py`: its docstring is the tool description, its signature the schema;
  the behavior is in `jobtracker/services/*`;
- the MCP skill for outside agents: `skills/job-search/SKILL.md` (keep it consistent when tools change);
- the loop: `_converse_openai` / `_converse_claude`, `MAX_STEPS`, approvals (`confirm=True` actions).

## 1. Read the queue and find the gaps

1. `eval queue`: the flags to fix, grouped by category. `eval pull`: the cases that already exist.
2. For each flag, read its turn: `curl -s "$JOBTRACKER_MONITOR_URL/api/turns/<turn_id>"` (user message, the
   model's text and thinking, every tool call with arguments and results), and the flag's note.
3. Group flags by **root cause**, not by category (one unclear tool description can cause wrong arguments *and*
   redundant calls). For each group, find the exact lines responsible in the prompt / tool / code, and why the model
   did what it did. Separate real gaps from noise (a one-off the model would rarely repeat) and from flags that are
   not the assistant's fault (a bug in a service, missing data).
4. ⏸ Present the gap analysis: groups, causes with file:line, the fix you'd make (not yet made), flags you suggest
   dismissing (and why). Ask the user to confirm or correct.

## 2. Build the cases (then freeze)

For each confirmed group:
1. `eval draft --flag <id> [--flag <id>…]` (flags of the same turn) → `evals/local/cases/<id>.json` with the message,
   the conversation before it, the person's data (`fixture`), and recorded outside calls (web pages…).
2. Edit the draft:
   - trim the `fixture` to what the case needs, and make it match the situation at that time (e.g. remove the
     application the turn created); keep the date-sensitive data in mind (runs happen today, `source.date` says when
     the original happened);
   - `checks`: the code checks that define "fixed" (see `jobtracker/evals/checks.py`): the right tool called with the
     right arguments, the wrong one not called, no duplicate calls, status completed, reply contains / doesn't…
     Prefer checks that would have FAILED on the original turn (`snapshot.source` shows what happened).
   - `rubric`: 1-4 yes/no questions for what code can't check (intent understood, task complete, faithful to the
     data, style, level of language, in character). Phrase them so "yes" = good.
   - add 1-2 **variants** (copy the file, new `id`, other wording or another company) so the fix generalizes.
3. ⏸ Show each case (message, checks, rubric, variants). On the user's OK: `eval push <file> --freeze` for each.
   Frozen cases never change: they are the regression set from now on, together with all earlier frozen cases.

## 3. Baseline run (before the fix)

1. `eval pull`, then `eval run --label baseline` (all frozen cases). It prints the estimated cost and stops.
2. ⏸ Tell the user the cost; run again with `--yes` only after they agree.
3. Grade the rubric of each result in `evals/local/runs/baseline-*/results.json` ("verdict": "yes" | "no", and a
   one-line "reason"), honestly, from the reply and tool calls only. Then `eval report baseline`.
4. The new cases must **fail** here (they reproduce the flagged behavior). If one passes, first find out whether
   the code already changed since the flagged turn (`source.date`): `git log --since=<that date> -- jobtracker/
   skills/` for changes to the prompt, tools or services involved. Never "fix" something already fixed, and never
   bend a case until it fails on fixed code.
   - Relevant changes since: check the case reproduces the flag on the code of that time. Make a worktree at the
     last commit before the turn (`git worktree add ../jt-at-flag <commit>`), run only that case from there
     (`cd ../jt-at-flag && <repo>/.venv/bin/python -m jobtracker eval run --label at-flag --cases <id>`, ⏸ cost),
     then remove the worktree. Fails there and passes now → **already fixed** by that commit: no fix in step 4,
     the case stays frozen as a regression test, and in step 6 resolve its flags with `--fixed-in <that commit>`.
     Passes there too → the case doesn't capture the problem: fix it as below.
   - No relevant change: the case doesn't capture the problem: fix the case (it's a new one, so: add a variant)
     or tell the user the flag may be noise.
   ⏸ Tell the user which cases are already fixed (and by which commit) before going on.

## 4. Fix

Make the smallest change that closes each gap: the instruction, the tool description/schema, the service behavior,
or the skill. Don't special-case the eval wording. Add unit tests for code changes. Run `ruff check .` and the
test suite (`.venv/bin/python -m unittest discover -t . -s tests`).

## 5. After run (once) and the noise check

1. ⏸ Cost again, then `eval run --label after --yes`. Grade the rubric the same way (same strictness), `eval report`.
2. `eval compare baseline after`:
   - **fixed**: what we wanted;
   - **REGRESSED** (passed before, fails now): re-run only those, twice: `eval run --label recheck --cases <ids>
     --repeat 2 --yes` (⏸ cost). Fails 3/3 → a real break: look for a conflict, confusion or contradiction between the
     new change and earlier instructions/tools, fix it, and run step 5 again. Fails 1/3 → randomness: say so.
   - new cases still failing → back to step 4 (tell the user before another paid run).

## 6. Close

1. ⏸ Report: per group, the cause, the fix, before → after; regressions and how they were settled; total eval cost.
2. On the user's OK: commit (and push if they want), then `eval resolve --flags <ids> --fixed-in <commit>` for the
   flags of the fixed cases. They leave the queue; their cases stay frozen as regression tests.
3. Dismissed flags: `eval resolve --flags <ids> --status dismissed`.

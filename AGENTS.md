# Agent Instructions for this Repo

Read this fully before doing anything. It exists so you don't have to rediscover the
environment, structure, or past decisions from scratch every session.

## Environment — READ THIS FIRST

- **OS: Windows. Shell: PowerShell.** Not Linux, not bash, not zsh.
- Never use bash-style syntax: no `&&` chaining, no heredocs, no `export VAR=`.
  Use PowerShell equivalents (`;` or separate commands, `$env:VAR=`, etc.)
- **When creating or editing files, use direct file-write tools/APIs (e.g. your editor's
  file-write function), not shell heredocs or `python -c "..."` string execution.**
  PowerShell mangles embedded quotes and parens passed through `-c`, which has previously
  caused a real bug: a function got written using `chr()` character codes instead of plain
  strings as a workaround for quoting failures. Don't do that. If you hit a quoting error,
  the fix is to write the file directly, not to avoid string literals.
- **Multi-line `python -c "..."` bodies are parsed as PowerShell script blocks** and fail
  with `ScriptBlock should only be specified as a value of the Command parameter`. This
  happens when the command contains `{`, `}`, `(`, `)`, or literal newlines inside the
  double-quoted argument. The reliable workaround is:
  1. Write a temporary `.py` script using a PowerShell literal here-string:
     `@' ... '@ | Out-File -FilePath "tmp.py" -Encoding utf8`
  2. Execute it: `python "tmp.py"`
  3. Delete the temp script when done. Triple-quoted docstrings inside here-strings can
     themselves cause Python syntax errors; if that happens, write the function body
     line-by-line with `f.write()` calls in the script instead of embedding the source
     as a triple-quoted string.
- Paths use backslashes; don't assume `/`.
- If you're ever about to run a command and you're not sure it's valid PowerShell, stop
  and check rather than guessing from Unix habits.

## What this project is

A personal, single-user fantasy football decision-support tool built on the `ffbot`
Python package (which scrapes Yahoo Fantasy Sports — it does NOT authenticate or submit
transactions; it's read-only web scraping). This tool adds a decision layer on top:
add/drop recommendations, lineup optimization, bench depth checks, and a step-by-step
weekly action plan — for one user, one Yahoo league.

Full original design spec: see `ffbot-agent-spec.md` in this repo. Read it if you need
context on *why* something is structured a certain way — it was written before most of
this code existed and explains the intended architecture (decision logic separated from
execution, dry-run by default, etc.).

## Config

`config.yaml` holds defaults: `league_id`, `team_id`, `positions`, scoring settings,
`bench_depth_minimums`, `ir_statuses`, VOR thresholds. `--league-id` and `--team-id` CLI
flags override these per-run without editing the file.

**Important:** `team_name` is NOT a maintained config value — it is derived at runtime
from `team_id` via `get_team_name_from_id()` in `decision_engine.py`, using the `Owner`
and `Owner ID` columns from ffbot's scraped DataFrame. Do not reintroduce a separately
configured `team_name` — that previously caused a real bug where the roster shown and
the team the optimizer analyzed could silently point at two different teams.

Yahoo credentials/tokens (currently unused — see Automation section below) go in `.env`,
never committed. Template is `.env.example`.

## File structure

```
config.yaml          Defaults for league_id, team_id, positions, thresholds
data_layer.py         get_fresh_data(), caching via ffbot's save()/load(),
                       get_latest_cached_or_fresh(max_age_hours),
                       get_team_name_from_id(), validate_team_identifiers()
decision_engine.py     Core logic: get_my_roster(), flag_bench_depth_gaps(),
                       recommend_adds_drops(), recommend_lineup(),
                       build_action_plan(), simulate_post_move_roster()
                       (team_name resolution moved to data_layer)
executor.py            STUBS ONLY — submit_add_drop(), set_lineup(). Do not wire these to
                       a real API without explicit instruction (see Automation below).
report.py              Terminal + markdown report formatting, including the Action Plan
                       section (moves, resulting lineup, bench, comparison notes)
run_weekly.py           CLI entry point — see Usage below
tests/                 pytest suite. Run the FULL suite after any change, not just new tests.
```

## Usage (current CLI as of the CLI-override feature)

```
python run_weekly.py                          # dry-run, uses config.yaml defaults, 12h cache
python run_weekly.py --force-refresh          # bypass cache, re-scrape (~7 min)
python run_weekly.py --week 3                 # override auto-detected week
python run_weekly.py --league-id X --team-id Y  # analyze a different league/team for this run only
python run_weekly.py --execute                # NOT YET WIRED — executor.py is stubs only, see below
```

Reports save to `reports/league_{league_id}_team_{team_id}_week_{n}.md` — filenames are
unique per league/team/week so different runs never overwrite each other.

## Design principles already established — don't relitigate these

1. **Structured output, not strings.** Decision functions return dataclasses
   (`AddDropRecommendation`, `LineupSlot`, `ActionPlan`, `BenchDepthWarning`, etc.), which
   both the report formatter and (eventually) the executor consume. Don't have decision
   functions return raw formatted text — formatting belongs in `report.py`.
2. **Decision logic never touches Yahoo directly.** All Yahoo interaction is either
   `ffbot`'s scraping (read-only, in `data_layer.py`) or the `executor.py` stubs
   (currently no-ops). Keep this separation.
3. **`--dry-run` is the default; `--execute` must be explicit.** Never make execution
   the default behavior.
4. **Negative-VOR recommendations are filtered out of the main list**, shown separately
   under "Not Recommended" — don't merge them back into the primary ranked list.
5. **A bench player only counts toward `bench_depth_minimums` if it clears
   `min_usable_projection`** (currently 1.0) — a rostered player with a 0.0 projection
   does not count as real depth. This was a deliberate fix; don't revert to simple
   slot-occupancy counting.
6. **The Action Plan's "Resulting Starting Lineup" is computed from the POST-move
   simulated roster**, not the pre-move roster. This was also a deliberate fix for a real
   bug — don't let lineup calculation drift back to using the current roster when
   add/drop moves are pending.
7. **Bye week lookahead is run every week and surfaced before the Action Plan.**
   `check_upcoming_byes()` scans the next `lookahead_weeks` (default 2) for 0/null
   projections and returns `ByeWeekWarning` objects. Starters are flagged as higher
   priority than bench players. The report shows an "Upcoming Byes" section before
   the Action Plan so forward-looking context doesn't get buried.
8. **Streaming thresholds are position-specific, not a single global value.**
   `min_vor_gain_to_recommend_add` in `config.yaml` is now a dict keyed by position
   (QB/TE/K/DEF use a low bar ~1–2.0; RB/WR use a high bar ~8.0) with a `default`
   fallback for any position not explicitly listed. `_assess_confidence()` and
   `_build_add_drop_reason()` both read the position-specific value so reasoning text
   and confidence stay aligned. Don't collapse this back to a single scalar — the
   position distinction is a deliberate strategy choice.

## Automation / executor.py — investigated, deliberately NOT built

An investigation (see git history / prior session) confirmed:
- `ffbot` has zero authentication — it cannot submit transactions, read-only scraping only.
- Yahoo's official Fantasy Sports API DOES support write operations (add/drop, lineup
  changes, waiver claims) via OAuth 2.0, but requires a Yahoo developer app registration
  and approval, uses XML payloads, and has real operational risks (waiver timing windows,
  no built-in retry logic, partial-failure states).
- Decision at the time: not worth building for a single-user, single-league tool — the
  `executor.py` stubs exist so the seam is ready, but implementing real API calls is
  future work, not a default task. Don't build this unless explicitly asked to.

## Git Remote

The repository's `origin` remote points to `https://github.com/fq4/colorpicker.git` — this is the correct upstream for this project. Do not change it.

## Testing

Always run the full suite after any change, not just tests for what you touched:

```
python -m pytest tests/ -v
```

As of the last commit: 61 tests passing. If your change doesn't add or update a test,
that's a signal you may have skipped verification — this project's owner checks test
coverage claims against actual pytest output, not summaries.

## A note on trust

This project's owner reads the actual code you write, not just your summary of it, and
has caught real bugs this way (the `chr()` obfuscation in `_generate_comparison_notes`,
the team_name/team_id desync, lineup-computed-before-moves, missing import in run_weekly.py).
Prefer plain, readable code. If you hit a technical wall (like a shell quoting issue), say so
and ask, rather than working around it in a way that makes the code harder to read or verify.

**Passing unit tests does not guarantee the CLI entry point works.** After any change that
adds or renames functions used in `run_weekly.py`, always:
1. Add the new function to the `from decision_engine import (...)` block at the top of
   `run_weekly.py` (line ~35).
2. Run the script itself end-to-end at least once (`python run_weekly.py --dry-run`) after
   changes, not just `pytest`. The tests mock out the entry point; the real script can still
   crash with `NameError` if an import was forgotten.

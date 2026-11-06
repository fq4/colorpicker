# ffbot_ui — Fantasy Football Decision Agent

> A personal, single-user decision-support tool for Yahoo Fantasy Football.
> It does **not** submit transactions — it reads your roster via `ffbot`'s
> read-only scraper, runs an optimizer, and produces add/drop recommendations
> with an actionable weekly plan.

**Last updated:** 2026-11-05

---

## What it does

1. Scrapes your Yahoo Fantasy Football league roster and player projections
   (cached locally to avoid repeated scraping).
2. Builds your optimal starting lineup for the current week.
3. Identifies bench-depth gaps and upcoming bye weeks.
4. Recommends free-agent adds and add/drop moves ranked by VOR gain.
5. Produces a step-by-step **Action Plan** showing exactly what to do, the
   resulting lineup, and any remaining warnings.
6. Saves a markdown report to `reports/` and prints a terminal summary.
7. Optionally sends structured player data and deterministic output to an
  independent LLM evaluator for a validated second opinion. This is disabled
  by default and cannot execute transactions or change lineups.
8. Evaluates complete add/drop transactions against HOLD with legality,
  starter-impact, current-week, ROS, VOR, scarcity, and opportunity-cost metrics.
9. Optionally compares your optimized lineup with an opponent's optimized-lineup proxy,
  estimates P(win) from completed-week actuals, and evaluates role-aware bench/starter
  swaps and matchup-aware transaction scores.
10. Optionally evaluates cached Yahoo projections with a separate walk-forward analytics
   package. Model predictions are advisory and are used by the optimizer only when the
   false-by-default `use_model_sd` or `use_model_projections` flag is enabled.

All recommendations are dry-run by default. The `executor.py` stubs exist as
placeholders but are not wired to Yahoo's write API.

---

## Attribution and dependency note

This project uses the upstream `ffbot` package as a dependency for read-only Yahoo
Fantasy Football data collection. We do not modify the `ffbot` source code in this
repository; this project adds a separate decision layer, recommendation engine,
reporting, and analysis tooling on top of it.

`ffbot` is distributed under the MIT license. If you publish or redistribute this
project, it is good practice to keep attribution to `ffbot` and to include your
own repository license if you distribute the code.

---

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the weekly analysis (dry-run, uses config.yaml defaults)
python run_weekly.py

# Force re-scrape Yahoo data (bypasses ~12h cache)
python run_weekly.py --force-refresh

# Override league/team for a single run
python run_weekly.py --league-id 123456 --team-id 5

# Override this week's opponent by Yahoo team ID
python run_weekly.py --opponent-id 6

# Execute recommendations (not yet wired to Yahoo API)
python run_weekly.py --execute

# Opt in to the independent LLM assessment for one run
python run_weekly.py --llm-evaluate

# Build the local analytics database from cached CSV scrapes (safe to repeat)
python -m analytics ingest

# Save an explicitly requested pre-kickoff snapshot (scrapes only when invoked)
python -m analytics snapshot --week 10

# Evaluate cached clean projections against completed actuals
python -m analytics evaluate --week 9

# Write the prediction CSV consumed by the optional optimizer integration
python -m analytics predict --week 9
```

---

## Available CLI flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | bool | `True` | Show recommendations without executing |
| `--execute` | bool | `False` | Request execution; currently executor stubs submit nothing |
| `--force-refresh` | bool | `False` | Bypass cache and re-scrape Yahoo data |
| `--week N` | int | auto-detected | Override the week number |
| `--config PATH` | str | `config.yaml` | Path to config file |
| `--max-cache-age H` | float | `12.0` | Max cache age in hours before re-scraping |
| `--league-id N` | int | from config.yaml | Override `league_id` for this run |
| `--team-id N` | int | from config.yaml | Override `team_id` for this run |
| `--opponent-id N` | int | schedule for this week | Override this week's opponent team ID |
| `--waiver-priority N` | int | from config.yaml | Override `waiver_priority` (display-only) |
| `--scoring-type TYPE` | str | from config.yaml | Override `scoring_type`: `half-ppr`, `ppr`, or `standard` (display-only) |
| `--positions STR` | str | from config.yaml | Override `positions` slot string (functional — affects roster limit and lineup optimization) |
| `--waiver-type TYPE` | str | from config.yaml | Override `waiver_type`: `continual_rolling` or `faab` (display-only) |
| `--llm-evaluate` | bool | `False` | Enable the optional independent LLM assessment for this run |

### Examples

```bash
# Analyze a different league/team without editing config.yaml
python run_weekly.py --league-id 492312 --team-id 7

# Override scoring display and waiver priority for this run
python run_weekly.py --scoring-type ppr --waiver-priority 5

# Use a custom roster structure (e.g. fewer bench spots for a smaller league)
python run_weekly.py --positions "QB, WR, RB, TE, K, DEF, BN, BN, IR"

# Combine multiple overrides
python run_weekly.py --league-id 999 --team-id 3 --scoring-type standard --force-refresh

# Analyze a matchup without editing config.yaml
python run_weekly.py --opponent-id 6
```

---

## Config (`config.yaml`)

| Key | Purpose | Where to find it in Yahoo |
|-----|---------|---------------------------|
| `league_id` | Your Yahoo league ID | League settings page URL or API |
| `team_id` | Your team's numeric ID | Team roster page / scraped data |
| `positions` | Roster slot template | **League Settings → Roster** — match exactly (e.g. `QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR`) |
| `scoring_type` | Display label for scoring format | **League Settings → Scoring** — `half-ppr`, `ppr`, or `standard` |
| `waiver_type` | Display label for waiver system | **League Settings → Waivers** — `continual_rolling` or `faab` |
| `waiver_priority` | Your current waiver priority number | **League Settings → Waivers** — update weekly |
| `bench_depth_minimums` | Minimum usable bench players per position | Tune over time based on your roster |
| `ir_statuses` | Injury statuses that disqualify a player from bench depth | Standard IR statuses |
| `min_usable_projection` | Floor projection for a player to count as usable depth | Default `1.0` |
| `min_vor_gain_to_recommend_add` | Per-position VOR threshold for add recommendations | Tune over time |
| `schedule` | Optional week-to-opponent-team-ID map | League schedule |
| `position_sd` | Fallback player scoring SD by position when fewer than 3 actuals exist | Tune from league history |
| `win_probability_thresholds` | Favorite/underdog P(win) thresholds | Tune over time |
| `lineup_swap_margin` | Minimum role-adjusted edge for a bench/starter swap | Tune over time |
| `matchup_ros_weight` | Small weight on ROS transaction value alongside this week's P(win) | Tune over time |
| `kickoff_times` | First kickoff timestamp per target week; used to label clean projections | Verify/update against the NFL schedule |
| `use_model_sd` | Use walk-forward forecast-error SD estimates when available | `false` by default |
| `use_model_projections` | Use blend projections only after the configured out-of-sample quality gate passes | `false` by default |
| `model_projection_min_mae_improvement` | Minimum MAE improvement over Yahoo required for model projections | `0.25` points |
| `model_projection_min_weeks` | Minimum number of completed test weeks required by the projection gate | `6` |
| `llm_evaluator` | Optional second-opinion provider, model, timeout, and candidate limit | Disabled by default |

### Important notes

- Set `schedule` to a week-to-opponent-team-ID mapping (for example, `{9: 6}`),
  or pass `--opponent-id` for a one-run override. IDs are validated against the
  already-loaded league data; opponent names are resolved at runtime.
- The opponent lineup is an optimal-lineup proxy, not their confirmed submitted lineup.
  Win probability uses a normal approximation with independent player outcomes. Current
  live scores are not reported because ffbot does not preserve whether a numeric current
  `Week N` value was an actual or a projection.
- **`team_name` is no longer maintained in `config.yaml`**. It is derived at runtime
  from `team_id` via the scraped data. Do not add it back — that previously caused
  a silent desync bug where the roster shown and the optimizer analyzed different teams.
- **`positions` directly controls roster size and lineup optimization.** If your league
  has a different slot layout, update this string. You can also override it per-run with
  `--positions` without editing the file.
- **The LLM evaluator is advisory only.** It receives structured data after the
  deterministic engine runs. Unknown player references and malformed JSON are rejected;
  provider failures leave the deterministic report intact. Put credentials in the
  environment variable named by `llm_evaluator.api_key_env`, never in YAML or reports.

---

## File structure

```
config.yaml          Defaults for league_id, team_id, positions, thresholds
data_layer.py         get_fresh_data(), direct CSV caching,
                       get_latest_cached_or_fresh(max_age_hours),
                       get_team_name_from_id()
decision_engine.py     Core logic: get_my_roster(), flag_bench_depth_gaps(),
                       recommend_adds_drops(), recommend_lineup(),
                       build_action_plan(), simulate_post_move_roster()
                       (team_name resolution moved to data_layer)
reasoning_engine.py    Complete-roster transaction evaluation, legality checks,
                       HOLD comparison, reasoning ledger, and contradiction checks
llm_evaluator.py       Optional provider boundary, structured prompt, and output validation
executor.py            STUBS ONLY — submit_add_drop(), set_lineup()
report.py              Terminal + markdown report formatting
run_weekly.py           CLI entry point
tests/                 pytest suite
AGENTS.md              Environment and design notes for future sessions
prompt-strategy-additions.md  Strategy spec for decision engine behavior
```

---

## Reports

Reports are saved to:

```
reports/league_{league_id}_team_{team_id}_week_{n}.md
```

The filename includes `league_id` and `team_id` so different runs never overwrite
each other. Cached data goes to `data/`, logs to `logs/`. All are gitignored.

---

## Testing

```bash
python -m pytest tests/ -v
```

As of the last verified run: **186 tests passing**.

---

## Known limitations

- `ffbot` is read-only scraping — it cannot authenticate or submit transactions.
- Yahoo's official Fantasy Sports API supports write operations but requires OAuth 2.0,
  XML payloads, and has operational risks (waiver timing, partial failures).
- The `executor.py` stubs exist as a seam for future implementation but are not wired.
- Current-week actuals cannot be distinguished from projections in ffbot's numeric `Week N`
  field, so live points-banked P(win) is not reported. NFL game correlation is not modeled
  because scraped data has no game-opponent identifiers.
- Analytics uses only pre-kickoff projection snapshots and completed-week actuals. It is
  optional and does not add dependencies. Generated databases, predictions, and accuracy
  reports live under the gitignored `analytics_out/` directory. Update kickoff timestamps
  from the published schedule when the season changes; Week 18's 2026 kickoff time is TBA.

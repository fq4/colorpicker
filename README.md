# ffbot_ui — Fantasy Football Decision Agent

> A personal, single-user decision-support tool for Yahoo Fantasy Football.
> It does **not** submit transactions — it reads your roster via `ffbot`'s
> read-only scraper, runs an optimizer, and produces add/drop recommendations
> with an actionable weekly plan.

**Last updated:** 2026-09-07

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

All recommendations are dry-run by default. The `executor.py` stubs exist as
placeholders but are not wired to Yahoo's write API.

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

# Execute recommendations (not yet wired to Yahoo API)
python run_weekly.py --execute
```

---

## Available CLI flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | bool | `True` | Show recommendations without executing |
| `--execute` | bool | `False` | Submit recommendations via Yahoo API |
| `--force-refresh` | bool | `False` | Bypass cache and re-scrape Yahoo data |
| `--week N` | int | auto-detected | Override the week number |
| `--config PATH` | str | `config.yaml` | Path to config file |
| `--max-cache-age H` | float | `12.0` | Max cache age in hours before re-scraping |
| `--league-id N` | int | from config.yaml | Override `league_id` for this run |
| `--team-id N` | int | from config.yaml | Override `team_id` for this run |
| `--waiver-priority N` | int | from config.yaml | Override `waiver_priority` (display-only) |
| `--scoring-type TYPE` | str | from config.yaml | Override `scoring_type`: `half-ppr`, `ppr`, or `standard` (display-only) |
| `--positions STR` | str | from config.yaml | Override `positions` slot string (functional — affects roster limit and lineup optimization) |
| `--waiver-type TYPE` | str | from config.yaml | Override `waiver_type`: `continual_rolling` or `faab` (display-only) |

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
| `min_vor_loss_to_flag_drop` | VOR threshold below which a drop is flagged | Default `-50.0` |

### Important notes

- **`team_name` is no longer maintained in `config.yaml`**. It is derived at runtime
  from `team_id` via the scraped data. Do not add it back — that previously caused
  a silent desync bug where the roster shown and the optimizer analyzed different teams.
- **`positions` directly controls roster size and lineup optimization.** If your league
  has a different slot layout, update this string. You can also override it per-run with
  `--positions` without editing the file.

---

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

As of the last run: **77 tests passing**.

---

## Known limitations

- `ffbot` is read-only scraping — it cannot authenticate or submit transactions.
- Yahoo's official Fantasy Sports API supports write operations but requires OAuth 2.0,
  XML payloads, and has operational risks (waiver timing, partial failures).
- The `executor.py` stubs exist as a seam for future implementation but are not wired.

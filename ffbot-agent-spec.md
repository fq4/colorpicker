# Fantasy Football Decision Agent — Build Spec

## Purpose
Build a tool on top of the `ffbot` Python package (Yahoo Fantasy Football scraper/optimizer) that:
1. Pulls fresh league/roster/free-agent data every week
2. Makes clear, ranked add/drop and start/sit recommendations before lineup lock
3. Shows the reasoning in a simple dashboard
4. Is structured so add/drop/lineup actions can eventually be executed automatically, without a rewrite

This is a personal tool for one user managing one team in one Yahoo league. Keep it simple — no need for multi-user auth, multi-league support, or a hosted service unless requested later.

---

## Configuration

Create a `config.yaml` (not hardcoded in scripts) with:

```yaml
league_id: 492312
team_id: 7
team_name: "انتصاب يستمر ثلاث عشرة ساعة"   # for matching Owner column
positions: "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR"
is_idp: false
scoring_type: "half-ppr"   # confirmed: league gives 0.5 pts per reception
waiver_type: "continual_rolling"   # confirmed from league settings — not FAAB
waiver_priority: 10   # update weekly as it changes
season: 2026

# thresholds the decision engine uses — tune over time
min_vor_gain_to_recommend_add: 5.0
min_vor_loss_to_flag_drop: -50.0
bench_depth_minimums:
  RB: 1
  WR: 1
  QB: 0
  TE: 0

notify:
  method: "console"   # console | email | none (extend later)
```

Store Yahoo auth/session details (cookies or OAuth token, whatever `ffbot` requires) in a `.env` file or local secrets file — **never commit this**. Add both `.env` and any scraped data caches to `.gitignore`.

---

## Data Layer

- Wrap `ffbot.scrape(league_id, is_IDP=...)` in a function `get_fresh_data()` that returns the full `df` with columns: `ID, Name, Team, Position, Owner, Owner ID, Status, % Owned, Week 1..18, Remaining, VOR`.
- Cache each week's scrape to disk (`data/week_{n}_{timestamp}.csv`) via `ffbot.save()` so we don't have to re-scrape (it takes ~10 min) if the agent is run multiple times same day.
- Add a `get_latest_cached_or_fresh(max_age_hours=12)` helper: use cache if recent enough, otherwise re-scrape.
- Use `ffbot.current_week()` to determine the active week automatically — don't hardcode it.

---

## Decision Engine

This is the core value-add beyond raw `ffbot.optimize()` output. Build a module `decision_engine.py` with functions:

### 1. `get_my_roster(df, team_name) -> DataFrame`
Filter to `Owner == team_name`, return columns: `Name, Team, Position, Status, Week {current}, VOR`.

### 2. `flag_bench_depth_gaps(my_roster, positions_config) -> list[str]`
Check whether the roster has at least the minimum usable bench depth per position (per `bench_depth_minimums` in config), **excluding players with `Status == 'IR'`** from counting as depth. Return human-readable warnings, e.g. `"No usable bench RB — Dylan Laube dropped, Robbie Ouzts is on IR"`.

### 3. `rank_free_agents(df, position, current_week, exclude_status=['IR', 'PUP-R', 'O'])`
Filter free agents/waivers at a position, sorted by **that week's specific projection** (not season total or `Remaining`), with `VOR` as a tiebreaker. Surface `Status` clearly. Deprioritize (but don't hard-exclude) players tagged `Q` — flag them instead.

### 4. `recommend_adds_drops(df, my_roster, current_week)`
Combine `ffbot.optimize()` output with the above:
- Run `ffbot.optimize(df, week, team_id, positions)` for the raw add/drop/VOR suggestions.
- Cross-reference against `flag_bench_depth_gaps` — if a suggested drop would create a position depth gap, downgrade/flag that recommendation rather than silently suggesting it.
- Cross-reference injury `Status` for both the add and the incumbent drop target — a swap that looks great on VOR but drops your only healthy player at a thin position should be flagged, not silently executed.
- Output a ranked list with a **plain-English reason** for each recommendation (not just numbers) — e.g. `"Add Woody Marks (RB, FA) — no injury tag, WK{n} proj {x} pts, fills RB depth gap left by Laube drop"`.

### 5. `recommend_lineup(df, my_roster, positions_config, current_week)`
Given the roster, assign the optimal starters to slots for the *current week specifically* (using `Week {n}` column, not season totals), respecting `positions_config`. Flag any starter with `Status` in `['Q','D','O','IR']` so the user can make a game-day call, and suggest the best bench alternative if that player sits.

---

## Output / Dashboard

Keep this lightweight to start — don't over-engineer a web app on day one.

**Phase 1 (do this first):** A single command, e.g. `python run_weekly.py`, that:
- Refreshes data (or uses cache)
- Prints a clean terminal report: current lineup w/ flags → recommended lineup → recommended adds/drops w/ reasons → bench depth warnings
- Also writes the same report to `reports/week_{n}.md` so there's a saved record

**Phase 2 (once Phase 1 works):** A simple local dashboard — a static HTML page (or a small Streamlit app) regenerated each run, showing:
- This week's starting lineup with projected points and injury flags (color-coded)
- Recommended moves as cards: player photo optional, name, projection, VOR, one-line reasoning, "why not" for rejected options
- A season-long log of past recommendations vs. what was actually played, so accuracy can be tracked over time

**Phase 3 (optional, later):** Notifications — e.g. a cron job that emails/texts the report every Tuesday (after waivers process) and again Saturday night (before Sunday lock).

---

## Path to Automation

Design now so this doesn't require a rewrite later:

- Every recommendation function should return **structured data** (dict/dataclass), not just printed strings — e.g. `{"action": "add_drop", "add": "Woody Marks", "drop": "Harrison Mevis", "reason": "...", "confidence": "high"}`. The dashboard and any future auto-executor both consume this same structure.
- Separate **decision logic** from **execution**. Never have the decision engine directly call anything that touches Yahoo's site. Leave a clear seam — a not-yet-implemented `executor.py` with stub functions `submit_add_drop(...)`, `set_lineup(...)` — so that when ready, only that file needs real implementation (likely via Yahoo Fantasy Sports' official OAuth API, since `ffbot` itself only scrapes and doesn't submit transactions).
- Add a `--dry-run` (default True) / `--execute` flag pattern from day one, even while `executor.py` is just a stub that logs "would have submitted X" — this makes turning on automation later a config flip, not a redesign.
- Log every recommendation and (once automation exists) every action taken, with timestamps, to a simple file or SQLite DB — useful both for debugging and for evaluating whether the decision engine is actually making good calls over a season.

---

## Nice-to-haves (only if time allows, don't block on these)

- Matchup context: pull opponent defense rank if easily available, factor into recommendations as a tiebreaker.
- Trade evaluator: reuse the same VOR/projection data to evaluate proposed trades, not just adds/drops.
- Multi-week lookahead: use the `Week 1..18` / `Remaining` columns to flag adds that look great this week but bad long-term (bye weeks, tough remaining schedule) vs. the reverse.
- Waiver budget/priority awareness: if the league uses FAAB, factor bid strategy in; if rolling priority, factor in how far down the list the user is (config already has `waiver_priority` — surface it in recommendations, e.g. "you're 10th, don't burn priority on a low-confidence add").

---

## Tech constraints / notes for the agent

- Language: Python (matches `ffbot`).
- Keep dependencies minimal: `ffbot`, `pandas`, `pyyaml`, `python-dotenv`; add `streamlit` only if building Phase 2 dashboard.
- `ffbot.scrape()` takes ~10 minutes — always cache, never re-scrape unnecessarily, and show progress/feedback to the user during long calls.
- Handle non-ASCII team/owner names correctly (this user's team name includes Arabic characters) — test string matching against `Owner` column explicitly for this.
- Write this incrementally: get Phase 1 (terminal report + recommendations) fully working and tested against real data before touching the dashboard or automation stubs.

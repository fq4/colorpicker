# Confirmed Bugs

This file tracks confirmed issues identified during code review. No code changes are included here.

## 1. Incorrect bye-week detection

- **Affected:** `decision_engine.py` - `check_upcoming_byes()`
- **Priority:** High
- **Issue:** The function flags a bye whenever a single week's projection is `0` or `null`, without checking that the week is genuinely sandwiched between normal projections on both sides. This contradicts the function's own docstring.
- **Why it matters:** A missing or incomplete projection, an injury, or a season boundary can be interpreted as a bye week, producing false bye warnings and potentially influencing roster or lineup decisions incorrectly.

## 2. Bench alternatives can themselves be injured or unusable

- **Affected:** `decision_engine.py` - `_find_bench_alternative()`
- **Priority:** High
- **Issue:** When recommending a bench replacement for an injured or questionable starter, the function does not validate the replacement's own injury/status. It can recommend another injured or zero-projection player as the "alternative."
- **Why it matters:** The resulting recommendation can leave the lineup with another player who is unavailable or has no meaningful projection, defeating the purpose of the replacement recommendation.

## 3. Analysis week and report week can disagree

- **Affected:** `run_weekly.py` - `run_weekly()`, `main()`
- **Priority:** High
- **Issue:** The week used for analysis is computed inside `run_weekly()`, while the week used to label the report is obtained separately with `ffbot.current_week()` in `main()`. If cached data represents an older week than the current real-world week, these values can differ.
- **Why it matters:** The report header can identify a different week from the one actually analyzed, making the output misleading and potentially causing users to act on the wrong week's recommendations.

## 4. `Out` is conflated with IR-slot eligibility

- **Affected:** `config.yaml` - `ir_statuses`
- **Priority:** Medium
- **Issue:** The `ir_statuses` list includes `"O"` (Out), which conflates a single-week injury status with actual IR-slot eligibility.
- **Why it matters:** An Out player may not be eligible for an IR roster slot. Treating every Out player as an IR player can distort roster-state and bench-depth calculations. Whether Out players should be excluded from bench-depth counting in the same way as true IR players should be reconsidered.

## 5. Fuzzy player lookup has no disambiguation

- **Affected:** `decision_engine.py` - `_lookup_player()`
- **Priority:** Medium
- **Issue:** If an exact player-name match is not found, `_lookup_player()` falls back to a fuzzy substring match and blindly returns the first result with `.iloc[0]`, with no disambiguation.
- **Why it matters:** Similar or duplicate player names can cause the recommendation to be associated with the wrong player, team, projection, or status.

## 6. Weekly cache writes can use disconnected locations

- **Affected:** `data_layer.py` - `save_weekly_cache()`
- **Priority:** Medium
- **Issue:** `save_weekly_cache()` accepts the application's `data_dir`, but calls `ffbot.save(df, week)` without passing that directory. The result is that ffbot's own cache write and the application's manual CSV cache write may end up in two different, disconnected locations.
- **Why it matters:** The application can appear to save data successfully while later reads use a different cache location, creating stale-data, duplicate-cache, or cache-consistency problems.

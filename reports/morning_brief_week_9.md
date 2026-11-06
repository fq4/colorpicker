# Morning Brief — Week 9

## (a) Data freshness and tests

- The one authorized `--force-refresh` scrape succeeded at **2026-11-05 23:10:34 -05:00**.
  It wrote `data/league_492312_week_9_20261105_231034.csv` with 1,196 players.
- Analytics ingested one new snapshot; the final dry run read that cached snapshot
  at **0.1 hours old**. No second scrape was made.
- The final full suite result after the overnight changes and documentation updates:
  **191 passed** (`python -m pytest tests/ -v`).

## (b) Week 9 matchup

- **Opponent:** Gnus In Motion (team ID 5)
- **Projected totals:** my team 114.6; opponent 113.3
- **Win probability:** 51.7%, **toss-up**
- **Lineup mode:** expected value (projection)
- **Swaps:** no eligible bench/starter swap cleared the 1.0-point margin.
- **Top recommended transactions:**
  1. Add David Montgomery, drop Omar Cooper Jr. — matchup score **+0.0322**.
     Montgomery is listed Out and projects for 0.0 this week; treat the suggestion
     as speculative/ROS-driven, not a Week 9 points boost.
  2. Add Rome Odunze, drop Keenan Allen — **+0.0206**.
  3. Add Vikings DEF, drop Saints DEF — **+0.0139**.
- Justice Hill's waiver add has a larger matchup score (+0.0769) but remains in
  **Not Recommended** because its VOR gain is negative (-23.6).

**Caveats:** Thursday-night players are already locked. Some Week 9 values for
players who have already played are actuals, but ffbot removes the projection/actual
marker; the reported P(win) is therefore not fully certain and is not a points-banked
live probability. The opponent lineup is an optimal-lineup proxy, not the opponent's
confirmed submitted lineup.

## (c) Recommended lineup

| Slot | Player | Projection |
|---|---|---:|
| QB | Dak Prescott | 20.4 |
| K | Cam Little | 17.0 |
| DEF | Jaguars | 11.0 |
| TE | T.J. Hockenson | 8.9 |
| RB1 | Josh Jacobs | 13.0 |
| RB2 | Tyler Allgeier | 10.7 |
| WR1 | Matthew Golden | 12.9 |
| WR2 | Jalen Coker | 10.8 |
| W/R | Rachaad White | 10.0 |

**Total projected points: 114.6.**

## (d) Analytics

- Yahoo MAE is **3.588**; the position-specific blend MAE is **3.610**, across
  **3,706** player-week predictions and eight completed target weeks. The model
  does **not** beat Yahoo, so the projection quality gate remains closed.
- Pooled blend ±1 SD coverage is **74.7%**. Position coverage after the new
  forecast-error priors is:

| Position | N | Residual SD | MAE | Coverage before | Coverage after |
|---|---:|---:|---:|---:|---:|
| DEF | 242 | 5.320 | 4.049 | 82.6% | 70.2% |
| K | 245 | 4.690 | 3.766 | 64.1% | 68.2% |
| QB | 255 | 8.067 | 6.424 | 57.6% | 67.8% |
| RB | 846 | 5.484 | 3.662 | 82.0% | 76.1% |
| TE | 817 | 4.038 | 2.618 | 85.9% | 78.0% |
| WR | 1,301 | 5.158 | 3.537 | 86.2% | 75.2% |

- With the 72-hour age threshold, removing 940 older projections leaves 2,766
  samples: Yahoo MAE **3.566**, model MAE **3.601**. The Yahoo-vs-model conclusion
  does not change; there were no eligible projections of unknown age.
- The full accuracy report includes projection-age sensitivity, per-position
  residual statistics, and every rostered player-week where the analytics and
  engine bye rules disagree: [accuracy_report.md](../analytics_out/accuracy_report.md).

## (e) `use_model_sd` readiness comparison

Both Week 9 runs used temporary config copies and the cached pre-refresh scrape
(3.4 hours old); the real `config.yaml` flags were not changed.

| Setting | My / opponent projection | P(win) and role | Lineup mode | Swaps |
|---|---|---|---|---|
| `use_model_sd: false` | 103.8 / 116.6 | 34.2%, underdog | ceiling | none |
| `use_model_sd: true` | 103.8 / 116.6 | 32.1%, underdog | ceiling | none |

The top two transaction scores were unchanged in both runs: David Montgomery for
Omar Cooper Jr. **+0.0323** and Rome Odunze for Keenan Allen **+0.0206**. The
projection gate remained closed; with the flag enabled, the active uncertainty
source was Yahoo forecast-error SD.

**Readiness decision: no-go for enabling `use_model_sd` yet.** The criterion is
±1 SD coverage between 60% and 76% for every position over the latest four
completed weeks (Weeks 5–8). Coverage for the uncertainty source used with the
projection gate closed was DEF 71.1%, K 68.4%, QB 65.8%, RB 71.1%, TE 78.0%, and
WR 75.8%. TE is outside the criterion. Blend-model SD coverage also exceeded the
upper limit at TE (78.7%) and WR (76.3%).

## (f) What changed overnight

- Bye-week handling now distinguishes analytics' zero/missing clean-projection
  rule from the engine's team-wide all-zero rule (minimum three rostered players).
- Analytics now uses position-specific pooled walk-forward forecast-error SD priors;
  `position_sd` and the model opt-in flags were left unchanged.
- Projection age is stored and evaluated with/without projections older than a
  configurable threshold (72 hours by default).
- Exact, unique scraped team-name matches filled the schedule for Weeks 9–15.
- Commits on this branch:
  - Bye rules: `bd93f15da0f3483c2c3d7b85facf95d64ef0a092`
  - Forecast-error priors: `68ced7254b42e7f4d10c8dd01b40e9ae91932d73`
  - Projection-age sensitivity: `617293bc5ed7a9560ba21dd58f229f4341aedf76`
  - Week 9–15 schedule: `b401790166c4aa519635d9bff247f4b5e471a1ba`
  - The Task 6 brief and documentation are included in the final branch commit.

## (g) Not done and assumptions

- NFL game-level correlation (including DEF/K versus the opposing QB hedge) remains
  unmodeled because the scraped data has no game-opponent identifiers.
- Live points-banked P(win) remains unavailable because ffbot does not preserve
  whether a numeric current-week value is a projection or an actual.
- All seven requested opponent names matched exactly and uniquely. The scraped
  ID-to-name table is:

| Team ID | Scraped name |
|---:|---|
| 1 | Laserlords |
| 2 | Chiefs |
| 3 | Cowpoke Chokers |
| 4 | Girl Power |
| 5 | Gnus In Motion |
| 6 | GoPats |
| 7 | Gnus In Lotion |
| 8 | Legion Of Doom |
| 9 | Runtime Exceptions |
| 10 | Philadelphia Ballers |

ID 7 is shown only because the full scraped ID-to-name table was requested; it was
not used to resolve any opponent name.

| Week | Opponent | Team ID |
|---:|---|---:|
| 9 | Gnus In Motion | 5 |
| 10 | Legion Of Doom | 8 |
| 11 | Runtime Exceptions | 9 |
| 12 | Cowpoke Chokers | 3 |
| 13 | Chiefs | 2 |
| 14 | GoPats | 6 |
| 15 | Philadelphia Ballers | 10 |

## (h) Decisions waiting for you

- Keep `use_model_sd` disabled until every position meets the stated calibration
  criterion over the latest four completed weeks; in particular, TE coverage needs
  improvement.
- Keep `use_model_projections` disabled: the blend does not beat Yahoo's out-of-sample
  MAE and the configured quality gate did not pass.
- No new live-scoring heuristic was enabled.

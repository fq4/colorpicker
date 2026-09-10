# Fantasy Football Decision Engine: Strategy Improvement Plan

## Purpose

This document is a strategy and research roadmap for improving the decision engine in this repository. It is intentionally **planning-only**: this change must not alter application code, configuration, tests, dependencies, or behavior.

The goal is not to make the engine more complicated for its own sake. The goal is to make its recommendations more accurate by improving the decision model, measuring what actually works, and distinguishing situations where the available evidence is strong from situations where the model is effectively guessing.

The central conclusion of this review is:

> The project currently has a useful rules-and-optimizer decision layer, but it is still primarily a **point-estimate optimizer with safety rules around it**. The biggest opportunity is to turn it into a **decision system that evaluates uncertainty, opportunity cost, future roster value, and the actual objective of winning weekly matchups**.

That distinction should drive the next generation of the project.

---

# 1. Current Engine Assessment

## What is already good

The current architecture has several strong foundations:

- `ffbot.optimize()` provides an existing full-season optimization signal rather than forcing the project to reinvent player valuation.
- The project does not blindly trust optimizer output. `recommend_adds_drops()` cross-checks recommendations against bench depth and roster context.
- Lineup recommendations use the current week's projection rather than season totals.
- Bye weeks, injury statuses, roster depth, and post-move lineup effects are explicitly considered.
- Position-specific streaming thresholds recognize that replaceability differs by position.
- Recommendations are structured dataclasses rather than report-only strings.
- The action-plan layer simulates the roster after proposed transactions, which is much closer to the real decision than evaluating each transaction in isolation.

The code therefore has a good **decision-support skeleton**. The next step should be improving the quality of the decisions rather than adding lots of cosmetic features.

## Important limitations discovered during review

### 1. The optimizer is effectively treated as the primary oracle

`recommend_adds_drops()` calls `ffbot.optimize()` and then decorates the resulting recommendations with project-specific checks. That is useful, but it means the project inherits whatever assumptions `ffbot.optimize()` makes about projections, replacement value, roster construction, and transaction value.

The engine currently does not independently ask:

> "What is the expected value of each available action given this roster, this week, this league, and the uncertainty in the projections?"

That should become the core question.

### 2. The engine mostly works with point estimates

A weekly projection such as 13.7 is treated almost like a measured quantity. In reality, a player's likely outcome is a distribution. Two players projected for 13.7 can have very different floors, ceilings, probabilities of being active, and probabilities of actually beating one another.

This matters most when recommendations are close. Research on weekly fantasy projections shows that even good weekly projections explain only a modest portion of actual weekly variance. One recent 11-season analysis found weekly projections explained roughly 3% to 23% of actual variance depending on position. That is a strong argument for modeling uncertainty instead of pretending that the decimal point is precision. See:

- Fantasy Football Analytics, *We Analyzed 11 Seasons of DFS Projections* (2026): https://fantasyfootballanalytics.net/2026/09/we-analyzed-11-seasons-of-dfs-projections-heres-what-we-found.html
- Isaac Petersen, *Fantasy Football Analytics Textbook*, prediction accuracy: https://isaactpetersen.github.io/Fantasy-Football-Analytics-Textbook/evaluating-prediction-accuracy.html

### 3. The lineup optimizer is greedy

`recommend_lineup()` sorts players by the current-week projection and assigns strict positions before flex positions. That is sensible in many ordinary rosters, but it is not a general optimization algorithm.

A roster with multiple flexible eligibility paths can require evaluating assignments jointly. The correct question is not "who is the highest projected player for this slot?" but:

> "Which assignment of all eligible players to all starting slots produces the best legal lineup?"

This is a small optimization problem and should eventually be solved as one.

### 4. Add/drop value is not the same thing as VOR gain

VOR is useful because raw fantasy points cannot be compared directly across positions. Current research and fantasy analytics consistently use replacement value for this reason. But VOR alone does not capture:

- transaction/churn cost
- future weeks
- injury uncertainty
- roster flexibility
- bye-week problems
- playoff schedule
- probability of the player actually being startable
- opportunity cost of dropping the incumbent
- waiver priority
- whether the player can be held without blocking a more valuable future move

The current engine therefore has a good valuation ingredient, but not yet a complete transaction objective.

### 5. The engine does not yet learn from its own historical decisions

The repository has the beginnings of a logging/report architecture, but the decision engine does not yet have a formal historical evaluation loop.

That is the biggest strategic omission.

Without a walk-forward backtest, it is impossible to know whether a new rule is actually improving decisions or merely making reports sound smarter.

---

# 2. Highest-Priority Improvements

## Priority 0: Build the evaluation harness before adding many strategies

### Feature

Create a historical, walk-forward decision-evaluation framework before aggressively changing recommendation logic.

It should replay historical weeks using **only information that would have been available before lineup lock**, then compare the engine against simple baselines.

### Why this could improve accuracy

Fantasy strategy has an enormous danger of overfitting. A rule can sound intuitively correct while making decisions worse.

The backtest becomes the project's scientific instrument. Every proposed strategy should have to answer:

- Does it improve expected points?
- Does it improve actual weekly lineup points?
- Does it improve start/sit pairwise accuracy?
- Does it improve add/drop value over several weeks?
- Does it improve playoff qualification or championship probability?
- Does it work across multiple seasons rather than one lucky year?

### Required baselines

At minimum compare against:

1. Current engine.
2. Raw `ffbot.optimize()` recommendations.
3. Highest weekly projection.
4. Season-to-date average.
5. Simple trailing 3/4-game average.
6. Consensus/market ranking if historical ADP or ECR data is available.
7. Hold/no-move strategy.

A strategy that cannot beat a simple baseline should not be promoted into production.

### Validation method

Use strict walk-forward evaluation:

- Train/calibrate only on prior seasons/weeks.
- Freeze the strategy before evaluating the next period.
- Never allow future actual fantasy points to influence the decision being evaluated.
- Keep a completely untouched final season as a holdout.

This is more important than any individual feature in this document.

---

# 3. Replace Point Estimates with Uncertainty-Aware Decisions

## Feature

Represent each player's weekly expectation as at least:

- median projection
- floor / lower percentile
- ceiling / upper percentile
- estimated probability of exceeding alternatives
- confidence/reliability

Eventually this can become a P10/P50/P90 style model.

### Why it could help

A 14-point projection with a narrow range is a different decision from a 14-point projection with a huge range.

The current engine can produce false precision:

> Player A: 14.2
> Player B: 13.8
> Start A.

An uncertainty-aware engine might discover:

> A median: 14.2, P10: 9.8, P90: 18.1
> B median: 13.8, P10: 11.7, P90: 16.1
>
> A is slightly better on expected value, but B has a much safer floor.

The correct choice then depends on the matchup and desired risk profile.

### Strategy improvement

Introduce a concept of **decision margin**:

- Large projection gap + similar uncertainty = strong recommendation.
- Tiny projection gap + overlapping ranges = lean/pass.
- Tiny projection gap + radically different ranges = roster/matchup-dependent decision.

This prevents the engine from manufacturing confidence where none exists.

### Accuracy benefit

The greatest improvement may not be getting more calls right. It may be avoiding unnecessary wrong calls when two players are effectively indistinguishable.

Recent start/sit research illustrates this problem: one 2025 evaluation found a model could perform well on easy decisions while being close to a coin flip on genuinely difficult choices. That suggests the hard-decision band deserves special treatment rather than pretending every ranking is equally reliable.

Source: https://moneydawg.io/research/backtests~fantasy_eval~FINDINGS_START_SIT.md

---

# 4. Build a True Legal Lineup Optimizer

## Feature

Replace greedy slot assignment with a joint optimization over all players and starting slots.

Conceptually:

> maximize total lineup value subject to roster-slot eligibility and one-player-one-slot constraints.

### Why it could help

The current implementation fills strict positions first and flex positions afterward. That works often, but it can fail when flexible eligibility creates an opportunity cost.

The optimizer should evaluate the complete assignment rather than depending on slot ordering.

### Extend it beyond raw projection

Eventually the objective should support:

- expected points
- uncertainty penalty/bonus
- injury probability
- matchup-adjusted value
- correlation
- roster strategy

The first implementation should remain simple and use expected points. Complexity should only be added after backtesting demonstrates value.

---

# 5. Separate "Expected Points" from "Probability of Winning"

## Feature

Add two lineup modes:

### Expected-value mode

Maximize projected fantasy points.

### Matchup mode

Estimate the probability that the user's lineup beats the opponent's projected lineup.

### Why it could help

Fantasy football is not actually scored on expected points. It is scored on the realized weekly total relative to an opponent.

Suppose the user is projected to win comfortably. The engine may rationally prefer a lower-variance lineup.

If the user is projected to lose badly, maximizing ceiling may be more valuable than maximizing median.

That leads to a useful strategy concept:

- **Favorite:** emphasize floor and reliability.
- **Even matchup:** emphasize median/expected value.
- **Underdog:** emphasize ceiling and upside.

This is a decision-theory improvement rather than another fantasy "rule."

### Important safeguard

Do not implement this based on intuition alone. Backtest:

- maximize mean
- maximize median
- maximize win probability
- maximize a risk-adjusted objective

Then measure actual head-to-head results.

---

# 6. Add Projection Calibration and Model Blending

## Feature

Instead of treating `Week N` as truth, maintain historical error statistics for each position and situation.

Possible future inputs:

- current projection
- recent performance
- season average
- prior-year performance
- consensus projection
- market rank/ADP
- role/opportunity indicators

Then blend them according to walk-forward performance.

### Why it could help

Projection quality varies by position and circumstance. A model can systematically overestimate or underestimate certain player classes.

A historical calibration layer can learn:

- QB projection bias
- RB volume uncertainty
- WR target volatility
- TE touchdown volatility
- rookie uncertainty
- backup/committee uncertainty
- injury-return uncertainty

### Creative extension: confidence-weighted ensemble

Instead of choosing one projection source, treat each source as a noisy sensor.

Example concept:

`final_projection = weighted combination of available signals`

where weights are learned from **prior** out-of-sample accuracy.

A recent 11-season analysis of DFS projections found that historical source superiority does not necessarily persist reliably from season to season, which argues against permanently trusting one source. Equal or dynamically calibrated blending may be more robust.

Source: https://fantasyfootballanalytics.net/2026/09/we-analyzed-11-seasons-of-dfs-projections-heres-what-we-found.html

---

# 7. Turn Injury Status into Probability, Not a Boolean

## Feature

Replace simple status handling with estimated availability probability.

Current logic mostly treats statuses as categories such as healthy, questionable, or IR.

Future model:

`expected_value = probability_active × expected_points_if_active`

with separate uncertainty around performance after activation.

### Why it could help

A questionable player projected for 18 points should not automatically be treated like a healthy 18-point player.

Likewise, an "Out" designation before Sunday should not necessarily be treated as a season-long drop candidate.

The engine should distinguish:

- active probability
- expected points if active
- replacement value if inactive
- deadline for making the decision

### Creative extension: deadline-aware recommendations

The engine should be able to say:

> "Do not make this transaction yet. Player X is a 70% start probability and the decision can wait until Sunday morning."

This can reduce unnecessary roster churn.

---

# 8. Add Transaction Cost and Churn Penalties

## Feature

Not every positive-VOR transaction should be recommended.

Introduce a transaction-cost concept that can include:

- roster spot consumed
- waiver priority consumed
- expected future value of the dropped player
- likelihood the added player remains useful
- opportunity cost of using the transaction now

### Why it could help

The current engine can correctly identify that Player A is slightly better than Player B while still making a bad managerial decision by moving B.

A transaction is an investment, not a free comparison.

A useful conceptual score is:

`net_transaction_value = immediate_gain + future_gain + flexibility_gain - transaction_cost - opportunity_cost`

The exact weights should be learned through backtesting rather than chosen arbitrarily.

---

# 9. Model the Waiver Wire as an Inventory of Future Options

## Feature

Stop evaluating a free agent only by his current projection.

For each candidate estimate:

- current weekly value
- expected value over the next 2–4 weeks
- probability his role increases
- probability he becomes droppable
- future bye usefulness
- playoff usefulness
- likelihood he remains available later

### Why it could help

A mediocre-looking waiver add can be extremely valuable if the player is likely to gain a starting role.

Conversely, a one-week streamer can be valuable for one week but not worth using a roster spot if the same type of player will be freely available next week.

This turns the waiver engine from a **snapshot ranking** into an **option-value model**.

---

# 10. Add Opportunity-Cost / "Who Can I Drop?" Analysis

## Feature

For every suggested add, explicitly evaluate the entire roster as potential drop candidates.

Instead of:

> Best available player = Add X.

Ask:

> Best net roster improvement = Add X and remove Y.

Then compare X/Y against every plausible alternative transaction.

### Why it could help

This is especially important because a waiver add is not valuable in isolation. It is valuable relative to the roster spot it consumes.

The project already has some post-drop depth checking. The next step is to make the **drop opportunity cost part of the optimization objective itself**, rather than mainly a warning after `ffbot.optimize()` chooses the move.

---

# 11. Add Multi-Week Roster Value

## Feature

For every add/drop candidate, calculate:

- Week 1 value
- Weeks 1–2 value
- Weeks 1–4 value
- rest-of-season value
- playoff-window value

### Why it could help

A one-week projection can cause the engine to chase a temporary spike.

Conversely, a player who looks mediocre this week can be a very valuable addition if the next three weeks are strong and the roster can absorb the short-term cost.

### Recommended display

Every major transaction could eventually show:

| Horizon | Net value |
|---|---:|
| This week | +X |
| Next 2 weeks | +Y |
| Next 4 weeks | +Z |
| Playoffs | +W |

This makes short-term versus long-term strategy explicit.

---

# 12. Add Playoff-Window Optimization

## Feature

Once enough of the season is known, give Weeks 15–17/18 their own strategic value.

### Why it could help

A regular-season ranking is not necessarily a championship-optimal ranking.

A player with slightly lower rest-of-season value may become much more valuable if his playoff schedule is favorable and his position is difficult to replace.

### Guardrail

Do not over-optimize for hypothetical playoffs early in the season. The engine should gradually increase playoff weighting as the team becomes more likely to qualify.

This suggests a dynamic weighting model:

`playoff_weight = f(current_week, playoff_probability, roster_strength)`

---

# 13. Add Schedule and Matchup Context, But Treat It as a Weak Signal

## Feature

Use matchup information as one input rather than a giant matchup multiplier.

Potential factors:

- opponent fantasy points allowed by position
- defensive efficiency
- pace/play volume
- implied team total
- home/away
- weather
- offensive line health
- quarterback availability

### Why it could help

Streaming positions are particularly matchup-sensitive. Current fantasy strategy literature consistently treats QB/TE/DST/K as more streamable than core RB/WR assets.

NFL's own fantasy coverage explicitly describes D/ST as highly matchup-dependent and recommends streaming rather than treating a defense as a set-and-forget asset.

Sources:

- NFL.com: https://www.nfl.com/news/week-1-waiver-wire-streaming-defenses-to-target-0ap3000000524295
- Athlon: https://athlonsports.com/fantasy/fantasy-football-streaming-strategy-advice

### Critical warning

Do not create a crude rule such as:

> "Defense ranks 30th against WR = +3 points."

Matchup variables should be validated independently. Otherwise the model will double-count information already present in the underlying projection.

---

# 14. Add Correlation and Game-Environment Awareness

## Feature

Eventually model player correlation, particularly:

- QB + WR/TE
- opposing QB + WR/TE
- RB + defense
- game-total environments

### Why it could help

When optimizing for variance, correlated players can change the distribution of the entire lineup.

Example concept:

- QB + WR stack can increase lineup ceiling.
- QB + opposing WR can create a different high-scoring game environment.
- RB + defense can create positive game-script correlation in some circumstances.

This should not automatically become "always stack." The correct use is to adjust lineup distribution when the objective is ceiling, floor, or win probability.

This is particularly interesting for the proposed favorite/underdog decision mode.

---

# 15. Build a Roster Dependency Graph

## Feature

Represent relationships such as:

- handcuff RB
- backup QB
- WR/TE dependent on quarterback
- players whose value rises if a teammate is inactive
- players competing for the same role

### Why it could help

Roster value is not additive.

Two players with identical projections can have very different strategic value depending on what else is already rostered.

Example:

> Holding a backup RB may be more valuable if you already own the starter because one injury creates a concentrated opportunity.

This is an example of **insurance value** that ordinary VOR misses.

### Creative extension

Calculate a player's value as:

`player_value = standalone_value + roster_synergy + insurance_value + future_option_value`

This is a promising area for differentiating the project from ordinary ranking tools.

---

# 16. Add Roster Flexibility as an Explicit Metric

## Feature

Measure how many future lineup states the roster can legally support.

Potential metric:

**Lineup Flexibility Score** = number/quality of plausible legal starting combinations above a minimum projection threshold.

### Why it could help

A roster is more valuable when injuries and bye weeks can be absorbed without emergency waiver moves.

This gives the engine a way to recognize that a multi-position player or deep bench option can be valuable even when his immediate projection is mediocre.

It also provides a quantitative reason not to make some otherwise-positive swaps.

---

# 17. Improve Confidence: Calibrate It Instead of Hard-Coding It

## Current weakness

The current confidence system is mostly threshold-driven:

- flagged = low
- sufficiently large VOR gain = high
- otherwise medium

That is convenient but not statistically meaningful.

### Proposed feature

Eventually make confidence empirical.

For example:

> "When this type of recommendation had a projected edge of 4–6 points historically, it produced a positive realized gain 68% of the time."

Then confidence becomes a measured probability rather than a label attached to a threshold.

### Why it could help

The user can distinguish:

- **High confidence:** historically reliable situation.
- **Medium:** useful but uncertain.
- **Low:** mostly speculative.
- **Pass:** evidence too weak to justify action.

The most important new category may actually be **PASS**.

A decision engine should know when it does not have enough edge to justify acting.

---

# 18. Introduce a "No Move" / Hysteresis Rule

## Feature

Require a meaningful advantage before changing a roster or lineup decision.

### Why it could help

Projection noise can cause the engine to oscillate:

> Start A today.
> Start B tomorrow.
> Add A.
> Drop A.
> Add B.

A small projected edge may not justify action.

This is essentially hysteresis from control systems: require the new state to be sufficiently better than the old state before switching.

For transactions:

`new_value - old_value > transaction_threshold`

For lineup changes:

`new_expected_value - old_expected_value > decision_margin`

This should be especially strong for RB/WR and weaker for streaming positions.

---

# 19. Late-Information / Lineup-Lock Scheduler

## Feature

Make recommendations deadline-aware.

Instead of one static weekly report, classify decisions as:

- decide now
- monitor
- wait for inactive designation
- must decide before early game
- late-game contingency

### Why it could help

Information arrives over the week. A recommendation made Tuesday should not be treated as equally final on Sunday morning.

The best action can be to **wait**.

This is another reason the current binary healthy/questionable model is insufficient.

---

# 20. Add Waiver Priority Economics

## Feature

The configuration already knows waiver type and priority. The decision engine should eventually use them.

For rolling priority:

- estimate how likely a player is to reach the user's priority.
- estimate the opportunity cost of using the priority now.
- classify adds as priority-worthy or disposable.

For FAAB:

- estimate expected marginal value by bid amount.
- estimate probability of winning at each bid.
- optimize expected value rather than recommending a single arbitrary bid.

### Why it could help

A player being the best free agent does not mean he is worth consuming a scarce acquisition resource.

---

# 21. Market Signal: Use ADP / Ownership as Information, Not Truth

## Feature

If historical ADP becomes available, use it as a market signal.

Do not simply rank players by ADP.

Instead calculate:

`model_value - market_expectation`

### Why it could help

ADP contains information about the collective market's expectations, injury news, role assumptions, and player popularity.

But it is not a projection.

Recent 2026 analysis has shown that ADP can lag preseason role/injury information, while other backtests suggest a strategy that selectively overrides ADP can outperform an ADP-first policy. The important lesson is not "ignore ADP" or "trust ADP"; it is to treat ADP as **price information**.

Sources:

- https://www.nypost.com/2026/09/05/betting/savvy-fantasy-football-drafters-can-exploit-adp-like-with-patriots-running-backs-bears-receivers/
- https://overadp.com/2026/adp-vs-model/

---

# 22. Add a Real Decision Ledger

## Feature

Every weekly decision should eventually be recorded with:

- timestamp
- information available at decision time
- recommendation
- alternatives considered
- projection values
- uncertainty values
- actual result
- counterfactual result
- transaction made/not made

### Why it could help

This creates the feedback loop necessary to improve the engine scientifically.

It also prevents hindsight contamination. Six weeks later, the system should still be able to answer:

> "What did the engine know when it made that recommendation?"

---

# 23. Evaluate Decisions, Not Just Players

This should become a core philosophy of the project.

Traditional fantasy tools ask:

> Who is the best player?

A decision engine should ask:

> What decision produces the best expected outcome from this exact state?

That means the evaluation framework should include at least four levels:

### Player-level

Did projections predict player outcomes?

### Pairwise

When the engine preferred A over B, did A actually outperform B?

### Action-level

Did an add/drop improve the roster over the following horizon?

### Outcome-level

Did the decisions improve:

- weekly points
- matchup win rate
- playoff qualification
- playoff advancement
- championship probability

The project should resist optimizing one metric while silently damaging another.

---

# 24. Recommended Feature Order

## Phase 1 — Measurement first

1. Historical walk-forward backtest harness.
2. Decision ledger.
3. Baseline comparison framework.
4. Projection error/calibration reports.
5. Pairwise start/sit evaluation.

**Reason:** Without this phase, later improvements cannot be trusted.

## Phase 2 — Fix the decision mathematics

6. Joint legal lineup optimizer.
7. Uncertainty/range model.
8. No-move decision margin.
9. Empirical confidence calibration.
10. Injury availability probabilities.

**Reason:** These directly attack the biggest weakness: overconfidence in point estimates.

## Phase 3 — Improve roster decisions

11. Net transaction value.
12. Drop opportunity-cost analysis.
13. Multi-week value.
14. Waiver priority economics.
15. Roster flexibility score.
16. Roster dependency/insurance value.

**Reason:** These turn player rankings into actual roster management.

## Phase 4 — Improve weekly strategy

17. Matchup context.
18. Favorite/underdog lineup objectives.
19. Correlation/game environment.
20. Late-information scheduling.
21. Streaming optimization.

**Reason:** These should be added only after the core projection and decision model is measured.

## Phase 5 — Longer-term strategy

22. Playoff-window optimization.
23. Market/ADP signal.
24. Dynamic strategy selection.
25. Learned ensemble weighting.

---

# 25. A More Ambitious Future Architecture

The eventual decision pipeline should look conceptually like this:

```text
RAW DATA
   |
   v
PLAYER STATE
(injury, role, schedule, availability)
   |
   v
PROJECTION ENSEMBLE
(expected points + uncertainty)
   |
   v
PLAYER VALUE
(VOR + replacement + market + future value)
   |
   v
ROSTER STATE
(depth + flexibility + dependencies + waivers)
   |
   v
ACTION GENERATOR
(lineup / add / drop / hold / wait)
   |
   v
ACTION SIMULATOR
(evaluate resulting roster)
   |
   v
OBJECTIVE
(expected points / win probability / playoff value)
   |
   v
DECISION
   |
   v
CONFIDENCE + ALTERNATIVES + REASON
   |
   v
DECISION LEDGER
   |
   v
WALK-FORWARD EVALUATION
```

The key conceptual change is that **the player is no longer the unit of optimization. The action is.**

---

# 26. What NOT to Do

Several tempting improvements should explicitly be avoided unless a backtest proves they work.

### Do not simply add more fantasy rules

Examples:

- "Always start players against Team X."
- "Never draft two RBs from the same team."
- "Always stack QB + WR."
- "Always stream defense."
- "Avoid players after a bad game."

These are hypotheses, not facts.

### Do not pile matchup multipliers onto projections

This risks double-counting information already present in the projection source.

### Do not use future actual results in calibration

That is leakage and will make the backtest look spectacular while making the live system worthless.

### Do not optimize exclusively for average weekly points

The goal is ultimately winning fantasy matchups, not producing a beautiful projection spreadsheet.

### Do not make the engine more aggressive merely because it can

A good decision engine should frequently say:

> **No meaningful edge. Hold.**

That may be one of the most valuable recommendations it can make.

---

# 27. Success Criteria

The project should not call these improvements successful merely because the reports look more sophisticated.

A feature should graduate from experimental to production only if walk-forward testing demonstrates a meaningful improvement against the current engine or a simple baseline.

Recommended acceptance criteria:

- Improvement holds across multiple seasons.
- Improvement survives an untouched holdout season.
- Improvement is not limited to one position.
- Improvement is not caused by future-data leakage.
- Improvement remains after accounting for multiple comparisons / strategy testing.
- Recommendation confidence is calibrated against realized outcomes.
- The feature does not materially increase unnecessary roster churn.

For close start/sit decisions, report confidence intervals around accuracy rather than claiming victory from a tiny percentage difference.

---

# 28. Final Strategic Recommendation

If only five improvements are built next, build these:

### 1. Walk-forward backtesting

This is the foundation. It tells us which ideas are actually useful.

### 2. Uncertainty-aware projections

The current point-estimate model is too certain for the amount of weekly variance in fantasy football.

### 3. Joint lineup optimization

Remove the remaining greedy assignment assumptions.

### 4. Net transaction value

Evaluate the add/drop action against the whole roster, future weeks, and transaction opportunity cost rather than relying primarily on VOR gain.

### 5. Decision-specific objectives

Eventually let the engine distinguish between maximizing expected points, maximizing win probability, protecting a lead, and maximizing upside when an underdog.

These five improvements reinforce one another:

**better measurement -> better projections -> better roster valuation -> better action selection -> better outcome evaluation.**

That is a much more promising path than simply adding more fantasy-football heuristics.

---

# Research Notes

The strategy recommendations above were informed by current fantasy analytics research and strategy literature, including:

- Fantasy Football Analytics, multi-season projection accuracy research: https://fantasyfootballanalytics.net/2026/09/we-analyzed-11-seasons-of-dfs-projections-heres-what-we-found.html
- Fantasy Football Analytics, historical source weighting and projection methodology: https://fantasyfootballanalytics.net/which-dfs-projections-are-most-accurate
- Fantasy Football Analytics textbook, prediction evaluation and calibration: https://isaactpetersen.github.io/Fantasy-Football-Analytics-Textbook/evaluating-prediction-accuracy.html
- Start/sit backtest emphasizing pairwise decisions and the difficulty of close calls: https://moneydawg.io/research/backtests~fantasy_eval~FINDINGS_START_SIT.md
- NFL.com discussion of D/ST matchup dependence and streaming: https://www.nfl.com/news/week-1-waiver-wire-streaming-defenses-to-target-0ap3000000524295
- Athlon overview of streaming and replaceable positions: https://athlonsports.com/fantasy/fantasy-football-streaming-strategy-advice
- Current VOR/VBD research and draft-state valuation concepts: https://www.draftmyteam.net/guides/value-over-replacement/ and https://www.green18.app/draft-science/
- Current research on ADP versus model-driven drafting: https://overadp.com/2026/adp-vs-model/

These sources should be treated as evidence and hypotheses, not as rules to hard-code. The project's own historical backtest should ultimately decide which strategies deserve implementation.

---

# Implementation Constraint for This Plan

**This document is a plan only.** No code, configuration, tests, dependencies, or existing documentation should be changed as part of creating this plan. Each future feature should be implemented separately, tested independently, and evaluated through the walk-forward framework before being adopted.

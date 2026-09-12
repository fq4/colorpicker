# Fantasy Football Reasoning Engine Implementation

> **Implementation status:** The first deterministic transaction-reasoning phase is now
> implemented in `reasoning_engine.py` and integrated with `AddDropRecommendation`.
> Implemented behavior includes complete add/drop simulation, roster size/duplicate/legal
> starter validation, HOLD comparison, current-week and ROS deltas, VOR delta, starter
> impact, configurable streaming-position classification, scarcity, opportunity cost,
> drop resistance, alternatives, standardized confidence, contradiction checks, and a
> structured reasoning ledger. The existing optimizer, lineup engine, action-plan ordering,
> bye lookahead, and LLM advisory boundary remain intact.
>
> Remaining future work includes richer bye-coverage modeling, waiver timing economics,
> historical outcome scoring, and deeper candidate generation. The sections below remain
> the design target; future changes must preserve the implemented deterministic boundary.

## 1. Purpose

This document defines improvements to the fantasy football recommendation engine's decision-making architecture.

The goal is not simply to make the engine produce more recommendations or to make its explanations sound more intelligent.

The goal is to make the engine make **better roster decisions**.

The engine should reason about complete roster states and transactions rather than evaluating free agents independently.

The central design principle is:

> A player's value does not automatically make acquiring that player a good transaction.

A good recommendation must consider the entire resulting roster, the lineup it produces, what was sacrificed to make the transaction, and the difference between immediate and rest-of-season value.

---

# 2. Problems Identified

The existing engine has demonstrated several classes of reasoning problems.

## 2.1 Player value is being confused with transaction value

A player can have positive VOR while still being a poor addition to a particular roster.

For example:

* A TE with positive VOR may not be worth adding if the roster already has a strong TE.
* An RB with positive VOR may not be worth acquiring if doing so requires dropping valuable RB depth.
* A DEF may have useful current-week value while another DEF is more valuable for the rest of the season.

The engine must therefore evaluate:

```text
PLAYER VALUE
```

separately from:

```text
TRANSACTION VALUE
```

---

## 2.2 Resulting roster legality is not guaranteed

A recommendation can currently produce an impossible roster state.

For example, a league with six bench slots must never produce a recommended roster containing seven bench players.

Roster legality must become a hard constraint.

It cannot merely be another scoring factor.

---

## 2.3 The engine does not consistently account for opportunity cost

Adding a player is only half of a transaction.

The engine must ask:

> What am I giving up to acquire this player?

The correct question is not:

```text
Is Player X good?
```

It is:

```text
Is the roster better after acquiring Player X and removing Player Y?
```

---

## 2.4 Current-week and rest-of-season value can conflict

A player can be a strong Week 1 play but a poor ROS roster asset.

Conversely, a player can have little immediate value but be useful for future roster construction.

These dimensions must remain separate.

The engine should never hide a significant current-week/ROS tradeoff inside a single unexplained score.

---

## 2.5 VOR is useful but insufficient

VOR should remain an important analytical metric.

However:

```text
positive VOR ≠ automatic add
```

VOR should inform the decision rather than dictate it.

The engine must consider:

* positional scarcity
* roster construction
* starting lineup impact
* opportunity cost
* bench depth
* bye coverage
* streaming characteristics
* current-week value
* ROS value

---

## 2.6 Explanations can contradict calculations

The engine has produced cases where the numerical transaction result contradicts the natural-language explanation.

For example, a transaction can have a negative calculated VOR delta while the explanation claims that the transaction improves VOR.

This must become a detectable engine error.

Calculated values are authoritative.

Narrative explanations must be generated from those values and validated against them.

---

# 3. Target Architecture

The recommendation engine should conceptually operate as follows:

```text
CURRENT ROSTER
      │
      ▼
Generate Candidate Actions
      │
      ▼
Validate Candidate Action
      │
      ├── Illegal → Reject
      │
      ▼
Simulate Resulting Roster
      │
      ▼
Optimize Best Legal Lineup
      │
      ▼
Evaluate Resulting Roster
      │
      ├── Current Week
      ├── Rest of Season
      ├── VOR
      ├── Scarcity
      ├── Depth
      ├── Bye Coverage
      └── Opportunity Cost
      │
      ▼
Compare Against HOLD
      │
      ▼
Compare Meaningful Alternatives
      │
      ▼
Contradiction / Sanity Validation
      │
      ▼
Assign Confidence
      │
      ▼
Rank Recommendations
      │
      ▼
Generate Final Recommendation
```

The implementation should adapt this architecture to the existing repository rather than unnecessarily rewriting working systems.

---

# 4. Roster State Model

Introduce or strengthen a centralized representation of the complete roster state.

The roster state should contain enough information to determine:

* all rostered players
* starting lineup
* bench
* IR
* positional eligibility
* roster capacity
* league configuration
* current-week projections
* ROS projections
* VOR
* bye weeks
* free-agent availability
* waiver status

The exact data structures should follow the existing project architecture.

Avoid duplicating roster rules in multiple parts of the application.

There should be one authoritative roster-validation mechanism.

---

# 5. Legal Roster Validation

Create a centralized roster validation layer.

Every simulated roster must pass validation before it can become a recommendation.

At minimum validate:

* total roster size
* starting lineup size
* bench capacity
* positional eligibility
* required positional slots
* required bench minimums
* IR rules where applicable
* FLEX eligibility
* duplicate players
* add/drop requirements
* waiver constraints where represented by the data

## 5.1 Hard constraint

An illegal roster must never be ranked as a valid recommendation.

Do not allow the scoring system to compensate for an illegal roster.

For example:

```text
Illegal roster +10 projected points
```

must still lose to:

```text
Legal roster +1 projected point
```

The illegal roster should simply be rejected.

## 5.2 Tests

Add tests covering:

* too many bench players
* too few required bench positions
* invalid starting lineup
* invalid FLEX player
* duplicate player
* missing drop when roster is full
* valid add/drop
* valid waiver claim
* valid IR behavior where supported

---

# 6. Transaction Simulation

Every candidate add/drop must be evaluated as a complete state transition.

Conceptually:

```text
Current Roster
    ↓
Add Player
    ↓
Drop Player
    ↓
Validate
    ↓
Resulting Roster
```

The engine should not evaluate an addition independently from the corresponding drop.

For each candidate transaction calculate:

* before roster state
* after roster state
* before optimal lineup
* after optimal lineup
* current-week lineup projection before
* current-week lineup projection after
* current-week delta
* ROS roster value before
* ROS roster value after
* ROS delta
* VOR before
* VOR after
* VOR delta

Where possible, retain these values as structured data rather than deriving them later from prose.

---

# 7. HOLD as an Explicit Action

The engine must always consider:

```text
HOLD
```

as a valid alternative.

A transaction should not be recommended merely because it improves some isolated metric.

The engine should determine whether:

```text
Transaction Result > HOLD
```

according to the appropriate decision criteria.

The HOLD action should also have a fully evaluated roster state.

---

# 8. Current-Week Evaluation

Current-week value should be evaluated independently.

At minimum calculate:

* best legal lineup
* total projected points
* position-by-position starters
* current-week starter changes caused by transaction
* current-week lineup delta

The key measure is not simply:

```text
new player's projection
```

but:

```text
resulting lineup projection
-
current lineup projection
```

This distinction is essential.

If a newly acquired player remains on the bench, the immediate lineup improvement may be zero.

---

# 9. Rest-of-Season Evaluation

ROS value should be evaluated independently from current-week value.

At minimum calculate:

* total roster ROS value
* ROS VOR
* positional ROS depth
* future replacement availability
* bye coverage
* expected roster flexibility

A transaction can therefore have:

```text
Positive Week 1
Negative ROS
```

or:

```text
Negative Week 1
Positive ROS
```

The engine should preserve that distinction.

---

# 10. Opportunity Cost

Every add/drop must explicitly account for the value sacrificed.

Conceptually:

```text
Transaction Value =
Resulting Roster Value
-
Current Roster Value
```

The calculation should not be reduced to:

```text
Added Player VOR
```

Evaluate the dropped player using available information including:

* current-week projection
* ROS projection
* VOR
* positional scarcity
* roster depth
* bye coverage
* starting potential
* replacement availability

The engine should choose the best legitimate drop candidate rather than arbitrarily selecting one.

---

# 11. Drop Resistance

Introduce a derived concept called:

```text
dropResistance
```

This represents how difficult it is to justify dropping a player.

It should incorporate objective information available to the engine, including:

* ROS value
* current-week value
* VOR
* positional scarcity
* positional depth
* bye coverage
* starter potential
* replacement availability

Do not make drop resistance an arbitrary subjective number.

Document its calculation.

Drop resistance should influence which rostered player is considered when evaluating a new acquisition.

A player with substantial strategic value should require a stronger reason to be dropped.

---

# 12. Dynamic Positional Scarcity

Existing VOR thresholds should remain supported.

However, they should not be the sole source of positional reasoning.

Scarcity should be evaluated at multiple levels:

## League scarcity

How difficult is the position to replace across the league?

## Roster scarcity

How difficult is the position to replace on this specific roster?

## Waiver scarcity

How good are the realistic alternatives currently available?

The same player can therefore have different strategic importance depending on the roster.

---

# 13. Starter Upgrade Detection

Every potential acquisition should determine whether it actually improves the starting lineup.

Classify the effect as one of:

```text
STARTER_UPGRADE
NO_STARTER_CHANGE
STARTER_DOWNGRADE
```

If the player cannot enter the optimal lineup, the engine should not pretend that acquiring the player immediately improves the lineup.

FLEX eligibility must be included.

---

# 14. Transaction Purpose Classification

Every meaningful transaction should receive one primary classification:

```text
STARTER_UPGRADE
BENCH_UPGRADE
DEPTH
BYE_COVERAGE
STREAMING
SPECULATIVE_UPSIDE
ROSTER_CONSOLIDATION
NO_MEANINGFUL_IMPROVEMENT
```

The classification should be derived from the resulting roster state.

Do not classify a transaction merely from the position of the acquired player.

---

# 15. Streaming Positions

The architecture should support configurable streaming positions.

At minimum:

```text
DEF
K
```

should be treated as potentially streamable.

Streaming positions have different roster-management characteristics from positions such as RB and WR.

For streaming positions, current-week projection and matchup opportunity can receive greater emphasis.

However, ROS value must not be ignored.

The configuration should allow future changes rather than permanently hard-coding assumptions.

---

# 16. Bye-Week Analysis

Use actual bye-week information available in the player data.

Evaluate:

* number of starters sharing a bye
* bench coverage
* positional coverage
* whether a proposed transaction creates a future weakness
* whether a transaction removes useful bye coverage

Do not overreact to every bye overlap.

Bye analysis should materially affect a decision only when it changes roster resilience.

---

# 17. Recommendation Confidence

Add a standardized confidence classification:

```text
MUST_DO
STRONG
LEAN
SPECULATIVE
DO_NOT_MAKE
```

Confidence should be evidence-based.

Factors may include:

* magnitude of calculated advantage
* current-week benefit
* ROS benefit
* opportunity cost
* positional scarcity
* roster legality
* number of assumptions
* quality/completeness of available data
* whether the move requires speculation

A small advantage with significant uncertainty should not receive the same confidence as a clear improvement.

---

# 18. Alternative-Action Analysis

For meaningful roster decisions, evaluate alternatives.

At minimum consider:

```text
HOLD
Best candidate transaction
Alternative drop candidate
Alternative player
Streaming alternative where applicable
```

The engine does not necessarily need to expose every alternative in its final user-facing output.

However, the decision engine should know why the selected action beats reasonable alternatives.

This prevents:

```text
Best free agent
```

from automatically becoming:

```text
Best transaction
```

---

# 19. Reasoning Ledger

Introduce a structured decision ledger for recommendations.

The ledger should capture the factual basis of a decision.

Example:

```text
ACTION:
Lions DEF for Ravens DEF

CURRENT WEEK:
+1.03 projected starter points

VOR:
+4.84 relative to dropped player

ROS:
Positive/negative result from complete roster comparison

DEPTH:
Retains Texans as alternate DEF

COST:
One transaction and loss of Ravens

ALTERNATIVES:
HOLD
Other available DEF
Other streaming options

DECISION:
STRONG
```

The exact implementation format is up to the existing architecture.

The important requirement is that the final explanation can be traced back to calculated values.

---

# 20. Contradiction Detection

Add a validation stage after recommendation generation.

The system must verify that the recommendation's explanation agrees with the calculated data.

Detect contradictions such as:

### VOR contradiction

Explanation says:

```text
VOR improves
```

while:

```text
vorDelta < 0
```

### Projection contradiction

Explanation says:

```text
improves current week
```

while:

```text
currentWeekDelta <= 0
```

### Threshold contradiction

Explanation says:

```text
clears threshold
```

while the calculated metric does not clear the threshold.

### Starter contradiction

Explanation says:

```text
starter upgrade
```

while the optimized resulting lineup is unchanged or worse.

### Roster contradiction

Explanation says:

```text
no drop required
```

while roster capacity requires a drop.

### Legality contradiction

Recommendation produces an illegal roster.

## Required behavior

When a contradiction is detected:

1. Do not emit the contradictory explanation.
2. Recalculate or regenerate the explanation from authoritative values.
3. If the recommendation itself is invalid, reject it.
4. Record the contradiction for diagnostics/testing.

---

# 21. Calculations Are Authoritative

The engine must establish a clear hierarchy:

```text
Raw supplied data
        ↓
Calculated roster state
        ↓
Calculated transaction metrics
        ↓
Recommendation classification
        ↓
Natural-language explanation
```

Natural-language reasoning must never override calculated values.

Do not allow an LLM-generated explanation to change:

* projections
* VOR
* roster legality
* lineup optimization
* transaction deltas
* positional eligibility

The LLM, if used, should explain the decision rather than invent the decision.

---

# 22. LLM Reasoning Boundary

If the project uses an LLM to generate recommendations or explanations, clearly separate deterministic calculations from LLM reasoning.

The LLM may be responsible for:

* interpreting calculated tradeoffs
* explaining why a transaction is preferable
* identifying strategic considerations supported by supplied data
* presenting alternatives
* communicating uncertainty

The LLM should not be responsible for:

* determining whether a roster is legal
* calculating VOR
* calculating projections
* determining roster capacity
* deciding whether a player is positionally eligible
* inventing missing data
* overriding deterministic transaction results

Where possible, provide the LLM with structured calculated state rather than raw unstructured data.

---

# 23. Recommendation Scoring

Review the existing recommendation score.

Do not simply add many weighted variables to the existing formula without understanding how the existing system works.

Prefer a layered decision process:

## Layer 1: Hard constraints

Reject illegal transactions.

## Layer 2: Mandatory checks

Evaluate:

* starter impact
* opportunity cost
* HOLD comparison
* positional viability

## Layer 3: Strategic metrics

Evaluate:

* VOR
* ROS value
* scarcity
* depth
* bye coverage
* streaming characteristics

## Layer 4: Decision classification

Assign:

* recommendation type
* confidence
* reasoning ledger

This reduces the risk that a single arbitrary weight overwhelms a fundamentally bad transaction.

---

# 24. Regression Scenario

The implementation must use the existing fantasy scenario that exposed the reasoning problems.

Known candidates include:

### Lions DEF

The engine currently identifies Lions as a useful Week 1 addition.

The new implementation must determine the appropriate drop through complete roster evaluation.

It must not simply add Lions and leave the roster over capacity.

### Schultz / Pollard

The existing recommendation indicates a positive-looking rationale while the transaction has a negative VOR delta.

The new system must reject the transaction when the complete roster evaluation demonstrates that it is inferior.

### Titans / Texans

The engine currently evaluates Titans as a possible addition while dropping Texans.

The new system must account for:

* current-week improvement
* ROS difference
* DEF depth
* alternative drop candidates

### Deebo

The engine should distinguish:

* immediate current-week value
* ROS value
* waiver timing
* roster capacity
* opportunity cost
* whether the acquisition can actually improve the starting lineup

Do not hard-code the desired answer.

The framework should naturally reach the appropriate conclusion from the supplied data.

### Kicker alternatives

The engine must not recommend sacrificing a valuable position player merely because a kicker has a slightly higher projection.

Evaluate the complete roster state.

---

# 25. Testing Requirements

Add or update automated tests covering:

## Roster validation

* legal roster
* oversized bench
* missing required bench position
* invalid starter
* invalid FLEX
* duplicate player
* full-roster add without drop
* valid add/drop

## Transaction evaluation

* add/drop simulation
* before/after roster state
* current-week delta
* ROS delta
* VOR delta
* opportunity cost

## Lineup optimization

* optimal starting lineup before transaction
* optimal starting lineup after transaction
* FLEX changes
* bench-only acquisition
* starter upgrade
* starter downgrade

## Decision logic

* HOLD comparison
* positional scarcity
* drop resistance
* bye coverage
* streaming position
* transaction classification
* confidence classification

## Contradiction detection

Test deliberately contradictory values/explanations.

The engine must identify and reject/correct them.

## Regression testing

Run the existing fantasy scenario and verify that the known reasoning failures are addressed.

Do not hard-code expected answers merely to pass the regression tests.

The tests should validate the underlying behavior.

---

# 26. Documentation Requirements

After implementation, search the repository for all documentation related to:

* suggestion engine
* recommendation engine
* roster evaluation
* VOR
* waiver logic
* add/drop logic
* lineup optimization
* scoring
* architecture
* configuration
* testing
* fantasy strategy
* decision logic

Update relevant existing documentation.

Do not create duplicate documentation unnecessarily.

Documentation must describe the **actual implemented behavior**.

At minimum document:

* roster-state simulation
* legal roster validation
* transaction evaluation
* opportunity cost
* drop resistance
* dynamic scarcity
* current-week vs ROS separation
* lineup optimization
* HOLD comparison
* streaming positions
* bye analysis
* confidence levels
* contradiction detection
* reasoning ledger
* LLM/deterministic calculation boundaries

If the repository already has a README, architecture document, engine specification, or developer guide, update it where appropriate.

---

# 27. Code Quality Requirements

Follow the existing project's:

* language conventions
* module structure
* naming conventions
* testing framework
* formatting
* linting
* configuration patterns

Avoid unnecessary dependencies.

Avoid duplicating business logic.

Prefer reusable functions/services for:

* roster validation
* roster simulation
* lineup optimization
* transaction evaluation
* scarcity evaluation
* contradiction validation

Keep deterministic calculations testable independently from any LLM integration.

---

# 28. Backward Compatibility

Preserve existing public interfaces where practical.

If an interface must change:

1. identify all callers
2. update them
3. update tests
4. update documentation

Do not silently change the meaning of existing fields.

If new fields are added, use descriptive names and document them.

---

# 29. Observability and Debugging

Where practical, make it possible to inspect why a recommendation was selected.

Useful diagnostic information includes:

```text
candidate transaction
legal/illegal
drop candidate
current-week delta
ROS delta
VOR delta
starter impact
scarcity impact
bye impact
opportunity cost
confidence
rejected alternatives
contradictions detected
```

Do not expose excessive internal diagnostic information in the normal user-facing recommendation unless the existing application supports it.

The goal is to make bad decisions diagnosable.

---

# 30. Acceptance Criteria

The implementation is considered successful only when all of the following are true.

## Roster integrity

No recommendation can produce an illegal roster.

## Transaction reasoning

The engine evaluates complete add/drop transactions rather than evaluating additions in isolation.

## Opportunity cost

The engine explicitly considers the player being removed.

## Current week

The engine calculates actual starting-lineup impact.

## ROS

The engine separately evaluates rest-of-season roster impact.

## VOR

VOR remains available but is no longer treated as an automatic transaction command.

## Scarcity

Positional scarcity can vary based on the roster and available alternatives.

## Streaming

K/DEF can be treated differently from permanent roster positions.

## HOLD

Doing nothing is explicitly evaluated.

## Alternatives

Meaningful alternatives can be compared.

## Contradictions

The engine detects explanations that conflict with calculated results.

## Confidence

Recommendations include evidence-based confidence.

## Testing

New behavior is covered by automated tests.

## Documentation

Relevant documentation accurately reflects the implemented architecture.

## Regression

The known problematic scenario no longer produces internally contradictory or illegal recommendations.

---

# 31. Implementation Strategy

Implement this incrementally.

Recommended order:

### Phase 1

Inspect existing architecture and identify:

* roster representation
* lineup optimizer
* VOR calculation
* suggestion engine
* free-agent evaluation
* recommendation output
* existing tests

Do not modify code until the existing flow is understood.

### Phase 2

Implement centralized roster validation.

### Phase 3

Implement complete roster-state simulation.

### Phase 4

Connect transaction evaluation to lineup optimization.

### Phase 5

Add current-week and ROS transaction deltas.

### Phase 6

Add opportunity cost and drop resistance.

### Phase 7

Add dynamic positional scarcity and streaming-position handling.

### Phase 8

Add HOLD and alternative-action comparison.

### Phase 9

Add confidence and reasoning ledger.

### Phase 10

Add contradiction detection.

### Phase 11

Run regression scenarios and refine the decision process.

### Phase 12

Update documentation and run the complete test suite.

Do not attempt to implement every feature in one giant refactor if the existing architecture makes that risky.

Commit logical milestones when appropriate.

---

# 32. Final Self-Review

Before declaring the implementation complete, perform an independent review of the resulting engine.

Ask:

1. Can it recommend an illegal roster?
2. Can it recommend a player simply because that player has positive VOR?
3. Does it know what the dropped player is worth?
4. Does it compare against HOLD?
5. Does it know whether the acquired player actually starts?
6. Does it distinguish Week 1 from ROS?
7. Does it recognize streaming positions?
8. Does it consider bye coverage?
9. Can its explanation contradict its calculations?
10. Can an LLM hallucination override deterministic calculations?
11. Can it explain why one transaction beats another?
12. Are the calculations independently testable?
13. Are the new tests meaningful rather than simply testing implementation details?
14. Does the documentation describe what the code actually does?

Fix problems discovered during this review.

---

# 33. Final Deliverables

When implementation is complete, provide a concise report containing:

## Files Changed

List every modified or created file.

## Major Changes

Summarize the actual architectural changes.

## Tests

List:

* tests added
* tests modified
* test suite results

## Documentation

List documentation updated.

## Regression Results

Explain how the known problematic fantasy scenario behaves under the new engine.

## Remaining Limitations

Clearly identify anything that could not be implemented or any known weakness remaining.

Do not claim a feature is implemented unless it actually exists and has been tested.

---

# 34. Commit

After all implementation, testing, and documentation updates are complete, create a clear git commit describing the work.

Suggested commit message:

```text
Improve roster transaction reasoning engine
```

The commit should include the implementation, tests, and documentation that belong to this change.

Do not commit unrelated changes.

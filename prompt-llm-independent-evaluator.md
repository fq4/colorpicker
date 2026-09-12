# LLM Independent Fantasy Evaluator — Implementation Plan and Status

## Implemented In This Repository

The first optional evaluation phase is implemented in `llm_evaluator.py` and wired
into `run_weekly.py` and `report.py`:

- A provider-neutral interface with an OpenAI-compatible HTTP adapter.
- Structured league context, bounded player data, and deterministic engine output;
  the markdown report is never used as the internal input.
- An independent/adversarial JSON-only prompt with explicit no-transaction authority.
- Validation of required fields, enums, numeric values, and player references against
  the supplied candidate universe.
- Disabled-by-default configuration plus `--llm-evaluate` opt-in.
- Graceful handling of unavailable providers and malformed responses.
- Optional terminal/Markdown second-opinion sections that never replace the engine's
  recommendations or action plan.
- Fake-provider tests covering valid output, no transaction, malformed JSON, invented
  players, disabled operation, provider failure, and report rendering.

Historical persistence, outcome scoring, model comparison, richer candidate tiers, and
additional provider adapters remain future work. The sections below describe the
intended direction and acceptance criteria for those later phases.

## Purpose

Add an **optional second-opinion LLM evaluator** to ffbot_ui. The existing `decision_engine.py` remains the deterministic decision-maker. The LLM should act as an independent fantasy analyst that receives the same underlying Yahoo player data plus the deterministic engine's structured recommendations and identifies agreement, disagreements, missed opportunities, and questionable reasoning.

This is deliberately an evaluator/adversary, not an LLM replacement for the optimizer.

The existing project already has a strong seam for this: `RecommendationReport` contains the roster, lineup, add/drop recommendations, low-value recommendations, action plan, and bye warnings. The report layer already turns these structured objects into markdown. Keep the LLM integration downstream of the deterministic engine so the existing pipeline remains usable without an API key or network access.

## Important current-code observations

- `decision_engine.py` already returns structured dataclasses rather than requiring an LLM to parse markdown. Use those objects as the primary input to the evaluator.
- `RecommendationReport` is already a natural evaluation payload.
- `data_layer.py` already normalizes expected columns and caches the scraped DataFrame. The LLM evaluator should consume the normalized/cached data and must never trigger an additional Yahoo scrape.
- `run_weekly.py` already has a clear pipeline: data -> roster -> depth -> lineup -> add/drop -> action plan -> report. Insert evaluation after `RecommendationReport` is assembled and before report rendering.
- `config.yaml` already contains strategy thresholds. The evaluator should see the active values so it can detect contradictions such as a recommendation that appears inconsistent with its configured threshold.
- The project is intentionally read-only/dry-run. The LLM must never be given authority to execute Yahoo actions.
- The current markdown report is human-readable, but it should NOT be the canonical input to the LLM. Structured data should be serialized directly from the dataclasses/DataFrame. This avoids brittle markdown parsing.

## Proposed architecture

```text
Yahoo/ffbot scrape
       |
       v
normalized DataFrame + cache
       |
       v
existing deterministic decision engine
       |
       +---- RecommendationReport
       |          |
       |          +---- lineup
       |          +---- add/drop recommendations
       |          +---- action plan
       |          +---- warnings
       |
       v
LLM evaluator (OPTIONAL)
       |
       +---- independent analysis
       +---- agreement/disagreement
       +---- missed opportunities
       +---- questionable recommendations
       +---- confidence
       +---- machine-readable evaluation
       |
       v
report.py
       |
       +---- existing report
       +---- Independent LLM Assessment section
```

## Do not make the LLM the optimizer yet

Do NOT replace `recommend_lineup()`, `recommend_adds_drops()`, or `build_action_plan()` with LLM decisions in this phase.

The first objective is to collect evidence about whether an LLM can consistently identify useful mistakes in the deterministic engine. This makes the feature testable and gives us a path toward improving the deterministic strategy based on observed disagreements.

## LLM input design

Provide the model with three logically separate inputs:

### 1. League/config context

Include only relevant configuration:

- week
- season
- scoring type
- waiver type
- waiver priority
- roster slot configuration
- position-specific VOR thresholds
- bench-depth minimums
- minimum usable projection
- locked positions

Do not include credentials, cookies, OAuth tokens, or unnecessary scraped account information.

### 2. Structured player data

Do not dump every DataFrame column blindly. Build a compact evaluation dataset containing at minimum:

- ID
- Name
- Team
- Position
- Status
- `% Owned`
- current week projection
- Week 1-18 projections
- Remaining
- VOR
- Owner/current roster state

Prefer JSON rather than CSV inside the prompt because the evaluator needs unambiguous field names and types.

Consider limiting free agents to a useful candidate pool to control token cost, but retain enough data for the LLM to discover missed opportunities. A sensible first implementation is all rostered players plus available players above a configurable ownership/projection/VOR relevance threshold. Make this configurable rather than hard-coded.

### 3. Deterministic engine output

Serialize the `RecommendationReport` into JSON:

- current roster
- depth warnings
- bye warnings
- recommended lineup
- recommended adds/drops
- negative-VOR/low-value recommendations
- action plan
- resulting lineup
- comparison notes

The LLM should see the engine's recommendations only AFTER seeing the raw player information. This reduces anchoring on the engine's answer.

## Prompt strategy

Use a system/developer prompt with these principles:

```text
You are an independent fantasy football analyst reviewing a deterministic
fantasy decision engine.

Your job is to find useful decisions, not to agree with the engine.

Treat the supplied raw player data as authoritative for numerical facts.
Treat the deterministic engine output as a proposal that may contain errors.

Independently evaluate:
- current-week lineup
- add/drop candidates
- drop selection
- rest-of-season value
- VOR
- positional scarcity
- roster construction
- bench depth
- upcoming byes
- ownership
- short-term streaming value
- opportunity cost

Do not invent injuries, schedules, roles, statistics, or league settings.
If information is unavailable, say so.

Do not recommend a transaction merely because a numerical value is slightly
higher. Consider whether the improvement is meaningful and whether the move
creates a worse roster elsewhere.

Do not blindly use VOR. Do not blindly use projection.
Do not manufacture disagreement for its own sake.

Distinguish FACT, INFERENCE, and SPECULATION.

The existing engine is not wrong merely because you disagree with it. Explain
material disagreements and identify why the alternative is likely better.

You have no authority to execute transactions. Produce analysis only.
```

Then supply the structured league context, player data, and engine output as clearly delimited JSON sections.

## Required structured response

Prefer structured JSON output from the model rather than parsing prose. Define a schema similar to:

```json
{
  "overall_assessment": "string",
  "engine_score": 0,
  "lineup": {
    "agreement": "agree|modify|reject",
    "recommended_changes": [],
    "reasoning": "string"
  },
  "transactions": [
    {
      "action": "add_drop|hold|fa_add|waiver_claim",
      "add_player": "string|null",
      "drop_player": "string|null",
      "classification": "must_make|strong|optional|speculative|do_not_make",
      "confidence": "high|medium|low",
      "current_week_projection_delta": 0.0,
      "vor_delta": 0.0,
      "rest_of_season_assessment": "string",
      "starting_lineup_impact": "string",
      "reasoning": "string"
    }
  ],
  "engine_agreements": [],
  "engine_corrections": [],
  "missed_opportunities": [],
  "holds": [],
  "strategic_opportunities": [],
  "data_limitations": [],
  "bottom_line": "string"
}
```

Do not require the model to calculate values that the application can calculate reliably. Where possible, provide computed projection/VOR deltas in the input and ask the LLM to interpret them.

## Very important: detect contradictions

The evaluator should explicitly look for internal inconsistencies in the existing engine/report. Examples:

- Recommendation says a move clears a threshold but the actual gain does not.
- A recommendation claims to improve the lineup but the resulting lineup has the same or lower projection.
- A player is dropped despite being materially more valuable for the rest of the season than the replacement.
- A player is added because of VOR while the roster already has adequate depth at that position.
- A transaction improves VOR but consumes a valuable roster spot without improving the starting lineup.
- A low-VOR player is dropped even though they provide unique position/slot flexibility.
- A waiver recommendation ignores waiver priority or the cost of using a claim when that information is available.
- A streaming move is treated like a long-term roster improvement.
- A player is recommended despite an injury/status flag that should materially change the recommendation.
- The engine's stated reason does not match its actual numerical inputs.

These should be first-class evaluation fields rather than buried in prose.

## Evaluate the drop decision separately

This deserves special attention.

The existing engine can produce an apparently attractive add because the add has positive VOR, while the selected drop may be questionable. The LLM should independently ask:

> Of every legal roster player, is this actually the player I would sacrifice to acquire the proposed player?

Compare the proposed drop against:

- lowest current-week value
- lowest VOR
- lowest rest-of-season value
- positional redundancy
- roster flexibility
- injury/availability risk
- future usefulness

The LLM should be allowed to conclude that the **add is good but the proposed drop is wrong**.

## Evaluate current week versus season value separately

Do not collapse these into one score.

For every meaningful move, ask:

1. Does this improve this week's lineup?
2. Does this improve the rest-of-season roster?
3. Does it improve both?
4. If it helps only this week, is the gain large enough to justify the roster transaction?
5. If it hurts this week but helps later, is the sacrifice justified?

This distinction is likely to uncover weaknesses in an optimizer that is primarily projection-driven.

## Add a "no move" option

The LLM must be able to recommend:

```text
NO TRANSACTION
```

A good evaluator should not feel obligated to find a move every week. This is especially important when the available free agent only provides a tiny improvement.

## Confidence should mean something

Do not ask for arbitrary confidence labels. Define them:

### HIGH
The data strongly supports the conclusion and there is little strategic ambiguity.

### MEDIUM
The recommendation is reasonable but depends on assumptions or competing considerations.

### LOW
The recommendation is speculative, highly sensitive to missing information, or based on a small numerical edge.

## Suggested report integration

Add an optional report section after the deterministic recommendations and before the final Action Plan:

```text
## Independent LLM Assessment

Engine score: 78/100

### Overall Assessment
...

### Engine Decisions I Agree With
...

### Engine Decisions I Would Change
...

### Missed Opportunities
...

### Independent Transaction Recommendations
...

### Strategic Watch List
...

### Bottom Line
...
```

The existing deterministic Action Plan should remain clearly labeled as the engine's action plan. Do not silently substitute the LLM output for it.

## Configuration proposal

Add an optional section such as:

```yaml
llm_evaluator:
  enabled: false
  provider: "openai"
  model: ""
  max_candidates: 150
  include_week_history: true
  include_engine_report: true
  timeout_seconds: 60
  save_raw_response: false
```

Do not hard-code a paid provider into the core decision engine. Keep a provider interface so a local model (for example Ollama) can eventually be used without changing the evaluation logic.

A useful interface would conceptually be:

```text
LLMEvaluator.evaluate(context, player_data, recommendation_report)
    -> LLMEvaluation
```

The core engine should still work if the evaluator is disabled, unavailable, times out, or returns malformed output.

## Local-model compatibility

The user already has an interest in local LLMs, so design the provider boundary to support both hosted and local models.

Do not make the first implementation depend on a specific vendor SDK if a small HTTP adapter can keep the boundary clean.

The evaluator should have:

- provider name
- model name
- timeout
- retry policy
- structured-output validation
- clear logging
- graceful failure

Never log secrets or full prompts by default.

## Cost/token controls

Raw Week 1-18 data for hundreds of players can become expensive. Build a candidate selection layer.

Potential tiers:

### Tier 1 — Always include
- user's roster
- current starters
- bench players
- engine-recommended adds/drops

### Tier 2 — Include if relevant
- free agents above projection threshold
- free agents with positive VOR
- players near engine thresholds
- players with high ownership or unusual ownership changes if available

### Tier 3 — Optional exploration pool
- additional low-owned players
- stash candidates
- deep positional candidates

Make the maximum candidate count configurable.

## Historical learning / backtesting hook

This is potentially the most valuable long-term feature.

Every LLM evaluation should eventually be stored alongside the weekly report with:

- season
- week
- league/team identifiers
- engine recommendations
- LLM recommendations
- engine confidence
- LLM confidence
- timestamp/model
- eventual actual player performance
- whether the engine or LLM decision produced the better outcome

Do NOT attempt to judge recommendations immediately. Save the decision now and score it later when actual weekly/season results are known.

This allows future analysis such as:

- How often does the LLM disagree with the engine?
- How often are disagreements correct?
- Which positions produce useful disagreements?
- Does high LLM confidence correlate with better decisions?
- Does the LLM catch bad drop selections?
- Does it improve streaming decisions?
- Does it overreact to tiny projection differences?
- Does one model outperform another?

This turns the LLM from an expensive opinion generator into an experimentally measurable component.

## Evaluation metrics to add eventually

Track at least:

### Agreement rate
Percentage of recommendations where engine and LLM agree.

### Useful disagreement rate
Percentage of disagreements where the LLM alternative subsequently performs better.

### False-positive transaction rate
Recommended moves that produce no meaningful improvement.

### Drop-quality score
How often the chosen drop was actually the best/near-best drop candidate.

### Starting-lineup improvement
Actual points gained versus the engine's lineup.

### Short-term decision accuracy
Actual Week N outcome versus predicted decision value.

### Long-term decision accuracy
Rest-of-season value attributable to the decision.

### Confidence calibration
Whether HIGH-confidence decisions actually outperform MEDIUM/LOW decisions.

Do not optimize these metrics until enough historical observations exist.

## Tests required

Add tests that do NOT call a real LLM.

Use a fake/mock evaluator and deterministic fixtures.

At minimum test:

1. LLM evaluator disabled -> weekly run behaves exactly as before.
2. LLM evaluator unavailable -> weekly run still succeeds.
3. Malformed LLM JSON -> evaluation is rejected safely and the deterministic report remains valid.
4. Valid evaluation -> report contains the independent assessment.
5. Engine recommendation and LLM recommendation agree -> agreement recorded correctly.
6. LLM challenges an engine recommendation -> disagreement recorded correctly.
7. LLM identifies a missed free agent -> missed opportunity is represented.
8. LLM recommends no transaction -> report accepts no-move outcome.
9. LLM cannot invent a player outside the supplied dataset without the response being flagged/validated.
10. Candidate limiting works and keeps rostered players plus important engine candidates.
11. Secrets/config credentials never appear in saved evaluation output or normal logs.
12. Existing 77+ test suite remains green.

## Validation of model output

Treat LLM output as untrusted input.

Validate:

- player names against supplied player IDs/names
- position values against supplied data
- numeric fields are numeric
- confidence/classification values are enumerated
- no executable action is generated
- no recommendation can directly trigger `executor.py`

Prefer player IDs internally over player names when possible. Names are for presentation.

If a model refers to a player that is not in the supplied dataset, preserve the response as an invalid/unsupported claim rather than silently accepting it.

## Security

The LLM evaluator must never receive:

- Yahoo cookies
- OAuth tokens
- account passwords
- authentication headers
- browser session data
- unnecessary personal account information

The evaluator is an analysis component only.

Keep `--execute` behavior completely independent from LLM output in this phase.

## Suggested implementation order

### Phase 1 — Data contract
Create a serializer that converts the current recommendation report and relevant DataFrame rows into a compact JSON evaluation payload.

No LLM call yet.

### Phase 2 — Provider interface
Create a small evaluator abstraction with a fake provider for tests.

### Phase 3 — Prompt
Implement the independent/adversarial analyst prompt above and require structured JSON output.

### Phase 4 — Validation
Validate the model response against the supplied player universe and schema.

### Phase 5 — Reporting
Add the optional Independent LLM Assessment section to terminal/markdown output.

### Phase 6 — Configuration
Add disabled-by-default configuration and CLI controls only after the core path works.

### Phase 7 — Historical tracking
Persist the evaluation alongside the weekly report so future backtesting can score it.

## Important design decision

Do not ask the LLM to read the existing markdown report and then make suggestions. That approach wastes tokens and makes the system dependent on report formatting.

Instead:

```text
Structured raw player data
          +
Structured deterministic recommendation
          |
          v
     LLM evaluator
          |
          v
Structured evaluation
          |
          v
Human-readable report
```

The markdown report should be an output, not an internal API.

## What success looks like

For a report like the current Week 2 example, the LLM should be able to say something along the lines of:

```text
ENGINE: Add Lions / Drop Sam Darnold
LLM:    Agree
Why:    Large VOR improvement and DEF streaming is a reasonable use of the roster spot.

ENGINE: Add Matthew Golden / Drop Romeo Doubs
LLM:    Modify
Why:    The current-week projection difference is negligible. The transaction may
        create unnecessary roster churn unless Golden has a meaningful role/upside
        advantage supported by the supplied data.

ENGINE: Add Jakobi Meyers / Drop Jalen Coker
LLM:    Reject
Why:    The proposed gain is below the configured WR threshold and does not create
        a meaningful starting-lineup improvement.

MISSED OPPORTUNITY:
The engine should consider X because ...

BOTTOM LINE:
Make move 1. Hold on moves 2 and 3 unless additional information changes the analysis.
```

The exact conclusions must come from the supplied data. The example above is only demonstrating the desired evaluator behavior.

## Acceptance criteria

The coder agent should consider this feature complete only when:

- the deterministic engine remains functional without an LLM
- LLM evaluation is disabled by default
- raw structured data, not markdown, is sent to the evaluator
- the evaluator receives enough information to challenge the engine intelligently
- output is schema-validated
- invalid model output cannot break the weekly run
- no LLM output can execute a Yahoo transaction
- tests cover agreement, disagreement, missed opportunity, no-move, malformed output, and unavailable-provider cases
- the report clearly distinguishes deterministic recommendations from LLM second opinions
- evaluation data can be retained for later historical scoring
- the full existing test suite passes

## Final instruction to the coding agent

Implement this incrementally. Before changing code, inspect the existing repository for the exact report/dataclass/config/test seams and avoid duplicating logic that already exists.

Do not rewrite the decision engine merely to accommodate the LLM.

Do not add automatic transaction execution.

Do not assume an LLM recommendation is correct.

The long-term goal is to create a measurable **engine-vs-LLM experiment**, where disagreements become data that can be backtested and used to improve the deterministic fantasy strategy.

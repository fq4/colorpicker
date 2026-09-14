# Independent Code Review — REVIEW_2

Reviewed 2026-09-14. This is a documentation-only record of the existing implementation; no fixes are included. Findings are prioritized by functional impact. Line numbers refer to the source reviewed on this date.

## Scope and verification

Read `AGENTS.md` and `README.md` first, then application source and relevant installed `ffbot` source. The documents describe read-only scraping, structured recommendations, an optimal current-week lineup, post-move planning, usable bench depth, position locks, and optional reasoning/LLM assessments.

The read-only executor boundary, default dry-run behavior, negative-VOR separation, post-move lineup recomputation, and default-disabled reasoning/LLM gates are implemented. The findings below identify failures or qualifications to the other claims.

The full test command was `python -m pytest tests/ -v`. The initial sandbox run reported **132 passed, 335 warnings, 2 errors**; both errors concerned access to pytest's temporary directory. The rerun with the required access reported **134 passed, 333 warnings in 44.06s**, with no failures. Warnings were PuLP deprecations. The documented counts of 113 and 121 are stale.

Small isolated reproductions confirmed the findings marked **Reproduced**. The unmodified live `python run_weekly.py --dry-run` command was traced through source but was not run against Yahoo. No remaining `chr()` obfuscation or executable `eval`/`exec` payloads were found in application Python.

## High

### 1. Mandatory Action Plan drops bypass position locks

**Evidence:** `run_weekly.py:75-82` constructs the Action Plan configuration without `locked_positions`; `decision_engine.py:1386-1394` selects mandatory drops using only starter and IR exclusions:

```python
for _, row in bench_df.iterrows():
    name = str(row.get("Name", ""))
    if name in starter_names or name in ir_names:
        continue
    suggested.append({
        "name": name,
        "position": str(row.get("Position", "")),
        "vor": _safe_float(row.get("VOR")),
    })
```

**Reproduced:** With a full `QB, BN` roster, a locked TE on the bench, and an incoming WR, the plan inserted a drop of the locked TE.

**Why it matters:** The Action Plan can instruct the user to drop a player at a position the report explicitly promises is protected from add/drop suggestions.

### 2. Roster-limit enforcement can silently leave an illegal plan

**Evidence:** `decision_engine.py:1408-1414`:

```python
if move.add and not move.drop and roster_size >= max_roster_size:
    try:
        drop_candidate = next(suggested_iter)
    except StopIteration:
        # No more candidates; keep the move as-is and let it overflow
        adjusted_moves.append(move)
        continue
```

At `decision_engine.py:1468-1472`, no remaining candidates and no inserted drops result in `roster_limit_warnings = []`. At `decision_engine.py:1369`, capacity is calculated as:

```python
max_roster_size = len(parse_positions(positions_config["positions"]))
```

**Reproduced:** A one-slot roster containing its starter accepted a pure add, producing two players and no remaining warnings.

**Related source-verified gaps:** Capacity counts an unused IR slot as ordinary capacity; mandatory-drop candidates come from the original roster without excluding players already scheduled for removal; the counter at `decision_engine.py:1435-1436` decrements for any drop even if an earlier move already removed that player.

**Why it matters:** The advertised executable sequence can exceed capacity or depend on duplicate drops while presenting roster-limit enforcement as successful.

### 3. Greedy lineup selection can miss a valid, much stronger lineup

**Evidence:** `decision_engine.py:1089` and `decision_engine.py:1099-1102`:

```python
players = players.sort_values(by=week_col, ascending=False).reset_index(drop=True)
```

```python
if player_positions & compatible:
    best = row
    break  # players are sorted by projection desc
```

**Reproduced:** For `QB, TE` slots, a QB/TE player projected at 20 and a QB-only player projected at 19 produced only the dual-eligible player at QB, totaling 20; placing the QB-only player at QB and the dual-eligible player at TE yields 39.

**Why it matters:** The promised optimal lineup can omit a fillable starting slot, and reasoning-engine legality checks inherit the same error by calling this function.

### 4. IR players are excluded from starting only when an IR slot exists

**Evidence:** The IR-removal logic is inside the conditional beginning at `decision_engine.py:1056`; removal occurs at `decision_engine.py:1072-1074`:

```python
if "IR" in slots:
    # ... build ir_slots ...
    players = players[
        ~players["Status"].apply(lambda s: _is_ir(s, ir_statuses))
    ]
```

**Reproduced:** With `QB, BN`, an IR QB projected at 20 started ahead of a healthy QB projected at 10. When an IR slot does exist, the loop at `decision_engine.py:1060` labels every IR-status player as occupying IR without limiting that list to the configured number of IR slots.

**Why it matters:** Changing roster configuration can produce unavailable starters or an IR allocation exceeding the configured capacity.

### 5. Accepted malformed LLM output can crash deterministic report generation

**Evidence:** `llm_evaluator.py:213-215` validates only the lineup agreement enum:

```python
lineup = data["lineup"]
if not isinstance(lineup, dict) or lineup.get("agreement") not in {"agree", "modify", "reject"}:
    raise EvaluatorError("lineup.agreement must be agree, modify, or reject")
```

Fields such as `engine_agreements` are not type-checked, but `report.py:438-441` iterates them:

```python
values = data.get(key) or []
if values:
    lines.append(f"  {title}:")
    lines.extend(f"    - {value}" for value in values)
```

**Reproduced:** An otherwise accepted evaluation with `engine_agreements: 42` passed validation and caused `TypeError: 'int' object is not iterable` in report formatting. An invented player in `lineup.recommended_changes` also passed validation; player-universe checks cover transaction add/drop references, not all references.

**Why it matters:** Malformed provider output can prevent terminal output and Markdown saving despite the documented guarantee that evaluator failures leave the deterministic report intact.

## Medium

### 6. Bench-depth accounting disagrees with the lineup and with configured thresholds

**Evidence:** `decision_engine.py:203-204` excludes FLEX slots from starter needs:

```python
if slot in FLEX_SLOTS:
    continue  # flex doesn't lock a specific position
```

At `decision_engine.py:344-346`:

```python
usable_count = len(pos_players)
starter_need = starter_counts.get(pos, 0)
bench_depth = usable_count - starter_need
```

**Reproduced:** Three usable RBs filling `RB, RB, W/R` with an RB bench minimum of one produced no warning, although every RB was starting.

Separately, `decision_engine.py:629-633` builds an internal `positions_config` without `min_usable_projection`, while `run_weekly.py:81` includes it. Recommendation depth checks therefore fall back to 1.0 when top-level warnings use a customized value.

**Why it matters:** The tool can report nonexistent bench depth and apply different depth standards to warnings and transaction flags.

### 7. The suggested bench alternative can already be another starter

**Evidence:** `decision_engine.py:1113-1116` selects alternatives before later lineup slots have been assigned:

```python
if is_flagged:
    bench_alt = _find_bench_alternative(
        players, used_names, compatible, week_col, ir_statuses
    )
```

**Reproduced:** With two WR slots and receivers projected at 20/Q, 19, and 10, the 19-point WR was suggested as the first WR's bench alternative and then assigned WR2.

**Why it matters:** Injury guidance can offer a replacement who is already needed in another starting slot.

### 8. Recommendation text falsely claims a threshold was cleared

**Evidence:** `decision_engine.py:978-985` checks only that gain is nonzero before stating it clears the threshold:

```python
if rec.vor_gain is not None and rec.vor_gain != 0:
    position = (rec.add_position or "").strip().upper()
    threshold = float((min_vor_add_config or {}).get(position, min_vor_add_default))
    if position and min_vor_add_config:
        parts.append(
            f"VOR gain {rec.vor_gain:+.1f} clears the streaming threshold "
            f"({threshold:.1f}) for {position}"
        )
```

**Reproduced:** `VOR gain +2.0 clears the streaming threshold (8.0) for RB.` Confidence correctly compares the values at `decision_engine.py:1017`; below-threshold recommendations are not excluded from the Action Plan.

**Why it matters:** The explanation gives mathematically false support for a recommendation and contradicts the confidence calculation.

### 9. Sequential optimizer results are filtered and reordered as independent moves

**Evidence:** In the installed dependency, `C:/Users/Ken/AppData/Roaming/Python/Python312/site-packages/ffbot/optimizer.py:210-211` fixes an earlier add into subsequent solutions:

```python
prob += add[p] == 1
known_adds.add(p)
```

The application sorts at `decision_engine.py:925` and removes flagged moves at `decision_engine.py:1350`:

```python
recs.sort(key=lambda r: (r.flagged, -(r.vor_gain if r.vor_gain is not None else 0)))
```

```python
moves = [r for r in add_drop_recs if not r.flagged]
```

`run_weekly.py:176-177` also removes negative-VOR recommendations from the actionable list. Individual depth/reasoning evaluations use the original roster rather than the accumulated plan state.

**Source-verified risk:** Later optimizer choices can depend on an earlier addition that the wrapper removes, and the resulting combined transaction sequence is not revalidated against those dependencies.

**Why it matters:** Keeping individually acceptable rows does not establish that the resulting sequence retains the optimizer's roster assumptions.

### 10. Team-name filtering can still disagree with team-ID optimization

**Evidence:** `data_layer.py:171` normalizes the resolved name, but `decision_engine.py:270` filters the unnormalized name column:

```python
id_to_name[owner_id] = str(owner).strip()
```

```python
my_roster = df[df["Owner"] == team_name].copy()
```

The installed `ffbot/optimizer.py:87` instead uses:

```python
Roster0[p] = owner_id == team
```

**Source-verified risk:** Duplicate owner names merge application rosters, while leading/trailing whitespace can make the displayed roster empty; the optimizer still selects by ID.

**Why it matters:** Runtime name derivation does not fully prevent the displayed roster and optimized roster from referring to different player sets.

### 11. Important computed assessment fields are never displayed

**Evidence:** `decision_engine.py:842-848` stores results including:

```python
rec.transaction_vor_delta = transaction_eval.vor_delta
rec.drop_resistance = transaction_eval.drop_resistance
rec.reasoning_ledger = transaction_eval.ledger
rec.contradictions = transaction_eval.contradictions
```

`reasoning_engine.py:160-172` places HOLD comparisons, opportunity cost, scarcity, alternatives, and contradictions in the ledger, but `report.py:544-548` renders only condensed prose:

```python
if rec.reasoning_detail:
    lines.append("")
    lines.append("### Reasoning Engine Detail")
    for part in rec.reasoning_detail.split(" | "):
        lines.append(f"- {part}")
```

Additional omissions:

- `llm_evaluator.py:173-187` requests lineup changes/reasoning, current-week and VOR deltas, ROS assessment, starting-lineup impact, strategic opportunities, and data limitations; the assessment formatters at `report.py:420-490` omit these fields.
- `decision_engine.py:375` sets `low_proj_players=low_proj_names`; no report reads those names.
- `decision_engine.py:1136` sets `bench_alt_status=bench_alt.status if bench_alt else ""`; alternative rendering omits that status.

Some deterministic fields enter the optional LLM prompt through `asdict()`; they are hidden from human-readable reports rather than entirely unused.

**Why it matters:** Users do not receive computed contradictions, limitations, and decision evidence needed to assess the recommendations.

### 12. Cached execution and optimizer failures can produce misleading analysis

**Evidence:** `run_weekly.py:104` calls `ffbot.current_week()` before cache loading; `run_weekly.py:118-119` then replaces it with the cache's week:

```python
if week is None:
    current_week = fetched_week
```

`data_layer.py:84-87` selects by modification time and age, without matching current week, season, or IDP mode:

```python
latest = max(files, key=os.path.getmtime)
age_hours = (time.time() - os.path.getmtime(latest)) / 3600
if age_hours <= max_age_hours:
```

`run_weekly.py:325` also unconditionally replaces a YAML cache age with the CLI default:

```python
config["cache_max_age_hours"] = args.max_cache_age
```

At `decision_engine.py:653-655`, optimizer errors become an empty recommendation list:

```python
except Exception as e:
    logger.error(f"Optimizer failed: {e}")
    return []
```

The installed `ffbot/optimizer.py:70` additionally uses `TIMES = [t for t in range(week, 18)]`, excluding Week 18 even though the application analyzes Week 18 projections.

**Why it matters:** A cached default run still requires network week discovery, can revert to an earlier week, and can save a report indistinguishable from a successful no-recommendations result after optimizer failure.

### 13. Reports overwrite previous runs despite documentation promises

**Evidence:** `report.py:571-573`:

```python
path = os.path.join(reports_dir, f"league_{league_id}_team_{team_id}_week_{week}.md")
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
```

`AGENTS.md:93` and `README.md:170-171` claim different runs never overwrite one another; the filename has no run, season, or hypothetical-scenario component.

**Why it matters:** Repeated or hypothetical runs for the same league/team/week replace the previous report and its tracking history.

## Low

### 14. Action Plan wording contradicts the structured moves and lineup

**Evidence:** `report.py:607-608` assumes every drop has an add:

```python
if rec.drop:
    lines.append(f"  {i}. Add {rec.add} ({rec.add_position}) - drop {rec.drop} ({rec.drop_position})")
```

The Markdown equivalent is at `report.py:713-714`.

**Reproduced:** A mandatory drop renders as `1. Add None (None) - drop Locked bench (TE)`.

Separately, `decision_engine.py:1578` iterates `final_lineup.bench`, while `decision_engine.py:1604-1605` can describe that bench player as starting:

```python
f"{bench_name} ({bench_pos}, {bench_proj}) starts over "
f"{starter_name} ({starter_pos}, {starter_proj}) - higher projection"
```

**Why it matters:** The instructions can misdescribe both the transaction to perform and the lineup the tool actually calculated.

### 15. Advertised settings and interfaces do not exist or have no effect

**Evidence:** `config.yaml:21` and `config.yaml:62-63` define:

```yaml
min_vor_loss_to_flag_drop: -50.0
```

```yaml
notify:
  method: "console"   # console | email | none (extend later)
```

No application code reads either setting. `README.md:120` nevertheless describes the drop threshold as functional. Other verified documentation gaps:

- `README.md:144` and `AGENTS.md:67` list `validate_team_identifiers()`, but there is no definition.
- `README.md:142` and `AGENTS.md:65` claim caching uses `ffbot.save()/load()`; `data_layer.py:39` uses `df.to_csv(...)` and `data_layer.py:106` uses `pd.read_csv(...)`.
- `README.md:121` advertises an LLM candidate limit, but `llm_evaluator.py:135-146` selects roster/referenced names without reading a limit.
- `README.md:75` describes `--execute` as submitting via Yahoo despite the executor stubs.
- `README.md:25` describes transaction reasoning without its opt-in qualification; the actual gate is `decision_engine.py:825-828`.

**Why it matters:** Maintainers and users can rely on configuration controls or interfaces that do not provide the documented behavior.

## Execution-path and gate notes

The source trace is: `main()` parses flags and loads config; `run_weekly()` discovers the week, loads or scrapes data, inserts missing columns, resolves team identity, builds the roster/depth/lineup, runs and filters optimizer recommendations, builds the simulated Action Plan, checks upcoming byes, and optionally invokes the LLM evaluator; `main()` prints the terminal report and saves Markdown using `report.week`. The executor branch is skipped in dry-run mode.

The reasoning gate at `decision_engine.py:825-828` and LLM gate at `run_weekly.py:208` work for the default false settings, and both disabled-path tests passed. Reasoning is applied only to add/drop pairs, not pure adds or later mandatory drops. `streaming_positions` is used inside the gated reasoning engine. No default-path bypass of either engine was found.

Bye warnings are computed from the original roster and original lineup at `run_weekly.py:188`, so they can mention players later dropped and omit incoming players. Their detection uses positive projections on either side of a zero/null week (`decision_engine.py:417-440`), rather than treating every zero/null projection as a confirmed bye.

Minor unused helpers, local variables, and imports were intentionally excluded from the prioritized findings because they have less functional impact than the issues above.

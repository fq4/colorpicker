# Suggested Features

## Implemented: opponent-aware weekly optimization

The weekly optimizer now supports an opponent team ID from `--opponent-id` or a
week-to-team-ID `schedule` map in `config.yaml`. It uses the opponent roster already
present in ffbot's league DataFrame, builds an optimal-lineup proxy, estimates scoring
variance from completed-week actuals, computes a normal-model win probability, and
selects role-aware bench/starter swaps. Matchup-aware transaction scores combine the
change in this week's win probability with a small configurable rest-of-season weight.

The probability estimate assumes independent player scores. The opponent lineup is a
projection proxy, not a prediction of the manager's actual submitted lineup. When fewer
than three completed games are available, position-specific SD defaults are used.

## Follow-ups requiring better source data

- **Live points-banked probability:** ffbot parses both `*`-prefixed current-week
  projections and completed-game actuals into the same numeric `Week N` DataFrame
  column. The source marker is discarded, so current-week actuals cannot be reliably
  distinguished from projections. Do not report live P(win) until the data source
  preserves actual/projection provenance.
- **NFL game correlation:** the current league DataFrame contains player NFL team
  abbreviations but no matchup/opponent-game identifiers. DEF/K versus opposing-QB
  hedge effects are therefore not modeled. Do not infer game relationships from player
  names or rosters.
- **Calibration and validation:** probability estimates use a simple normal
  approximation and independence assumption; future season data could be used to
  evaluate calibration before adding more complex distributions or correlations.

## Still useful small enhancements

- Lightweight personal results tracking against saved weekly reports, once a simple
  reliable way to associate recommendations with later actuals is available.
- Improve lineup-swap comparison notes to explicitly explain close projection margins
  and the matchup role posture.

Probability modeling is now an intentional part of this single-team tool's scope;
the previous exclusion of uncertainty and win-probability modeling is superseded.

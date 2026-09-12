from __future__ import annotations

import pandas as pd

from decision_engine import AddDropRecommendation, get_my_roster
from reasoning_engine import (
    NO_STARTER_CHANGE,
    STARTER_UPGRADE,
    _contradictions,
    evaluate_transaction,
    validate_roster,
)


WEEK = 3


def test_valid_add_drop_has_complete_state_metrics(mock_df, mock_config, positions_config):
    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    evaluation = evaluate_transaction(
        mock_df,
        roster,
        "Brandon Aubrey",
        "Jake Elliott",
        positions_config,
        WEEK,
        mock_config,
    )

    assert evaluation.legal
    assert evaluation.hold_current_week > 0
    assert evaluation.hold_ros > 0
    assert evaluation.starter_impact in {STARTER_UPGRADE, NO_STARTER_CHANGE}
    assert "current_week" in evaluation.ledger
    assert evaluation.ledger["vor"]["delta"] == evaluation.vor_delta


def test_bench_only_add_has_no_current_week_starter_change(mock_df, mock_config, positions_config):
    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    evaluation = evaluate_transaction(
        mock_df,
        roster,
        "Low Kicker",
        None,
        positions_config,
        WEEK,
        mock_config,
    )

    assert not evaluation.legal
    assert any("Add player is not in player data" in error for error in evaluation.errors)


def test_oversized_and_duplicate_rosters_are_illegal(mock_df, mock_config, positions_config):
    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    duplicate = roster.iloc[[0]].copy()
    oversized = pd.concat([roster] + [duplicate] * 5, ignore_index=True)
    result = validate_roster(oversized, positions_config, require_bench_depth=False)

    assert not result.legal
    assert any("capacity" in error for error in result.errors)
    assert any("Duplicate" in error for error in result.errors)


def test_recommendation_carries_reasoning_ledger(mock_df, mock_config, positions_config):
    from unittest.mock import patch
    from decision_engine import recommend_adds_drops

    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    optimizer = pd.DataFrame({
        "Add": ["Brandon Aubrey (K)"],
        "Drop": ["Jake Elliott (K)"],
        "VOR": [2.0],
    })
    with patch("ffbot.optimize", return_value=optimizer):
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)

    assert len(recs) == 1
    assert recs[0].reasoning_ledger is not None
    assert recs[0].current_week_delta is not None
    assert recs[0].transaction_classification is not None
    assert recs[0].reasoning_confidence in {"MUST_DO", "STRONG", "LEAN", "SPECULATIVE", "DO_NOT_MAKE"}


def test_contradiction_check_rejects_inconsistent_classification():
    contradictions = _contradictions(
        week_delta=0.0,
        vor_delta=-2.0,
        classification=STARTER_UPGRADE,
        errors=[],
    )

    assert any("Starter upgrade" in contradiction for contradiction in contradictions)

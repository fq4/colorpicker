from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from decision_engine import AddDropRecommendation, get_my_roster, recommend_adds_drops
from report import build_markdown_report, build_terminal_report
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
    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    optimizer = pd.DataFrame({
        "Add": ["Brandon Aubrey (K)"],
        "Drop": ["Jake Elliott (K)"],
        "VOR": [2.0],
    })
    config = dict(mock_config)
    config["reasoning_engine"] = {"enabled": True}
    with patch("ffbot.optimize", return_value=optimizer):
        recs = recommend_adds_drops(mock_df, roster, WEEK, config)

    assert len(recs) == 1
    assert recs[0].reasoning_ledger is not None
    assert recs[0].current_week_delta is not None
    assert recs[0].transaction_classification is not None
    assert recs[0].reasoning_confidence in {"MUST_DO", "STRONG", "LEAN", "SPECULATIVE", "DO_NOT_MAKE"}
    assert recs[0].reasoning_detail is not None
    assert "Reasoning Engine Detail" not in recs[0].reason


def test_reasoning_engine_disabled_by_default_skips_evaluation_and_output(mock_df, mock_config):
    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    optimizer = pd.DataFrame({
        "Add": ["Brandon Aubrey (K)"],
        "Drop": ["Jake Elliott (K)"],
        "VOR": [2.0],
    })
    with patch("ffbot.optimize", return_value=optimizer), patch("reasoning_engine.evaluate_transaction") as mock_eval:
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)

    assert len(recs) == 1
    assert recs[0].reasoning_ledger is None
    assert recs[0].reasoning_detail is None
    mock_eval.assert_not_called()

    terminal = build_terminal_report(recs[0] and SimpleNamespace(
        my_roster=roster,
        depth_warnings=[],
        bye_week_warnings=[],
        lineup=None,
        add_drop_recs=recs,
        low_value_recs=[],
        action_plan=None,
        week=WEEK,
        hypothetical_drop=None,
        llm_evaluation=None,
        llm_evaluation_error=None,
        llm_evaluation_prompt=None,
    ), WEEK, 2026, mock_config)
    markdown = build_markdown_report(SimpleNamespace(
        my_roster=roster,
        depth_warnings=[],
        bye_week_warnings=[],
        lineup=None,
        add_drop_recs=recs,
        low_value_recs=[],
        action_plan=None,
        week=WEEK,
        hypothetical_drop=None,
        llm_evaluation=None,
        llm_evaluation_error=None,
        llm_evaluation_prompt=None,
    ), WEEK, 2026, mock_config)
    assert "Reasoning Engine Detail" not in terminal
    assert "Reasoning Engine Detail" not in markdown
    assert "Transaction analysis:" not in terminal
    assert "Transaction analysis:" not in markdown


def test_reasoning_engine_enabled_via_config_creates_dedicated_detail_section(mock_df, mock_config):
    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    optimizer = pd.DataFrame({
        "Add": ["Brandon Aubrey (K)"],
        "Drop": ["Jake Elliott (K)"],
        "VOR": [2.0],
    })
    config = dict(mock_config)
    config["reasoning_engine"] = {"enabled": True}
    with patch("ffbot.optimize", return_value=optimizer), patch("reasoning_engine.evaluate_transaction") as mock_eval:
        mock_eval.return_value = SimpleNamespace(
            current_week_delta=2.5,
            ros_delta=4.0,
            vor_delta=1.5,
            starter_impact="STARTER_UPGRADE",
            classification="STARTER_UPGRADE",
            confidence="STRONG",
            drop_resistance=2.0,
            ledger={"summary": "ok"},
            contradictions=[],
            legal=True,
            errors=[],
        )
        recs = recommend_adds_drops(mock_df, roster, WEEK, config)

    assert len(recs) == 1
    assert recs[0].reasoning_detail is not None
    assert "Classification: STARTER_UPGRADE" in recs[0].reasoning_detail
    assert "Confidence: STRONG" in recs[0].reasoning_detail

    terminal = build_terminal_report(SimpleNamespace(
        my_roster=roster,
        depth_warnings=[],
        bye_week_warnings=[],
        lineup=None,
        add_drop_recs=recs,
        low_value_recs=[],
        action_plan=None,
        week=WEEK,
        hypothetical_drop=None,
        llm_evaluation=None,
        llm_evaluation_error=None,
        llm_evaluation_prompt=None,
    ), WEEK, 2026, config)
    markdown = build_markdown_report(SimpleNamespace(
        my_roster=roster,
        depth_warnings=[],
        bye_week_warnings=[],
        lineup=None,
        add_drop_recs=recs,
        low_value_recs=[],
        action_plan=None,
        week=WEEK,
        hypothetical_drop=None,
        llm_evaluation=None,
        llm_evaluation_error=None,
        llm_evaluation_prompt=None,
    ), WEEK, 2026, config)
    assert "Reasoning Engine Detail" in terminal
    assert "Reasoning Engine Detail" in markdown
    assert "Classification: STARTER_UPGRADE" in terminal
    assert "Classification: STARTER_UPGRADE" in markdown


def test_flag_reason_uses_pipe_separator_when_combining_existing_flags_and_illegality(mock_df, mock_config):
    """When the reasoning engine flags a transaction as illegal and the rec
    already has a pre-existing flag, both must be joined with ' | '.

    We set K bench-depth minimum to 2 so the roster has a genuine bench-depth
    gap at K before any move. Dropping Jake Elliott (a K) while adding Brandon
    Aubrey (a K) does not fix the bench-depth gap — both starters occupy the
    single K starter slot, leaving zero bench K's. This triggers the
    'exacerbates existing bench depth gap' flag BEFORE the reasoning engine
    appends its illegality message, so the test exercises the ' | ' joins.    """
    import pandas as pd
    from unittest.mock import patch

    config = dict(mock_config)
    config["reasoning_engine"] = {"enabled": True}
    config["bench_depth_minimums"] = {"RB": 1, "WR": 1, "QB": 1, "TE": 0, "K": 2}

    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    optimizer = pd.DataFrame({
        "Add": ["Brandon Aubrey (K)"],
        "Drop": ["Jake Elliott (K)"],
        "VOR": [2.0],
    })
    with patch("ffbot.optimize", return_value=optimizer), patch("reasoning_engine.evaluate_transaction") as mock_eval:
        mock_eval.return_value = SimpleNamespace(
            current_week_delta=-1.0,
            ros_delta=-2.0,
            vor_delta=-3.0,
            starter_impact="NO_STARTER_CHANGE",
            classification="NO_MEANINGFUL_IMPROVEMENT",
            confidence="DO_NOT_MAKE",
            drop_resistance=0.1,
            ledger={"summary": "ok"},
            contradictions=[],
            legal=False,
            errors=["Illegal transaction: drop not on roster"],
        )
        recs = recommend_adds_drops(mock_df, roster, WEEK, config)

    assert len(recs) == 1
    assert " | " in recs[0].flag_reason
    assert "; " not in recs[0].flag_reason
    assert "Illegal transaction" in recs[0].flag_reason
    assert recs[0].flag_reason.count("Illegal transaction") == 1


def test_cli_flag_enables_reasoning_engine_for_single_run(monkeypatch):
    import run_weekly

    monkeypatch.setattr("sys.argv", ["run_weekly.py", "--reasoning-engine"])
    with patch("run_weekly.load_config", return_value={
        "league_id": 1,
        "team_id": 1,
        "positions": "QB, WR, RB, BN",
        "bench_depth_minimums": {},
        "ir_statuses": ["IR"],
        "llm_evaluator": {"enabled": False},
    }), patch("run_weekly.setup_logging"), patch("run_weekly.run_weekly") as mock_run, patch(
        "run_weekly.save_markdown_report"
    ):
        mock_run.return_value = SimpleNamespace(
            week=1,
            my_roster=None,
            lineup=None,
            add_drop_recs=[],
            low_value_recs=[],
            depth_warnings=[],
            bye_week_warnings=[],
            action_plan=None,
            hypothetical_drop=None,
            llm_evaluation=None,
            llm_evaluation_error=None,
        )
        run_weekly.main()

    assert mock_run.call_args.args[0]["reasoning_engine"]["enabled"] is True


def test_contradiction_check_rejects_inconsistent_classification():
    contradictions = _contradictions(
        week_delta=0.0,
        vor_delta=-2.0,
        classification=STARTER_UPGRADE,
        errors=[],
    )

    assert any("Starter upgrade" in contradiction for contradiction in contradictions)

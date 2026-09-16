"""Regression tests for the six fixes from REVIEW_2."""
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from decision_engine import (
    ActionPlan, AddDropRecommendation, LineupRecommendation,
    _build_add_drop_reason, build_action_plan, flag_bench_depth_gaps,
    get_my_roster, recommend_adds_drops, recommend_lineup,
)
from report import build_markdown_report, build_terminal_report


def _player(name, pos, owner=7, proj=10.0):
    return {"Name": name, "Team": "X", "Position": pos, "Owner": "Wrong Name",
            "Owner ID": owner, "Status": "", "Week 3": proj, "VOR": proj}


def _positions():
    return {"positions": "RB, RB, W/R, BN, BN, BN", "bench_depth_minimums": {"RB": 1},
            "ir_statuses": ["IR"], "min_usable_projection": 1.0}


def test_flex_depth_reproduction_flows_through_recommendation_and_action_plan():
    df = pd.DataFrame([_player("RB1", "RB"), _player("RB2", "RB"), _player("RB3", "RB")])
    roster = get_my_roster(df, "anything", 3, team_id=7)
    lineup = recommend_lineup(df, roster, _positions(), 3)
    assert "RB" in {w.position for w in flag_bench_depth_gaps(roster, _positions(), lineup)}
    with patch("ffbot.optimize", return_value=pd.DataFrame(columns=["Add", "Drop", "VOR"])):
        assert recommend_adds_drops(df, roster, 3, {"league_id": 1, "team_id": 7,
            "positions": _positions()["positions"], "bench_depth_minimums": {"RB": 1},
            "ir_statuses": ["IR"], "min_usable_projection": 1.0}) == []
    plan = build_action_plan(df, roster, [], _positions(), 3)
    assert plan.final_lineup is not None
    assert plan.remaining_warnings and plan.remaining_warnings[0].position == "RB"


def test_vor_threshold_wording_above_and_below():
    base = dict(action="fa_add", add="A", add_position="RB", add_team="X", vor_gain=2.0)
    below = _build_add_drop_reason(AddDropRecommendation(**base), 3, "Week 3", set(), 5.0, {"RB": 8.0})
    above = _build_add_drop_reason(AddDropRecommendation(**{**base, "vor_gain": 8.0}), 3, "Week 3", set(), 5.0, {"RB": 8.0})
    assert "below the 8.0 threshold" in below and "clears the streaming threshold" not in below
    assert "clears the streaming threshold" in above


def test_team_id_filter_ignores_inconsistent_owner_text():
    df = pd.DataFrame([_player("Mine", "RB", 7), _player("Other", "RB", 8)])
    roster = get_my_roster(df, "different owner text", 3, team_id=7)
    assert roster["Name"].tolist() == ["Mine"]


def test_stale_cache_week_is_logged(monkeypatch):
    import run_weekly
    monkeypatch.setattr(run_weekly, "get_latest_cached_or_fresh", lambda *a, **k: (pd.DataFrame([_player("Mine", "RB")]), 2))
    monkeypatch.setattr("ffbot.current_week", lambda: 3)
    monkeypatch.setattr(run_weekly, "ensure_data_columns", lambda df: df)
    monkeypatch.setattr("data_layer.get_team_name_from_id", lambda df, team: "owner")
    monkeypatch.setattr(run_weekly, "get_my_roster", lambda *a, **k: pd.DataFrame(columns=["Name", "Position", "Status", "Week 2", "VOR"]))
    monkeypatch.setattr(run_weekly, "recommend_lineup", lambda *a: LineupRecommendation())
    monkeypatch.setattr(run_weekly, "flag_bench_depth_gaps", lambda *a, **k: [])
    monkeypatch.setattr(run_weekly, "recommend_adds_drops", lambda *a: [])
    monkeypatch.setattr(run_weekly, "build_action_plan", lambda *a, **k: ActionPlan([], []))
    monkeypatch.setattr(run_weekly, "check_upcoming_byes", lambda *a, **k: [])
    with patch.object(run_weekly.logger, "warning") as warning:
        run_weekly.run_weekly({"league_id": 1, "team_id": 7, "positions": "RB", "season": 2026})
    assert any("current week is now Week 3" in str(call) for call in warning.call_args_list)


def test_optimizer_failure_is_visible_in_reports():
    df = pd.DataFrame([_player("Mine", "RB")])
    with patch("ffbot.optimize", side_effect=RuntimeError("boom")):
        recs = recommend_adds_drops(df, df, 3,
                                    {"league_id": 1, "team_id": 7, "positions": "RB"})
    assert recs == []
    report = SimpleNamespace(my_roster=None, depth_warnings=[], bye_week_warnings=[], lineup=None,
        add_drop_recs=[], low_value_recs=[], action_plan=None, warnings=[recommend_adds_drops.last_error],
        hypothetical_drop=None, llm_evaluation=None, llm_evaluation_error=None, llm_evaluation_prompt=None)
    assert "Optimizer failed" in build_terminal_report(report, 3, 2026, {})
    assert "Optimizer failed" in build_markdown_report(report, 3, 2026, {})


def test_pure_drop_action_plan_has_no_none_text():
    rec = AddDropRecommendation(action="drop", drop="Locked bench", drop_position="TE")
    plan = ActionPlan([rec], [])
    terminal = __import__("report")._format_action_plan_terminal(plan, 3)
    markdown = __import__("report")._md_action_plan(plan, 3)
    assert "None" not in terminal and "None" not in markdown

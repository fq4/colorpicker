import pandas as pd
import pytest

from decision_engine import (
    AddDropRecommendation,
    ActionPlan,
    LineupRecommendation,
    MatchupReport,
    RecommendationReport,
    build_matchup_analysis,
    build_action_plan,
    get_my_roster,
    get_opponent_roster,
    matchup_transaction_score,
    _player_standard_deviation,
    recommend_lineup,
    win_probability,
)


def test_win_probability_uses_normal_cdf():
    assert win_probability(0.0, 0.0) == 0.5
    assert win_probability(1.0, 0.0) == 1.0
    assert win_probability(-1.0, 0.0) == 0.0
    assert win_probability(0.0, 1.0) == 0.5
    assert win_probability(1.0, 1.0) == pytest.approx(0.8413447)


def test_player_sd_excludes_team_bye_zero_but_keeps_other_zero():
    roster = pd.DataFrame(
        [
            {
                "Name": "Runner",
                "Team": "BUF",
                "Position": "RB",
                "Week 1": 12,
                "Week 2": 14,
                "Week 3": 0,
                "Week 4": 13,
                "Week 5": 0,
            },
            {
                "Name": "Teammate",
                "Team": "BUF",
                "Position": "WR",
                "Week 1": 10,
                "Week 2": 12,
                "Week 3": 0,
                "Week 4": 11,
                "Week 5": 10,
            },
            {
                "Name": "Third Teammate",
                "Team": "BUF",
                "Position": "TE",
                "Week 1": 8,
                "Week 2": 10,
                "Week 3": 0,
                "Week 4": 9,
                "Week 5": 10,
            },
            {
                "Name": "Other Team Player",
                "Team": "MIA",
                "Position": "WR",
                "Week 1": 8,
                "Week 2": 10,
                "Week 3": 12,
                "Week 4": 10,
                "Week 5": 10,
            },
        ]
    )
    player = roster.iloc[0]

    without_dnp = _player_standard_deviation(
        player.drop(labels=["Week 5"]),
        5,
        {"RB": 9.0},
        roster.drop(columns=["Week 5"]),
    )
    with_dnp = _player_standard_deviation(
        player, 6, {"RB": 9.0}, roster
    )

    assert without_dnp == pytest.approx(1.0)
    assert with_dnp == pytest.approx(pd.Series([12, 14, 13, 0]).std(ddof=1))


def test_engine_does_not_exclude_lone_rostered_player_zero_as_bye():
    roster = pd.DataFrame(
        [
            {
                "Name": "Kicker",
                "Team": "BUF",
                "Position": "K",
                "Week 1": 0,
                "Week 2": 0,
                "Week 3": 0,
            }
        ]
    )

    standard_deviation = _player_standard_deviation(
        roster.iloc[0], 4, {"K": 4.0}, roster
    )

    assert standard_deviation == 0.0

def test_opponent_roster_is_resolved_and_validated(mock_df):
    name, roster = get_opponent_roster(mock_df, 7, 2, 3)

    assert name == "Team B"
    assert roster["Name"].tolist() == ["Patrick Mahomes"]

    with pytest.raises(ValueError, match="must differ"):
        get_opponent_roster(mock_df, 7, 7, 3)
    with pytest.raises(ValueError, match="not found"):
        get_opponent_roster(mock_df, 7, 99, 3)


def test_underdog_lineup_uses_higher_ceiling_bench_player():
    rows = [
        {
            "ID": 1, "Name": "Starter A", "Team": "A", "Position": "RB",
            "Owner": "My Team", "Owner ID": 7, "Status": "", "VOR": 1,
            "Week 1": 20, "Week 2": 20, "Week 3": 20, "Week 4": 20,
        },
        {
            "ID": 2, "Name": "Starter B", "Team": "B", "Position": "RB",
            "Owner": "My Team", "Owner ID": 7, "Status": "", "VOR": 1,
            "Week 1": 18, "Week 2": 18, "Week 3": 18, "Week 4": 18,
        },
        {
            "ID": 3, "Name": "High Ceiling", "Team": "C", "Position": "RB",
            "Owner": "My Team", "Owner ID": 7, "Status": "", "VOR": 1,
            "Week 1": 0, "Week 2": 30, "Week 3": 0, "Week 4": 17,
        },
        {
            "ID": 6, "Name": "C Teammate", "Team": "C", "Position": "RB",
            "Owner": "Opponent", "Owner ID": 8, "Status": "", "VOR": 1,
            "Week 1": 10, "Week 2": 10, "Week 3": 10, "Week 4": 1,
        },
        {
            "ID": 4, "Name": "Opponent A", "Team": "D", "Position": "RB",
            "Owner": "Opponent", "Owner ID": 8, "Status": "", "VOR": 1,
            "Week 1": 30, "Week 2": 30, "Week 3": 30, "Week 4": 30,
        },
        {
            "ID": 5, "Name": "Opponent B", "Team": "E", "Position": "RB",
            "Owner": "Opponent", "Owner ID": 8, "Status": "", "VOR": 1,
            "Week 1": 30, "Week 2": 30, "Week 3": 30, "Week 4": 30,
        },
    ]
    df = pd.DataFrame(rows)
    positions = {
        "positions": "RB, RB",
        "ir_statuses": ["IR"],
        "bench_depth_minimums": {},
    }
    own_roster = get_my_roster(df, "My Team", 4, team_id=7)
    opponent_roster = get_my_roster(df, "Opponent", 4, team_id=8)
    baseline = recommend_lineup(df, own_roster, positions, 4)

    selected, matchup = build_matchup_analysis(
        df,
        own_roster,
        baseline,
        "Opponent",
        8,
        opponent_roster,
        positions,
        4,
        {
            "position_sd": {"default": 1.0, "RB": 1.0},
            "win_probability_thresholds": {"favorite": 0.6, "underdog": 0.4},
            "matchup_z": 0.5,
            "lineup_swap_margin": 1.0,
        },
    )

    assert matchup.role == "underdog"
    assert "High Ceiling" in {starter.player for starter in selected.starters}
    assert matchup.swing_decisions == ["High Ceiling for Starter B at RB2"]


def test_matchup_transaction_score_combines_weekly_probability_and_ros():
    assert matchup_transaction_score(0.03, 5.0, 0.002) == pytest.approx(0.04)
    assert matchup_transaction_score(-0.02, 5.0, 0.002) == pytest.approx(-0.01)
    assert matchup_transaction_score(0.0, None, 0.002) == 0.0


def test_nonpositive_matchup_transaction_is_skipped(mock_df, mock_config, positions_config):
    roster = get_my_roster(mock_df, mock_config["team_name"], 3, team_id=7)
    rec = AddDropRecommendation(
        action="fa_add",
        add="Derek Carr",
        add_position="QB",
        matchup_transaction_score=-0.01,
    )

    plan = build_action_plan(
        mock_df, roster, [rec], positions_config, 3
    )

    assert plan.moves == []
    assert plan.skipped_recs[0].flagged
    assert "non-positive" in plan.skipped_recs[0].flag_reason


def test_weekly_pipeline_uses_the_scheduled_opponent(monkeypatch, mock_df, mock_config):
    import ffbot
    import run_weekly

    monkeypatch.setattr(ffbot, "current_week", lambda: 3)
    monkeypatch.setattr(
        run_weekly,
        "get_latest_cached_or_fresh",
        lambda *args, **kwargs: (mock_df.copy(), 3),
    )
    monkeypatch.setattr(run_weekly, "flag_bench_depth_gaps", lambda *args, **kwargs: [])
    monkeypatch.setattr(run_weekly, "recommend_adds_drops", lambda *args: [])

    config = {
        **mock_config,
        "schedule": {"3": 2},
        "position_sd": {"default": 8.0},
        "win_probability_thresholds": {"favorite": 0.6, "underdog": 0.4},
        "lineup_swap_margin": 1.0,
        "matchup_z": 0.5,
    }

    report = run_weekly.run_weekly(config)

    assert report.matchup.opponent_team_id == 2
    assert report.matchup.opponent_name == "Team B"
    assert report.matchup.opponent_lineup.starters


def test_matchup_report_precedes_action_plan_in_both_formats():
    from report import build_markdown_report, build_terminal_report

    report = RecommendationReport(
        lineup=LineupRecommendation(total_projection=90.0),
        matchup=MatchupReport(
            opponent_team_id=8,
            opponent_name="Opponent",
            opponent_lineup=LineupRecommendation(),
            opponent_projection=99.0,
            opponent_sd=10.0,
            team_sd=9.0,
            win_probability=0.4,
            role="underdog",
            lineup_mode="ceiling (projection + z × SD)",
            swing_decisions=["Bench RB for Starter RB"],
        ),
        action_plan=ActionPlan([], []),
    )
    terminal = build_terminal_report(report, 9, 2026, {})
    markdown = build_markdown_report(report, 9, 2026, {})

    assert terminal.index("--- Matchup ---") < terminal.index("YOUR ACTION PLAN")
    assert "P(win): 40.0%" in terminal
    assert markdown.index("## Matchup") < markdown.index("## Your Action Plan")
    assert "P(win):** 40.0%" in markdown

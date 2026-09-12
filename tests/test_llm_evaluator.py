from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest

from decision_engine import AddDropRecommendation, RecommendationReport, recommend_lineup, get_my_roster
from llm_evaluator import (
    EvaluatorError,
    IndependentLLMEvaluator,
    build_evaluation_context,
    build_user_prompt,
)


WEEK = 3


def valid_response(add_player="Brandon Aubrey", drop_player="Jake Elliott"):
    return json.dumps({
        "overall_assessment": "The engine is mostly sound, but review the kicker swap.",
        "engine_score": 78,
        "lineup": {"agreement": "modify", "recommended_changes": [], "reasoning": "Small edge."},
        "transactions": [{
            "action": "add_drop",
            "add_player": add_player,
            "drop_player": drop_player,
            "classification": "optional",
            "confidence": "medium",
            "current_week_projection_delta": 2.0,
            "vor_delta": 2.0,
            "rest_of_season_assessment": "Short-term stream only.",
            "starting_lineup_impact": "Adds kicker projection.",
            "reasoning": "The drop should be evaluated independently.",
        }],
        "engine_agreements": ["The lineup is reasonable."],
        "engine_corrections": ["The drop is not clearly optimal."],
        "missed_opportunities": [],
        "holds": ["Hold the rest of the roster."],
        "strategic_opportunities": [],
        "data_limitations": [],
        "bottom_line": "Consider the move, but do not force it.",
    })


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.system_prompt = None
        self.user_payload = None

    def complete(self, system_prompt, user_payload):
        self.system_prompt = system_prompt
        self.user_payload = user_payload
        return self.response


def build_context(mock_df, mock_config, positions_config):
    roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
    lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
    recommendation = AddDropRecommendation(
        action="add_drop", add="Brandon Aubrey", add_position="K",
        drop="Jake Elliott", drop_position="K", add_projection=10.5,
        drop_projection=8.5, vor_gain=2.0, flagged=False,
    )
    report = RecommendationReport(
        my_roster=roster,
        lineup=lineup,
        add_drop_recs=[recommendation],
        week=WEEK,
    )
    return build_evaluation_context(mock_df, report, mock_config, WEEK)


def test_valid_fake_evaluation_is_structured_and_raw_data_precedes_engine(
    mock_df, mock_config, positions_config
):
    provider = FakeProvider(valid_response())
    evaluation = IndependentLLMEvaluator(provider).evaluate(
        build_context(mock_df, mock_config, positions_config)
    )

    assert evaluation.data["engine_score"] == 78
    assert "Brandon Aubrey" in provider.user_payload
    assert provider.user_payload.index("PLAYER_DATA_JSON") < provider.user_payload.index("DETERMINISTIC_ENGINE_OUTPUT_JSON")


def test_no_transaction_is_valid(mock_df, mock_config, positions_config):
    response = json.loads(valid_response())
    response["transactions"] = [{
        "action": "hold", "add_player": None, "drop_player": None,
        "classification": "do_not_make", "confidence": "high",
        "current_week_projection_delta": 0.0, "vor_delta": 0.0,
        "rest_of_season_assessment": "Hold.",
        "starting_lineup_impact": "None.", "reasoning": "No move is justified.",
    }]
    evaluation = IndependentLLMEvaluator(FakeProvider(json.dumps(response))).evaluate(
        build_context(mock_df, mock_config, positions_config)
    )
    assert evaluation.data["transactions"][0]["action"] == "hold"


def test_malformed_json_is_rejected(mock_df, mock_config, positions_config):
    with pytest.raises(EvaluatorError, match="valid JSON"):
        IndependentLLMEvaluator(FakeProvider("not json")).evaluate(
            build_context(mock_df, mock_config, positions_config)
        )


def test_unknown_player_reference_is_rejected(mock_df, mock_config, positions_config):
    with pytest.raises(EvaluatorError, match="unsupported player"):
        IndependentLLMEvaluator(FakeProvider(valid_response("Invented Player", None))).evaluate(
            build_context(mock_df, mock_config, positions_config)
        )


def test_player_data_only_contains_roster_and_referenced_candidates(
    mock_df, mock_config, positions_config
):
    config = dict(mock_config)
    context = build_context(mock_df, config, positions_config)
    names = {player["name"] for player in context.players}

    assert set(mock_df[mock_df["Owner"] == mock_config["team_name"]]["Name"]).issubset(names)
    assert {"Brandon Aubrey", "Jake Elliott"}.issubset(names)
    assert "Travis Kelce" not in names
    assert all("week_projections" not in player for player in context.players)


def test_report_renders_valid_assessment_without_replacing_engine(mock_df, mock_config, positions_config):
    from report import build_markdown_report, build_terminal_report

    report = RecommendationReport(
        my_roster=get_my_roster(mock_df, mock_config["team_name"], WEEK),
        llm_evaluation=json.loads(valid_response()),
        add_drop_recs=[],
    )
    markdown = build_markdown_report(report, WEEK, 2026, mock_config)
    terminal = build_terminal_report(report, WEEK, 2026, mock_config)
    assert "Independent LLM Assessment" in markdown
    assert "Second opinion" in markdown
    assert "The engine is mostly sound" in terminal
    assert "Recommended Adds/Drops" in markdown


def test_disabled_weekly_run_does_not_call_evaluator(mock_df, mock_config):
    from run_weekly import run_weekly

    class FailingEvaluator:
        def evaluate(self, context):
            raise AssertionError("disabled evaluator was called")

    config = dict(mock_config)
    config["llm_evaluator"] = {"enabled": False}
    with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, WEEK)), \
         patch("ffbot.optimize", return_value=pd.DataFrame(columns=["Add", "Drop", "VOR"])), \
         patch("ffbot.current_week", return_value=WEEK):
        report = run_weekly(config, evaluator=FailingEvaluator())

    assert report.llm_evaluation is None
    assert report.llm_evaluation_error is None
    assert report.llm_evaluation_prompt is None


def test_unavailable_evaluator_does_not_break_weekly_run(mock_df, mock_config):
    from run_weekly import run_weekly

    class UnavailableEvaluator:
        def evaluate(self, context):
            raise EvaluatorError("provider unavailable")

    config = dict(mock_config)
    config["llm_evaluator"] = {"enabled": True}
    with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, WEEK)), \
         patch("ffbot.optimize", return_value=pd.DataFrame(columns=["Add", "Drop", "VOR"])), \
         patch("ffbot.current_week", return_value=WEEK):
        report = run_weekly(config, evaluator=UnavailableEvaluator())

    assert report.lineup is not None
    assert report.llm_evaluation is None
    assert report.llm_evaluation_error == "provider unavailable"
    assert report.llm_evaluation_prompt is not None
    assert "PLAYER_DATA_JSON" in report.llm_evaluation_prompt


def test_unconfigured_provider_report_includes_prompt(mock_df, mock_config):
    from report import build_markdown_report, build_terminal_report
    from run_weekly import run_weekly

    config = dict(mock_config)
    config["llm_evaluator"] = {"enabled": True, "model": ""}
    with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, WEEK)), \
         patch("ffbot.optimize", return_value=pd.DataFrame(columns=["Add", "Drop", "VOR"])), \
         patch("ffbot.current_week", return_value=WEEK):
        report = run_weekly(config)

    markdown = build_markdown_report(report, WEEK, 2026, config)
    terminal = build_terminal_report(report, WEEK, 2026, config)
    assert "LLM evaluator model is not configured" in markdown
    assert "Prompt Messages That Would Have Been Sent" in markdown
    assert "SYSTEM_PROMPT:" in markdown
    assert "PLAYER_DATA_JSON" in markdown
    assert "PLAYER_DATA_JSON" in terminal

import csv
import logging

import pandas as pd
import pytest

from analytics.integration import load_model_predictions
from analytics.models import _error_sd, compute_metrics, forecast_walk_forward
from analytics.store import AnalyticsStore
from decision_engine import (
    LineupRecommendation,
    LineupSlot,
    _lineup_standard_deviation,
)


LOGGER = logging.getLogger("analytics-test")


def _write_snapshot(path, rows, columns):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_ingest_is_idempotent_and_marks_completed_team_byes(tmp_path):
    csv_path = tmp_path / "league_123_week_2_20261001_120000.csv"
    columns = [
        "ID", "Name", "Team", "Position", "Owner ID", "Week 1", "Week 2"
    ]
    _write_snapshot(
        csv_path,
        [
            {"ID": 1, "Name": "BUF RB", "Team": "BUF", "Position": "RB", "Owner ID": 1, "Week 1": 0, "Week 2": 14},
            {"ID": 2, "Name": "BUF WR", "Team": "BUF", "Position": "WR", "Owner ID": 2, "Week 1": 0, "Week 2": 11},
            {"ID": 3, "Name": "MIA RB", "Team": "MIA", "Position": "RB", "Owner ID": 1, "Week 1": 0, "Week 2": 12},
            {"ID": 4, "Name": "MIA WR", "Team": "MIA", "Position": "WR", "Owner ID": 2, "Week 1": 9, "Week 2": 10},
        ],
        columns,
    )
    store = AnalyticsStore(tmp_path / "analytics.sqlite3")
    locks = {
        1: "2030-09-01T20:00:00-04:00",
        2: "2030-09-08T20:00:00-04:00",
    }

    first = store.ingest_file(csv_path, 123, locks)
    second = store.ingest_file(csv_path, 123, locks)

    assert first["inserted"] is True
    assert second["inserted"] is False
    assert len(store.snapshot_inventory(123)) == 1
    actuals = {
        row["player_id"]: row for row in store.actuals(123)
    }
    assert actuals["1"]["is_bye"] == 1
    assert actuals["2"]["is_bye"] == 1
    assert actuals["3"]["is_bye"] == 0
    assert actuals["3"]["value"] == 0
    store.close()


def test_walk_forward_forecast_does_not_read_target_or_future_actuals():
    class MemoryStore:
        def __init__(self):
            self.actual_rows = [
                {"player_id": "1", "player_name": "Receiver", "position": "WR", "target_week": 1, "value": 10.0, "is_bye": 0},
                {"player_id": "1", "player_name": "Receiver", "position": "WR", "target_week": 2, "value": 12.0, "is_bye": 0},
                {"player_id": "1", "player_name": "Receiver", "position": "WR", "target_week": 3, "value": 500.0, "is_bye": 0},
                {"player_id": "1", "player_name": "Receiver", "position": "WR", "target_week": 4, "value": 1000.0, "is_bye": 0},
            ]
            self.projections = {
                week: [{
                    "player_id": "1",
                    "player_name": "Receiver",
                    "position": "WR",
                    "target_week": week,
                    "snapshot_time": f"2026-09-0{week}T12:00:00-04:00",
                    "value": 11.0 + week,
                }]
                for week in range(1, 4)
            }

        def actuals(self, _league_id):
            return self.actual_rows

        def clean_projections(self, _league_id, week):
            return self.projections.get(week, [])

    store = MemoryStore()
    before = forecast_walk_forward(store, 123, 3, {"default": 8.0})[3]["1"]
    store.actual_rows[2]["value"] = -10000.0
    store.actual_rows[3]["value"] = -20000.0
    after = forecast_walk_forward(store, 123, 3, {"default": 8.0})[3]["1"]

    assert after["model_proj"] == before["model_proj"]
    assert after["ewma"] == before["ewma"]
    assert after["trained_through_week"] == 2


def test_metric_math_and_forecast_error_sd_shrinkage():
    metrics = compute_metrics([1.0, 2.0], [2.0, 4.0])

    assert metrics["mae"] == pytest.approx(1.5)
    assert metrics["rmse"] == pytest.approx((2.5) ** 0.5)
    assert metrics["bias"] == pytest.approx(1.5)
    assert metrics["rank_correlation"] == pytest.approx(1.0)
    assert _error_sd([0.0, 2.0], 8.0) == 8.0
    assert _error_sd([0.0, 2.0, 4.0], 8.0) < 8.0


def _prediction_csv(
    path, trained_week=8, weeks=4, yahoo_mae=10, model_mae=9, league_id=123
):
    columns = [
        "league_id", "player_id", "player_name", "position",
        "yahoo_proj", "model_proj", "model_sd", "model_version",
        "trained_through_week", "yahoo_sd", "blend_weight",
        "projection_weeks", "yahoo_oos_mae", "model_oos_mae",
        "projection_gate_passed",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_snapshot(
        path,
        [
            {
                "league_id": league_id,
                "player_id": 1,
                "player_name": "Receiver",
                "position": "WR",
                "yahoo_proj": 12,
                "model_proj": 13,
                "model_sd": 5,
                "model_version": "position-blend-v1",
                "trained_through_week": trained_week,
                "yahoo_sd": 7,
                "blend_weight": 0.5,
                "projection_weeks": weeks,
                "yahoo_oos_mae": yahoo_mae,
                "model_oos_mae": model_mae,
                "projection_gate_passed": "true",
            }
        ],
        columns,
    )


def test_missing_prediction_file_falls_back_to_yahoo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "league_id": 123,
        "use_model_sd": True,
        "use_model_projections": True,
    }
    df = pd.DataFrame([{"ID": 1, "Week 9": 12.0}])

    result, sd_map, info = load_model_predictions(df, config, 9, LOGGER)

    assert result["Week 9"].tolist() == [12.0]
    assert sd_map == {}
    assert info["status"] == "prediction file missing"


def test_stale_predictions_fall_back_to_yahoo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "analytics_out" / "league_123_week_9_predictions.csv"
    _prediction_csv(path, trained_week=7)
    config = {"league_id": 123, "use_model_sd": True, "use_model_projections": True}
    df = pd.DataFrame([{"ID": 1, "Week 9": 12.0}])

    result, sd_map, info = load_model_predictions(df, config, 9, LOGGER)

    assert result["Week 9"].tolist() == [12.0]
    assert sd_map == {}
    assert info["status"].startswith("stale predictions")


def test_model_projection_gate_fallback_and_independent_yahoo_sd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "analytics_out" / "league_123_week_9_predictions.csv"
    _prediction_csv(path, weeks=3, yahoo_mae=10, model_mae=9)
    config = {
        "league_id": 123,
        "use_model_sd": True,
        "use_model_projections": True,
        "model_projection_min_weeks": 6,
        "model_projection_min_mae_improvement": 0.25,
    }
    df = pd.DataFrame([{"ID": 1, "Week 9": 12.0}])

    result, sd_map, info = load_model_predictions(df, config, 9, LOGGER)

    assert result["Week 9"].tolist() == [12.0]
    assert sd_map == {"1": 7.0}
    assert info["used_projections"] is False
    assert info["used_sd"] is True
    assert info["sd_source"] == "Yahoo forecast-error SD"


def test_qualified_model_projection_and_model_sd_apply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "analytics_out" / "league_123_week_9_predictions.csv"
    _prediction_csv(path, weeks=6, yahoo_mae=10, model_mae=9.5)
    config = {
        "league_id": 123,
        "use_model_sd": True,
        "use_model_projections": True,
        "model_projection_min_weeks": 6,
        "model_projection_min_mae_improvement": 0.25,
    }
    df = pd.DataFrame([{"ID": 1, "Week 9": 12.0}])

    result, sd_map, info = load_model_predictions(df, config, 9, LOGGER)

    assert result["Week 9"].tolist() == [13.0]
    assert sd_map == {"1": 5.0}
    assert info["used_projections"] is True
    assert info["sd_source"] == "blend-model forecast-error SD"


def test_model_file_is_advisory_when_both_flags_are_disabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "analytics_out" / "league_123_week_9_predictions.csv"
    _prediction_csv(path, weeks=6, yahoo_mae=10, model_mae=9)
    config = {
        "league_id": 123,
        "use_model_sd": False,
        "use_model_projections": False,
        "model_projection_min_weeks": 6,
        "model_projection_min_mae_improvement": 0.25,
    }
    df = pd.DataFrame([{"ID": 1, "Week 9": 12.0}])

    result, sd_map, info = load_model_predictions(df, config, 9, LOGGER)

    assert result["Week 9"].tolist() == [12.0]
    assert sd_map == {}
    assert info["used_projections"] is False
    assert info["used_sd"] is False
    assert info["player_values"][0]["model_proj"] == 13.0


def test_missing_player_sd_uses_the_existing_position_fallback():
    roster = pd.DataFrame(
        [
            {"ID": 1, "Name": "Model SD", "Team": "BUF", "Position": "QB", "Week 1": 10},
            {"ID": 2, "Name": "Fallback SD", "Team": "MIA", "Position": "QB", "Week 1": 12},
        ]
    )
    lineup = LineupRecommendation(
        starters=[
            LineupSlot("QB", "Model SD", "BUF", "QB", 10, 0),
            LineupSlot("QB2", "Fallback SD", "MIA", "QB", 12, 0),
        ]
    )

    standard_deviation = _lineup_standard_deviation(
        roster,
        lineup,
        2,
        {"default": 8.0, "QB": 8.0},
        model_sd_by_player={"1": 5.0},
    )

    assert standard_deviation == pytest.approx((5.0 ** 2 + 8.0 ** 2) ** 0.5)


@pytest.mark.parametrize(
    ("failure", "reason_fragment"),
    [
        ("wrong_league", "belongs to league 999"),
        ("missing_columns", "missing required columns"),
        ("duplicates", "Duplicate player_id"),
        ("bad_values", "Invalid model_proj"),
        ("unreadable", "test unreadable"),
    ],
)
def test_weekly_run_falls_back_when_analytics_input_fails(
    tmp_path, monkeypatch, failure, reason_fragment
):
    from pathlib import Path

    from run_weekly import _load_model_predictions_safely

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "analytics_out" / "league_123_week_9_predictions.csv"
    if failure == "wrong_league":
        _prediction_csv(path, league_id=999)
    elif failure == "missing_columns":
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_snapshot(path, [{"league_id": 123}], ["league_id"])
    else:
        _prediction_csv(path)
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            row = next(reader)
            columns = reader.fieldnames
        if failure == "duplicates":
            _write_snapshot(path, [row, row], columns)
        elif failure == "bad_values":
            row["model_proj"] = "not-a-number"
            _write_snapshot(path, [row], columns)

    if failure == "unreadable":
        original_open = Path.open

        def fail_prediction_open(self, *args, **kwargs):
            if self.resolve() == path.resolve():
                raise PermissionError("test unreadable prediction file")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_prediction_open)

    class CaptureLogger:
        def __init__(self):
            self.warnings = []

        def info(self, message):
            pass

        def warning(self, message):
            self.warnings.append(message)

    logger = CaptureLogger()
    yahoo_df = pd.DataFrame([{"ID": 1, "Week 9": 12.0}])
    config = {
        "league_id": 123,
        "use_model_sd": True,
        "use_model_projections": True,
    }

    result, sd_by_player, info = _load_model_predictions_safely(
        yahoo_df, config, 9, logger
    )

    assert result.equals(yahoo_df)
    assert sd_by_player == {}
    assert info["used_projections"] is False
    assert info["used_sd"] is False
    assert reason_fragment in info["failure_reason"]
    assert logger.warnings

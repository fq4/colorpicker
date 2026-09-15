"""Tests for lightweight completed-week actual score capture."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from data_layer import capture_actual_results


def test_capture_actual_results_parses_team_and_saves_csv(tmp_path):
    scraped = pd.DataFrame(
        [
            {"Name": "Alpha QB", "Owner ID": 7.0, "Week 1": 21.34},
            {"Name": "Bravo RB", "Owner ID": 7, "Week 1": "14.5"},
            {"Name": "Other Team WR", "Owner ID": 3.0, "Week 1": 30.0},
            {"Name": "No Score Yet", "Owner ID": 7.0, "Week 1": None},
        ]
    )

    actuals, path = capture_actual_results(
        scraped,
        league_id=492312,
        team_id=7,
        week=1,
        results_dir=str(tmp_path),
    )

    assert Path(path).name == "league_492312_team_7_week_1_actuals.csv"
    assert actuals.to_dict("records") == [
        {
            "league_id": 492312,
            "team_id": 7,
            "week": 1,
            "player_name": "Alpha QB",
            "actual_points": 21.34,
        },
        {
            "league_id": 492312,
            "team_id": 7,
            "week": 1,
            "player_name": "Bravo RB",
            "actual_points": 14.5,
        },
    ]

    saved = pd.read_csv(path)
    pd.testing.assert_frame_equal(saved, actuals, check_dtype=False)

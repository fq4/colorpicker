"""
Mock data fixtures for testing the decision engine without scraping Yahoo.
"""
from __future__ import annotations

import pandas as pd
import pytest


def _make_player(
    pid: int,
    name: str,
    team: str,
    position: str,
    owner: str,
    owner_id: float,
    status: str | None,
    owned_pct: str,
    week3: float,
    vor: float,
    week4: float = 0.0,
    week5: float = 0.0,
):
    """Helper to create a player row dict."""
    return {
        "ID": pid,
        "Name": name,
        "Team": team,
        "Position": position,
        "Owner": owner,
        "Owner ID": owner_id,
        "Status": status,
        "% Owned": owned_pct,
        "Week 1": 10.0,
        "Week 2": 12.0,
        "Week 3": week3,
        "Week 4": week4,
        "Week 5": week5,
        "Remaining": week3 + week4 + week5,
        "VOR": vor,
    }


@pytest.fixture
def mock_df() -> pd.DataFrame:
    """A mock scraped DataFrame with realistic columns and players."""
    rows = [
        # --- User's team (team_id=7, team_name="انتصاب يستمر ثلاث عشرة ساعة") ---
        # Starters
        _make_player(1, "Josh Allen", "BUF", "QB", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "75.3", 24.5, 85.3),
        _make_player(2, "Tyreek Hill", "MIA", "WR", "انتصاب يستمر ثلاث عشرة ساعة", 7, "Q", "98.2", 22.3, 142.1),
        _make_player(3, "CeeDee Lamb", "DAL", "WR", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "92.1", 19.8, 118.5),
        _make_player(4, "Breece Hall", "NYJ", "RB", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "88.5", 18.2, 95.0),
        _make_player(5, "Jahmyr Gibbs", "DET", "RB", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "72.0", 15.5, 82.3),
        _make_player(6, "Sam LaPorta", "DET", "TE", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "65.4", 10.2, 45.0),
        _make_player(7, "Jake Elliott", "PHI", "K", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "45.0", 8.5, 12.0),
        _make_player(8, "San Francisco", "SF", "DEF", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "52.3", 7.8, 8.0),
        # Bench — WR depth
        _make_player(9, "Jordan Addison", "MIN", "WR", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "32.1", 11.0, 35.0),
        # Bench — RB on IR
        _make_player(10, "Robbie Ouzts", "FA", "RB", "انتصاب يستمر ثلاث عشرة ساعة", 7, "IR", "0.1", 0.0, -2.0),
        _make_player(11, "Kirk Cousins", "ATL", "QB", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "5.0", 12.0, 30.0),
        _make_player(12, "Joe Milton", "TEN", "QB", "انتصاب يستمر ثلاث عشرة ساعة", 7, "", "0.5", 0.0, -15.0),
        # --- Free agents ---
        _make_player(20, "Woody Marks", "JAX", "RB", "Free Agent", float("nan"), "", "3.2", 15.8, 52.0),
        _make_player(21, "Isaiah Likely", "BAL", "TE", "Free Agent", float("nan"), "", "8.5", 9.5, 40.0),
        _make_player(22, "Kaden Elliss", "NO", "RB", "Free Agent", float("nan"), "Q", "2.1", 12.3, 38.0),
        _make_player(23, "Chase Brown", "WAS", "RB", "Free Agent", float("nan"), "", "15.0", 13.5, 48.0),
        _make_player(24, "Derek Carr", "NO", "QB", "Free Agent", float("nan"), "", "5.0", 18.7, 55.0),
        _make_player(25, "Brandon Aubrey", "DAL", "K", "Free Agent", float("nan"), "", "18.0", 10.5, 18.0),
        _make_player(26, "Baltimore", "BAL", "DEF", "Free Agent", float("nan"), "", "35.0", 6.2, 7.0),
        _make_player(27, "Rashid Shaheed", "NO", "WR", "Free Agent", float("nan"), "Q", "20.0", 14.3, 60.0),
        _make_player(28, "Jayden Reed", "GB", "WR", "Free Agent", float("nan"), "", "28.0", 13.8, 52.0),
        # --- Other teams ---
        _make_player(30, "Travis Kelce", "KC", "TE", "Team A", 1, "", "99.9", 16.5, 110.0),
        _make_player(31, "Patrick Mahomes", "KC", "QB", "Team B", 2, "", "99.8", 25.8, 92.0),
        _make_player(32, "Derrick Henry", "TEN", "RB", "Team C", 3, "", "85.0", 14.2, 78.0),
        _make_player(33, "Harrison Mevis", "STL", "K", "Team D", 4, "", "12.0", 6.1, 5.0),
        _make_player(34, "DJ Moore", "CHI", "WR", "Team E", 5, "", "88.0", 18.8, 105.0),
        _make_player(35, "Dylan Laube", "LV", "RB", "Team F", 6, "", "4.5", 8.8, 28.0),
        _make_player(36, "Taysom Hill", "NO", "QB,WR,RB,TE", "Team G", 8, "", "30.0", 12.0, 50.0),
    ]

    df = pd.DataFrame(rows)
    # Ensure all week columns exist
    for w in range(1, 19):
        col = f"Week {w}"
        if col not in df.columns:
            df[col] = 0.0
    return df


@pytest.fixture
def mock_config() -> dict:
    """Mock config matching config.yaml structure."""
    return {
        "league_id": 492312,
        "team_id": 7,
        "team_name": "انتصاب يستمر ثلاث عشرة ساعة",
        "positions": "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR",
        "is_idp": False,
        "scoring_type": "half-ppr",
        "waiver_type": "continual_rolling",
        "waiver_priority": 10,
        "season": 2026,
        "min_vor_gain_to_recommend_add": {
            "QB": 2.0,
            "TE": 2.0,
            "K": 1.0,
            "DEF": 1.0,
            "RB": 8.0,
            "WR": 8.0,
        },
        "min_vor_loss_to_flag_drop": -50.0,
        "min_usable_projection": 1.0,
        "bench_depth_minimums": {"RB": 1, "WR": 1, "QB": 1, "TE": 0},
        "ir_statuses": ["IR", "IR-R", "NFI", "NFI-R", "NFI-A", "COVID", "PUP", "PUP-R", "O"],
        "notify": {"method": "console"},
    }


@pytest.fixture
def positions_config(mock_config: dict) -> dict:
    """Extract positions_config for decision engine functions."""
    return {
        "positions": mock_config["positions"],
        "bench_depth_minimums": mock_config["bench_depth_minimums"],
        "ir_statuses": mock_config["ir_statuses"],
        "min_usable_projection": mock_config["min_usable_projection"],
    }

import pandas as pd
import pytest

from decision_engine import recommend_lineup


def make_roster(players):
    return pd.DataFrame([
        {"Name": name, "Position": position, "Week 1": projection,
         "Team": "X", "Status": status, "VOR": 0.0}
        for name, position, projection, status in players
    ], columns=["Name", "Position", "Week 1", "Team", "Status", "VOR"])


def test_dual_eligible_player_fills_te_to_maximize_total():
    roster = make_roster([("Dual", "QB,TE", 20, ""), ("QB only", "QB", 19, "")])
    lineup = recommend_lineup(roster, roster, {"positions": "QB, TE"}, 1)
    assert {s.slot: s.player for s in lineup.starters} == {"QB": "QB only", "TE": "Dual"}
    assert lineup.total_projection == 39
    assert not lineup.bench


def test_single_position_players_keep_best_starters_and_flex():
    roster = make_roster([
        ("QB1", "QB", 20, ""), ("QB2", "QB", 15, ""),
        ("WR1", "WR", 19, ""), ("WR2", "WR", 18, ""),
        ("RB1", "RB", 17, ""), ("RB2", "RB", 16, ""),
        ("TE1", "TE", 10, ""),
    ])
    lineup = recommend_lineup(roster, roster, {"positions": "QB, WR, RB, TE, W/R, BN, BN"}, 1)
    assert {s.slot: s.player for s in lineup.starters} == {
        "QB": "QB1", "WR1": "WR1", "RB1": "RB1", "TE": "TE1", "W/R": "WR2",
    }
    assert lineup.total_projection == 84
    assert {s.player for s in lineup.bench} == {"QB2", "RB2"}


def test_assignment_handles_overlapping_flex_slots():
    roster = make_roster([("WR", "WR", 20, ""), ("TE", "TE", 19, ""), ("RB", "RB", 18, "")])
    lineup = recommend_lineup(roster, roster, {"positions": "W/R/T, W/R"}, 1)
    assert {s.slot: s.player for s in lineup.starters} == {"W/R/T": "TE", "W/R": "WR"}
    assert lineup.total_projection == 39


@pytest.mark.parametrize("players", [[], [("QB", "QB", 10, "")]])
def test_assignment_handles_unfillable_slots(players):
    roster = make_roster(players)
    lineup = recommend_lineup(roster, roster, {"positions": "QB, TE"}, 1)
    assert len(lineup.starters) == len(players)
    assert lineup.total_projection == (10 if players else 0)

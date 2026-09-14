import pandas as pd
import pytest

from decision_engine import AddDropRecommendation, build_action_plan, flag_bench_depth_gaps, recommend_lineup
from reasoning_engine import validate_roster


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


def test_ir_player_never_starts_without_ir_slot():
    roster = make_roster([("IR QB", "QB", 20, "IR"), ("Healthy QB", "QB", 10, "")])
    lineup = recommend_lineup(roster, roster, {"positions": "QB, BN"}, 1)
    assert [s.player for s in lineup.starters] == ["Healthy QB"]
    assert [(s.player, s.slot) for s in lineup.bench] == [("IR QB", "BN")]
    assert lineup.total_projection == 10


def test_excess_ir_players_occupy_unusable_normal_bench_capacity():
    roster = make_roster([
        ("Starter", "QB", 10, ""), ("IR1", "QB", 30, "IR"),
        ("IR2", "QB", 20, "IR-R"), ("IR3", "QB", 15, "PUP"),
    ])
    config = {"positions": "QB, BN, BN, IR", "bench_depth_minimums": {"QB": 1}}
    lineup = recommend_lineup(roster, roster, config, 1)
    assert [s.player for s in lineup.starters] == ["Starter"]
    assert sum(s.slot == "IR" for s in lineup.bench) == 1
    assert sum(s.slot == "BN" for s in lineup.bench) == 2
    assert {s.player for s in lineup.bench} == {"IR1", "IR2", "IR3"}
    depth = flag_bench_depth_gaps(roster, config)
    assert len(depth) == 1
    assert depth[0].usable_count == depth[0].starter_count == 1
    assert validate_roster(roster, config, require_bench_depth=False).legal
    smaller = {**config, "positions": "QB, BN, IR"}
    invalid = validate_roster(roster, smaller, require_bench_depth=False)
    assert not invalid.legal
    assert "Roster has 4 players but capacity is 3" in invalid.errors

    # All normal slots are occupied: another healthy player cannot use IR space.
    incoming = make_roster([("Incoming", "WR", 5, "")])
    df = pd.concat([roster, incoming], ignore_index=True)
    plan = build_action_plan(df, roster, [AddDropRecommendation(action="fa_add", add="Incoming")], config, 1, lineup)
    assert not plan.moves
    assert len(plan.simulated_roster) == 4
    assert plan.remaining_warnings


def test_empty_ir_slot_is_not_healthy_player_capacity():
    roster = make_roster([("Starter", "QB", 10, ""), ("Bench", "QB", 5, "")])
    config = {"positions": "QB, BN, IR"}
    df = pd.concat([roster, make_roster([("Incoming", "WR", 4, "")])], ignore_index=True)
    lineup = recommend_lineup(df, roster, config, 1)
    plan = build_action_plan(df, roster, [AddDropRecommendation(action="fa_add", add="Incoming")], config, 1, lineup)
    assert [r.drop for r in plan.moves if r.drop] == ["Bench"]
    assert len(plan.simulated_roster) == 2
    assert not validate_roster(df, config, require_bench_depth=False).legal


def test_capacity_validation_preserves_empty_roster_error():
    result = validate_roster(pd.DataFrame(), {"positions": "QB, BN, IR"})
    assert not result.legal
    assert result.errors == ["Roster is empty"]

import pandas as pd
import pytest

from decision_engine import AddDropRecommendation, RecommendationReport, build_action_plan, recommend_lineup
from report import build_markdown_report, build_terminal_report
from run_weekly import build_positions_config


@pytest.mark.parametrize("unlocked_bench", [False, True])
def test_mandatory_drops_respect_locks(unlocked_bench):
    rows = [
        {"Name": "Starter", "Position": "QB", "Team": "X", "Status": "", "Week 1": 20, "VOR": 20},
        {"Name": "Locked", "Position": "TE,WR", "Team": "X", "Status": "", "Week 1": 1, "VOR": -20},
    ]
    if unlocked_bench:
        rows.append({"Name": "Unlocked", "Position": "RB", "Team": "X", "Status": "", "Week 1": 2, "VOR": 2})
    roster = pd.DataFrame(rows)
    incoming = {"Name": "Incoming", "Position": "WR", "Team": "X", "Status": "", "Week 1": 10, "VOR": 10}
    df = pd.DataFrame([*rows, incoming])
    config = {"positions": "QB, BN" + (", BN" if unlocked_bench else ""), "locked_positions": ["te"]}
    positions = build_positions_config(config)
    plan = build_action_plan(
        df, roster, [AddDropRecommendation(action="fa_add", add="Incoming", add_position="WR")],
        positions, 1, recommend_lineup(df, roster, positions, 1),
    )
    assert all(move.drop != "Locked" for move in plan.moves)
    assert "Locked" in set(plan.simulated_roster["Name"])
    assert len(plan.simulated_roster) == len(roster)
    if unlocked_bench:
        assert [move.drop for move in plan.moves if move.drop] == ["Unlocked"]
        assert "Incoming" in set(plan.simulated_roster["Name"])
    else:
        assert not plan.moves
        assert plan.skipped_recs[0].add == "Incoming"
        assert "Incoming" not in set(plan.simulated_roster["Name"])
        report = RecommendationReport(action_plan=plan)
        for formatter in (build_terminal_report, build_markdown_report):
            assert "Cannot free a roster slot without touching a locked position" in formatter(report, 1, 2026, config)

import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch

from decision_engine import AddDropRecommendation, RecommendationReport, build_action_plan, recommend_lineup
from report import build_markdown_report, build_terminal_report
from run_weekly import build_positions_config
from report import save_markdown_report


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


def test_hypothetical_save_preserves_real_report(tmp_path):
    normal = Path(save_markdown_report("real history", 1, 123, 7, str(tmp_path)))
    report = RecommendationReport(hypothetical_drop="Player")
    content = build_markdown_report(report, 1, 2026, {"league_id": 123, "team_id": 7})
    hypothetical = Path(save_markdown_report(
        content, 1, 123, 7, str(tmp_path), hypothetical_drop=report.hypothetical_drop,
    ))
    assert hypothetical != normal
    assert hypothetical.name == "league_123_team_7_week_1_hypothetical.md"
    assert normal.read_text(encoding="utf-8") == "real history"
    assert hypothetical.read_text(encoding="utf-8") == content
    assert f"reports/{hypothetical.name}" in content


def test_cli_passes_hypothetical_report_to_save(tmp_path):
    import run_weekly

    config = {"league_id": 123, "team_id": 7}
    report = RecommendationReport(week=1, hypothetical_drop="Player")
    with patch("sys.argv", ["run_weekly.py", "--dry-run", "--hypothetical-drop", "Player"]), \
         patch.object(run_weekly, "setup_logging"), \
         patch.object(run_weekly, "load_config", return_value=config), \
         patch.object(run_weekly, "run_weekly", return_value=report), \
         patch.object(run_weekly, "save_markdown_report") as save:
        run_weekly.main()
    assert save.call_args.kwargs["hypothetical_drop"] == "Player"


@pytest.mark.parametrize("malformed", [{"engine_agreements": 42}, {"transactions": [42]}, 42])
@pytest.mark.parametrize("formatter", [build_terminal_report, build_markdown_report])
def test_malformed_llm_section_preserves_deterministic_report(
    malformed, formatter, mock_df, mock_config, tmp_path,
):
    from decision_engine import get_my_roster

    roster = get_my_roster(mock_df, mock_config["team_name"], 3)
    positions = build_positions_config(mock_config)
    lineup = recommend_lineup(mock_df, roster, positions, 3)
    rec = AddDropRecommendation(action="fa_add", add="Brandon Aubrey", add_position="K", vor_gain=6)
    plan = build_action_plan(mock_df, roster, [rec], positions, 3, lineup)
    report = RecommendationReport(my_roster=roster, lineup=lineup, add_drop_recs=[rec], action_plan=plan)
    # Freeze the generated timestamp so the comparison cannot cross a minute boundary.
    with patch("report.datetime") as clock:
        clock.now.return_value.strftime.return_value = "2026-09-14 12:00"
        baseline = formatter(report, 3, 2026, mock_config)
        report.llm_evaluation = malformed
        with patch("report.logger.warning") as warning:
            rendered = formatter(report, 3, 2026, mock_config)
        warning.assert_called_once()
        assert "response was malformed" in warning.call_args.args[0]
    fallback = "⚠️ LLM assessment unavailable — response was malformed"
    assert fallback in rendered
    assert rendered.replace(fallback + "\n\n", "") == baseline
    if formatter is build_markdown_report:
        saved = Path(save_markdown_report(rendered, 3, 123, 7, str(tmp_path)))
        assert saved.read_text(encoding="utf-8") == rendered

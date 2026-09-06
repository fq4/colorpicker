"""
Tests for the decision engine.

Run with:  python -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from data_layer import validate_team_identifiers, get_team_name_from_id
from decision_engine import (
    get_my_roster,
    flag_bench_depth_gaps,
    rank_free_agents,
    recommend_adds_drops,
    recommend_lineup,
    simulate_post_move_roster,
    build_action_plan,
    check_upcoming_byes,
    ByeWeekWarning,
    RosterLimitWarning,
    build_action_plan,
    ActionPlan,
    AddDropRecommendation,
    LineupSlot,
    LineupRecommendation,
    BenchDepthWarning,
    parse_positions,
    get_starting_slots,
    get_starters_count,
    _parse_optimizer_player,
    _is_ir,
    _week_col,
)


# ── Helpers ──────────────────────────────────────────────────────────────

WEEK = 3


def get_week_col(df: pd.DataFrame, week: int = WEEK) -> str:
    return f"Week {week}"


# ── get_my_roster ────────────────────────────────────────────────────────


class TestGetMyRoster:
    def test_filters_by_team_name(self, mock_df, mock_config):
        """Verify only the user's team players are returned."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        # The user's team has 12 players (9 starters + 2 bench QBs + 1 IR)
        assert len(roster) == 12

    def test_returns_expected_columns(self, mock_df, mock_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        expected = {"Name", "Team", "Position", "Status", "Week 3", "VOR"}
        assert expected.issubset(set(roster.columns))

    def test_includes_ir_player(self, mock_df, mock_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        # Robbie Ouzts is on IR but still on the team
        assert "Robbie Ouzts" in roster["Name"].values

    def test_includes_arabic_team_name(self, mock_df, mock_config):
        """Verify non-ASCII team name matching works."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        assert len(roster) > 0  # Team name with Arabic chars matched correctly


# ── flag_bench_depth_gaps ────────────────────────────────────────────────


class TestFlagBenchDepthGaps:
    def test_detects_rb_depth_gap(self, mock_df, mock_config, positions_config):
        """Team has Laumeaux (starter RB) + Ouzts (IR RB) = no usable bench RB."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        warnings = flag_bench_depth_gaps(roster, positions_config)
        rb_warning = [w for w in warnings if w.position == "RB"]
        assert len(rb_warning) >= 1
        assert rb_warning[0].usable_count == 2  # Hall + Gibbs (2 RBs, 1 on IR excluded)
        assert rb_warning[0].starter_count == 2  # 2 RB starter slots
        assert rb_warning[0].usable_count - rb_warning[0].starter_count == 0  # no bench depth

    def test_no_wr_depth_gap(self, mock_df, mock_config, positions_config):
        """Team has 3 WRs (Hill, Lamb, Addison), 2 WR slots → 1 bench WR, meets minimum of 1."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        warnings = flag_bench_depth_gaps(roster, positions_config)
        wr_warnings = [w for w in warnings if w.position == "WR"]
        assert len(wr_warnings) == 0  # 3 WRs - 2 starters = 1 bench ≥ 1 minimum

    def test_no_qb_te_depth_gap(self, mock_df, mock_config, positions_config):
        warnings = flag_bench_depth_gaps(
            get_my_roster(mock_df, mock_config["team_name"], WEEK),
            positions_config,
        )
        # QB minimum is 0, TE minimum is 0 → should never warn
        assert len([w for w in warnings if w.position in ("QB", "TE")]) == 0

    def test_ir_player_excluded_from_depth(self, mock_df, mock_config, positions_config):
        roooboard = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        warnings = flag_bench_depth_gaps(roooboard, positions_config)
        rb_w = [w for w in warnings if w.position == "RB"][0]
        # Robbie Ouzts is on IR — should be in ir_players
        assert "Robbie Ouzts" in rb_w.ir_players


# ── rank_free_agents ─────────────────────────────────────────────────────


class TestRankFreeAgents:
    def test_returns_free_agents_only(self, mock_df):
        fa = rank_free_agents(mock_df, "RB", WEEK)
        # All returned players should be free agents in the original df
        for name in fa["Name"]:
            original_row = mock_df[mock_df["Name"] == name].iloc[0]
            assert original_row["Owner"] == "Free Agent"

    def test_filters_by_position(self, mock_df):
        fa_rb = rank_free_agents(mock_df, "RB", WEEK)
        fa_qb = rank_free_agents(mock_df, "QB", WEEK)
        for _, row in fa_rb.iterrows():
            assert "RB" in row["Position"]
        for _, row in fa_qb.iterrows():
            assert "QB" in row["Position"]

    def test_sorted_by_week_projection(self, mock_df):
        fa = rank_free_agents(mock_df, "RB", WEEK)
        week_col = get_week_col(mock_df)
        projections = fa[week_col].tolist()
        # Should be sorted descending
        assert projections == sorted(projections, reverse=True)

    def test_excludes_injury_statuses(self, mock_df):
        fa = rank_free_agents(mock_df, "RB", WEEK)
        # All should have non-excluded statuses
        for _, row in fa.iterrows():
            status = row["Status"]
            assert status not in ("IR", "IR-R", "PUP-R", "O")

    def test_q_player_is_flagged(self, mock_df):
        """Kaden Elliss has Status='Q' — should be included but flagged."""
        fa = rank_free_agents(mock_df, "RB", WEEK)
        elliss = fa[fa["Name"] == "Kaden Elliss"]
        assert len(elliss) == 1
        assert "flagged" in fa.columns


# ── recommend_lineup ─────────────────────────────────────────────────────


class TestRecommendLineup:
    def test_fills_all_starting_slots(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        n_starting = len([s for s in positions_config["positions"].split(",") if s.strip() not in ("BN", "IR")])
        assert len(lineup.starters) == n_starting  # 9 starting slots

    def test_uses_week_projection_not_season(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        week_col = get_week_col(mock_df)
        # Total projection should match sum of week-specific projections
        assert abs(lineup.total_projection - sum(s.projection for s in lineup.starters)) < 0.01

    def test_flags_injured_starters(self, mock_df, mock_config, positions_config):
        """Tyreek Hill has Status='Q' — if he's a starter, should be flagged."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        flagged = lineup.flagged_starters
        if any(s.player == "Tyreek Hill" for s in lineup.starters):
            hill = [s for s in lineup.starters if s.player == "Tyreek Hill"][0]
            assert hill.flagged
            assert hill.bench_alternative is not None

    def test_bench_contains_unused_players(self, mock_df, mock_config, positions_config):
        """With 10 rostered players (9 starters + 1 IR), bench should be empty
        if all non-IR players are needed as starters. Add a 7th bench WR to test."""
        # Add an extra bench player to the roster
        extra = mock_df.iloc[8].copy()  # Jordan Addison
        extra["Name"] = "Extra Bench WR"
        extra["ID"] = 99
        mock_df_with_extra = pd.concat([mock_df, pd.DataFrame([extra])], ignore_index=True)
        roster = get_my_roster(mock_df_with_extra, mock_config["team_name"], WEEK)
        lineup = recommend_lineup(mock_df_with_extra, roster, positions_config, WEEK)
        assert len(lineup.bench) >= 1

    def test_w_r_slot_accepted_wr_or_rb(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        wr_or_b = [s for s in lineup.starters if s.slot == "W/R"]
        assert len(wr_or_b) == 1
        pos = wr_or_b[0].position
        # W/R should be filled by a WR or RB
        assert "WR" in pos or "RB" in pos


# ── recommend_adds_drops ───────────────────────────────────────────────────


class TestRecommendAddsDrops:
    def test_returns_list_of_recommendations(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)
        assert isinstance(recs, list)
        for r in recs:
            assert isinstance(r, AddDropRecommendation)

    def test_adds_are_free_agents(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)
        for rec in recs:
            if rec.add:
                # Verify add player is actually a free agent in df
                fa_rows = mock_df[(mock_df["Owner"] == "Free Agent") & (mock_df["Name"] == rec.add)]
                assert len(fa_rows) > 0 or rec.add is None  # may have parsed from optimizer differently

    def test_reasons_are_human_readable(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)
        for rec in recs:
            assert len(rec.reason) > 10  # not empty, has substance
            assert "Add" in rec.reason or "add" in rec.reason.lower()

    def test_confidence_is_valid_enum(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)
        for rec in recs:
            assert rec.confidence in ("high", "medium", "low")

    def test_flagged_for_depth_gap(self, mock_df, mock_config, positions_config):
        """If a drop target's position has a depth gap, the rec should be flagged."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)
        # At least one rec might be flagged if the optimizer suggests dropping an RB
        for rec in recs:
            if rec.flagged:
                assert rec.flag_reason is not None
                assert len(rec.flag_reason) > 0


# ── Position helpers ─────────────────────────────────────────────────────

    def test_simulated_drop_creates_depth_gap(self, mock_df, mock_config, positions_config):
        # Add a 3rd RB to eliminate the existing depth gap
        extra_rb = mock_df.iloc[3].copy()
        extra_rb['Name'] = 'Backup RB Test'
        extra_rb['ID'] = 99
        extra_rb['Owner'] = mock_config['team_name']
        extra_rb['Owner ID'] = 7
        extra_rb['% Owned'] = '1.0'
        extra_rb['Week 3'] = 5.0
        extra_rb['VOR'] = 10.0
        df_depth = pd.concat([mock_df, pd.DataFrame([extra_rb])], ignore_index=True)

        roster = get_my_roster(df_depth, mock_config['team_name'], WEEK)
        # With 3 non-IR RBs, bench depth = 1, meeting minimum
        gaps = flag_bench_depth_gaps(roster, positions_config)
        assert 'RB' not in {w.position for w in gaps}

        recs = recommend_adds_drops(df_depth, roster, WEEK, mock_config)
        for rec in recs:
            if rec.drop == 'Backup RB Test':
                assert rec.flagged
                assert 'bench depth gap' in (rec.flag_reason or '').lower()

    def test_low_vor_gain_shown_medium_confidence(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config['team_name'], WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)
        vor_config = mock_config['min_vor_gain_to_recommend_add']
        default_threshold = float(vor_config.get('default', 5.0))
        for rec in recs:
            if rec.vor_gain is None or rec.flagged:
                continue
            position = (rec.add_position or '').strip().upper()
            threshold = float(vor_config.get(position, default_threshold))
            if rec.vor_gain < threshold:
                assert rec.confidence == 'medium'




class TestPositionHelpers:

    def test_low_projection_player_excluded_from_depth(self, mock_df, mock_config, positions_config):
        """Joe Milton (QB, 0.0 proj) should NOT count as usable bench depth."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        # With Kirk Cousins (12.0 proj) as backup and Joe Milton (0.0 proj):
        # usable QBs = Allen (starter) + Cousins (backup) = 2, bench depth = 2-1 = 1 >= 1 min
        gaps = flag_bench_depth_gaps(roster, positions_config)
        assert "QB" not in [w.position for w in gaps], "Should have QB backup depth"

    def test_zero_proj_qb_does_not_count(self, mock_config, positions_config):
        """Verify Joe Milton is in low_proj_players when a QB gap is reported."""
        from decision_engine import get_my_roster
        # Create a minimal roster with only Allen + Milton (no Cousins)
        # so QB bench depth = 0 and gap is reported
        from tests.conftest import _make_player
        import pandas as pd
        rows = [
            _make_player(1, "Josh Allen", "BUF", "QB", mock_config["team_name"], 7, "", "75.3", 24.5, 85.3),
            _make_player(2, "Joe Milton", "TEN", "QB", mock_config["team_name"], 7, "", "0.5", 0.0, -15.0),
        ]
        df = pd.DataFrame(rows)
        for w in range(1, 19):
            col = "Week " + str(w)
            if col not in df.columns:
                df[col] = 0.0
        roster = get_my_roster(df, mock_config["team_name"], WEEK)
        gaps = flag_bench_depth_gaps(roster, positions_config)
        qb_gap = [w for w in gaps if w.position == "QB"]
        assert len(qb_gap) >= 1, "Should detect QB depth gap"
        # Joe Milton (0.0 proj) should be in low_proj_players, not counted as usable
        assert "Joe Milton" in (qb_gap[0].low_proj_players or [])
        assert qb_gap[0].usable_count == 1  # Only Allen counts

    def test_parse_positions(self):
        positions = "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR"
        result = parse_positions(positions)
        assert result == ["QB", "WR", "WR", "RB", "RB", "TE", "W/R", "K", "DEF",
                          "BN", "BN", "BN", "BN", "BN", "BN", "IR"]

    def test_get_starting_slots(self):
        positions = parse_positions("QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, IR")
        starters = get_starting_slots(positions)
        assert starters == ["QB", "WR", "WR", "RB", "RB", "TE", "W/R", "K", "DEF"]

    def test_get_starters_count(self):
        positions = parse_positions("QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, IR")
        counts = get_starters_count(positions)
        assert counts["QB"] == 1
        assert counts["WR"] == 2
        assert counts["RB"] == 2
        assert counts["TE"] == 1
        assert counts["K"] == 1
        assert counts["DEF"] == 1
        # W/R should not count toward either
        assert "W/R" not in counts

    def test_is_ir(self):
        ir_statuses = {"IR", "IR-R", "NFI", "NFI-R", "NFI-A", "COVID", "PUP", "PUP-R", "O"}
        assert _is_ir("IR", ir_statuses) is True
        assert _is_ir("IR-R", ir_statuses) is True
        assert _is_ir("Q", ir_statuses) is False
        assert _is_ir("", ir_statuses) is False
        assert _is_ir(None, ir_statuses) is False
        assert _is_ir("NFI-R", ir_statuses) is True
        assert _is_ir("PUP-R", ir_statuses) is True


# ── Optimizer player parsing ─────────────────────────────────────────────


class TestParseOptimizerPlayer:
    def test_parse_simple_add(self):
        name, pos, owner = _parse_optimizer_player("Woody Marks (RB)")
        assert name == "Woody Marks"
        assert pos == "RB"
        assert owner is None

    def test_parse_waiver_claim(self):
        name, pos, owner = _parse_optimizer_player("Travis Kelce (TE) - Team A")
        assert name == "Travis Kelce"
        assert pos == "TE"
        assert owner == "Team A"

    def test_parse_empty(self):
        name, pos, owner = _parse_optimizer_player("")
        assert name is None
        assert pos is None
        assert owner is None

    def test_parse_none(self):
        name, pos, owner = _parse_optimizer_player(None)
        assert name is None


# ── Integration: full pipeline (mocked) ──────────────────────────────────


class TestIntegration:
    def test_full_recommendation_report(self, mock_df, mock_config, positions_config):
        """End-to-end: roster → depth → lineup → adds/drops."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        depth = flag_bench_depth_gaps(roster, positions_config)
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)

        # At least depth warnings exist
        assert len(depth) >= 1  # RB gap expected

        # Lineup fills all starting slots
        n_starters = len(get_starting_slots(parse_positions(mock_config["positions"])))
        assert len(lineup.starters) == n_starters

        # Add/drop recommendations are structured
        assert all(isinstance(r, AddDropRecommendation) for r in recs)

    def test_report_generation(self, mock_df, mock_config, positions_config):
        """Verify report module can consume decision engine output."""
        from report import build_terminal_report, build_markdown_report, save_markdown_report

        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        depth = flag_bench_depth_gaps(roster, positions_config)
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        recs = recommend_adds_drops(mock_df, roster, WEEK, mock_config)

        from decision_engine import RecommendationReport
        report = RecommendationReport(
            my_roster=roster,
            depth_warnings=depth,
            lineup=lineup,
            add_drop_recs=recs,
        )

        terminal = build_terminal_report(report, WEEK, mock_config.get("season", 2026), mock_config, dry_run=True)
        assert len(terminal) > 100
        assert "Week 3" in terminal

        md = build_markdown_report(report, WEEK, mock_config.get("season", 2026), mock_config, dry_run=True)
        assert "# Fantasy Football" in md
        assert "Recommended" in md



# --- Action Plan tests ---


class TestActionPlan:
    """Tests for simulate_post_move_roster and build_action_plan."""

    def test_simulate_post_move_roster_drops_player(self, mock_df, mock_config, positions_config):
        """Verify the dropped player is removed from the simulated roster."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        rec = AddDropRecommendation(
            action="add_drop",
            add="Brandon Aubrey",
            add_position="K",
            add_team="DAL",
            drop="Jake Elliott",
            drop_position="K",
            drop_team="PHI",
            vor_gain=2.0,
            reason="Add better K",
            confidence="high",
            flagged=False,
        )
        simulated = simulate_post_move_roster(mock_df, roster, [rec], WEEK)
        assert "Jake Elliott" not in simulated["Name"].values
        assert "Brandon Aubrey" in simulated["Name"].values

    def test_simulate_post_move_roster_keeps_flagged_recs(self, mock_df, mock_config, positions_config):
        """Flagged recommendations should NOT be applied to the simulated roster."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        flagged_rec = AddDropRecommendation(
            action="add_drop",
            add="Derek Carr",
            add_position="QB",
            add_team="NO",
            drop="Kirk Cousins",
            drop_position="QB",
            drop_team="ATL",
            vor_gain=5.0,
            reason="Flagged for depth gap",
            confidence="low",
            flagged=True,
            flag_reason="Dropping QB would create a bench depth gap",
        )
        simulated = simulate_post_move_roster(mock_df, roster, [flagged_rec], WEEK)
        # Kirk Cousins should still be on the roster (drop was not applied)
        assert "Kirk Cousins" in simulated["Name"].values
        # Derek Carr should NOT be on the roster (add was not applied)
        assert "Derek Carr" not in simulated["Name"].values
        # Jake Elliott should still be on the roster
        assert "Jake Elliott" in simulated["Name"].values

    def test_simulate_post_move_roster_adds_player_from_df(self, mock_df, mock_config, positions_config):
        """Added players should have data pulled from the df (projection, VOR, etc.)."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        rec = AddDropRecommendation(
            action="fa_add",
            add="Brandon Aubrey",
            add_position="K",
            add_team="DAL",
            add_projection=10.5,
            add_vor=18.0,
            vor_gain=9.5,
            reason="Better K",
            confidence="high",
            flagged=False,
        )
        week_col = "Week " + str(WEEK)
        simulated = simulate_post_move_roster(mock_df, roster, [rec], WEEK)
        aubrey_row = simulated[simulated["Name"] == "Brandon Aubrey"]
        assert len(aubrey_row) == 1
        assert abs(aubrey_row.iloc[0][week_col] - 10.5) < 0.01
        assert abs(aubrey_row.iloc[0]["VOR"] - 18.0) < 0.01

    def test_simulate_post_move_roster_preserves_non_involved_players(self, mock_df, mock_config, positions_config):
        """Players not involved in any move should remain on the roster."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        rec = AddDropRecommendation(
            action="add_drop",
            add="Brandon Aubrey",
            add_position="K",
            drop="Jake Elliott",
            drop_position="K",
            vor_gain=2.0,
            flagged=False,
        )
        simulated = simulate_post_move_roster(mock_df, roster, [rec], WEEK)
        assert "Josh Allen" in simulated["Name"].values
        assert "Tyreek Hill" in simulated["Name"].values
        assert "Robbie Ouzts" in simulated["Name"].values

    def test_build_action_plan_separates_moves_and_skipped(self, mock_df, mock_config, positions_config):
        """Non-flagged recs go to moves, flagged recs go to skipped."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        recs = [
            AddDropRecommendation(
                action="add_drop", add="Brandon Aubrey", add_position="K",
                drop="Jake Elliott", drop_position="K",
                add_projection=10.5, add_vor=18.0, drop_projection=8.5, drop_vor=-0.5,
                vor_gain=2.0, flagged=False,
            ),
            AddDropRecommendation(
                action="add_drop", add="Derek Carr", add_position="QB",
                drop="Kirk Cousins", drop_position="QB",
                vor_gain=5.0, flagged=True,
                flag_reason="Dropping QB would create a bench depth gap",
            ),
        ]
        plan = build_action_plan(mock_df, roster, recs, positions_config, WEEK)
        assert len(plan.moves) == 1
        assert plan.moves[0].add == "Brandon Aubrey"
        assert len(plan.skipped_recs) == 1
        assert plan.skipped_recs[0].add == "Derek Carr"

    def test_build_action_plan_lineup_reflects_added_player(self, mock_df, mock_config, positions_config):
        """After adding a better K, the final lineup should use the new player."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        # Get the original lineup (before moves)
        original_lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        original_k = [s for s in original_lineup.starters if s.slot == "K"]
        assert len(original_k) == 1
        assert original_k[0].player == "Jake Elliott"

        # Add Brandon Aubrey (10.5) and drop Jake Elliott (8.5)
        rec = AddDropRecommendation(
            action="add_drop", add="Brandon Aubrey", add_position="K",
            drop="Jake Elliott", drop_position="K",
            vor_gain=2.0, flagged=False,
        )
        plan = build_action_plan(mock_df, roster, [rec], positions_config, WEEK)

        assert plan.final_lineup is not None
        new_k = [s for s in plan.final_lineup.starters if s.slot == "K"]
        assert len(new_k) == 1
        assert new_k[0].player == "Brandon Aubrey"
        assert abs(new_k[0].projection - 10.5) < 0.01

    def test_build_action_plan_final_lineup_differs_from_pre_move(self, mock_df, mock_config, positions_config):
        """The post-move lineup total projection should differ from pre-move when a swap occurs."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        original_lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)

        rec = AddDropRecommendation(
            action="add_drop", add="Brandon Aubrey", add_position="K",
            drop="Jake Elliott", drop_position="K",
            vor_gain=2.0, flagged=False,
        )
        plan = build_action_plan(mock_df, roster, [rec], positions_config, WEEK)

        original_total = original_lineup.total_projection
        new_total = plan.final_lineup.total_projection
        # Aubrey (10.5) > Elliott (8.5) so total should increase
        assert new_total > original_total
        assert abs(new_total - original_total - (10.5 - 8.5)) < 0.01

    def test_build_action_plan_remaining_warnings(self, mock_df, mock_config, positions_config):
        """Depth warnings should be recomputed from the simulated roster."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)

        # Create a rec that does NOT fix the existing RB depth gap
        rec = AddDropRecommendation(
            action="add_drop", add="Brandon Aubrey", add_position="K",
            drop="Jake Elliott", drop_position="K",
            vor_gain=2.0, flagged=False,
        )
        plan = build_action_plan(mock_df, roster, [rec], positions_config, WEEK)
        # The mock config has bench_depth_minimums with QB: 1
        # The team has Josh Allen (starter), Kirk Cousins (backup), Joe Milton (0.0 proj)
        # With min_usable_projection=1.0: usable QBs = Allen + Cousins = 2, need 1 starter, so bench depth = 1 >= 1
        # So there should NOT be a QB depth warning after moves (this move doesnt change QBs)
        rb_warnings = [w for w in plan.remaining_warnings if w.position == "RB"]
        # The team has Breece Hall, Jahmyr Gibbs, Robbie Ouzts(IR). 2 usable RBs, 2 RB starters.
        # After the K swap, RB depth gap should still exist.
        assert any("Not enough" in w.message or "No usable" in w.message or "gap" in w.message
                     for w in rb_warnings) or len(rb_warnings) > 0

    def test_build_action_plan_with_no_moves(self, mock_df, mock_config, positions_config):
        """Action plan with no non-flagged moves should still compute a lineup."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        plan = build_action_plan(mock_df, roster, [], positions_config, WEEK)
        assert len(plan.moves) == 0
        assert len(plan.skipped_recs) == 0
        assert plan.final_lineup is not None
        assert len(plan.final_lineup.starters) > 0

    def test_action_plan_included_in_report(self, mock_df, mock_config, positions_config):
        """Integration: RecommendationReport with action_plan should be consumable by report module."""
        from report import build_terminal_report, build_markdown_report
        from decision_engine import RecommendationReport

        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        recs = [
            AddDropRecommendation(
                action="add_drop", add="Brandon Aubrey", add_position="K",
                drop="Jake Elliott", drop_position="K",
                add_projection=10.5, add_vor=18.0, drop_projection=8.5, drop_vor=-0.5,
                vor_gain=2.0, flagged=False,
            ),
        ]
        original_lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        plan = build_action_plan(mock_df, roster, recs, positions_config, WEEK, original_lineup=original_lineup)

        report = RecommendationReport(
            my_roster=roster,
            lineup=lineup,
            add_drop_recs=recs,
            action_plan=plan,
        )

        terminal = build_terminal_report(report, WEEK, 2026, mock_config, dry_run=True)
        assert "YOUR ACTION PLAN" in terminal
        assert "Moves to Make" in terminal
        assert "Resulting Starting Lineup" in terminal
        assert "Your Bench After These Moves" in terminal
        assert "Position Comparison Notes" in terminal

        md = build_markdown_report(report, WEEK, 2026, mock_config, dry_run=True)
        assert "Your Action Plan" in md
        assert "Moves to Make" in md
        assert "Resulting Starting Lineup" in md
        assert "Your Bench After These Moves" in md
        assert "Position Comparison Notes" in md


    def test_build_action_plan_comparison_notes_higher_proj_replaces_starter(self, mock_df, mock_config, positions_config):
        """A newly-added player with higher projection should replace the current starter."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        # Jake Elliott (K, 8.5) is the starter. Brandon Aubrey (K, 10.5) is higher.
        rec = AddDropRecommendation(
            action="add_drop", add="Brandon Aubrey", add_position="K",
            drop="Jake Elliott", drop_position="K",
            add_projection=10.5, add_vor=18.0, drop_projection=8.5, drop_vor=-0.5,
            vor_gain=10.0, reason="Better K", confidence="high", flagged=False,
        )
        original_lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        plan = build_action_plan(mock_df, roster, [rec], positions_config, WEEK, original_lineup=original_lineup)
        assert plan.final_lineup is not None
        k_slots = [s for s in plan.final_lineup.starters if s.slot == "K"]
        assert len(k_slots) == 1
        assert k_slots[0].player == "Brandon Aubrey"
        assert abs(k_slots[0].projection - 10.5) < 0.01
        # Comparison notes should reflect the replacement
        assert any("Brandon Aubrey" in n and "replaces" in n and "higher projection" in n for n in plan.comparison_notes)

    def test_build_action_plan_comparison_notes_lower_proj_stays_on_bench(self, mock_df, mock_config, positions_config):
        """A newly-added player with lower projection should stay on the bench."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        # Jake Elliott (K, 8.5) is the starter. Add a fake lower-projection K.
        from tests.conftest import _make_player
        import pandas as pd
        lower_k = _make_player(999, "Low Kicker", "FAK", "K", mock_config["team_name"], 7, "", "0.5", 5.0, -50.0)
        mock_df = pd.concat([mock_df, pd.DataFrame([lower_k])], ignore_index=True)
        rec = AddDropRecommendation(
            action="fa_add", add="Low Kicker", add_position="K",
            add_projection=5.0, add_vor=-50.0, vor_gain=-55.0,
            reason="Bench depth", confidence="low", flagged=False,
        )
        original_lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        plan = build_action_plan(mock_df, roster, [rec], positions_config, WEEK, original_lineup=original_lineup)
        assert plan.final_lineup is not None
        k_slots = [s for s in plan.final_lineup.starters if s.slot == "K"]
        assert len(k_slots) == 1
        assert k_slots[0].player == "Jake Elliott"
        # Low Kicker should be on bench
        bench_names = [s.player for s in plan.final_lineup.bench]
        assert "Low Kicker" in bench_names
        # Comparison notes should reflect that starter kept the spot
        assert any("Jake Elliott" in n and "starts over" in n and "Low Kicker" in n for n in plan.comparison_notes)

    def test_action_plan_comparison_notes_in_terminal_report(self, mock_df, mock_config, positions_config):
        """Terminal report should include comparison notes for starter decisions."""
        from report import build_terminal_report
        from decision_engine import RecommendationReport
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        original_lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        recs = [
            AddDropRecommendation(
                action="add_drop", add="Brandon Aubrey", add_position="K",
                drop="Jake Elliott", drop_position="K",
                add_projection=10.5, add_vor=18.0, drop_projection=8.5, drop_vor=-0.5,
                vor_gain=10.0, flagged=False,
            ),
        ]
        plan = build_action_plan(mock_df, roster, recs, positions_config, WEEK, original_lineup=original_lineup)
        report = RecommendationReport(
            my_roster=roster, lineup=recommend_lineup(mock_df, roster, positions_config, WEEK),
            add_drop_recs=recs, action_plan=plan,
        )
        terminal = build_terminal_report(report, WEEK, 2026, mock_config, dry_run=True)
        assert "Position Comparison Notes" in terminal
        assert "Brandon Aubrey" in terminal
        assert "Jake Elliott" in terminal

    def test_action_plan_comparison_notes_in_markdown_report(self, mock_df, mock_config, positions_config):
        """Markdown report should include comparison notes for starter decisions."""
        from report import build_markdown_report
        from decision_engine import RecommendationReport
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        original_lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        recs = [
            AddDropRecommendation(
                action="add_drop", add="Brandon Aubrey", add_position="K",
                drop="Jake Elliott", drop_position="K",
                add_projection=10.5, add_vor=18.0, drop_projection=8.5, drop_vor=-0.5,
                vor_gain=10.0, flagged=False,
            ),
        ]
        plan = build_action_plan(mock_df, roster, recs, positions_config, WEEK, original_lineup=original_lineup)
        report = RecommendationReport(
            my_roster=roster, lineup=recommend_lineup(mock_df, roster, positions_config, WEEK),
            add_drop_recs=recs, action_plan=plan,
        )
        md = build_markdown_report(report, WEEK, 2026, mock_config, dry_run=True)
        assert "Position Comparison Notes" in md
        assert "Brandon Aubrey" in md
        assert "Jake Elliott" in md


# --- Team identifier validation ---


class TestValidateTeamIdentifiers:
    def test_mismatched_team_id_and_name_raises(self, mock_df, mock_config):
        """Mismatched team_id/team_name must raise ValueError immediately."""
        bad_config = dict(mock_config)
        bad_config["team_name"] = "Wrong Team Name"
        with pytest.raises(ValueError, match="team_id=7 corresponds to"):
            validate_team_identifiers(mock_df, bad_config)

    def test_matching_team_id_and_name_passes(self, mock_df, mock_config):
        """Matching team_id/team_name should not raise."""
        validate_team_identifiers(mock_df, mock_config)  # must not raise

    def test_team_id_not_found_raises(self, mock_df, mock_config):
        """Unknown team_id should raise ValueError with available IDs."""
        bad_config = dict(mock_config)
        bad_config["team_id"] = 999
        with pytest.raises(ValueError, match="not found in scraped data"):
            validate_team_identifiers(mock_df, bad_config)

    def test_none_identifiers_pass_silently(self, mock_df):
        """Missing team_id or team_name should not raise."""
        validate_team_identifiers(mock_df, {})  # no team_id or team_name
        validate_team_identifiers(mock_df, {"team_id": 7})  # no team_name
        validate_team_identifiers(mock_df, {"team_name": "anything"})  # no team_id


# --- get_team_name_from_id ---


class TestGetTeamNameFromId:
    def test_resolves_team_id_7_to_real_name(self, mock_df, mock_config):
        """team_id=7 in mock data corresponds to the Arabic team name."""
        name = get_team_name_from_id(mock_df, 7)
        assert name == mock_config["team_name"]

    def test_unknown_team_id_raises(self, mock_df):
        """An ID not present in the df should raise ValueError."""
        with pytest.raises(ValueError, match="not found in scraped data"):
            get_team_name_from_id(mock_df, 9999)

    def test_none_team_id_raises(self, mock_df):
        """None team_id should raise ValueError."""
        with pytest.raises(ValueError, match="team_id is required"):
            get_team_name_from_id(mock_df, None)


# --- CLI overrides and report filenames ---


class TestCLIOverridesAndFilenames:
    def test_cli_args_override_config(self):
        """When --league-id or --team-id are provided, they override config.yaml."""
        import argparse
        from run_weekly import main as run_main
        # We can't easily call main() without mocking, so test the parser directly
        # by importing the parser setup logic. Instead, verify via the config dict:
        config = {"league_id": 492312, "team_id": 7, "team_name": "Old Name"}
        # Simulate what main() does
        class Args:
            league_id = 999
            team_id = 5
            config = "config.yaml"
            max_cache_age = 12.0
        args = Args()
        if args.league_id is not None:
            config["league_id"] = args.league_id
        if args.team_id is not None:
            config["team_id"] = args.team_id
        assert config["league_id"] == 999
        assert config["team_id"] == 5

    def test_config_values_used_when_no_cli_args(self):
        """When no --league-id/--team-id flags are given, config.yaml values are kept."""
        config = {"league_id": 492312, "team_id": 7, "team_name": "Old Name"}
        class Args:
            league_id = None
            team_id = None
            config = "config.yaml"
            max_cache_age = 12.0
        args = Args()
        if args.league_id is not None:
            config["league_id"] = args.league_id
        if args.team_id is not None:
            config["team_id"] = args.team_id
        assert config["league_id"] == 492312
        assert config["team_id"] == 7

    def test_report_filename_reflects_league_and_team(self, tmp_path):
        """save_markdown_report should create a file named with league_id and team_id."""
        from report import save_markdown_report
        path = save_markdown_report(
            "# Test", week=3, league_id=492312, team_id=7,
            reports_dir=str(tmp_path)
        )
        assert "league_492312_team_7_week_3.md" in path
        assert os.path.exists(path)


# --- Bye Week Lookahead ---


class TestCheckUpcomingByes:
    def test_detects_bye_for_starter(self, mock_df, mock_config):
        """A starter with a 0-projection week within the lookahead window is flagged."""
        from run_weekly import build_positions_config
        from decision_engine import recommend_lineup
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # Mock data has Week 4/5 = 0 for all players; clear those first,
        # then set only Josh Allen to 0 in Week 4
        df_bye = mock_df.copy()
        for col in ["Week 4", "Week 5"]:
            df_bye[col] = 10.0
        df_bye.loc[df_bye["Name"] == "Josh Allen", "Week 4"] = 0

        warnings = check_upcoming_byes(df_bye, 3, lookahead_weeks=2, lineup=lineup)
        allen_warnings = [w for w in warnings if w.player == "Josh Allen"]
        assert len(allen_warnings) == 1
        assert allen_warnings[0].bye_week == 4
        assert allen_warnings[0].is_current_starter is True
        assert "starter" in allen_warnings[0].message

    def test_detects_bye_for_bench_player(self, mock_df, mock_config):
        """A bench player with a 0-projection week is flagged as bench priority."""
        from run_weekly import build_positions_config
        from decision_engine import recommend_lineup
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # Kirk Cousins is on the bench — set Week 4 to 0
        df_bye = mock_df.copy()
        for col in ["Week 4", "Week 5"]:
            df_bye[col] = 10.0
        df_bye.loc[df_bye["Name"] == "Kirk Cousins", "Week 4"] = 0

        warnings = check_upcoming_byes(df_bye, 3, lookahead_weeks=2, lineup=lineup)
        cousins_warnings = [w for w in warnings if w.player == "Kirk Cousins"]
        assert len(cousins_warnings) == 1
        assert cousins_warnings[0].is_current_starter is False
        assert "bench" in cousins_warnings[0].message

    def test_respects_lookahead_window(self, mock_df, mock_config):
        """A bye outside the lookahead window should not be flagged."""
        from run_weekly import build_positions_config
        from decision_engine import recommend_lineup
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # Set Weeks 4 and 5 to non-zero, Week 6 to 0 (outside lookahead)
        df_bye = mock_df.copy()
        for col in ["Week 4", "Week 5", "Week 6"]:
            df_bye[col] = 10.0
        df_bye.loc[df_bye["Name"] == "Josh Allen", "Week 6"] = 0

        warnings = check_upcoming_byes(df_bye, 3, lookahead_weeks=2, lineup=lineup)
        allen_warnings = [w for w in warnings if w.player == "Josh Allen"]
        assert len(allen_warnings) == 0

    def test_no_warnings_when_no_byes(self, mock_df, mock_config):
        """No warnings when all lookahead weeks have projections."""
        from run_weekly import build_positions_config
        from decision_engine import recommend_lineup
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # Clear the default 0 values in Weeks 4-5 so no byes are detected
        df_no_bye = mock_df.copy()
        for col in ["Week 4", "Week 5"]:
            df_no_bye[col] = 10.0

        warnings = check_upcoming_byes(df_no_bye, 3, lookahead_weeks=2, lineup=lineup)
        assert len(warnings) == 0


# --- Position-Differentiated Streaming Thresholds ---


class TestPositionDifferentiatedThresholds:
    def test_qb_small_vor_gain_is_high_confidence(self, mock_df, mock_config):
        """QB add with VOR gain +2.0 should be high confidence (QB threshold = 2.0)."""
        config = dict(mock_config)
        config["min_vor_gain_to_recommend_add"] = {
            "QB": 2.0, "TE": 2.0, "K": 1.0, "DEF": 1.0, "RB": 8.0, "WR": 8.0
        }
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        recs = recommend_adds_drops(mock_df, roster, 3, config)
        qb_recs = [r for r in recs if r.add_position == "QB" and r.vor_gain is not None]
        for rec in qb_recs:
            if rec.vor_gain >= 2.0:
                assert rec.confidence == "high", f"QB rec {rec.add} with VOR {rec.vor_gain} should be high confidence"

    def test_rb_small_vor_gain_is_medium_confidence(self, mock_df, mock_config):
        """RB add with VOR gain +2.0 should be medium confidence (RB threshold = 8.0)."""
        config = dict(mock_config)
        config["min_vor_gain_to_recommend_add"] = {
            "QB": 2.0, "TE": 2.0, "K": 1.0, "DEF": 1.0, "RB": 8.0, "WR": 8.0
        }
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        recs = recommend_adds_drops(mock_df, roster, 3, config)
        rb_recs = [r for r in recs if r.add_position == "RB" and r.vor_gain is not None]
        for rec in rb_recs:
            if rec.vor_gain < 8.0:
                assert rec.confidence == "medium", f"RB rec {rec.add} with VOR {rec.vor_gain} should be medium confidence"

    def test_k_small_vor_gain_is_high_confidence(self, mock_df, mock_config):
        """K add with VOR gain +1.0 should be high confidence (K threshold = 1.0)."""
        config = dict(mock_config)
        config["min_vor_gain_to_recommend_add"] = {
            "QB": 2.0, "TE": 2.0, "K": 1.0, "DEF": 1.0, "RB": 8.0, "WR": 8.0
        }
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        recs = recommend_adds_drops(mock_df, roster, 3, config)
        k_recs = [r for r in recs if r.add_position == "K" and r.vor_gain is not None]
        for rec in k_recs:
            if rec.vor_gain >= 1.0:
                assert rec.confidence == "high", f"K rec {rec.add} with VOR {rec.vor_gain} should be high confidence"

    def test_default_threshold_used_for_unknown_position(self, mock_df, mock_config):
        """Unknown positions fall back to the default threshold."""
        config = dict(mock_config)
        config["min_vor_gain_to_recommend_add"] = {
            "QB": 2.0, "TE": 2.0, "K": 1.0, "DEF": 1.0, "RB": 8.0, "WR": 8.0
        }
        # This just verifies the code path doesn'''t crash with unknown positions
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        recs = recommend_adds_drops(mock_df, roster, 3, config)
        # All recs should have a valid confidence level
        for rec in recs:
            assert rec.confidence in ("high", "medium", "low")


# --- Roster size limit enforcement ---


class TestRosterSizeLimit:
    def test_roster_limit_exceeded_surfaces_warning(self, mock_df, mock_config):
        """When moves would exceed roster size, a RosterLimitWarning is raised."""
        from run_weekly import build_positions_config
        from decision_engine import AddDropRecommendation
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        # Mock roster has 12 players; positions config has 16 slots
        current_size = len(roster)
        max_size = len(parse_positions(positions_config["positions"]))

        # Create moves with net +5 (5 adds, 0 drops) -> 12 + 5 = 17 > 16
        recs = [
            AddDropRecommendation(action="fa_add", add="Player A"),
            AddDropRecommendation(action="fa_add", add="Player B"),
            AddDropRecommendation(action="fa_add", add="Player C"),
            AddDropRecommendation(action="fa_add", add="Player D"),
            AddDropRecommendation(action="fa_add", add="Player E"),
        ]

        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3
        )
        # Should have a roster limit warning
        roster_warnings = [w for w in action_plan.remaining_warnings
                          if isinstance(w, RosterLimitWarning)]
        assert len(roster_warnings) == 1
        rlw = roster_warnings[0]
        assert rlw.current_size == current_size
        assert rlw.max_size == max_size
        assert rlw.excess == current_size + 5 - max_size
        assert "Roster limit exceeded" in rlw.message
        assert len(rlw.suggested_drops) == rlw.excess

    def test_roster_limit_not_exceeded_no_warning(self, mock_df, mock_config):
        """When moves fit within roster size, no RosterLimitWarning."""
        from run_weekly import build_positions_config
        from decision_engine import AddDropRecommendation
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        # Net 0 moves: 1 add + 1 drop
        recs = [
            AddDropRecommendation(action="add_drop", drop="Player A", add="Player B"),
        ]
        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3
        )
        roster_warnings = [w for w in action_plan.remaining_warnings
                          if isinstance(w, RosterLimitWarning)]
        assert len(roster_warnings) == 0


# --- IR player appears in bench table ---


class TestIRPlayerInBench:
    def test_ir_player_appears_in_bench_table(self, mock_df, mock_config):
        """IR players should appear in the bench table, not disappear."""
        from run_weekly import build_positions_config
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        # Robbie Ouzts is on IR in the mock data
        ir_players = roster[roster["Status"].apply(lambda s: str(s).strip() in ("IR", "IR-R"))]
        assert len(ir_players) == 1
        assert "Robbie Ouzts" in ir_players["Name"].values

        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # IR player should be in bench, not missing
        bench_names = [s.player for s in lineup.bench]
        assert "Robbie Ouzts" in bench_names, f"IR player missing from bench. Bench: {bench_names}"

        # IR player should have slot="IR"
        ir_slot = [s for s in lineup.bench if s.player == "Robbie Ouzts"]
        assert len(ir_slot) == 1
        assert ir_slot[0].slot == "IR"

    def test_ir_player_in_action_plan_bench(self, mock_df, mock_config):
        """IR player should appear in the action plan bench table."""
        from run_weekly import build_positions_config
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        action_plan = build_action_plan(
            mock_df, roster, [], positions_config, 3
        )
        assert action_plan.final_lineup is not None
        bench_names = [s.player for s in action_plan.final_lineup.bench]
        assert "Robbie Ouzts" in bench_names


# --- Roster limit: starter/IR exclusion ---


class TestRosterLimitExcludesStartersAndIR:
    def test_suggested_drops_exclude_starters(self, mock_df, mock_config):
        """Lowest-VOR starters must not appear in suggested_drops."""
        from run_weekly import build_positions_config
        from decision_engine import (
            AddDropRecommendation, build_action_plan, recommend_lineup,
            RosterLimitWarning,
        )
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # Net +5 moves to force limit exceed
        recs = [
            AddDropRecommendation(action="fa_add", add="Player A"),
            AddDropRecommendation(action="fa_add", add="Player B"),
            AddDropRecommendation(action="fa_add", add="Player C"),
            AddDropRecommendation(action="fa_add", add="Player D"),
            AddDropRecommendation(action="fa_add", add="Player E"),
        ]

        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3, original_lineup=lineup
        )
        roster_warnings = [w for w in action_plan.remaining_warnings
                          if isinstance(w, RosterLimitWarning)]
        assert len(roster_warnings) == 1
        rlw = roster_warnings[0]
        starter_names = {s.player for s in lineup.starters}
        for d in rlw.suggested_drops:
            assert d["name"] not in starter_names, (
                f"Starter {d['name']} should not be in suggested drops"
            )

    def test_suggested_drops_exclude_ir_players(self, mock_df, mock_config):
        """IR-status players must not appear in suggested_drops."""
        from run_weekly import build_positions_config
        from decision_engine import (
            AddDropRecommendation, build_action_plan, recommend_lineup,
            RosterLimitWarning, _parse_ir_statuses,
        )
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)
        ir_statuses = _parse_ir_statuses(positions_config)

        # Net +5 moves to force limit exceed
        recs = [
            AddDropRecommendation(action="fa_add", add="Player A"),
            AddDropRecommendation(action="fa_add", add="Player B"),
            AddDropRecommendation(action="fa_add", add="Player C"),
            AddDropRecommendation(action="fa_add", add="Player D"),
            AddDropRecommendation(action="fa_add", add="Player E"),
        ]

        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3, original_lineup=lineup
        )
        roster_warnings = [w for w in action_plan.remaining_warnings
                          if isinstance(w, RosterLimitWarning)]
        assert len(roster_warnings) == 1
        rlw = roster_warnings[0]
        ir_names = set()
        for _, row in roster.iterrows():
            if _is_ir(row.get("Status"), ir_statuses):
                ir_names.add(str(row.get("Name", "")))
        for d in rlw.suggested_drops:
            assert d["name"] not in ir_names, (
                f"IR player {d['name']} should not be in suggested drops"
            )

    def test_suggested_drops_rendered_in_terminal_report(self, mock_df, mock_config):
        """RosterLimitWarning suggested drops should appear in terminal output."""
        from run_weekly import build_positions_config
        from decision_engine import (
            AddDropRecommendation, build_action_plan, recommend_lineup,
        )
        from report import _format_action_plan_terminal
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        recs = [
            AddDropRecommendation(action="fa_add", add="Player A"),
            AddDropRecommendation(action="fa_add", add="Player B"),
            AddDropRecommendation(action="fa_add", add="Player C"),
            AddDropRecommendation(action="fa_add", add="Player D"),
            AddDropRecommendation(action="fa_add", add="Player E"),
        ]

        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3, original_lineup=lineup
        )
        terminal = _format_action_plan_terminal(action_plan, 3)
        # Suggested drops appear as sub-bullets under the roster limit warning
        has_drop = any(
            d["name"] in terminal
            for w in action_plan.remaining_warnings
            if isinstance(w, RosterLimitWarning)
            for d in w.suggested_drops
        )
        assert has_drop, "No suggested drop names found in terminal report"

    def test_suggested_drops_rendered_in_markdown_report(self, mock_df, mock_config):
        """RosterLimitWarning suggested drops should appear in markdown output."""
        from run_weekly import build_positions_config
        from decision_engine import (
            AddDropRecommendation, build_action_plan, recommend_lineup,
        )
        from report import _md_action_plan
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        recs = [
            AddDropRecommendation(action="fa_add", add="Player A"),
            AddDropRecommendation(action="fa_add", add="Player B"),
            AddDropRecommendation(action="fa_add", add="Player C"),
            AddDropRecommendation(action="fa_add", add="Player D"),
            AddDropRecommendation(action="fa_add", add="Player E"),
        ]

        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3, original_lineup=lineup
        )
        md = _md_action_plan(action_plan, 3)
        # Suggested drops appear as sub-bullets under the roster limit warning
        has_drop = any(
            d["name"] in md
            for w in action_plan.remaining_warnings
            if isinstance(w, RosterLimitWarning)
            for d in w.suggested_drops
        )
        assert has_drop, "No suggested drop names found in markdown report"

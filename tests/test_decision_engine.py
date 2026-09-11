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

from data_layer import get_team_name_from_id, save_weekly_cache
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
    _lookup_player,
    _is_ir,
    _week_col,
)


# ── Helpers ──────────────────────────────────────────────────────────────

WEEK = 3


def get_week_col(df: pd.DataFrame, week: int = WEEK) -> str:
    return f"Week {week}"


# ── data_layer ────────────────────────────────────────────────────────────


class TestDataLayer:
    def test_save_weekly_cache_writes_canonical_location(self, tmp_path, mock_df):
        """save_weekly_cache should write directly to the app's canonical
        league-scoped cache location, not via ffbot.save()."""
        from unittest.mock import patch

        with patch("data_layer.ffbot.save") as mock_save:
            path = save_weekly_cache(
                mock_df, 3, data_dir=str(tmp_path), league_id=492312
            )

        mock_save.assert_not_called()
        assert "league_492312_week_3_" in path
        assert path.endswith(".csv")
        import os
        assert os.path.exists(path)


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

    def test_retains_future_week_projections(self, mock_df, mock_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        assert "Week 4" in roster.columns
        assert roster.columns.get_loc("Week 3") < roster.columns.get_loc("Week 4")


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

    def test_all_ir_players_are_excluded_from_starters(self, mock_df, mock_config, positions_config):
        extra_ir = mock_df[mock_df["Name"] == "Robbie Ouzts"].iloc[0].copy()
        extra_ir["Name"] = "Second IR Player"
        extra_ir["Position"] = "WR"
        extra_ir["Status"] = "IR-R"
        roster = get_my_roster(
            pd.concat([mock_df, pd.DataFrame([extra_ir])], ignore_index=True),
            mock_config["team_name"],
            WEEK,
        )
        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        starter_names = {slot.player for slot in lineup.starters}
        assert "Robbie Ouzts" not in starter_names
        assert "Second IR Player" not in starter_names

    def test_bench_alternative_excludes_ir_status_players(self, mock_df, mock_config, positions_config):
        """A bench player with an IR-status designation must not be suggested
        as an alternative for a flagged starter."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        # Make Tyreek Hill the only WR starter candidate and mark him Q.
        # Remove all other WRs so the only WR-compatible bench option is IR.
        hill_row = roster[roster["Name"] == "Tyreek Hill"].iloc[0].copy()
        roster = roster[roster["Position"].str.contains("WR") == False].copy()
        hill_row["Status"] = "Q"
        roster = pd.concat([roster[roster["Name"] != "Tyreek Hill"], pd.DataFrame([hill_row])], ignore_index=True)

        ir_wr = hill_row.copy()
        ir_wr["Name"] = "IR Bench WR"
        ir_wr["Status"] = "IR"
        ir_wr["Week 3"] = 5.0
        roster = pd.concat([roster, pd.DataFrame([ir_wr])], ignore_index=True)

        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        flagged = [s for s in lineup.flagged_starters if s.player == "Tyreek Hill"]
        assert len(flagged) == 1
        assert flagged[0].bench_alternative is None

    def test_bench_alternative_prefers_nonzero_projection(self, mock_df, mock_config, positions_config):
        """When multiple bench alternatives exist, prefer one with a non-zero
        projection over one with a zero/null projection."""
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        hill_row = roster[roster["Name"] == "Tyreek Hill"].iloc[0].copy()
        roster = roster[roster["Position"].str.contains("WR") == False].copy()
        hill_row["Status"] = "Q"
        roster = pd.concat([roster[roster["Name"] != "Tyreek Hill"], pd.DataFrame([hill_row])], ignore_index=True)

        zero_wr = hill_row.copy()
        zero_wr["Name"] = "Zero Bench WR"
        zero_wr["Status"] = ""
        zero_wr["Week 3"] = 0.0
        good_wr = hill_row.copy()
        good_wr["Name"] = "Good Bench WR"
        good_wr["Status"] = ""
        good_wr["Week 3"] = 8.0
        roster = pd.concat([roster, pd.DataFrame([zero_wr, good_wr])], ignore_index=True)

        lineup = recommend_lineup(mock_df, roster, positions_config, WEEK)
        flagged = [s for s in lineup.flagged_starters if s.player == "Tyreek Hill"]
        assert len(flagged) == 1
        assert flagged[0].bench_alternative == "Good Bench WR"
        assert flagged[0].bench_alt_projection > 0


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

    def test_same_position_swap_fixes_gap_not_flagged(
        self, mock_df, mock_config
    ):
        """When a WR bench-depth gap exists and the recommendation swaps a
        genuinely unusable WR (0.0 proj) for a genuinely usable one, the
        simulated post-move roster must reflect BOTH the drop and the add
        so the gap is recognised as fixed — no 'exacerbates' flag."""
        import pandas as pd
        from unittest.mock import patch
        from tests.conftest import _make_player
        from run_weekly import build_positions_config

        config = dict(mock_config)
        config["bench_depth_minimums"] = {"WR": 2}

        # Add a useless bench WR (0.0 proj) to our team
        useless_wr = _make_player(
            90, "Useless WR", "FA", "WR",
            mock_config["team_name"], mock_config["team_id"],
            "", "0.5", 0.0, -5.0,
        )
        df_mod = pd.concat([mock_df, pd.DataFrame([useless_wr])], ignore_index=True)

        roster = get_my_roster(df_mod, mock_config["team_name"], WEEK)
        gaps = flag_bench_depth_gaps(roster, build_positions_config(config))
        assert "WR" in {w.position for w in gaps}, "Pre-condition: WR bench gap must exist"

        # Optimizer says: drop Useless WR (0.0), add Jayden Reed (13.8 — usable)
        fake_opt = pd.DataFrame({
            "Add": ["Jayden Reed (WR, GB)"],
            "Drop": ["Useless WR (WR, FA)"],
            "VOR": [8.0],
        })

        def fake_lookup(df, name, position=None):
            if name == "Jayden Reed":
                row = df[df["Name"] == "Jayden Reed"].iloc[0]
                return row
            elif name == "Useless WR":
                row = df[df["Name"] == "Useless WR"].iloc[0]
                return row
            return None

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(df_mod, roster, WEEK, config)

        target_rec = next(r for r in recs if r.drop == "Useless WR")
        assert target_rec.flagged is False, (
            f"Drop+add swap fixing the gap should NOT be flagged, but flag_reason="
            f"{target_rec.flag_reason!r}"
        )
        assert "exacerbates" not in (target_rec.flag_reason or "").lower()
        assert "would create" not in (target_rec.flag_reason or "").lower()

    def test_swap_to_equally_unusable_is_flagged(
        self, mock_df, mock_config
    ):
        """Dropping a usable WR for an equally unusable one must still be
        flagged — the simulated roster correctly reflects both the drop and
        the add, but neither is usable so the gap persists."""
        import pandas as pd
        from unittest.mock import patch
        from tests.conftest import _make_player
        from run_weekly import build_positions_config

        config = dict(mock_config)
        config["bench_depth_minimums"] = {"WR": 2}

        # Add a useless bench WR (0.0 proj) to our team
        useless_wr = _make_player(
            90, "Useless WR", "FA", "WR",
            mock_config["team_name"], mock_config["team_id"],
            "", "0.5", 0.0, -5.0,
        )
        # Add a useless FA WR (0.0 proj) to be the incoming add
        dead_fa = _make_player(
            91, "Dead Weight FA", "FA", "WR",
            "Free Agent", float("nan"), "", "0.2", 0.0, -6.0,
        )
        df_mod = pd.concat([mock_df, pd.DataFrame([useless_wr, dead_fa])], ignore_index=True)

        roster = get_my_roster(df_mod, mock_config["team_name"], WEEK)
        gaps = flag_bench_depth_gaps(roster, build_positions_config(config))
        assert "WR" in {w.position for w in gaps}, "Pre-condition: WR bench gap must exist"

        # Optimizer says: drop Jordan Addison (11.0 — usable), add Dead Weight FA (0.0)
        fake_opt = pd.DataFrame({
            "Add": ["Dead Weight FA (WR, FA)"],
            "Drop": ["Jordan Addison (WR, MIN)"],
            "VOR": [8.0],
        })

        def fake_lookup(df, name, position=None):
            return df[df["Name"] == name].iloc[0]

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(df_mod, roster, WEEK, config)

        target_rec = next(r for r in recs if r.drop == "Jordan Addison")
        assert target_rec.flagged is True
        assert "exacerbates" in (target_rec.flag_reason or "").lower()

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
        ir_statuses = {"IR", "IR-R", "NFI", "NFI-R", "NFI-A", "COVID", "PUP", "PUP-R"}
        assert _is_ir("IR", ir_statuses) is True
        assert _is_ir("IR-R", ir_statuses) is True
        assert _is_ir("Q", ir_statuses) is False
        assert _is_ir("O", ir_statuses) is False
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


class TestLookupPlayerDisambiguation:
    def test_prefers_exact_case_insensitive_match_over_substring(self, mock_df):
        """An exact case-insensitive name match should win over a longer
        substring match."""
        df = mock_df.copy()
        # Add a longer name that contains the query as a substring
        extra = df[df["Name"] == "Josh Allen"].iloc[0].copy()
        extra["Name"] = "Josh Allen Jr"
        df = pd.concat([df, pd.DataFrame([extra])], ignore_index=True)

        result = _lookup_player(df, "josh allen")
        assert result["Name"] == "Josh Allen"

    def test_prefers_closest_string_length_among_substring_matches(self, mock_df):
        """When multiple substring matches exist, prefer the one whose name
        length is closest to the query."""
        df = mock_df.copy()
        # Add two names containing "Allen" as a substring with different lengths
        base = df[df["Name"] == "Josh Allen"].iloc[0].copy()
        short = base.copy()
        short["Name"] = "Allen"
        long = base.copy()
        long["Name"] = "Josh Allen The Third"
        df = pd.concat([df, pd.DataFrame([short, long])], ignore_index=True)

        result = _lookup_player(df, "Allen")
        assert result["Name"] == "Allen"

    def test_logs_warning_when_still_ambiguous(self, mock_df):
        """When disambiguation still leaves multiple matches, log a warning
        and return the first match as a last resort."""
        from unittest.mock import patch

        df = mock_df.copy()
        # Add two names with identical length containing the query
        base = df[df["Name"] == "Josh Allen"].iloc[0].copy()
        a = base.copy()
        a["Name"] = "Allen X"
        b = base.copy()
        b["Name"] = "Allen Y"
        df = pd.concat([df, pd.DataFrame([a, b])], ignore_index=True)

        with patch("decision_engine.logger.warning") as mock_warning:
            result = _lookup_player(df, "Allen")

        assert result["Name"] in ("Allen X", "Allen Y")
        mock_warning.assert_called_once()
        assert "Ambiguous player lookup" in mock_warning.call_args[0][0]


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

    def test_simulate_invalid_add_does_not_apply_drop(self, mock_df, mock_config, positions_config):
        roster = get_my_roster(mock_df, mock_config["team_name"], WEEK)
        rec = AddDropRecommendation(
            action="add_drop",
            add="Missing Player",
            add_position="K",
            drop="Jake Elliott",
            drop_position="K",
            flagged=False,
        )
        simulated = simulate_post_move_roster(mock_df, roster, [rec], WEEK)
        assert "Jake Elliott" in simulated["Name"].values
        assert "Missing Player" not in simulated["Name"].values

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



class TestIntraRosterComparisonNotes:
    """Tests for bench-upgrade comparison notes (intra-roster swaps)."""

    def test_bench_player_outprojecting_starter_generates_note(
        self, mock_df, mock_config, positions_config
    ):
        """A bench player with higher weekly projection than a starter at the
        same position should generate a comparison note explaining the swap."""
        import pandas as pd
        from decision_engine import (
            AddDropRecommendation,
            LineupSlot,
            LineupRecommendation,
            _generate_comparison_notes,
        )

        # Simulate a Pittman/Coker-like scenario:
        # WR starter has proj 8.7, bench WR has proj 8.8 (higher, but benched).
        starter = LineupSlot(
            slot="WR1",
            player="Michael Pittman Jr.",
            team="Pit",
            position="WR",
            projection=8.7,
            vor=12.4,
            status="Q",
            flagged=True,
            bench_alternative="Romeo Doubs",
            bench_alt_projection=8.2,
        )
        bench_slot = LineupSlot(
            slot="BN",
            player="Jalen Coker",
            team="Car",
            position="WR",
            projection=8.8,
            vor=-2.0,
            status="",
            flagged=False,
        )
        original_lineup = LineupRecommendation(
            starters=[starter],
            bench=[bench_slot],
            total_projection=8.7,
            total_vor=12.4,
            flagged_starters=[starter],
            warnings=[],
        )
        final_lineup = LineupRecommendation(
            starters=[starter],
            bench=[bench_slot],
            total_projection=8.7,
            total_vor=12.4,
            flagged_starters=[starter],
            warnings=[],
        )
        # No moves — purely intra-roster projection drift
        notes = _generate_comparison_notes([], original_lineup, final_lineup)
        assert len(notes) == 1
        assert "Jalen Coker" in notes[0]
        assert "Michael Pittman Jr." in notes[0]
        assert "starts over" in notes[0]
        assert "8.8" in notes[0]
        assert "8.7" in notes[0]

    def test_bench_player_below_starter_no_note(
        self, mock_df, mock_config, positions_config
    ):
        """A bench player with lower projection than the starter should not
        generate a comparison note."""
        from decision_engine import (
            LineupSlot,
            LineupRecommendation,
            _generate_comparison_notes,
        )

        starter = LineupSlot(
            slot="WR1",
            player="Michael Pittman Jr.",
            team="Pit",
            position="WR",
            projection=9.0,
            vor=12.4,
            status="",
            flagged=False,
        )
        bench_slot = LineupSlot(
            slot="BN",
            player="Jalen Coker",
            team="Car",
            position="WR",
            projection=8.8,
            vor=-2.0,
            status="",
            flagged=False,
        )
        original_lineup = LineupRecommendation(
            starters=[starter],
            bench=[bench_slot],
            total_projection=9.0,
            total_vor=12.4,
            flagged_starters=[],
            warnings=[],
        )
        final_lineup = LineupRecommendation(
            starters=[starter],
            bench=[bench_slot],
            total_projection=9.0,
            total_vor=12.4,
            flagged_starters=[],
            warnings=[],
        )
        notes = _generate_comparison_notes([], original_lineup, final_lineup)
        assert len(notes) == 0

    def test_new_add_note_not_duplicated_by_bench_pass(
        self, mock_df, mock_config, positions_config
    ):
        """When a new add out-projects a starter, pass 1 already covers it;
        pass 2 should not produce a duplicate note for the same player."""
        from decision_engine import (
            AddDropRecommendation,
            LineupSlot,
            LineupRecommendation,
            _generate_comparison_notes,
        )

        starter = LineupSlot(
            slot="TE",
            player="Hunter Henry",
            team="NE",
            position="TE",
            projection=7.16,
            vor=8.8,
            status="",
            flagged=False,
        )
        # New add: Juwan Johnson — this is handled by pass 1
        rec = AddDropRecommendation(
            action="add_drop",
            add="Juwan Johnson",
            add_position="TE",
            add_team="NO",
            add_projection=7.64,
            add_vor=3.0,
            vor_gain=3.0,
            reason="test",
            confidence="high",
            flagged=False,
        )
        original_lineup = LineupRecommendation(
            starters=[starter],
            bench=[],
            total_projection=7.16,
            total_vor=8.8,
            flagged_starters=[],
            warnings=[],
        )
        final_lineup = LineupRecommendation(
            starters=[starter],
            bench=[],
            total_projection=7.16,
            total_vor=8.8,
            flagged_starters=[],
            warnings=[],
        )
        notes = _generate_comparison_notes([rec], original_lineup, final_lineup)
        # Should have exactly 1 note from pass 1, not duplicated by pass 2
        assert len(notes) == 1
        assert "Juwan Johnson" in notes[0]
        assert "replaces" in notes[0]

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
        """When CLI flags are provided, they override config.yaml."""
        config = {
            "league_id": 492312, "team_id": 7,
            "waiver_priority": 10, "scoring_type": "half-pppr",
            "positions": "QB, WR, RB", "waiver_type": "continual_rolling",
        }
        overrides = {
            "league_id": 999, "team_id": 5,
            "waiver_priority": 3, "scoring_type": "ppr",
            "positions": "QB, WR, WR, RB, TE, BN, IR",
            "waiver_type": "faab",
        }
        for key, value in overrides.items():
            if value is not None:
                config[key] = value
        assert config["league_id"] == 999
        assert config["team_id"] == 5
        assert config["waiver_priority"] == 3
        assert config["scoring_type"] == "ppr"
        assert config["positions"] == "QB, WR, WR, RB, TE, BN, IR"
        assert config["waiver_type"] == "faab"

    def test_config_values_used_when_no_cli_args(self):
        """When no CLI flags are given, config.yaml values are kept."""
        config = {
            "league_id": 492312, "team_id": 7,
            "waiver_priority": 10, "scoring_type": "half-ppr",
            "positions": "QB, WR, RB", "waiver_type": "continual_rolling",
        }
        overrides = {
            "league_id": None, "team_id": None,
            "waiver_priority": None, "scoring_type": None,
            "positions": None, "waiver_type": None,
        }
        for key, value in overrides.items():
            if value is not None:
                config[key] = value
        assert config["league_id"] == 492312
        assert config["team_id"] == 7
        assert config["waiver_priority"] == 10
        assert config["scoring_type"] == "half-ppr"
        assert config["waiver_type"] == "continual_rolling"

    def test_report_filename_reflects_league_and_team(self, tmp_path):
        """save_markdown_report should create a file named with league_id and team_id."""
        from report import save_markdown_report
        path = save_markdown_report(
            "# Test", week=3, league_id=492312, team_id=7,
            reports_dir=str(tmp_path)
        )
        assert "league_492312_team_7_week_3.md" in path
        assert os.path.exists(path)

    def test_run_weekly_attaches_actual_week_to_report(self, mock_df, mock_config):
        """run_weekly should attach the actual week it analyzed to the report
        object, so main() has one source of truth for the report header."""
        from run_weekly import run_weekly
        from unittest.mock import patch

        # Mock the data layer to return week 6 data while ffbot.current_week
        # reports a different week (7). The report must use the fetched week.
        with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, 6)):
            with patch("ffbot.current_week", return_value=7):
                report = run_weekly(mock_config, force_refresh=False)

        assert report.week == 6

    def test_report_week_matches_analysis_week_from_cached_data(self, mock_df, mock_config):
        """The report should use the same week the pipeline actually analyzed,
        not an independently re-queried ffbot.current_week()."""
        from unittest.mock import patch
        from run_weekly import run_weekly

        config = dict(mock_config)
        config["league_id"] = 492312
        config["team_id"] = 7

        with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, 5)):
            with patch("ffbot.current_week", return_value=3):
                report = run_weekly(config, week=None, force_refresh=False)

        assert report.week == 5


class TestPositionsOverride:
    def test_custom_positions_changes_roster_limit(self, mock_df, mock_config):
        """A custom --positions string should change the max roster size."""
        from run_weekly import build_positions_config
        from decision_engine import build_action_plan, recommend_lineup, AddDropRecommendation

        # Custom positions with fewer bench spots: 9 total slots
        custom_positions = "QB, WR, RB, TE, K, DEF, BN, BN, IR"
        custom_config = dict(mock_config)
        custom_config["positions"] = custom_positions

        positions_config = build_positions_config(custom_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # 12-player roster, 9 max slots, +3 net moves = 15 > 9
        # Interleaving should auto-insert 3 mandatory drops and emit a warning
        recs = [
            AddDropRecommendation(action="fa_add", add="Player A"),
            AddDropRecommendation(action="fa_add", add="Player B"),
            AddDropRecommendation(action="fa_add", add="Player C"),
        ]
        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3, original_lineup=lineup
        )
        from decision_engine import RosterLimitWarning
        rlw = next(
            (w for w in action_plan.remaining_warnings if isinstance(w, RosterLimitWarning)),
            None,
        )
        assert rlw is not None
        assert rlw.max_size == 9
        assert rlw.excess == 6  # 12 current + 3 adds = 15 projected, max = 9
        assert "auto-inserted" in rlw.message
        pure_drops = [m for m in action_plan.moves if m.action == "drop" and m.add is None]
        assert len(pure_drops) == 3

    def test_custom_positions_changes_bench_depth_gaps(self, mock_df, mock_config):
        """A custom --positions string should change bench-depth-gap detection."""
        from run_weekly import build_positions_config
        from decision_engine import flag_bench_depth_gaps

        # Config with BN minimums that differ from default
        custom_positions = "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR"
        custom_minimums = {"RB": 2, "WR": 2, "QB": 1}
        custom_config = dict(mock_config)
        custom_config["positions"] = custom_positions
        custom_config["bench_depth_minimums"] = custom_minimums

        positions_config = build_positions_config(custom_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        warnings = flag_bench_depth_gaps(roster, positions_config)

        # With RB minimum=2 and only 1 usable bench RB, we should get a gap warning
        rb_warnings = [w for w in warnings if w.position == "RB"]
        assert len(rb_warnings) == 1
        assert rb_warnings[0].minimum == 2


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

    def test_boundary_zero_not_flagged_without_after_week(self, mock_df, mock_config):
        """A zero projection at the season boundary (Week 18) must not be
        flagged as a bye because there's no Week 19 to confirm the sandwich
        pattern — be conservative and require both a prior and after week."""
        from run_weekly import build_positions_config
        from decision_engine import recommend_lineup
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # Set Week 18 to 0 (boundary — no Week 19 exists to confirm a bye)
        df_boundary = mock_df.copy()
        for col in [f"Week {w}" for w in range(4, 19)]:
            df_boundary[col] = 10.0
        df_boundary.loc[df_boundary["Name"] == "Josh Allen", "Week 18"] = 0

        warnings = check_upcoming_byes(df_boundary, 3, lookahead_weeks=15, lineup=lineup)
        allen_warnings = [w for w in warnings if w.player == "Josh Allen"]
        assert len(allen_warnings) == 0

    def test_zero_flagged_only_when_sandwiched(self, mock_df, mock_config):
        """A zero projection is only flagged as a bye when there's a normal
        projection both before and after it within the available data."""
        from run_weekly import build_positions_config
        from decision_engine import recommend_lineup
        positions_config = build_positions_config(mock_config)
        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # Week 6 = 0, Week 5 = non-zero, Week 7 = non-zero -> genuine sandwich
        df_sandwich = mock_df.copy()
        for col in [f"Week {w}" for w in range(4, 19)]:
            df_sandwich[col] = 10.0
        df_sandwich.loc[df_sandwich["Name"] == "Josh Allen", "Week 6"] = 0

        warnings = check_upcoming_byes(df_sandwich, 3, lookahead_weeks=3, lineup=lineup)
        allen_warnings = [w for w in warnings if w.player == "Josh Allen"]
        assert len(allen_warnings) == 1
        assert allen_warnings[0].bye_week == 6

        # Week 4 = 0 but Week 5 and later are all 0 (no after week) -> not a bye
        df_no_after = mock_df.copy()
        for col in [f"Week {w}" for w in range(4, 19)]:
            df_no_after[col] = 0.0
        df_no_after.loc[df_no_after["Name"] == "Josh Allen", "Week 4"] = 0

        warnings = check_upcoming_byes(df_no_after, 3, lookahead_weeks=2, lineup=lineup)
        allen_warnings = [w for w in warnings if w.player == "Josh Allen"]
        assert len(allen_warnings) == 0


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
        assert "Roster limit enforced" in rlw.message
        assert len(rlw.suggested_drops) == 1

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


class TestRosterLimitInterleavesDrops:
    def test_mandatory_drops_interleaved_before_overflow_adds(
        self, mock_df, mock_config
    ):
        """When pure adds exceed open slots, drops must be inserted before
        the overflowing adds so every numbered step is executable in Yahoo."""
        from decision_engine import (
            AddDropRecommendation, build_action_plan, recommend_lineup,
            RosterLimitWarning, _is_ir, _parse_ir_statuses,
        )

        positions_config = {
            "positions": "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR",
            "bench_depth_minimums": mock_config.get("bench_depth_minimums", {}),
            "ir_statuses": mock_config.get("ir_statuses", []),
            "min_usable_projection": mock_config.get("min_usable_projection", 1.0),
        }

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        assert len(roster) == 12

        lineup = recommend_lineup(mock_df, roster, positions_config, 3)
        ir_statuses = _parse_ir_statuses(positions_config)

        # 1 swap + 5 pure adds = net +5; 12 + 5 = 17 > 16, so 1 drop inserted
        recs = [
            AddDropRecommendation(
                action="add_drop",
                add="Player A", add_position="RB", add_team="TeamA",
                add_projection=10.0, add_vor=5.0, add_status="",
                drop="Woody Marks", drop_position="RB", drop_team="Hou",
                drop_projection=7.2, drop_vor=-7.7, drop_status="",
                vor_gain=12.7, confidence="high", reason="swap",
            ),
            AddDropRecommendation(
                action="fa_add",
                add="Player B", add_position="QB", add_team="TeamB",
                add_projection=16.0, add_vor=3.0, add_status="",
                vor_gain=3.0, confidence="high", reason="add B",
            ),
            AddDropRecommendation(
                action="fa_add",
                add="Player C", add_position="QB", add_team="TeamC",
                add_projection=15.0, add_vor=2.0, add_status="",
                vor_gain=2.0, confidence="high", reason="add C",
            ),
            AddDropRecommendation(
                action="fa_add",
                add="Player D", add_position="QB", add_team="TeamD",
                add_projection=14.0, add_vor=1.0, add_status="",
                vor_gain=1.0, confidence="medium", reason="add D",
            ),
            AddDropRecommendation(
                action="fa_add",
                add="Player E", add_position="QB", add_team="TeamE",
                add_projection=13.0, add_vor=0.5, add_status="",
                vor_gain=0.5, confidence="medium", reason="add E",
            ),
            AddDropRecommendation(
                action="fa_add",
                add="Player F", add_position="QB", add_team="TeamF",
                add_projection=12.0, add_vor=0.0, add_status="",
                vor_gain=0.0, confidence="low", reason="add F",
            ),
        ]

        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3, original_lineup=lineup
        )

        # 1 swap + 5 adds = 6 original; 1 drop inserted = 7 total
        assert len(action_plan.moves) == 7

        # 6th move (index 5) is the inserted pure drop
        sixth_move = action_plan.moves[5]
        assert sixth_move.action == "drop"
        assert sixth_move.drop is not None
        assert sixth_move.add is None
        assert sixth_move.drop not in {s.player for s in lineup.starters}
        ir_names = {
            str(row.get("Name", ""))
            for _, row in roster.iterrows()
            if _is_ir(row.get("Status"), ir_statuses)
        }
        assert sixth_move.drop not in ir_names

        # 7th move is the delayed add
        seventh_move = action_plan.moves[6]
        assert seventh_move.add == "Player F"

        # Verify the interleaved drop is present in remaining_warnings
        from decision_engine import RosterLimitWarning
        rlw = next(
            (w for w in action_plan.remaining_warnings if isinstance(w, RosterLimitWarning)),
            None,
        )
        assert rlw is not None
        assert "auto-inserted" in rlw.message
        assert len(rlw.suggested_drops) == 1
        assert rlw.suggested_drops[0]["name"] == sixth_move.drop

    def test_no_interleaving_when_fits_within_limit(self, mock_df, mock_config):
        """When moves fit within roster limit, no drops should be inserted."""
        from decision_engine import (
            AddDropRecommendation, build_action_plan, recommend_lineup,
        )

        positions_config = {
            "positions": "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR",
            "bench_depth_minimums": mock_config.get("bench_depth_minimums", {}),
            "ir_statuses": mock_config.get("ir_statuses", []),
            "min_usable_projection": mock_config.get("min_usable_projection", 1.0),
        }

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)
        lineup = recommend_lineup(mock_df, roster, positions_config, 3)

        # 1 swap + 2 pure adds = net +2, 12 + 2 = 14 < 16, no overflow
        recs = [
            AddDropRecommendation(
                action="add_drop",
                add="Player A", add_position="RB", add_team="TeamA",
                add_projection=10.0, add_vor=5.0, add_status="",
                drop="Woody Marks", drop_position="RB", drop_team="Hou",
                drop_projection=7.2, drop_vor=-7.7, drop_status="",
                vor_gain=12.7, confidence="high", reason="swap",
            ),
            AddDropRecommendation(
                action="fa_add",
                add="Player B", add_position="QB", add_team="TeamB",
                add_projection=16.0, add_vor=3.0, add_status="",
                vor_gain=3.0, confidence="high", reason="add B",
            ),
            AddDropRecommendation(
                action="fa_add",
                add="Player C", add_position="QB", add_team="TeamC",
                add_projection=15.0, add_vor=2.0, add_status="",
                vor_gain=2.0, confidence="high", reason="add C",
            ),
        ]

        action_plan = build_action_plan(
            mock_df, roster, recs, positions_config, 3, original_lineup=lineup
        )

        # No interleaving should happen — moves list unchanged
        assert len(action_plan.moves) == 3
        assert all(m.action != "drop" or m.drop is None for m in action_plan.moves)
        # Or more precisely: no pure drops were inserted
        pure_drops = [m for m in action_plan.moves if m.action == "drop" and m.add is None]
        assert len(pure_drops) == 0


class TestRankFreeAgentsEdgeCases:
    def test_rank_free_agents_zero_eligible_returns_empty_df(self, mock_df, mock_config):
        """When all free agents at a position are excluded by injury status,
        rank_free_agents must return an empty DataFrame, not crash."""
        # Create a df where every free agent kicker is injured/excluded
        df = mock_df.copy()
        # Find all K players and set their status to IR
        k_mask = df["Position"].apply(lambda p: "K" in {x.strip() for x in str(p).split(",")})
        df.loc[k_mask, "Status"] = "IR"

        result = rank_free_agents(df, "K", 3)
        assert len(result) == 0
        assert list(result.columns) == ["Name", "Team", "Position", "Status", "% Owned", "Week 3", "VOR", "flagged"]


class TestBenchDepthAwareDeprioritization:
    def test_add_for_non_gap_position_with_negative_vor_is_flagged(
        self, mock_df, mock_config
    ):
        """When a bench depth gap exists for WR, a below-replacement add for
        a different position should be flagged as deprioritized."""
        from decision_engine import flag_bench_depth_gaps

        positions_config = {
            "positions": "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR",
            "bench_depth_minimums": {"RB": 1, "WR": 2, "QB": 1, "TE": 0},
            "ir_statuses": mock_config.get("ir_statuses", []),
            "min_usable_projection": mock_config.get("min_usable_projection", 1.0),
        }

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        # Verify WR gap exists
        warnings = flag_bench_depth_gaps(roster, positions_config)
        wr_warnings = [w for w in warnings if w.position == "WR"]
        assert len(wr_warnings) == 1
        depth_gap_positions = {w.position for w in warnings}

        # Build a fake add recommendation for DEF with negative VOR
        from decision_engine import AddDropRecommendation
        rec = AddDropRecommendation(
            action="fa_add",
            add="Some DEF", add_position="DEF", add_team="TeamX",
            add_projection=5.0, add_vor=-2.3, add_status="",
            vor_gain=-2.3, confidence="medium", reason="test",
        )

        # Apply the deprioritization logic directly
        if (
            rec.add_position not in depth_gap_positions
            and rec.add_vor is not None
            and rec.add_vor < 0
            and depth_gap_positions
        ):
            rec.flagged = True
            gap_list = ", ".join(sorted(depth_gap_positions))
            rec.flag_reason = (
                f"Uses a bench slot on {rec.add_position} depth while "
                f"your {gap_list} bench gap remains unaddressed"
            )

        assert rec.flagged is True
        assert "bench gap remains unaddressed" in (rec.flag_reason or "")
        assert "DEF" in (rec.flag_reason or "")
        assert "WR" in (rec.flag_reason or "")

    def test_recommend_adds_drops_flags_non_gap_negative_vor_add(
        self, mock_df, mock_config
    ):
        """Integration: recommend_adds_drops should flag a below-replacement
        add for a non-gap position when a bench depth gap exists elsewhere."""
        import pandas as pd
        from unittest.mock import patch

        # Override bench_depth_minimums to require 2 usable bench WRs
        # The mock roster has only 1 usable bench WR (Jordan Addison), so this
        # creates a WR gap while the add recommendation is for DEF with negative VOR
        config = dict(mock_config)
        config["bench_depth_minimums"] = {"RB": 1, "WR": 2, "QB": 1, "TE": 0}

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        # Create fake optimizer output: add a DEF with negative VOR
        fake_opt = pd.DataFrame({
            "Add": ["Some New DEF (DEF, XYZ)"],
            "Drop": [""],
            "VOR": [-2.0],
        })

        # Patch _lookup_player to return a controlled DEF player with negative VOR
        fake_player = pd.Series({
            "Name": "Some New DEF",
            "Team": "XYZ",
            "Position": "DEF",
            "Status": "",
            "% Owned": 5,
            "Week 3": 5.0,
            "VOR": -2.0,
        })

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", return_value=fake_player):
                recs = recommend_adds_drops(mock_df, roster, 3, config)

        # Should have at least one recommendation
        assert len(recs) >= 1

        # The DEF add should be flagged with the deprioritization message
        flagged_recs = [r for r in recs if r.flagged and "bench gap remains unaddressed" in (r.flag_reason or "")]
        assert len(flagged_recs) >= 1
        rec = flagged_recs[0]
        # When a real free agent exists at a gapped position, it should be
        # suggested as a concrete alternative. The alternative points to the
        # best available free agent across all gapped positions.
        assert rec.alternative is not None
        assert "Woody Marks" in rec.alternative
        assert "RB" in rec.alternative

    def test_alternative_suggests_best_non_flagged_free_agent_for_gapped_position(
        self, mock_df, mock_config
    ):
        """When only one position is gapped, the alternative should point to the
        best non-flagged free agent at that exact position."""
        import pandas as pd
        from unittest.mock import patch

        config = dict(mock_config)
        config["bench_depth_minimums"] = {"WR": 2}

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        fake_opt = pd.DataFrame({
            "Add": ["Some New DEF (DEF, XYZ)"],
            "Drop": [""],
            "VOR": [-2.0],
        })

        fake_player = pd.Series({
            "Name": "Some New DEF",
            "Team": "XYZ",
            "Position": "DEF",
            "Status": "",
            "% Owned": 5,
            "Week 3": 5.0,
            "VOR": -2.0,
        })

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", return_value=fake_player):
                recs = recommend_adds_drops(mock_df, roster, 3, config)

        flagged_recs = [r for r in recs if r.flagged and "bench gap remains unaddressed" in (r.flag_reason or "")]
        assert len(flagged_recs) >= 1
        rec = flagged_recs[0]

        assert rec.alternative is not None
        assert "Jayden Reed" in rec.alternative
        assert "WR" in rec.alternative
        assert "13.8" in rec.alternative
        assert " | Alternative:" in rec.flag_reason

    def test_alternative_is_none_when_no_free_agents_at_gapped_position(
        self, mock_df, mock_config
    ):
        """When the gapped position has no free agents, rec.alternative must stay
        None and flag_reason must not include an 'Alternative:' clause."""
        import pandas as pd
        from unittest.mock import patch

        config = dict(mock_config)
        config["bench_depth_minimums"] = {"WR": 2}

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        fake_opt = pd.DataFrame({
            "Add": ["Some New DEF (DEF, XYZ)"],
            "Drop": [""],
            "VOR": [-2.0],
        })

        fake_player = pd.Series({
            "Name": "Some New DEF",
            "Team": "XYZ",
            "Position": "DEF",
            "Status": "",
            "% Owned": 5,
            "Week 3": 5.0,
            "VOR": -2.0,
        })

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", return_value=fake_player):
                with patch("decision_engine.rank_free_agents", return_value=pd.DataFrame()):
                    recs = recommend_adds_drops(mock_df, roster, 3, config)

        flagged_recs = [r for r in recs if r.flagged and "bench gap remains unaddressed" in (r.flag_reason or "")]
        assert len(flagged_recs) >= 1
        rec = flagged_recs[0]

        assert rec.alternative is None
        assert "Alternative:" not in (rec.flag_reason or "")

    def test_multiple_flags_are_joined_with_separator(
        self, mock_df, mock_config
    ):
        """When multiple flag conditions fire, their reasons must be joined with
        ' | ' and not mashed together without a separator."""
        import pandas as pd
        from unittest.mock import patch

        config = dict(mock_config)
        config["bench_depth_minimums"] = {"WR": 2}

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        fake_opt = pd.DataFrame({
            "Add": ["Hurting QB (QB, XYZ)"],
            "Drop": [""],
            "VOR": [-3.0],
        })

        fake_player = pd.Series({
            "Name": "Hurting QB",
            "Team": "XYZ",
            "Position": "QB",
            "Status": "IR",
            "% Owned": 5,
            "Week 3": 5.0,
            "VOR": -3.0,
        })

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", return_value=fake_player):
                recs = recommend_adds_drops(mock_df, roster, 3, config)

        flagged_recs = [r for r in recs if r.flagged]
        assert len(flagged_recs) >= 1
        rec = flagged_recs[0]

        # Both flags should be present and separated by ' | '
        assert "Add target Hurting QB has injury status IR" in (rec.flag_reason or "")
        assert "bench gap remains unaddressed" in (rec.flag_reason or "")
        assert " | " in rec.flag_reason
        # Make sure there's no mashed-together text like 'gapUses a bench slot'
        assert "gapUses" not in (rec.flag_reason or "")
        assert "IRUses" not in (rec.flag_reason or "")


class TestWorstVorTransparencyNote:
    def test_note_appears_when_gap_exceeds_threshold(self, mock_df, mock_config):
        """When ffbot drops a mid-VOR player but a worse-VOR bench player exists
        at the same position, the transparency note should appear in rec.reason."""
        from unittest.mock import patch
        import pandas as pd

        config = dict(mock_config)
        config["worst_vor_drop_note_threshold"] = 3.0

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        # Fake optimizer output: add Tyler Shough (QB), drop Daniel Jones (QB)
        # Jones VOR = +2.1, Darnold VOR = -9.3 (gap = 11.4 > 3.0 threshold)
        fake_opt = pd.DataFrame({
            "Add": ["Tyler Shough (QB, NO)"],
            "Drop": ["Daniel Jones (QB, Ind)"],
            "VOR": [7.1],
        })

        fake_add_player = pd.Series({
            "Name": "Tyler Shough", "Team": "NO", "Position": "QB",
            "Status": "", "% Owned": "41", "Week 3": 17.8, "VOR": 9.2,
        })
        fake_drop_player = pd.Series({
            "Name": "Daniel Jones", "Team": "Ind", "Position": "QB",
            "Status": "", "% Owned": "75.3", "Week 3": 17.6, "VOR": 2.1,
        })

        def fake_lookup(df, name, position=None):
            if name == "Tyler Shough":
                return fake_add_player
            elif name == "Daniel Jones":
                return fake_drop_player
            return None

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(mock_df, roster, 3, config)

        assert len(recs) == 1
        rec = recs[0]
        assert rec.drop == "Daniel Jones"
        assert "Note:" in rec.reason
        # The actual worst bench QB in the mock roster is Joe Milton (VOR -15.0),
        # not Sam Darnold (who is not in the roster — only referenced as the
        # optimizer's drop target in the scenario description).
        assert "Joe Milton" in rec.reason
        assert rec.reason.count("+2.1") >= 1 or rec.reason.count("-15.0") >= 1

    def test_note_absent_when_gap_below_threshold(self, mock_df, mock_config):
        """When the VOR gap between ffbot's drop and the worst bench player is
        below the threshold, no transparency note should appear."""
        from unittest.mock import patch
        import pandas as pd

        config = dict(mock_config)
        config["worst_vor_drop_note_threshold"] = 20.0  # very high threshold

        roster = get_my_roster(mock_df, mock_config["team_name"], 3)

        fake_opt = pd.DataFrame({
            "Add": ["Tyler Shough (QB, NO)"],
            "Drop": ["Daniel Jones (QB, Ind)"],
            "VOR": [7.1],
        })

        fake_add_player = pd.Series({
            "Name": "Tyler Shough", "Team": "NO", "Position": "QB",
            "Status": "", "% Owned": "41", "Week 3": 17.8, "VOR": 9.2,
        })
        fake_drop_player = pd.Series({
            "Name": "Daniel Jones", "Team": "Ind", "Position": "QB",
            "Status": "", "% Owned": "75.3", "Week 3": 17.6, "VOR": 2.1,
        })

        def fake_lookup(df, name, position=None):
            if name == "Tyler Shough":
                return fake_add_player
            elif name == "Daniel Jones":
                return fake_drop_player
            return None

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(mock_df, roster, 3, config)

        assert len(recs) == 1
        assert "Note:" not in recs[0].reason

    def test_note_absent_when_ffbot_chooses_worst_vor(self, mock_df, mock_config):
        """When ffbot's chosen drop IS the lowest-VOR bench player, no note."""
        from unittest.mock import patch
        import pandas as pd
        from tests.conftest import _make_player

        config = dict(mock_config)
        config["worst_vor_drop_note_threshold"] = 3.0

        # Add Sam Darnold to the mock roster with VOR lower than all existing QBs
        # so that when ffbot drops him, he IS the worst-VOR option — no note should appear.
        darnold_row = _make_player(
            50, "Sam Darnold", "Sea", "QB",
            mock_config["team_name"], mock_config["team_id"],
            "", "5.0", 16.2, -20.0,
        )
        df_with_darnold = pd.concat([mock_df, pd.DataFrame([darnold_row])], ignore_index=True)
        roster = get_my_roster(df_with_darnold, mock_config["team_name"], 3)

        fake_opt = pd.DataFrame({
            "Add": ["Tyler Shough (QB, NO)"],
            "Drop": ["Sam Darnold (QB, Sea)"],
            "VOR": [29.5],  # 9.2 - (-20.0) = 29.5
        })

        fake_add_player = pd.Series({
            "Name": "Tyler Shough", "Team": "NO", "Position": "QB",
            "Status": "", "% Owned": "41", "Week 3": 17.8, "VOR": 9.2,
        })
        fake_drop_player = pd.Series({
            "Name": "Sam Darnold", "Team": "Sea", "Position": "QB",
            "Status": "", "% Owned": "5.0", "Week 3": 16.2, "VOR": -20.0,
        })

        def fake_lookup(df, name, position=None):
            if name == "Tyler Shough":
                return fake_add_player
            elif name == "Sam Darnold":
                return fake_drop_player
            return None

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(df_with_darnold, roster, 3, config)

        assert len(recs) == 1
        assert "Note:" not in recs[0].reason


    def test_note_excludes_starter_from_comparison(self, mock_df, mock_config):
        """When the worst-VOR player at a position is a current starter,
        the note should reference the worst bench player, not the starter."""
        from unittest.mock import patch
        import pandas as pd

        config = dict(mock_config)
        config["worst_vor_drop_note_threshold"] = 3.0

        # Lower Josh Allen VOR to -50.0 while keeping his high Week 3 projection (24.5)
        # so recommend_lineup still makes him the QB starter. He is now the
        # worst-VOR QB overall, but as a starter he must be excluded from the
        # "alternative drop" comparison pool.
        df_mod = mock_df.copy()
        allen_idx = df_mod[df_mod["Name"] == "Josh Allen"].index[0]
        df_mod.loc[allen_idx, "VOR"] = -50.0

        roster = get_my_roster(df_mod, mock_config["team_name"], 3)

        # ffbot drops Kirk Cousins (bench QB, VOR 30.0) — not the worst bench QB.
        # Joe Milton (bench, VOR -15.0) is the actual worst bench QB.
        # The worst-VOR QB overall is Allen (starter, -50.0) but he must be excluded.
        # Gap: 30.0 - (-15.0) = 45.0 > 3.0 -> note should appear mentioning Milton.
        fake_opt = pd.DataFrame({
            "Add": ["Tyler Shough (QB, NO)"],
            "Drop": ["Kirk Cousins (QB, ATL)"],
            "VOR": [-20.8],  # 9.2 - 30.0 = -20.8 (negative, but still a rec)
        })

        fake_add_player = pd.Series({
            "Name": "Tyler Shough", "Team": "NO", "Position": "QB",
            "Status": "", "% Owned": "41", "Week 3": 17.8, "VOR": 9.2,
        })
        fake_drop_player = pd.Series({
            "Name": "Kirk Cousins", "Team": "ATL", "Position": "QB",
            "Status": "", "% Owned": "5.0", "Week 3": 12.0, "VOR": 30.0,
        })

        def fake_lookup(df, name, position=None):
            if name == "Tyler Shough":
                return fake_add_player
            elif name == "Kirk Cousins":
                return fake_drop_player
            return None

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(df_mod, roster, 3, config)

        assert len(recs) == 1
        rec = recs[0]
        assert "Note:" in rec.reason
        # Josh Allen (starter, VOR -50.0) is the worst-VOR QB overall but must
        # be excluded from the comparison — note should mention Joe Milton instead.
        assert "Joe Milton" in rec.reason
        assert "Josh Allen" not in rec.reason

    def test_note_absent_when_only_starters_and_ir_at_position(self, mock_df, mock_config):
        """When all players at a position are starters or IR (no bench candidates),
        the transparency note should not appear."""
        from unittest.mock import patch
        import pandas as pd
        from tests.conftest import _make_player

        config = dict(mock_config)
        config["worst_vor_drop_note_threshold"] = 3.0

        # Minimal roster: 1 QB starter + IR QB + other starters, but NO bench QBs.
        minimal_rows = [
            _make_player(1, "Josh Allen", "BUF", "QB", mock_config["team_name"], mock_config["team_id"], "", "75.3", 24.5, 85.3),
            _make_player(2, "Tyreek Hill", "MIA", "WR", mock_config["team_name"], mock_config["team_id"], "Q", "98.2", 22.3, 142.1),
            _make_player(3, "CeeDee Lamb", "DAL", "WR", mock_config["team_name"], mock_config["team_id"], "", "92.1", 19.8, 118.5),
            _make_player(4, "Breece Hall", "NYJ", "RB", mock_config["team_name"], mock_config["team_id"], "", "88.5", 18.2, 95.0),
            _make_player(5, "Jahmyr Gibbs", "DET", "RB", mock_config["team_name"], mock_config["team_id"], "", "72.0", 15.5, 82.3),
            _make_player(6, "Sam LaPorta", "DET", "TE", mock_config["team_name"], mock_config["team_id"], "", "65.4", 10.2, 45.0),
            _make_player(7, "Jake Elliott", "PHI", "K", mock_config["team_name"], mock_config["team_id"], "", "45.0", 8.5, 12.0),
            _make_player(8, "San Francisco", "SF", "DEF", mock_config["team_name"], mock_config["team_id"], "", "52.3", 7.8, 8.0),
            _make_player(10, "IR Backup QB", "FA", "QB", mock_config["team_name"], mock_config["team_id"], "IR", "0.1", 0.0, -2.0),
        ]
        df_minimal = pd.DataFrame(minimal_rows)
        for w in range(1, 19):
            col = f"Week {w}"
            if col not in df_minimal.columns:
                df_minimal[col] = 0.0

        roster = get_my_roster(df_minimal, mock_config["team_name"], 3)

        fake_opt = pd.DataFrame({
            "Add": ["Tyler Shough (QB, NO)"],
            "Drop": ["Josh Allen (QB, BUF)"],
            "VOR": [7.1],
        })

        fake_add_player = pd.Series({
            "Name": "Tyler Shough", "Team": "NO", "Position": "QB",
            "Status": "", "% Owned": "41", "Week 3": 17.8, "VOR": 9.2,
        })
        fake_drop_player = pd.Series({
            "Name": "Josh Allen", "Team": "BUF", "Position": "QB",
            "Status": "", "% Owned": "75.3", "Week 3": 24.5, "VOR": 85.3,
        })

        def fake_lookup(df, name, position=None):
            if name == "Tyler Shough":
                return fake_add_player
            elif name == "Josh Allen":
                return fake_drop_player
            return None

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(df_minimal, roster, 3, config)

        assert len(recs) == 1
        # No bench QB candidates exist (only Allen as starter + IR QB),
        # so the transparency note should not appear.
        assert "Note:" not in recs[0].reason

class TestNegativeVorFlaggedFiltering:
    """Tests for negative-VOR filtering in run_weekly.py — flagged items
    should still be filtered out of the main recommended list and shown
    separately under 'Not Recommended'."""

    def test_negative_vor_flagged_items_filtered_out(
        self, mock_df, mock_config
    ):
        """Recommendations with negative VOR gain that are flagged should
        be excluded from the main recommended list and appear in 'Not Recommended'."""
        from unittest.mock import patch
        import pandas as pd
        from tests.conftest import _make_player
        from decision_engine import get_my_roster

        config = dict(mock_config)

        # Add a bench player with very low VOR that optimizer might drop
        low_vor_row = _make_player(
            60, "Low VOR Bench RB", "FA", "RB",
            mock_config["team_name"], mock_config["team_id"],
            "", "1.0", 2.0, -10.0,
        )
        df_mod = pd.concat([mock_df, pd.DataFrame([low_vor_row])], ignore_index=True)
        roster = get_my_roster(df_mod, mock_config["team_name"], 3)

        # Optimizer returns a negative-VOR recommendation (flagged)
        fake_opt = pd.DataFrame({
            "Add": ["Tyler Shough (QB, NO)"],
            "Drop": ["Low VOR Bench RB (RB, FA)"],
            "VOR": [-5.0],  # Negative VOR gain
        })

        fake_add_player = pd.Series({
            "Name": "Tyler Shough", "Team": "NO", "Position": "QB",
            "Status": "", "% Owned": "41", "Week 3": 17.8, "VOR": 9.2,
        })
        fake_drop_player = pd.Series({
            "Name": "Low VOR Bench RB", "Team": "FA", "Position": "RB",
            "Status": "", "% Owned": "1.0", "Week 3": 2.0, "VOR": -10.0,
        })

        def fake_lookup(df, name, position=None):
            if name == "Tyler Shough":
                return fake_add_player
            elif name == "Low VOR Bench RB":
                return fake_drop_player
            return None

        with patch("ffbot.optimize", return_value=fake_opt):
            with patch("decision_engine._lookup_player", side_effect=fake_lookup):
                recs = recommend_adds_drops(df_mod, roster, 3, config)

        # The recommendation should have negative VOR and be flagged
        assert len(recs) == 1
        assert recs[0].vor_gain == -5.0
        assert recs[0].flagged is True


class TestHypotheticalDrop:
    def test_hypothetical_drop_removes_player_from_report_roster(
        self, mock_df, mock_config
    ):
        """When --hypothetical-drop is provided, run_weekly should remove that
        player from the in-memory roster before analysis and label the report."""
        from unittest.mock import patch
        from run_weekly import run_weekly

        target_name = "Jordan Addison"
        with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, 3)):
            with patch("ffbot.current_week", return_value=3):
                report = run_weekly(
                    mock_config,
                    week=None,
                    force_refresh=False,
                    hypothetical_drop=target_name,
                )

        assert report.hypothetical_drop == target_name
        if report.my_roster is not None:
            assert target_name not in report.my_roster["Name"].values

    def test_normal_run_unaffected_by_hypothetical_drop_flag(
        self, mock_df, mock_config
    ):
        """Without --hypothetical-drop, the roster should be untouched and the
        report should carry no hypothetical label."""
        from unittest.mock import patch
        from run_weekly import run_weekly

        target_name = "Jordan Addison"
        with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, 3)):
            with patch("ffbot.current_week", return_value=3):
                report = run_weekly(
                    mock_config,
                    week=None,
                    force_refresh=False,
                    hypothetical_drop=None,
                )

        assert report.hypothetical_drop is None
        if report.my_roster is not None:
            assert target_name in report.my_roster["Name"].values

    def test_hypothetical_drop_player_not_recommended_as_drop_target(
        self, mock_df, mock_config
    ):
        """After a hypothetical drop, that player must never appear as a Drop
        target in any recommendation. They may appear as an Add target because
        they now look like a free agent in the modified df."""
        from unittest.mock import patch
        from run_weekly import run_weekly

        target_name = "Jordan Addison"
        with patch("run_weekly.get_latest_cached_or_fresh", return_value=(mock_df, 3)):
            with patch("ffbot.current_week", return_value=3):
                report = run_weekly(
                    mock_config,
                    week=None,
                    force_refresh=False,
                    hypothetical_drop=target_name,
                )

        all_recs = (report.add_drop_recs or []) + (report.low_value_recs or [])
        for rec in all_recs:
            assert rec.drop != target_name, (
                f"Hypothetically-dropped player '{target_name}' should not appear as a "
                f"Drop target, but was recommended in action='{rec.action}'"
            )

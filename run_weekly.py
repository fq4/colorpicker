#!/usr/bin/env python3
"""
Weekly Fantasy Football Decision Agent — main entry point.

Phase 1: terminal report + saved markdown.

Usage:
    python run_weekly.py                 # dry-run, uses cache
    python run_weekly.py --execute       # execute recommendations via executor
    python run_weekly.py --force-refresh # bypass cache, re-scrape
    python run_weekly.py --week 5        # override auto-detected week
"""
from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

# Ensure UTF-8 output on all platforms (team names may contain non-ASCII)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from loguru import logger

from data_layer import (
    get_latest_cached_or_fresh,
    get_fresh_data,
    load_config,
    ensure_data_columns,
)
from decision_engine import (
    get_my_roster,
    flag_bench_depth_gaps,
    recommend_adds_drops,
    recommend_lineup,
    build_action_plan,
    check_upcoming_byes,
    RecommendationReport,
)
from report import build_terminal_report, build_markdown_report, save_markdown_report
from executor import submit_add_drop, set_lineup


def setup_logging(logs_dir: str = "logs"):
    """Configure loguru to log to both console and file."""
    os.makedirs(logs_dir, exist_ok=True)

    # Remove default handler
    logger.remove()

    # Console handler — INFO level
    logger.add(
        sys.stderr,
        level="INFO",
        format="<level>{message}</level>",
    )

    # File handler — DEBUG level with timestamps
    log_path = os.path.join(logs_dir, "agent.log")
    logger.add(
        log_path,
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
        rotation="7 days",
        retention="30 days",
    )

    logger.info(f"Logging to {log_path}")


def build_positions_config(config: dict) -> dict:
    """Build the positions_config dict expected by decision engine functions."""
    return {
        "positions": config["positions"],
        "bench_depth_minimums": config.get("bench_depth_minimums", {}),
        "ir_statuses": config.get("ir_statuses", []),
        "min_usable_projection": config.get("min_usable_projection", 1.0),
    }


def run_weekly(config: dict, week: int | None = None, force_refresh: bool = False, log_path: str = "logs", hypothetical_drop: str | None = None) -> RecommendationReport:
    """Execute the full weekly pipeline and return the recommendation report.

    1. Fetch or cache data
    2. Build roster, depth warnings, lineup, and add/drop recommendations
    """
    import ffbot

    # Determine week
    if week is not None:
        current_week = week
    else:
        current_week = ffbot.current_week()

    logger.info(f"=== FFBot Decision Agent — Week {current_week} ({config.get('season', 'N/A')}) ===")

    # 1. Data
    league_id = config["league_id"]
    is_idp = config.get("is_idp", False)
    max_age = 0 if force_refresh else config.get("cache_max_age_hours", 12)

    logger.info("Fetching data ...")
    if force_refresh:
        df, fetched_week = get_fresh_data(league_id, is_idp)
    else:
        df, fetched_week = get_latest_cached_or_fresh(league_id, is_idp, max_age_hours=max_age)
    if week is None:
        current_week = fetched_week

    # Ensure expected columns exist
    df = ensure_data_columns(df)

    # 1.5 Derive team_name from team_id via the scraped data
    from data_layer import get_team_name_from_id
    team_name = get_team_name_from_id(df, config["team_id"])
    config["team_name"] = team_name  # inject so all downstream readers stay in sync

    # Print resolved identity banner
    print()
    print("=" * 60)
    print(f"  League ID      : {config['league_id']}")
    print(f"  Team ID        : {config['team_id']}")
    print(f"  Team Name      : {team_name}")
    print(f"  Scoring        : {config.get('scoring_type', 'N/A')}")
    print(f"  Waiver Type    : {config.get('waiver_type', 'N/A')}")
    print(f"  Waiver Priority: {config.get('waiver_priority', 'N/A')}")
    print("=" * 60)
    print()

    # 2. Roster
    my_roster = get_my_roster(df, team_name, current_week)
    logger.info(f"Roster: {len(my_roster)} players for '{team_name}'")

    # Optional hypothetical drop (in-memory only, never touches Yahoo or cache)
    if hypothetical_drop:
        df = df.copy()
        my_roster = get_my_roster(df, team_name, current_week)
        mask = my_roster["Name"].str.lower() != hypothetical_drop.strip().lower()
        removed = int((~mask).sum())
        my_roster = my_roster[mask].reset_index(drop=True)

        if removed > 0:
            logger.info(f"Hypothetical drop: removed '{hypothetical_drop}' ({removed} player removed)")
            player_mask = df["Name"].str.lower() == hypothetical_drop.strip().lower()
            df.loc[player_mask, "Owner"] = "Free Agent"
            df.loc[player_mask, "Owner ID"] = float("nan")
        else:
            logger.warning(
                f"Hypothetical drop '{hypothetical_drop}' not found on roster; proceeding with full roster"
            )

    # 3. Depth warnings
    positions_config = build_positions_config(config)
    depth_warnings = flag_bench_depth_gaps(my_roster, positions_config)
    for w in depth_warnings:
        logger.warning(f"Bench depth: {w.message}")

    # 4. Lineup recommendation
    lineup = recommend_lineup(df, my_roster, positions_config, current_week)
    logger.info(f"Lineup projection: {lineup.total_projection:.1f} pts (VOR {lineup.total_vor:+.1f})")

    # 5. Add/drop recommendations
    add_drop_recs = recommend_adds_drops(df, my_roster, current_week, config)
    # Separate negative-VOR recommendations
    low_value = [r for r in add_drop_recs if r.vor_gain is not None and r.vor_gain < 0]
    add_drop_recs = [r for r in add_drop_recs if r.vor_gain is None or r.vor_gain >= 0]
    if low_value:
        logger.info(f"Filtered {len(low_value)} negative-VOR recommendations")
    logger.info(f"Add/drop recommendations: {len(add_drop_recs)}")

    # 6. Build action plan (simulated post-move roster + resulting lineup)
    action_plan = build_action_plan(
        df, my_roster, add_drop_recs, positions_config, current_week, original_lineup=lineup
    )

    # Check upcoming bye weeks
    bye_week_warnings = check_upcoming_byes(my_roster, current_week, lineup=lineup)
    for w in bye_week_warnings:
        logger.warning(f"Bye week: {w.message}")

    report = RecommendationReport(
        my_roster=my_roster,
        depth_warnings=depth_warnings,
        bye_week_warnings=bye_week_warnings,
        lineup=lineup,
        add_drop_recs=add_drop_recs,
        low_value_recs=low_value,
        action_plan=action_plan,
        week=current_week,
        hypothetical_drop=hypothetical_drop,
    )

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Fantasy Football Decision Agent — weekly add/drop & lineup recommendations"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show recommendations without executing (default)"
    )
    parser.add_argument(
        "--execute", action="store_true", default=False,
        help="Execute recommendations via Yahoo API"
    )
    parser.add_argument(
        "--force-refresh", action="store_true", default=False,
        help="Bypass cache and re-scrape Yahoo data"
    )
    parser.add_argument(
        "--week", type=int, default=None,
        help="Override auto-detected week number"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config file"
    )
    parser.add_argument(
        "--max-cache-age", type=float, default=12.0,
        dest="max_cache_age",
        help="Max cache age in hours before re-scraping (default: 12)"
    )
    parser.add_argument(
        "--league-id", type=int, default=None,
        dest="league_id",
        help="Override league_id from config.yaml for this run"
    )
    parser.add_argument(
        "--team-id", type=int, default=None,
        dest="team_id",
        help="Override team_id from config.yaml for this run"
    )
    parser.add_argument(
        "--waiver-priority", type=int, default=None,
        dest="waiver_priority",
        help="Override waiver_priority from config.yaml for this run (display-only)"
    )
    parser.add_argument(
        "--scoring-type", type=str, default=None,
        dest="scoring_type",
        choices=["half-ppr", "ppr", "standard"],
        help="Override scoring_type from config.yaml for this run (display-only)"
    )
    parser.add_argument(
        "--positions", type=str, default=None,
        dest="positions",
        help='Override positions from config.yaml for this run (e.g. "QB, WR, WR, RB, RB, TE, W/R, K, DEF, BN, BN, BN, BN, BN, BN, IR")'
    )
    parser.add_argument(
        "--waiver-type", type=str, default=None,
        dest="waiver_type",
        choices=["continual_rolling", "faab"],
        help="Override waiver_type from config.yaml for this run (display-only)"
    )
    parser.add_argument(
        "--hypothetical-drop", type=str, default=None,
        dest="hypothetical_drop",
        help="Simulate dropping a player from your roster for what-if analysis (does not change any data)"
    )
    parser.add_argument(
        "--locked-positions", type=str, default=None,
        dest="locked_positions",
        help="Comma-separated list of positions to lock (no add/drop suggestions, e.g. DEF,TE)"
    )
    args = parser.parse_args()

    dry_run = args.dry_run or not args.execute
    setup_logging()

    # Load config
    config = load_config(args.config)
    config["cache_max_age_hours"] = args.max_cache_age

    # Apply CLI overrides (config.yaml remains the default when flags are omitted)
    if args.league_id is not None:
        config["league_id"] = args.league_id
    if args.team_id is not None:
        config["team_id"] = args.team_id
    if args.waiver_priority is not None:
        config["waiver_priority"] = args.waiver_priority
    if args.scoring_type is not None:
        config["scoring_type"] = args.scoring_type
    if args.positions is not None:
        config["positions"] = args.positions
    if args.waiver_type is not None:
        config["waiver_type"] = args.waiver_type
    if args.locked_positions is not None:
        config["locked_positions"] = [p.strip() for p in args.locked_positions.split(",") if p.strip()]

    # Run pipeline (run_weekly will derive team_name from team_id via the df)
    report = run_weekly(config, week=args.week, force_refresh=args.force_refresh, hypothetical_drop=args.hypothetical_drop)

    # Use the same week the pipeline actually analyzed — one source of truth
    import ffbot
    actual_week = report.week if report.week is not None else ffbot.current_week()

    # Generate reports
    terminal_report = build_terminal_report(
        report, actual_week, config.get("season", 2026), config, dry_run=dry_run
    )
    print(terminal_report)

    # Save markdown report
    md_report = build_markdown_report(
        report, actual_week, config.get("season", 2026), config, dry_run=dry_run
    )
    save_markdown_report(
        md_report, actual_week,
        league_id=config["league_id"],
        team_id=config["team_id"],
    )

    # Execute (only if --execute)
    action_plan = report.action_plan
    executable_moves = action_plan.moves if action_plan is not None else report.add_drop_recs
    if not dry_run and executable_moves:
        logger.info("Executing recommendations ...")
        for rec in executable_moves:
            if rec.confidence == "low":
                logger.info(f"Skipping low-confidence recommendation: {rec.add} / {rec.drop}")
                continue
            result = submit_add_drop(
                add_player=rec.add,
                drop_player=rec.drop,
                waiver_priority=config.get("waiver_priority"),
                transaction_type="waiver" if rec.action in ("waiver_claim", "waiver_add") else "fa",
                dry_run=False,
            )
            logger.info(f"Execution result: {result}")

        final_lineup = action_plan.final_lineup if action_plan is not None else report.lineup
        if final_lineup:
            lineup_result = set_lineup(final_lineup, dry_run=False)
            logger.info(f"Lineup result: {lineup_result}")

    logger.info("=== Done ===")


if __name__ == "__main__":
    main()

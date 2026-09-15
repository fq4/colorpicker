#!/usr/bin/env python3
"""Capture final fantasy points for a completed week from a fresh ffbot scrape."""
import argparse

from data_layer import fetch_and_capture_actual_results, load_config


def main():
    parser = argparse.ArgumentParser(
        description="Capture final Yahoo fantasy points for a completed prior week."
    )
    parser.add_argument("--week", type=int, required=True, help="Completed week to capture")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--league-id", type=int, default=None, dest="league_id")
    parser.add_argument("--team-id", type=int, default=None, dest="team_id")
    args = parser.parse_args()

    config = load_config(args.config)
    league_id = args.league_id if args.league_id is not None else config["league_id"]
    team_id = args.team_id if args.team_id is not None else config["team_id"]

    actuals, path = fetch_and_capture_actual_results(
        league_id=league_id,
        team_id=team_id,
        week=args.week,
        is_idp=config.get("is_idp", False),
    )
    print(f"Saved {len(actuals)} player results to {path}")


if __name__ == "__main__":
    main()

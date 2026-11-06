import argparse
import logging
from datetime import datetime
from pathlib import Path

from analytics.evaluate import write_accuracy_report, write_predictions_csv
from analytics.models import forecast_walk_forward
from analytics.store import AnalyticsStore, parse_snapshot_filename
from data_layer import get_fresh_data, load_config


LOGGER = logging.getLogger("analytics")


def _week_locks(config):
    locks = config.get("kickoff_times", {})
    return {int(week): value for week, value in locks.items()}


def _database_path(args):
    return Path(args.output_dir) / "analytics.sqlite3"


def _max_configured_week(kickoff_times):
    return max(kickoff_times, default=0)


def _ensure_pre_kickoff(snapshot_time, week, kickoff_times):
    lock_value = kickoff_times.get(week)
    if lock_value is None:
        raise ValueError(f"No kickoff time configured for Week {week}.")
    lock = datetime.fromisoformat(str(lock_value))
    if lock.tzinfo is None:
        lock = lock.astimezone()
    if snapshot_time >= lock:
        raise ValueError(
            f"Week {week} snapshot at {snapshot_time.isoformat()} is not pre-kickoff "
            f"(lock: {lock.isoformat()})."
        )


def _snapshot_command(args, config, kickoff_times, store):
    week = args.week
    lock_value = kickoff_times.get(week)
    if lock_value is None:
        raise ValueError(f"No kickoff time configured for Week {week}.")
    lock = datetime.fromisoformat(str(lock_value))
    if lock.tzinfo is None:
        lock = lock.astimezone()

    if args.source:
        source = Path(args.source)
        parsed = parse_snapshot_filename(source, config["league_id"])
        if parsed is None:
            raise ValueError(f"Unrecognized snapshot file: {source}")
        source_week, snapshot_time = parsed
        if source_week not in {week, week - 1}:
            raise ValueError(
                f"Snapshot is Week {source_week}; requested Week {week} or its "
                "immediately preceding scrape week."
            )
        _ensure_pre_kickoff(snapshot_time, week, kickoff_times)
    else:
        now = datetime.now().astimezone()
        if now >= lock:
            raise ValueError(
                f"Week {week} kickoff has passed; refusing to label a later scrape "
                "as a pre-kickoff snapshot."
            )
        scrape_started_at = now.replace(microsecond=0)
        df, fetched_week = get_fresh_data(
            config["league_id"], config.get("is_idp", False)
        )
        if fetched_week not in {week, week - 1}:
            raise ValueError(
                f"Fresh scrape returned Week {fetched_week}; requested Week {week} "
                "requires the current week or its immediately preceding scrape week."
            )
        if fetched_week == week - 1 and f"Week {week}" not in df.columns:
            raise ValueError(
                f"Fresh scrape has no Week {week} values for the requested snapshot."
            )
        cache_week = fetched_week
        candidates = sorted(
            Path(args.data_dir).glob(
                f"league_{config['league_id']}_week_{cache_week}_*.csv"
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"Fresh scrape completed but no canonical cache was found in {args.data_dir}."
            )
        source = candidates[0]
        parsed = parse_snapshot_filename(source, config["league_id"])
        if parsed is None:
            raise ValueError(f"Fresh cache has an invalid filename: {source.name}")
        if parsed[1] < scrape_started_at:
            raise FileNotFoundError(
                "The scrape did not create a new timestamped cache; refusing to "
                "reuse an older file as this week's snapshot."
            )
        _ensure_pre_kickoff(parsed[1], week, kickoff_times)

    result = store.ingest_file(source, config["league_id"], kickoff_times)
    print(
        f"Saved Week {week} snapshot: {result['player_count']} players at "
        f"{result['snapshot_time']} ({source})"
    )


def _ingest_command(args, config, kickoff_times, store):
    results = store.ingest_directory(
        args.data_dir, config["league_id"], kickoff_times
    )
    inserted = sum(1 for _, result in results if result["inserted"])
    print(
        f"Ingested {inserted} new snapshots; {len(results) - inserted} were already "
        f"present. Database: {_database_path(args)}"
    )


def _evaluate_command(args, config, kickoff_times, store):
    week = args.week or _max_configured_week(kickoff_times)
    result = write_accuracy_report(
        Path(args.output_dir) / "accuracy_report.md",
        store,
        config["league_id"],
        week,
        config.get("position_sd", {}),
        args.data_dir,
        "reports",
        config.get("model_projection_min_mae_improvement", 0.25),
        config.get("model_projection_min_weeks", 6),
        config.get("forecast_error_sd_prior", {}),
        args.max_projection_age_hours,
    )
    yahoo_mae = result["overall"]["yahoo"]["mae"]
    blend_mae = result["overall"]["blend"]["mae"]
    conclusion = "beats Yahoo" if result["beats_yahoo"] else "does not beat Yahoo"
    print(
        f"Accuracy report: {Path(args.output_dir) / 'accuracy_report.md'}\n"
        f"Blend {conclusion}: Yahoo MAE {yahoo_mae:.3f}, model MAE {blend_mae:.3f}, "
        f"{result['overall']['blend']['n']} samples across "
        f"{result['completed_weeks']} weeks."
    )


def _predict_command(args, config, store):
    target_week = args.week
    forecasts = forecast_walk_forward(
        store,
        config["league_id"],
        target_week,
        config.get("position_sd", {}),
        config.get("forecast_error_sd_prior", {}),
    )
    report_result = write_accuracy_report(
        Path(args.output_dir) / "accuracy_report.md",
        store,
        config["league_id"],
        target_week,
        config.get("position_sd", {}),
        args.data_dir,
        "reports",
        config.get("model_projection_min_mae_improvement", 0.25),
        config.get("model_projection_min_weeks", 6),
        config.get("forecast_error_sd_prior", {}),
        args.max_projection_age_hours,
    )
    path = (
        Path(args.output_dir)
        / f"league_{config['league_id']}_week_{target_week}_predictions.csv"
    )
    gate = write_predictions_csv(
        path,
        config["league_id"],
        target_week,
        forecasts,
        report_result["eligible_by_week"],
        config.get("model_projection_min_mae_improvement", 0.25),
        config.get("model_projection_min_weeks", 6),
    )
    print(
        f"Predictions: {path} ({len(forecasts.get(target_week, {}))} players; "
        f"trained through Week {target_week - 1}). "
        f"Projection quality gate: {'passed' if gate['gate_passed'] else 'not passed'}."
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Optional, local walk-forward projection analytics."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default="analytics_out")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ingest", help="Idempotently ingest canonical cached scrapes.")

    snapshot_parser = subparsers.add_parser(
        "snapshot", help="Save an explicitly requested pre-kickoff scrape."
    )
    snapshot_parser.add_argument("--week", type=int, required=True)
    snapshot_parser.add_argument(
        "--source", help="Use an existing cached snapshot instead of scraping."
    )

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Run the walk-forward accuracy evaluation."
    )
    evaluate_parser.add_argument("--week", type=int)
    evaluate_parser.add_argument(
        "--max-projection-age-hours",
        type=float,
        default=72,
        help="Age threshold for the stale-projection sensitivity report (default: 72).",
    )

    predict_parser = subparsers.add_parser(
        "predict", help="Write the optional forecast CSV for one target week."
    )
    predict_parser.add_argument("--week", type=int, required=True)
    predict_parser.add_argument(
        "--max-projection-age-hours",
        type=float,
        default=72,
        help="Age threshold for the stale-projection sensitivity report (default: 72).",
    )
    return parser


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    kickoff_times = _week_locks(config)
    store = AnalyticsStore(_database_path(args))
    try:
        if args.command == "ingest":
            _ingest_command(args, config, kickoff_times, store)
        elif args.command == "snapshot":
            _snapshot_command(args, config, kickoff_times, store)
        elif args.command == "evaluate":
            _evaluate_command(args, config, kickoff_times, store)
        elif args.command == "predict":
            _predict_command(args, config, store)
    finally:
        store.close()


if __name__ == "__main__":
    main()

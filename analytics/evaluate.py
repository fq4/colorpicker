import csv
import hashlib
from collections import defaultdict
from pathlib import Path

from analytics.models import (
    MODEL_NAMES,
    compute_metrics,
    derive_forecast_error_sd_priors,
    forecast_walk_forward,
)


MODEL_LABELS = {
    "yahoo": "Yahoo as-is",
    "trailing_mean": "Trailing mean (last four non-bye games)",
    "ewma": "EWMA (alpha 0.4)",
    "bias_adjusted": "Yahoo + position bias",
    "blend": "Position-specific Yahoo/EWMA blend",
}


def _format(value):
    return "n/a" if value is None else f"{value:.3f}"


def _metric_records(forecasts, actual_lookup):
    groups = defaultdict(lambda: {"actuals": [], "predictions": []})
    coverage = defaultdict(lambda: [0, 0])
    eligible_by_week = defaultdict(lambda: {"actuals": [], "yahoo": [], "blend": []})

    for week, week_forecasts in forecasts.items():
        for player_id, forecast in week_forecasts.items():
            actual = actual_lookup.get((player_id, week))
            if actual is None or actual["is_bye"] or forecast["yahoo_proj"] <= 0:
                continue
            actual_value = actual["value"]
            group = "zero_actual" if actual_value == 0 else "positive_actual"
            eligible_by_week[week]["actuals"].append(actual_value)
            eligible_by_week[week]["yahoo"].append(forecast["yahoo_proj"])
            eligible_by_week[week]["blend"].append(forecast["model_proj"])

            for model in MODEL_NAMES:
                prediction = (
                    forecast["yahoo_proj"] if model == "yahoo" else
                    forecast["trailing_mean"] if model == "trailing_mean" else
                    forecast["ewma"] if model == "ewma" else
                    forecast["bias_adjusted"] if model == "bias_adjusted" else
                    forecast["model_proj"]
                )
                for actual_group in ("all", group):
                    key = (model, week, forecast["position"], actual_group)
                    groups[key]["actuals"].append(actual_value)
                    groups[key]["predictions"].append(prediction)

            sd_keys = {
                "yahoo": "yahoo_sd",
                "trailing_mean": "trailing_mean_sd",
                "ewma": "ewma_sd",
                "bias_adjusted": "bias_adjusted_sd",
                "blend": "model_sd",
            }
            predictions = {
                "yahoo": forecast["yahoo_proj"],
                "trailing_mean": forecast["trailing_mean"],
                "ewma": forecast["ewma"],
                "bias_adjusted": forecast["bias_adjusted"],
                "blend": forecast["model_proj"],
            }
            for model in MODEL_NAMES:
                key = (model, week)
                coverage[key][1] += 1
                if abs(actual_value - predictions[model]) <= forecast[sd_keys[model]]:
                    coverage[key][0] += 1

    metrics = {}
    for key, values in groups.items():
        metrics[key] = compute_metrics(values["actuals"], values["predictions"])
    return metrics, coverage, eligible_by_week


def _snapshot_diff_evidence(store, league_id, max_week):
    actual_lookup = {
        (row["player_id"], row["target_week"]): dict(row)
        for row in store.actuals(league_id)
    }
    evidence = {}
    for week in range(1, max_week + 1):
        changed = []
        matched = 0
        for projection in store.clean_projections(league_id, week):
            actual = actual_lookup.get((projection["player_id"], week))
            if actual is None or actual["is_bye"]:
                continue
            matched += 1
            delta = actual["value"] - projection["value"]
            if abs(delta) >= 0.01:
                changed.append(
                    (
                        abs(delta),
                        projection["player_name"],
                        projection["value"],
                        actual["value"],
                    )
                )
        changed.sort(reverse=True)
        evidence[week] = {
            "matched": matched,
            "changed": len(changed),
            "examples": changed[:2],
        }
    return evidence


def _bye_rule_disagreements(actual_rows):
    return [
        row
        for row in actual_rows
        if row["is_rostered"] and row["is_bye"] != row["engine_is_bye"]
    ]


def _position_sd_coverage(forecasts, actual_lookup):
    coverage = defaultdict(lambda: [0, 0])
    for week, week_forecasts in forecasts.items():
        for player_id, forecast in week_forecasts.items():
            actual = actual_lookup.get((player_id, week))
            if (
                actual is None
                or actual["is_bye"]
                or forecast["yahoo_proj"] <= 0
            ):
                continue
            position_coverage = coverage[forecast["position"]]
            position_coverage[1] += 1
            if abs(actual["value"] - forecast["model_proj"]) <= forecast["model_sd"]:
                position_coverage[0] += 1
    return coverage


def _projection_age_metrics(forecasts, actual_lookup, max_age_hours):
    eligible = {"actuals": [], "yahoo": [], "blend": []}
    recent = {"actuals": [], "yahoo": [], "blend": []}
    old_count = 0
    unknown_age_count = 0
    for week, week_forecasts in forecasts.items():
        for player_id, forecast in week_forecasts.items():
            actual = actual_lookup.get((player_id, week))
            if (
                actual is None
                or actual["is_bye"]
                or forecast["yahoo_proj"] <= 0
            ):
                continue
            values = (
                actual["value"],
                forecast["yahoo_proj"],
                forecast["model_proj"],
            )
            for key, value in zip(("actuals", "yahoo", "blend"), values):
                eligible[key].append(value)

            age = forecast.get("projection_age_hours")
            if age is None:
                unknown_age_count += 1
            elif age > max_age_hours:
                old_count += 1
                continue
            for key, value in zip(("actuals", "yahoo", "blend"), values):
                recent[key].append(value)
    return {
        "all": {
            "n": len(eligible["actuals"]),
            "yahoo": compute_metrics(eligible["actuals"], eligible["yahoo"]),
            "blend": compute_metrics(eligible["actuals"], eligible["blend"]),
        },
        "excluding_old": {
            "n": len(recent["actuals"]),
            "yahoo": compute_metrics(recent["actuals"], recent["yahoo"]),
            "blend": compute_metrics(recent["actuals"], recent["blend"]),
        },
        "old_count": old_count,
        "unknown_age_count": unknown_age_count,
    }


def _table_row(cells):
    return "| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |"


def write_predictions_csv(
    output_path,
    league_id,
    target_week,
    forecasts,
    eligible_by_week,
    minimum_improvement,
    minimum_weeks,
):
    prior_weeks = [
        week for week, values in eligible_by_week.items()
        if week < target_week and values["actuals"]
    ]
    historical_actuals = [
        actual
        for week in prior_weeks
        for actual in eligible_by_week[week]["actuals"]
    ]
    historical_yahoo = [
        prediction
        for week in prior_weeks
        for prediction in eligible_by_week[week]["yahoo"]
    ]
    historical_blend = [
        prediction
        for week in prior_weeks
        for prediction in eligible_by_week[week]["blend"]
    ]
    yahoo_metrics = compute_metrics(historical_actuals, historical_yahoo)
    blend_metrics = compute_metrics(historical_actuals, historical_blend)
    improvement = (
        yahoo_metrics["mae"] - blend_metrics["mae"]
        if yahoo_metrics["mae"] is not None and blend_metrics["mae"] is not None
        else None
    )
    gate_passed = (
        len(prior_weeks) >= minimum_weeks
        and improvement is not None
        and improvement >= minimum_improvement
    )
    fieldnames = [
        "league_id",
        "player_id",
        "player_name",
        "position",
        "yahoo_proj",
        "model_proj",
        "model_sd",
        "model_version",
        "trained_through_week",
        "yahoo_sd",
        "blend_weight",
        "projection_weeks",
        "yahoo_oos_mae",
        "model_oos_mae",
        "projection_gate_passed",
    ]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for forecast in forecasts.get(target_week, {}).values():
            writer.writerow(
                {
                    "league_id": league_id,
                    "player_id": forecast["player_id"],
                    "player_name": forecast["player_name"],
                    "position": forecast["position"],
                    "yahoo_proj": f'{forecast["yahoo_proj"]:.3f}',
                    "model_proj": f'{forecast["model_proj"]:.3f}',
                    "model_sd": f'{forecast["model_sd"]:.3f}',
                    "model_version": forecast["model_version"],
                    "trained_through_week": target_week - 1,
                    "yahoo_sd": f'{forecast["yahoo_sd"]:.3f}',
                    "blend_weight": f'{forecast["blend_weight"]:.4f}',
                    "projection_weeks": len(prior_weeks),
                    "yahoo_oos_mae": (
                        f'{yahoo_metrics["mae"]:.4f}'
                        if yahoo_metrics["mae"] is not None else ""
                    ),
                    "model_oos_mae": (
                        f'{blend_metrics["mae"]:.4f}'
                        if blend_metrics["mae"] is not None else ""
                    ),
                    "projection_gate_passed": str(gate_passed).lower(),
                }
            )
    return {
        "projection_weeks": len(prior_weeks),
        "yahoo_oos_mae": yahoo_metrics["mae"],
        "model_oos_mae": blend_metrics["mae"],
        "improvement": improvement,
        "gate_passed": gate_passed,
    }


def write_accuracy_report(
    output_path,
    store,
    league_id,
    max_week,
    position_sd,
    data_directory,
    reports_directory,
    minimum_improvement,
    minimum_weeks,
    forecast_error_sd_prior=None,
    max_projection_age_hours=72,
):
    forecasts_before = forecast_walk_forward(store, league_id, max_week, position_sd)
    actual_rows = store.actuals(league_id)
    actual_lookup = {
        (row["player_id"], row["target_week"]): dict(row)
        for row in actual_rows
    }
    derived_priors, prior_summary = derive_forecast_error_sd_priors(
        forecasts_before, actual_lookup, position_sd
    )
    forecasts = forecast_walk_forward(
        store, league_id, max_week, position_sd, forecast_error_sd_prior
    )
    coverage_before = _position_sd_coverage(forecasts_before, actual_lookup)
    coverage_after = _position_sd_coverage(forecasts, actual_lookup)
    metrics, coverage, eligible_by_week = _metric_records(forecasts, actual_lookup)

    overall = {}
    for model in MODEL_NAMES:
        actuals = []
        predictions = []
        for week, week_forecasts in forecasts.items():
            for player_id, forecast in week_forecasts.items():
                actual = actual_lookup.get((player_id, week))
                if (
                    actual is None
                    or actual["is_bye"]
                    or forecast["yahoo_proj"] <= 0
                ):
                    continue
                value = (
                    forecast["yahoo_proj"] if model == "yahoo" else
                    forecast["trailing_mean"] if model == "trailing_mean" else
                    forecast["ewma"] if model == "ewma" else
                    forecast["bias_adjusted"] if model == "bias_adjusted" else
                    forecast["model_proj"]
                )
                actuals.append(actual["value"])
                predictions.append(value)
        overall[model] = compute_metrics(actuals, predictions)

    projection_age = _projection_age_metrics(
        forecasts, actual_lookup, max_projection_age_hours
    )
    age_all_beats_yahoo = (
        projection_age["all"]["yahoo"]["mae"]
        > projection_age["all"]["blend"]["mae"]
        if projection_age["all"]["yahoo"]["mae"] is not None
        and projection_age["all"]["blend"]["mae"] is not None
        else False
    )
    age_filtered_beats_yahoo = (
        projection_age["excluding_old"]["yahoo"]["mae"]
        > projection_age["excluding_old"]["blend"]["mae"]
        if projection_age["excluding_old"]["yahoo"]["mae"] is not None
        and projection_age["excluding_old"]["blend"]["mae"] is not None
        else False
    )
    age_conclusion_changed = age_all_beats_yahoo != age_filtered_beats_yahoo

    valid_weeks = [
        week
        for week, values in eligible_by_week.items()
        if week <= max_week and values["actuals"]
    ]
    overall_yahoo = overall["yahoo"]
    overall_blend = overall["blend"]
    improvement = (
        overall_yahoo["mae"] - overall_blend["mae"]
        if overall_yahoo["mae"] is not None and overall_blend["mae"] is not None
        else None
    )
    beats_yahoo = improvement is not None and improvement > 0

    inventory = store.snapshot_inventory(league_id)
    canonical_hashes = {
        row["content_sha256"]
        for row in store.connection.execute(
            "SELECT content_sha256 FROM snapshots WHERE league_id = ?",
            (str(league_id),),
        ).fetchall()
    }
    canonical_paths = {
        Path(row["source_path"]).resolve() for row in inventory
    }
    all_csv_files = list(Path(data_directory).glob("*.csv"))
    duplicate_file_count = 0
    other_untracked_count = 0
    for path in all_csv_files:
        if path.resolve() in canonical_paths:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in canonical_hashes:
            duplicate_file_count += 1
        else:
            other_untracked_count += 1
    snapshot_diffs = _snapshot_diff_evidence(store, league_id, max_week)
    bye_disagreements = _bye_rule_disagreements(actual_rows)
    target_snapshot = {}
    for week in range(1, max_week + 1):
        rows = store.clean_projections(league_id, week)
        if rows:
            target_snapshot[week] = rows[0]

    report_lines = [
        "# Cached projection model accuracy",
        "",
        "This is an exploratory, walk-forward evaluation of Yahoo projections and "
        "four simple alternatives. Each forecast uses only actuals and clean "
        "projections available before its target week; zero actuals are kept as "
        "availability outcomes, while detected team bye weeks are excluded from "
        "forecast training and scoring. One season and gaps in scrape timing limit "
        "the strength of conclusions.",
        "",
        "## Plain conclusion",
        "",
        (
            f"The position-specific blend {'beats' if beats_yahoo else 'does not beat'} "
            f"Yahoo on aggregate out-of-sample MAE: Yahoo "
            f"{_format(overall_yahoo['mae'])}, blend {_format(overall_blend['mae'])}, "
            f"improvement {_format(improvement)} points across "
            f"{overall['blend']['n']:,} fantasy-relevant player-week predictions "
            f"and {len(valid_weeks)} completed target weeks."
        ),
        (
            f"The configured projection gate requires at least {minimum_weeks} "
            f"completed weeks and an MAE improvement of {minimum_improvement:.3f} "
            "points. The model remains opt-in regardless of the result."
        ),
        "",
        "## Cache and report audit",
        "",
        f"`data/` contains {len(all_csv_files)} CSV files: "
        f"{len(inventory)} canonical cached scrapes are ingested and "
        f"{duplicate_file_count} additional files have identical content to those "
        f"scrapes. {other_untracked_count} other CSV files are not included. "
        "Each ingested row below reports the week at scrape time, local timestamp, "
        "and player count.",
        "",
        _table_row(("Week at scrape", "Snapshot time", "Players", "Pre-kickoff", "File")),
        _table_row(("---:", "---", "---:", ":---:", "---")),
    ]
    for row in inventory:
        report_lines.append(
            _table_row(
                (
                    row["week_at_scrape"],
                    row["snapshot_time"],
                    row["player_count"],
                    "yes" if row["is_pre_kickoff"] else "no",
                    Path(row["source_path"]).name,
                )
            )
        )

    report_lines.extend(
        [
            "",
            "### Numeric projection/actual marker-loss evidence",
            "",
            "In the installed `ffbot` scraper (`scraper.py`, weekly projection loop), "
            "a `*`-prefixed not-yet-played value and an unprefixed completed score "
            "are both converted to floats and written to the same `Week N` field; "
            "the separate projection/actual columns are commented out. The following "
            "cached-data comparison shows pre-kickoff values changing in later "
            "snapshots, so the numeric field alone cannot identify provenance.",
            "",
            _table_row(
                ("Target week", "Matched non-bye players", "Changed values", "Examples (clean Yahoo -> later value)")
            ),
            _table_row(("---:", "---:", "---:", "---")),
        ]
    )
    for week, details in snapshot_diffs.items():
        examples = "; ".join(
            f"{name}: {before:.2f} -> {after:.2f}"
            for _, name, before, after in details["examples"]
        ) or "none"
        report_lines.append(
            _table_row(
                (week, details["matched"], details["changed"], examples)
            )
        )

    report_lines.extend(
        [
            "",
            "### Bye-rule disagreements",
            "",
            "Analytics treats a missing or zero clean Yahoo projection as a bye; "
            "the engine excludes a week only when at least three rostered players "
            "from the NFL team all have zero or missing actuals. The table lists "
            "every rostered player-week where those rules disagree.",
            "",
            _table_row(
                (
                    "Week",
                    "Player",
                    "NFL team",
                    "Actual",
                    "Clean projection",
                    "Analytics bye",
                    "Engine bye",
                )
            ),
            _table_row(("---:", "---", "---", "---:", "---:", ":---:", ":---:")),
        ]
    )
    for row in bye_disagreements:
        report_lines.append(
            _table_row(
                (
                    row["target_week"],
                    row["player_name"],
                    row["nfl_team"] or "n/a",
                    f'{row["value"]:.2f}',
                    (
                        f'{row["clean_projection_value"]:.2f}'
                        if row["clean_projection_value"] is not None
                        else "missing"
                    ),
                    "yes" if row["is_bye"] else "no",
                    "yes" if row["engine_is_bye"] else "no",
                )
            )
        )
    if not bye_disagreements:
        report_lines.append(_table_row(("none", "", "", "", "", "", "")))

    try:
        report_files = sorted(
            path.name for path in Path(reports_directory).glob("*.md")
        )
    except OSError:
        report_files = []
    report_lines.extend(
        [
            "",
            f"`reports/` contains {len(report_files)} Markdown reports: "
            + (", ".join(report_files) if report_files else "none found")
            + ".",
            "",
            "### Usable clean projection snapshots by target week",
            "",
            _table_row(("Target week", "Latest clean snapshot time", "Week at scrape")),
            _table_row(("---:", "---", "---:")),
        ]
    )
    for week in range(1, max_week + 1):
        snapshot = target_snapshot.get(week)
        if snapshot is None:
            cells = (week, "none", "n/a")
        else:
            snapshot_meta = store.connection.execute(
                """
                SELECT week_at_scrape FROM snapshots
                WHERE league_id = ? AND snapshot_time = ?
                ORDER BY id DESC LIMIT 1
                """,
                (str(league_id), snapshot["snapshot_time"]),
            ).fetchone()
            cells = (week, snapshot["snapshot_time"], snapshot_meta["week_at_scrape"])
        report_lines.append(_table_row(cells))

    report_lines.extend(
        [
            "",
            "## Overall out-of-sample metrics",
            "",
            "Bias is forecast minus actual; positive bias means overprediction. "
            "The zero-actual counts are availability/DNP outcomes, not bye weeks.",
            "",
            _table_row(
                (
                    "Model",
                    "N",
                    "MAE",
                    "RMSE",
                    "Bias",
                    "Spearman",
                    "Zero actual N",
                    "Zero MAE",
                    "±1 SD coverage",
                )
            ),
            _table_row(("---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:")),
        ]
    )
    for model in MODEL_NAMES:
        metric = overall[model]
        zero_actuals = []
        zero_predictions = []
        for week, week_forecasts in forecasts.items():
            for player_id, forecast in week_forecasts.items():
                actual = actual_lookup.get((player_id, week))
                if (
                    actual is None
                    or actual["is_bye"]
                    or actual["value"] != 0
                    or forecast["yahoo_proj"] <= 0
                ):
                    continue
                prediction = (
                    forecast["yahoo_proj"] if model == "yahoo" else
                    forecast["trailing_mean"] if model == "trailing_mean" else
                    forecast["ewma"] if model == "ewma" else
                    forecast["bias_adjusted"] if model == "bias_adjusted" else
                    forecast["model_proj"]
                )
                zero_actuals.append(0.0)
                zero_predictions.append(prediction)
        zero_metrics = compute_metrics(zero_actuals, zero_predictions)
        coverage_values = [
            coverage[(model, week)]
            for week in valid_weeks
            if (model, week) in coverage
        ]
        covered = sum(value[0] for value in coverage_values)
        total = sum(value[1] for value in coverage_values)
        coverage_pct = covered / total if total else None
        report_lines.append(
            _table_row(
                (
                    MODEL_LABELS[model],
                    metric["n"],
                    _format(metric["mae"]),
                    _format(metric["rmse"]),
                    _format(metric["bias"]),
                    _format(metric["rank_correlation"]),
                    zero_metrics["n"],
                    _format(zero_metrics["mae"]),
                    _format(coverage_pct),
                )
            )
        )

    report_lines.extend(
        [
            "",
            "## Projection-age sensitivity",
            "",
            f"Projection age is hours from the selected clean snapshot to that "
            f"target week's kickoff. The stale threshold is {max_projection_age_hours:g} "
            "hours; records with unknown age are retained and counted separately.",
            "",
            _table_row(
                ("Population", "N", "Yahoo MAE", "Model MAE", "Model beats Yahoo")
            ),
            _table_row(("---", "---:", "---:", "---:", ":---:")),
            _table_row(
                (
                    "All eligible projections",
                    projection_age["all"]["n"],
                    _format(projection_age["all"]["yahoo"]["mae"]),
                    _format(projection_age["all"]["blend"]["mae"]),
                    "yes" if age_all_beats_yahoo else "no",
                )
            ),
            _table_row(
                (
                    f"Exclude age > {max_projection_age_hours:g} hours",
                    projection_age["excluding_old"]["n"],
                    _format(projection_age["excluding_old"]["yahoo"]["mae"]),
                    _format(projection_age["excluding_old"]["blend"]["mae"]),
                    "yes" if age_filtered_beats_yahoo else "no",
                )
            ),
            "",
            (
                "The Yahoo-versus-model conclusion "
                + ("changes" if age_conclusion_changed else "does not change")
                + " when older projections are excluded. "
                + f"{projection_age['old_count']} eligible player-week projections "
                + f"are older than the threshold; "
                + f"{projection_age['unknown_age_count']} have unknown age."
            ),
        ]
    )

    report_lines.extend(
        [
            "",
            "## Forecast-error SD priors by position",
            "",
            "Residuals are pooled from walk-forward blend forecasts and actuals, "
            "excluding bye weeks and non-positive Yahoo projections. The derived "
            "prior uses sample residual SD with at least three observations; "
            "otherwise it falls back to the existing score-spread `position_sd`. "
            "Coverage compares blend residuals against each player's shrunk blend "
            "SD before versus after applying `forecast_error_sd_prior`.",
            "",
            _table_row(
                (
                    "Position",
                    "N",
                    "Residual SD",
                    "MAE",
                    "Derived prior",
                    "Configured prior",
                    "Coverage before",
                    "Coverage after",
                )
            ),
            _table_row(
                ("---", "---:", "---:", "---:", "---:", "---:", "---:", "---:")
            ),
        ]
    )
    for position in sorted(prior_summary):
        summary = prior_summary[position]
        prior_configured = (forecast_error_sd_prior or {}).get(
            position,
            (forecast_error_sd_prior or {}).get(
                "default",
                position_sd.get(position, position_sd.get("default", 8.0)),
            ),
        )
        before = coverage_before.get(position, [0, 0])
        after = coverage_after.get(position, [0, 0])
        report_lines.append(
            _table_row(
                (
                    position,
                    summary["n"],
                    _format(summary["residual_sd"]),
                    _format(summary["mae"]),
                    _format(derived_priors[position]),
                    _format(float(prior_configured)),
                    _format(before[0] / before[1] if before[1] else None),
                    _format(after[0] / after[1] if after[1] else None),
                )
            )
        )

    coverage_summary = []
    for model in MODEL_NAMES:
        values = [
            coverage[(model, week)]
            for week in valid_weeks
            if (model, week) in coverage
        ]
        covered = sum(value[0] for value in values)
        total = sum(value[1] for value in values)
        if total:
            coverage_summary.append(
                f"{MODEL_LABELS[model]} {covered / total:.1%}"
            )
    report_lines.append(
        "\nEmpirical ±1 SD coverage: "
        + ", ".join(coverage_summary)
        + " (nominal target about 68%)."
        if coverage_summary
        else "\nInsufficient eligible data to estimate interval coverage."
    )

    for actual_group, title in (
        ("all", "By target week and position"),
        ("zero_actual", "Zero-actual / DNP outcomes by week and position"),
    ):
        report_lines.extend(
            [
                "",
                f"## {title}",
                "",
                _table_row(
                    ("Model", "Week", "Position", "N", "MAE", "RMSE", "Bias", "Spearman")
                ),
                _table_row(("---", "---:", "---", "---:", "---:", "---:", "---:", "---:")),
            ]
        )
        for key in sorted(metrics, key=lambda value: (value[0], value[1], value[2], value[3])):
            model, week, position, group = key
            if group != actual_group:
                continue
            metric = metrics[key]
            report_lines.append(
                _table_row(
                    (
                        MODEL_LABELS[model],
                        week,
                        position,
                        metric["n"],
                        _format(metric["mae"]),
                        _format(metric["rmse"]),
                        _format(metric["bias"]),
                        _format(metric["rank_correlation"]),
                    )
                )
            )

    report_lines.extend(
        [
            "",
            "## Walk-forward model and uncertainty details",
            "",
            "- Yahoo-as-is is the baseline. Trailing mean uses the last four non-bye "
            "actuals; EWMA uses alpha 0.4. The bias correction and blend are fit "
            "only from completed earlier weeks.",
            "- The blend weight is fit independently by position using earlier "
            "walk-forward forecasts. No regression model was added.",
            "- Yahoo and blend SDs estimate forecast error, not player score spread. "
            "At three or more prior non-bye residuals, the sample error SD is shrunk "
            "toward the analytics-only forecast-error SD prior; otherwise that "
            "position's configured prior is used.",
            "- ±1 SD calibration coverage is shown for Yahoo and blend. A nominal "
            "normal interval target is about 68%; this sample is too small to treat "
            "calibration as definitive.",
            "- Snapshot filenames and current-week Yahoo values do not preserve "
            "Yahoo's projection-versus-actual marker. Snapshot diffs confirm that "
            "values can change after kickoff, so only pre-kickoff snapshots were "
            "used as forecasts.",
            "",
        ]
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(report_lines), encoding="utf-8")
    return {
        "forecasts": forecasts,
        "overall": overall,
        "eligible_by_week": eligible_by_week,
        "improvement": improvement,
        "beats_yahoo": beats_yahoo,
        "completed_weeks": len(valid_weeks),
        "forecast_error_sd_prior": prior_summary,
        "projection_age": projection_age,
        "projection_age_conclusion_changed": age_conclusion_changed,
    }

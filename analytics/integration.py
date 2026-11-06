import csv
import math
from pathlib import Path

from analytics.store import normalize_player_id


def load_model_predictions(df, config, week, logger):
    output_path = (
        Path("analytics_out")
        / f"league_{config['league_id']}_week_{week}_predictions.csv"
    )
    info = {
        "prediction_file": str(output_path),
        "projection_source": "Yahoo",
        "sd_source": "position SD / historical actuals",
        "projection_enabled": bool(config.get("use_model_projections", False)),
        "sd_enabled": bool(config.get("use_model_sd", False)),
        "used_projections": False,
        "used_sd": False,
        "status": "model flags disabled",
        "player_values": [],
    }

    if not output_path.exists():
        info["status"] = "prediction file missing"
        logger.info(
            "Analytics source: Yahoo projections and current uncertainty behavior "
            f"({info['status']}: {output_path})"
        )
        return df, {}, info

    with output_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "league_id",
            "player_id",
            "position",
            "yahoo_proj",
            "model_proj",
            "model_sd",
            "yahoo_sd",
            "trained_through_week",
            "projection_weeks",
            "yahoo_oos_mae",
            "model_oos_mae",
        }
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Analytics prediction file is missing required columns: "
                f"{', '.join(sorted(missing))}"
            )
        rows = list(reader)

    if not rows:
        info["status"] = "prediction file contains no players"
        logger.info(f"Analytics source: Yahoo projections ({info['status']})")
        return df, {}, info

    expected_trained_through = int(week) - 1
    for row in rows:
        if str(row["league_id"]) != str(config["league_id"]):
            raise ValueError(
                f"Analytics prediction file belongs to league {row['league_id']}, "
                f"not {config['league_id']}."
            )
        if int(row["trained_through_week"]) != expected_trained_through:
            info["status"] = (
                f"stale predictions (trained through Week {row['trained_through_week']}; "
                f"expected {expected_trained_through})"
            )
            logger.info(
                f"Analytics source: Yahoo projections and current uncertainty behavior "
                f"({info['status']})"
            )
            return df, {}, info

    prediction_by_id = {}
    for row in rows:
        player_id = normalize_player_id(row["player_id"])
        if not player_id:
            raise ValueError("Analytics prediction file contains an empty player_id.")
        if player_id in prediction_by_id:
            raise ValueError(f"Duplicate player_id in analytics file: {player_id}")
        values = {}
        for column in (
            "yahoo_proj",
            "model_proj",
            "model_sd",
            "yahoo_sd",
            "projection_weeks",
            "yahoo_oos_mae",
            "model_oos_mae",
        ):
            try:
                values[column] = float(row[column])
            except (TypeError, ValueError):
                if column in {"yahoo_oos_mae", "model_oos_mae"} and not row[column]:
                    values[column] = None
                else:
                    raise ValueError(
                        f"Invalid {column} for player {player_id} in analytics file."
                    )
            if values[column] is not None and not math.isfinite(values[column]):
                raise ValueError(
                    f"Non-finite {column} for player {player_id} in analytics file."
                )
        prediction_by_id[player_id] = {
            **values,
            "player_name": row.get("player_name", ""),
            "position": row["position"],
        }

    first_prediction = next(iter(prediction_by_id.values()))
    projection_weeks = int(first_prediction["projection_weeks"])
    yahoo_mae = first_prediction["yahoo_oos_mae"]
    model_mae = first_prediction["model_oos_mae"]
    improvement = (
        yahoo_mae - model_mae
        if yahoo_mae is not None and model_mae is not None
        else None
    )
    minimum_weeks = int(config.get("model_projection_min_weeks", 6))
    minimum_improvement = float(
        config.get("model_projection_min_mae_improvement", 0.25)
    )
    quality_gate_passed = (
        projection_weeks >= minimum_weeks
        and improvement is not None
        and improvement >= minimum_improvement
    )

    use_projections = bool(config.get("use_model_projections", False))
    if use_projections and quality_gate_passed:
        week_column = f"Week {week}"
        ids = df["ID"].map(normalize_player_id)
        model_values = ids.map(
            {
                player_id: values["model_proj"]
                for player_id, values in prediction_by_id.items()
            }
        )
        df = df.copy()
        df.loc[model_values.notna(), week_column] = model_values[model_values.notna()]
        info["projection_source"] = "position-specific blend model (opt-in)"
        info["used_projections"] = True
        info["status"] = "projection quality gate passed"
    elif use_projections:
        info["status"] = (
            f"projection quality gate not passed ({projection_weeks} weeks, "
            f"MAE improvement {improvement if improvement is not None else 'n/a'}; "
            f"requires {minimum_weeks} weeks and {minimum_improvement:.3f} points)"
        )

    use_sd = bool(config.get("use_model_sd", False))
    if use_sd:
        sd_field = "model_sd" if info["used_projections"] else "yahoo_sd"
        sd_by_player = {
            player_id: values[sd_field]
            for player_id, values in prediction_by_id.items()
        }
        info["sd_source"] = (
            "blend-model forecast-error SD"
            if sd_field == "model_sd"
            else "Yahoo forecast-error SD"
        )
        info["used_sd"] = True
    else:
        sd_by_player = {}

    info["projection_weeks"] = projection_weeks
    info["yahoo_oos_mae"] = yahoo_mae
    info["model_oos_mae"] = model_mae
    info["mae_improvement"] = improvement
    info["quality_gate_passed"] = quality_gate_passed
    info["player_values"] = [
        {
            "player_id": player_id,
            "player_name": values["player_name"],
            "position": values["position"],
            "yahoo_proj": values["yahoo_proj"],
            "model_proj": values["model_proj"],
        }
        for player_id, values in prediction_by_id.items()
    ]
    logger.info(
        f"Analytics source: projections={info['projection_source']}; "
        f"uncertainty={info['sd_source'] if info['used_sd'] else 'existing behavior'}; "
        f"quality gate={'passed' if quality_gate_passed else 'not passed'}"
    )
    return df, sd_by_player, info

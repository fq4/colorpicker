import math
from collections import defaultdict
from statistics import stdev

from analytics import MODEL_VERSION
from analytics.store import normalize_player_id


FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
MODEL_NAMES = ("yahoo", "trailing_mean", "ewma", "bias_adjusted", "blend")
EWMA_ALPHA = 0.4
TRAILING_GAMES = 4
SHRINKAGE_PRIOR_GAMES = 5


def _position_sd(position_sd, position):
    value = position_sd.get(position, position_sd.get("default", 8.0))
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return 8.0


def _forecast_error_prior_sd(position_sd, forecast_error_sd_prior, position):
    if forecast_error_sd_prior and (
        position in forecast_error_sd_prior or "default" in forecast_error_sd_prior
    ):
        return _position_sd(forecast_error_sd_prior, position)
    return _position_sd(position_sd, position)


def _error_sd(residuals, prior_sd):
    if len(residuals) < 3:
        return prior_sd
    mean = sum(residuals) / len(residuals)
    sample_variance = sum((value - mean) ** 2 for value in residuals) / (
        len(residuals) - 1
    )
    sample_sd = math.sqrt(sample_variance)
    weight = len(residuals) / (len(residuals) + SHRINKAGE_PRIOR_GAMES)
    return max(0.1, weight * sample_sd + (1 - weight) * prior_sd)


def derive_forecast_error_sd_priors(
    forecasts,
    actual_lookup,
    position_sd,
    minimum_residuals=3,
):
    residuals_by_position = defaultdict(list)
    for week, week_forecasts in forecasts.items():
        for player_id, forecast in week_forecasts.items():
            actual = actual_lookup.get((player_id, week))
            if (
                actual is None
                or actual["is_bye"]
                or forecast["yahoo_proj"] <= 0
            ):
                continue
            residuals_by_position[forecast["position"]].append(
                actual["value"] - forecast["model_proj"]
            )

    priors = {}
    summary = {}
    for position in sorted(FANTASY_POSITIONS):
        residuals = residuals_by_position[position]
        residual_sd = stdev(residuals) if len(residuals) >= 2 else None
        priors[position] = (
            residual_sd
            if len(residuals) >= minimum_residuals
            else _position_sd(position_sd, position)
        )
        summary[position] = {
            "n": len(residuals),
            "residual_sd": residual_sd,
            "mae": (
                sum(abs(residual) for residual in residuals) / len(residuals)
                if residuals
                else None
            ),
            "prior_sd": priors[position],
        }
    return priors, summary


def _history_values(actuals_by_player, player_id, before_week):
    return [
        row["value"]
        for row in actuals_by_player.get(player_id, ())
        if row["target_week"] < before_week and not row["is_bye"]
    ]


def _ewma(values, fallback):
    if not values:
        return fallback
    result = values[0]
    for value in values[1:]:
        result = EWMA_ALPHA * value + (1 - EWMA_ALPHA) * result
    return result


def _fit_blend_weights(history_rows):
    grouped = defaultdict(list)
    for row in history_rows:
        if row["actual"] is None or row["is_bye"]:
            continue
        grouped[row["position"]].append(row)

    weights = {}
    for position, rows in grouped.items():
        numerator = 0.0
        denominator = 0.0
        for row in rows:
            difference = row["yahoo"] - row["ewma"]
            numerator += difference * (row["actual"] - row["ewma"])
            denominator += difference * difference
        weights[position] = (
            min(1.0, max(0.0, numerator / denominator))
            if denominator > 0
            else 0.5
        )
    return weights


def forecast_walk_forward(
    store,
    league_id,
    max_week,
    position_sd,
    forecast_error_sd_prior=None,
):
    actual_rows = store.actuals(league_id)
    actuals_by_player = defaultdict(list)
    actual_lookup = {}
    for row in actual_rows:
        value = dict(row)
        actuals_by_player[value["player_id"]].append(value)
        actual_lookup[(value["player_id"], value["target_week"])] = value

    projections_by_week = {}
    for week in range(1, max_week + 1):
        rows = store.clean_projections(league_id, week)
        projections_by_week[week] = [
            dict(row)
            for row in rows
            if row["position"] in FANTASY_POSITIONS and row["player_id"]
        ]

    forecasts_by_week = {}
    residuals_by_player = defaultdict(
        lambda: {model: [] for model in MODEL_NAMES}
    )
    for target_week in range(1, max_week + 1):
        history_rows = []
        for prior_week in range(1, target_week):
            for row in projections_by_week[prior_week]:
                actual = actual_lookup.get((row["player_id"], prior_week))
                prior_forecast = forecasts_by_week.get(prior_week, {}).get(
                    row["player_id"]
                )
                if actual is None or prior_forecast is None:
                    continue
                history_rows.append(
                    {
                        "position": row["position"],
                        "yahoo": row["value"],
                        "ewma": prior_forecast["ewma"],
                        "actual": actual["value"],
                        "is_bye": bool(actual["is_bye"]),
                    }
                )

        biases = defaultdict(list)
        for prior_week in range(1, target_week):
            for row in projections_by_week[prior_week]:
                actual = actual_lookup.get((row["player_id"], prior_week))
                if actual is None or actual["is_bye"]:
                    continue
                biases[row["position"]].append(actual["value"] - row["value"])
        position_bias = {
            position: sum(values) / len(values)
            for position, values in biases.items()
            if values
        }
        blend_weights = _fit_blend_weights(history_rows)

        week_forecasts = {}
        for row in projections_by_week[target_week]:
            player_id = normalize_player_id(row["player_id"])
            position = row["position"]
            yahoo = float(row["value"])
            historical_values = _history_values(
                actuals_by_player, player_id, target_week
            )
            ewma_value = _ewma(historical_values, yahoo)
            trailing_values = historical_values[-TRAILING_GAMES:]
            trailing_value = (
                sum(trailing_values) / len(trailing_values)
                if trailing_values
                else yahoo
            )
            bias_adjusted = yahoo + position_bias.get(position, 0.0)
            blend_weight = blend_weights.get(position, 0.5)
            blend = blend_weight * yahoo + (1 - blend_weight) * ewma_value
            prior_sd = _forecast_error_prior_sd(
                position_sd, forecast_error_sd_prior or {}, position
            )
            player_residuals = residuals_by_player[player_id]
            week_forecasts[player_id] = {
                "league_id": str(league_id),
                "player_id": player_id,
                "player_name": row["player_name"],
                "position": position,
                "target_week": target_week,
                "snapshot_time": row["snapshot_time"],
                "projection_age_hours": row.get("projection_age_hours"),
                "yahoo_proj": yahoo,
                "trailing_mean": trailing_value,
                "ewma": ewma_value,
                "bias_adjusted": bias_adjusted,
                "model_proj": blend,
                "blend_weight": blend_weight,
                "yahoo_sd": _error_sd(player_residuals["yahoo"], prior_sd),
                "model_sd": _error_sd(player_residuals["blend"], prior_sd),
                "trailing_mean_sd": _error_sd(
                    player_residuals["trailing_mean"], prior_sd
                ),
                "ewma_sd": _error_sd(player_residuals["ewma"], prior_sd),
                "bias_adjusted_sd": _error_sd(
                    player_residuals["bias_adjusted"], prior_sd
                ),
                "model_version": MODEL_VERSION,
                "trained_through_week": target_week - 1,
                "residual_count": len(player_residuals["blend"]),
            }
        forecasts_by_week[target_week] = week_forecasts

        for player_id, forecast in week_forecasts.items():
            actual = actual_lookup.get((player_id, target_week))
            if actual is None or actual["is_bye"]:
                continue
            model_values = {
                "yahoo": forecast["yahoo_proj"],
                "trailing_mean": forecast["trailing_mean"],
                "ewma": forecast["ewma"],
                "bias_adjusted": forecast["bias_adjusted"],
                "blend": forecast["model_proj"],
            }
            for model, prediction in model_values.items():
                residuals_by_player[player_id][model].append(
                    actual["value"] - prediction
                )

    return forecasts_by_week


def compute_metrics(actuals, predictions):
    if not actuals:
        return {
            "n": 0,
            "mae": None,
            "rmse": None,
            "bias": None,
            "rank_correlation": None,
        }
    errors = [prediction - actual for actual, prediction in zip(actuals, predictions)]
    return {
        "n": len(errors),
        "mae": sum(abs(error) for error in errors) / len(errors),
        "rmse": math.sqrt(sum(error * error for error in errors) / len(errors)),
        "bias": sum(errors) / len(errors),
        "rank_correlation": _spearman(actuals, predictions),
    }


def _ranks(values):
    sorted_values = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(sorted_values):
        end = start + 1
        while end < len(sorted_values) and sorted_values[end][1] == sorted_values[start][1]:
            end += 1
        rank = (start + 1 + end) / 2
        for index in range(start, end):
            ranks[sorted_values[index][0]] = rank
        start = end
    return ranks


def _spearman(actuals, predictions):
    if len(actuals) < 2:
        return None
    actual_ranks = _ranks(actuals)
    prediction_ranks = _ranks(predictions)
    actual_mean = sum(actual_ranks) / len(actual_ranks)
    prediction_mean = sum(prediction_ranks) / len(prediction_ranks)
    numerator = sum(
        (actual_rank - actual_mean) * (prediction_rank - prediction_mean)
        for actual_rank, prediction_rank in zip(actual_ranks, prediction_ranks)
    )
    actual_ss = sum((rank - actual_mean) ** 2 for rank in actual_ranks)
    prediction_ss = sum((rank - prediction_mean) ** 2 for rank in prediction_ranks)
    denominator = math.sqrt(actual_ss * prediction_ss)
    return numerator / denominator if denominator else None

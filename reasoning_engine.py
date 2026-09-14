"""Deterministic complete-roster transaction reasoning.

This layer evaluates a proposed move as a state transition. It deliberately
owns calculations and legality checks; report prose and the optional LLM
remain downstream consumers of these structured results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd


STARTER_UPGRADE = "STARTER_UPGRADE"
NO_STARTER_CHANGE = "NO_STARTER_CHANGE"
STARTER_DOWNGRADE = "STARTER_DOWNGRADE"

TRANSACTION_CLASSES = {
    "STARTER_UPGRADE",
    "BENCH_UPGRADE",
    "DEPTH",
    "BYE_COVERAGE",
    "STREAMING",
    "SPECULATIVE_UPSIDE",
    "ROSTER_CONSOLIDATION",
    "NO_MEANINGFUL_IMPROVEMENT",
}

CONFIDENCE_LEVELS = {"MUST_DO", "STRONG", "LEAN", "SPECULATIVE", "DO_NOT_MAKE"}


@dataclass
class RosterValidation:
    legal: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TransactionEvaluation:
    legal: bool
    current_week_delta: float
    ros_delta: float
    vor_delta: float
    starter_impact: str
    classification: str
    confidence: str
    drop_resistance: float
    positional_scarcity: float
    opportunity_cost: float
    hold_current_week: float
    hold_ros: float
    alternative_drops: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    ledger: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def validate_roster(
    roster: pd.DataFrame,
    positions_config: dict,
    *,
    require_bench_depth: bool = True,
) -> RosterValidation:
    """Validate size, uniqueness, startability, eligibility, and depth."""
    from decision_engine import _roster_capacity, flag_bench_depth_gaps, get_starting_slots, parse_positions, recommend_lineup

    errors: list[str] = []
    warnings: list[str] = []
    slots = parse_positions(positions_config["positions"])
    if roster.empty:
        errors.append("Roster is empty")
        return RosterValidation(False, errors, warnings)
    max_size = _roster_capacity(roster, positions_config)
    if len(roster) > max_size:
        errors.append(f"Roster has {len(roster)} players but capacity is {max_size}")

    names = roster["Name"].astype(str)
    duplicates = names[names.duplicated()].unique().tolist()
    if duplicates:
        errors.append(f"Duplicate players: {', '.join(duplicates)}")

    week_columns = [c for c in roster.columns if str(c).startswith("Week ")]
    week = int(week_columns[0].split()[1]) if week_columns else 1
    lineup = recommend_lineup(roster, roster, positions_config, week)
    required_starters = len(get_starting_slots(slots))
    if len(lineup.starters) < required_starters:
        errors.append(
            f"Only {len(lineup.starters)} legal starters available; {required_starters} required"
        )
    for starter in lineup.starters:
        allowed = _slot_positions(starter.slot)
        player_positions = {p.strip() for p in str(starter.position).split(",")}
        if allowed and not player_positions.intersection(allowed):
            errors.append(f"{starter.player} is not eligible for {starter.slot}")

    if require_bench_depth:
        for warning in flag_bench_depth_gaps(roster, positions_config):
            errors.append(f"Required bench depth missing at {warning.position}")

    return RosterValidation(not errors, errors, warnings)


def evaluate_transaction(
    df: pd.DataFrame,
    my_roster: pd.DataFrame,
    add_name: Optional[str],
    drop_name: Optional[str],
    positions_config: dict,
    current_week: int,
    config: Optional[dict] = None,
) -> TransactionEvaluation:
    """Evaluate a complete add/drop against HOLD using authoritative calculations."""
    from decision_engine import _lookup_player, recommend_lineup, simulate_post_move_roster

    config = config or {}
    before_lineup = recommend_lineup(df, my_roster, positions_config, current_week)
    hold_week = before_lineup.total_projection
    hold_ros = _roster_metric(my_roster, df, "Remaining")
    hold_vor = _sum_value(my_roster, "VOR")

    errors: list[str] = []
    if drop_name and drop_name not in set(my_roster["Name"].astype(str)):
        errors.append(f"Drop player is not on roster: {drop_name}")
    if add_name and _lookup_player(df, add_name, None) is None:
        errors.append(f"Add player is not in player data: {add_name}")

    from decision_engine import AddDropRecommendation
    rec = AddDropRecommendation(action="add_drop", add=add_name, drop=drop_name)
    after = simulate_post_move_roster(df, my_roster, [rec], current_week)
    validation = validate_roster(after, positions_config, require_bench_depth=False)
    errors.extend(validation.errors)

    after_lineup = recommend_lineup(df, after, positions_config, current_week)
    after_week = after_lineup.total_projection
    after_ros = _roster_metric(after, df, "Remaining")
    after_vor = _sum_value(after, "VOR")
    week_delta = after_week - hold_week
    ros_delta = after_ros - hold_ros
    vor_delta = after_vor - hold_vor

    starter_impact = _starter_impact(before_lineup, after_lineup, week_delta)
    add_row = _lookup_player(df, add_name, None) if add_name else None
    position = str(add_row.get("Position", "")) if add_row is not None else ""
    scarcity = _scarcity_score(my_roster, df, position, current_week)
    drop_resistance = _drop_resistance(my_roster, drop_name, position, current_week)
    opportunity_cost = max(0.0, -ros_delta) + max(0.0, -vor_delta)
    alternatives = _alternative_drops(my_roster, position, drop_name, current_week)
    classification = _classify(
        week_delta, ros_delta, starter_impact, position, add_row, config
    )
    confidence = _confidence(errors, week_delta, ros_delta, opportunity_cost, classification)
    contradictions = _contradictions(week_delta, vor_delta, classification, errors)
    legal = not errors
    if not legal:
        classification = "NO_MEANINGFUL_IMPROVEMENT"
        confidence = "DO_NOT_MAKE"

    ledger = {
        "action": f"Add {add_name or 'none'} / Drop {drop_name or 'none'}",
        "current_week": {"before": hold_week, "after": after_week, "delta": week_delta},
        "ros": {"before": hold_ros, "after": after_ros, "delta": ros_delta},
        "vor": {"before": hold_vor, "after": after_vor, "delta": vor_delta},
        "starter_impact": starter_impact,
        "opportunity_cost": opportunity_cost,
        "drop_resistance": drop_resistance,
        "positional_scarcity": scarcity,
        "alternatives": alternatives,
        "decision": classification,
        "confidence": confidence,
        "contradictions": contradictions,
    }
    return TransactionEvaluation(
        legal=legal,
        current_week_delta=week_delta,
        ros_delta=ros_delta,
        vor_delta=vor_delta,
        starter_impact=starter_impact,
        classification=classification,
        confidence=confidence,
        drop_resistance=drop_resistance,
        positional_scarcity=scarcity,
        opportunity_cost=opportunity_cost,
        hold_current_week=hold_week,
        hold_ros=hold_ros,
        alternative_drops=alternatives,
        contradictions=contradictions,
        ledger=ledger,
        errors=errors,
    )


def _sum_value(roster: pd.DataFrame, column: str) -> float:
    if column not in roster.columns:
        return 0.0
    return float(pd.to_numeric(roster[column], errors="coerce").fillna(0).sum())


def _roster_metric(roster: pd.DataFrame, df: pd.DataFrame, column: str) -> float:
    if column in roster.columns:
        return _sum_value(roster, column)
    if "Name" not in roster.columns or column not in df.columns:
        return 0.0
    names = set(roster["Name"].astype(str))
    return _sum_value(df[df["Name"].astype(str).isin(names)], column)


def _slot_positions(slot: str) -> set[str]:
    from decision_engine import SLOT_POSITION_MAP
    for key, positions in SLOT_POSITION_MAP.items():
        if slot == key or slot.startswith(key):
            return positions
    return set()


def _starter_impact(before, after, delta: float) -> str:
    before_names = {slot.player for slot in before.starters}
    after_names = {slot.player for slot in after.starters}
    if delta > 0.01 and before_names != after_names:
        return STARTER_UPGRADE
    if delta < -0.01:
        return STARTER_DOWNGRADE
    return NO_STARTER_CHANGE


def _scarcity_score(roster: pd.DataFrame, df: pd.DataFrame, position: str, week: int) -> float:
    if not position:
        return 0.0
    roster_count = sum(position in {p.strip() for p in str(value).split(",")} for value in roster["Position"])
    available = df[df["Owner"].fillna("").str.lower().eq("free agent")]
    available_count = sum(position in {p.strip() for p in str(value).split(",")} for value in available["Position"])
    return float(max(0, 3 - roster_count) + (1.0 if available_count < 3 else 0.0))


def _drop_resistance(roster: pd.DataFrame, drop_name: Optional[str], position: str, week: int) -> float:
    if not drop_name or drop_name not in set(roster["Name"].astype(str)):
        return 0.0
    row = roster[roster["Name"] == drop_name].iloc[0]
    projection = _number(row.get(f"Week {week}"))
    vor = _number(row.get("VOR"))
    ros = _number(row.get("Remaining"))
    same_position = roster[roster["Position"].apply(lambda value: position in {p.strip() for p in str(value).split(",")})]
    depth_bonus = max(0.0, 2.0 - max(0, len(same_position) - 2))
    return round(max(0.0, projection / 10.0 + ros / 100.0 + vor / 10.0 + depth_bonus), 3)


def _number(value: Any) -> float:
    converted = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(converted) else float(converted)


def _alternative_drops(roster: pd.DataFrame, position: str, drop_name: Optional[str], week: int) -> list[str]:
    if not position:
        return []
    candidates = roster[roster["Name"].astype(str) != str(drop_name)].copy()
    candidates = candidates[candidates["Position"].apply(lambda value: position in {p.strip() for p in str(value).split(",")})]
    candidates["_value"] = pd.to_numeric(candidates.get("VOR", 0), errors="coerce").fillna(0)
    return candidates.sort_values("_value")["Name"].astype(str).head(3).tolist()


def _classify(week_delta, ros_delta, starter_impact, position, add_row, config) -> str:
    if starter_impact == STARTER_UPGRADE:
        return "STARTER_UPGRADE"
    if week_delta <= 0.01 and ros_delta <= 0.01:
        return "NO_MEANINGFUL_IMPROVEMENT"
    streaming = set(config.get("streaming_positions", ["K", "DEF"]))
    if position in streaming and week_delta > 0:
        return "STREAMING"
    if ros_delta > 0 and week_delta <= 0:
        return "SPECULATIVE_UPSIDE"
    if ros_delta > 0:
        return "BENCH_UPGRADE"
    return "DEPTH"


def _confidence(errors, week_delta, ros_delta, opportunity_cost, classification) -> str:
    if errors or classification == "NO_MEANINGFUL_IMPROVEMENT":
        return "DO_NOT_MAKE"
    if week_delta > 2 and ros_delta > 0 and opportunity_cost < 1:
        return "MUST_DO"
    if week_delta > 0.5 or ros_delta > 10:
        return "STRONG"
    if opportunity_cost > 10:
        return "SPECULATIVE"
    return "LEAN"


def _contradictions(week_delta, vor_delta, classification, errors) -> list[str]:
    contradictions = list(errors)
    if classification == "STARTER_UPGRADE" and week_delta <= 0:
        contradictions.append("Starter upgrade classification conflicts with non-positive current-week delta")
    if "NO_MEANINGFUL_IMPROVEMENT" == classification and (week_delta > 0.01 or vor_delta > 0.01):
        contradictions.append("No-improvement classification conflicts with positive calculated values")
    return contradictions

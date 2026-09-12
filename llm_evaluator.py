"""Optional independent LLM assessment for deterministic weekly reports.

This module is deliberately downstream of the decision engine. It serializes
structured inputs, calls a provider through a small interface, and validates
untrusted JSON before the result can reach the human-readable report.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import pandas as pd

from decision_engine import LineupRecommendation, RecommendationReport


SYSTEM_PROMPT = """You are an independent fantasy football analyst reviewing a deterministic fantasy decision engine.
Find useful decisions, not agreement for its own sake. Treat supplied player data as authoritative and the engine output as a proposal that may be wrong.
Evaluate current-week lineup value separately from rest-of-season value. Evaluate the proposed add and drop independently. Consider VOR, positional scarcity, roster construction, bench depth, byes, ownership, streaming value, and opportunity cost.
Do not invent players, stats, injuries, schedules, roles, or settings. If information is unavailable, say so. The evaluator may recommend NO TRANSACTION.
Detect contradictions between the engine's reasons, thresholds, action plan, and resulting lineup. Disagreements are signals, not automatic errors.
Distinguish facts, inferences, and speculation. You have no authority to execute transactions or change lineups. Return JSON only matching the requested schema.
"""


class EvaluatorError(Exception):
    """Base error for provider failures or invalid model output."""


class EvaluatorProvider(Protocol):
    """Minimal provider contract; fake providers can implement this directly."""

    def complete(self, system_prompt: str, user_payload: str) -> str:
        ...


@dataclass
class EvaluationContext:
    """The complete structured input sent to an evaluator provider."""

    league_context: dict[str, Any]
    players: list[dict[str, Any]]
    engine_output: dict[str, Any]
    player_universe: set[str]


@dataclass
class LLMEvaluation:
    """Validated model output safe for report formatting."""

    data: dict[str, Any]

    @property
    def overall_assessment(self) -> str:
        return self.data["overall_assessment"]


class OpenAICompatibleProvider:
    """Small HTTP adapter for OpenAI-compatible hosted or local endpoints."""

    def __init__(self, model: str, endpoint: str, api_key_env: str, timeout: float = 60):
        self.model = model
        self.endpoint = endpoint
        self.api_key = os.environ.get(api_key_env, "")
        self.timeout = timeout

    def complete(self, system_prompt: str, user_payload: str) -> str:
        if not self.model:
            raise EvaluatorError("LLM evaluator model is not configured")
        local_endpoint = self.endpoint.startswith(("http://localhost", "http://127.0.0.1"))
        if not self.api_key and not local_endpoint:
            raise EvaluatorError("LLM evaluator API key is not configured")

        request_body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=request_body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise EvaluatorError(f"LLM provider request failed: {exc}") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EvaluatorError("LLM provider returned an unexpected response") from exc


class IndependentLLMEvaluator:
    """Run and validate one independent assessment without execution authority."""

    def __init__(self, provider: EvaluatorProvider):
        self.provider = provider

    def evaluate(self, context: EvaluationContext) -> LLMEvaluation:
        response = self.provider.complete(SYSTEM_PROMPT, build_user_prompt(context))
        try:
            data = json.loads(response)
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvaluatorError("LLM response was not valid JSON") from exc
        validate_evaluation(data, context.player_universe)
        return LLMEvaluation(data=data)


def build_evaluation_context(
    df: pd.DataFrame,
    report: RecommendationReport,
    config: dict[str, Any],
    current_week: int,
) -> EvaluationContext:
    """Build a bounded structured payload without scraping or exposing secrets."""
    max_candidates = int(config.get("llm_evaluator", {}).get("max_candidates", 150))
    roster_names = set(report.my_roster["Name"].astype(str)) if report.my_roster is not None else set()
    recommendation_names = _recommendation_names(report)
    selected_names = roster_names | recommendation_names

    candidates = df.copy()
    candidates["_is_roster"] = candidates["Name"].astype(str).isin(roster_names)
    candidates["_is_recommended"] = candidates["Name"].astype(str).isin(recommendation_names)
    week_col = f"Week {current_week}"
    candidates["_projection"] = pd.to_numeric(candidates.get(week_col, 0), errors="coerce").fillna(0)
    candidates["_vor"] = pd.to_numeric(candidates.get("VOR", 0), errors="coerce").fillna(0)
    candidates = candidates.sort_values(
        by=["_is_roster", "_is_recommended", "_projection", "_vor"],
        ascending=[False, False, False, False],
    )
    chosen = candidates[candidates["Name"].astype(str).isin(selected_names)].copy()
    remaining = candidates[~candidates["Name"].astype(str).isin(selected_names)]
    slots_left = max(0, max_candidates - len(chosen))
    chosen = pd.concat([chosen, remaining.head(slots_left)], ignore_index=True)
    players = [_serialize_player(row, current_week) for _, row in chosen.iterrows()]

    universe = {player["name"] for player in players}
    league_context = {
        "week": current_week,
        "season": config.get("season"),
        "scoring_type": config.get("scoring_type"),
        "waiver_type": config.get("waiver_type"),
        "waiver_priority": config.get("waiver_priority"),
        "roster_slots": config.get("positions"),
        "vor_thresholds": config.get("min_vor_gain_to_recommend_add", 5.0),
        "bench_depth_minimums": config.get("bench_depth_minimums", {}),
        "min_usable_projection": config.get("min_usable_projection", 1.0),
        "locked_positions": config.get("locked_positions", []),
    }
    return EvaluationContext(
        league_context=league_context,
        players=players,
        engine_output=_serialize_report(report),
        player_universe=universe,
    )


def build_user_prompt(context: EvaluationContext) -> str:
    """Place raw facts before the deterministic proposal to reduce anchoring."""
    schema = {
        "overall_assessment": "string",
        "engine_score": "integer 0-100",
        "lineup": {"agreement": "agree|modify|reject", "recommended_changes": [], "reasoning": "string"},
        "transactions": [{
            "action": "add_drop|hold|fa_add|waiver_claim",
            "add_player": "string|null", "drop_player": "string|null",
            "classification": "must_make|strong|optional|speculative|do_not_make",
            "confidence": "high|medium|low",
            "current_week_projection_delta": "number",
            "vor_delta": "number",
            "rest_of_season_assessment": "string",
            "starting_lineup_impact": "string",
            "reasoning": "string",
        }],
        "engine_agreements": [], "engine_corrections": [],
        "missed_opportunities": [], "holds": [], "strategic_opportunities": [],
        "data_limitations": [], "bottom_line": "string",
    }
    return "\n".join(
        [
            "Analyze these structured facts. The player list is the complete universe you may reference.",
            "LEAGUE_CONTEXT_JSON:", json.dumps(context.league_context, ensure_ascii=False, sort_keys=True),
            "PLAYER_DATA_JSON:", json.dumps(context.players, ensure_ascii=False, sort_keys=True),
            "DETERMINISTIC_ENGINE_OUTPUT_JSON:", json.dumps(context.engine_output, ensure_ascii=False, sort_keys=True),
            "REQUIRED_OUTPUT_SCHEMA_JSON:", json.dumps(schema, ensure_ascii=False, sort_keys=True),
        ]
    )


def validate_evaluation(data: Any, player_universe: set[str]) -> None:
    """Reject malformed output and player references outside the supplied data."""
    if not isinstance(data, dict):
        raise EvaluatorError("LLM evaluation must be a JSON object")
    required = {"overall_assessment", "engine_score", "lineup", "transactions", "bottom_line"}
    missing = required - data.keys()
    if missing:
        raise EvaluatorError(f"LLM evaluation is missing fields: {', '.join(sorted(missing))}")
    if not isinstance(data["overall_assessment"], str) or not isinstance(data["bottom_line"], str):
        raise EvaluatorError("LLM assessment and bottom_line must be strings")
    score = data["engine_score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
        raise EvaluatorError("engine_score must be a number from 0 to 100")
    lineup = data["lineup"]
    if not isinstance(lineup, dict) or lineup.get("agreement") not in {"agree", "modify", "reject"}:
        raise EvaluatorError("lineup.agreement must be agree, modify, or reject")
    transactions = data["transactions"]
    if not isinstance(transactions, list):
        raise EvaluatorError("transactions must be a list")
    allowed_actions = {"add_drop", "hold", "fa_add", "waiver_claim"}
    allowed_classes = {"must_make", "strong", "optional", "speculative", "do_not_make"}
    allowed_confidence = {"high", "medium", "low"}
    for index, transaction in enumerate(transactions):
        if not isinstance(transaction, dict):
            raise EvaluatorError(f"transaction {index} must be an object")
        if transaction.get("action") not in allowed_actions:
            raise EvaluatorError(f"transaction {index} has an invalid action")
        if transaction.get("classification") not in allowed_classes:
            raise EvaluatorError(f"transaction {index} has an invalid classification")
        if transaction.get("confidence") not in allowed_confidence:
            raise EvaluatorError(f"transaction {index} has invalid confidence")
        for field in ("add_player", "drop_player"):
            player = transaction.get(field)
            if player is not None and player not in player_universe:
                raise EvaluatorError(f"transaction {index} references unsupported player: {player}")
        for field in ("current_week_projection_delta", "vor_delta"):
            value = transaction.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise EvaluatorError(f"transaction {index}.{field} must be a finite number")


def _recommendation_names(report: RecommendationReport) -> set[str]:
    names: set[str] = set()
    for rec in [*report.add_drop_recs, *report.low_value_recs]:
        if rec.add:
            names.add(str(rec.add))
        if rec.drop:
            names.add(str(rec.drop))
    if report.action_plan:
        for rec in report.action_plan.moves + report.action_plan.skipped_recs:
            if rec.add:
                names.add(str(rec.add))
            if rec.drop:
                names.add(str(rec.drop))
    return names


def _serialize_player(row: pd.Series, current_week: int) -> dict[str, Any]:
    def value(field: str) -> Any:
        raw = row.get(field)
        if pd.isna(raw):
            return None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return str(raw)

    weeks = {
        col: value(col)
        for col in row.index
        if str(col).startswith("Week ") and str(col)[5:].isdigit()
    }
    return {
        "id": value("ID"), "name": value("Name"), "team": value("Team"),
        "position": value("Position"), "status": value("Status"),
        "owned_pct": value("% Owned"), "owner": value("Owner"),
        "owner_id": value("Owner ID"), "current_projection": value(f"Week {current_week}"),
        "week_projections": weeks, "remaining": value("Remaining"), "vor": value("VOR"),
    }


def _serialize_report(report: RecommendationReport) -> dict[str, Any]:
    return {
        "week": report.week,
        "current_roster": _serialize_slots(report.my_roster),
        "depth_warnings": [asdict(w) for w in report.depth_warnings],
        "bye_warnings": [asdict(w) for w in report.bye_week_warnings],
        "lineup": _serialize_lineup(report.lineup),
        "recommendations": [asdict(r) for r in report.add_drop_recs],
        "low_value_recommendations": [asdict(r) for r in report.low_value_recs],
        "action_plan": _serialize_action_plan(report.action_plan),
    }


def _serialize_slots(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None:
        return []
    return [{str(key): _json_value(value) for key, value in row.items()} for _, row in df.iterrows()]


def _serialize_lineup(lineup: LineupRecommendation | None) -> dict[str, Any] | None:
    if lineup is None:
        return None
    return {
        "starters": [asdict(slot) for slot in lineup.starters],
        "bench": [asdict(slot) for slot in lineup.bench],
        "total_projection": lineup.total_projection,
        "total_vor": lineup.total_vor,
        "warnings": lineup.warnings,
    }


def _serialize_action_plan(action_plan: Any) -> dict[str, Any] | None:
    if action_plan is None:
        return None
    return {
        "moves": [asdict(rec) for rec in action_plan.moves],
        "skipped_recommendations": [asdict(rec) for rec in action_plan.skipped_recs],
        "final_lineup": _serialize_lineup(action_plan.final_lineup),
        "remaining_warnings": [asdict(warning) for warning in action_plan.remaining_warnings],
        "comparison_notes": list(action_plan.comparison_notes),
    }


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def create_provider(config: dict[str, Any]) -> OpenAICompatibleProvider:
    """Create the configured provider without putting credentials in config output."""
    settings = config.get("llm_evaluator", {})
    return OpenAICompatibleProvider(
        model=str(settings.get("model", "")),
        endpoint=str(settings.get("endpoint", "https://api.openai.com/v1/chat/completions")),
        api_key_env=str(settings.get("api_key_env", "OPENAI_API_KEY")),
        timeout=float(settings.get("timeout_seconds", 60)),
    )

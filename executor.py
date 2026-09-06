"""
Executor stubs — the seam between decision logic and action.

These functions are NOT yet wired to Yahoo's API.  They log what *would*
have been submitted and return a result object.  When real automation is
ready, only this file needs to be re-implemented.

Usage pattern:
    dry_run=True  (default)  — logs "would have submitted X"
    dry_run=False — would call Yahoo's API (TODO)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger


@dataclass
class ExecutionResult:
    """Result of an attempted (or simulated) execution."""

    action: str  # "add_drop", "set_lineup"
    success: bool
    executed: bool  # True if actually submitted, False if dry-run
    detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    errors: list[str] = field(default_factory=list)


def submit_add_drop(
    add_player: Optional[str],
    drop_player: Optional[str],
    waiver_priority: Optional[int] = None,
    transaction_type: str = "fa",  # "fa" | "waiver"
    dry_run: bool = True,
) -> ExecutionResult:
    """Submit (or simulate) an add/drop transaction.

    Parameters
    ----------
    add_player
        Name of the player to add.
    drop_player
        Name of the player to drop (required for add/drop, optional for FA add).
    waiver_priority
        Current waiver wire priority from config.
    transaction_type
        "fa" for free agent pickup, "waiver" for waiver claim.
    dry_run
        If True (default), only log what would have been done.
    """
    if transaction_type == "waiver" and waiver_priority is not None:
        detail = (
            f"WOULD submit waiver claim: add {add_player}, drop {drop_player} "
            f"(priority #{waiver_priority})"
        )
    elif drop_player:
        detail = f"WOULD add {add_player}, drop {drop_player} (free agent)"
    else:
        detail = f"WOULD add {add_player} (free agent, no drop)"

    if dry_run:
        logger.info(detail)
        return ExecutionResult(
            action="add_drop",
            success=True,
            executed=False,
            detail=detail,
        )

    # --- TODO: wire to Yahoo Fantasy Sports API ---
    logger.warning(
        "Executor is not yet implemented — falling back to dry-run logging."
    )
    logger.info(detail)
    return ExecutionResult(
        action="add_drop",
        success=False,
        executed=False,
        detail=detail,
        errors=["Yahoo API integration not yet implemented"],
    )


def set_lineup(
    lineup: list,
    dry_run: bool = True,
) -> ExecutionResult:
    """Submit (or simulate) setting the starting lineup.

    Parameters
    ----------
    lineup
        List of slot assignments (e.g. from recommend_lineup()).
    dry_run
        If True (default), only log what would have been done.
    """
    player_names = []
    if hasattr(lineup, "starters"):
        player_names = [s.player for s in lineup.starters]
    else:
        player_names = [str(l) for l in lineup]

    detail = f"WOULD set lineup: {', '.join(player_names)}"

    if dry_run:
        logger.info(detail)
        return ExecutionResult(
            action="set_lineup",
            success=True,
            executed=False,
            detail=detail,
        )

    # --- TODO: wire to Yahoo Fantasy Sports API ---
    logger.warning(
        "Executor is not yet implemented — falling back to dry-run logging."
    )
    logger.info(detail)
    return ExecutionResult(
        action="set_lineup",
        success=False,
        executed=False,
        detail=detail,
        errors=["Yahoo API integration not yet implemented"],
    )

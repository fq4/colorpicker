"""
Decision engine — roster analysis, lineup optimization, and add/drop recommendations.

All recommendation functions return structured dataclass objects so that both
the dashboard (report.py) and any future auto-executor (executor.py) can consume
the same data without parsing strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from loguru import logger

INF = float("inf")


# --------------------------------------------------------------------------- #
#  Dataclasses for structured output                                          #
# --------------------------------------------------------------------------- #


@dataclass
class AddDropRecommendation:
    """A single add/drop (or waiver) recommendation."""

    action: str  # "add_drop", "fa_add", "waiver_add", "waiver_claim"
    add: Optional[str] = None
    add_team: Optional[str] = None
    add_position: Optional[str] = None
    add_projection: Optional[float] = None
    add_vor: Optional[float] = None
    add_status: Optional[str] = None
    add_owned_pct: Optional[str] = None
    drop: Optional[str] = None
    drop_team: Optional[str] = None
    drop_position: Optional[str] = None
    drop_projection: Optional[float] = None
    drop_vor: Optional[float] = None
    drop_status: Optional[str] = None
    vor_gain: Optional[float] = None
    reason: str = ""
    confidence: str = "medium"  # "high", "medium", "low"
    flagged: bool = False
    flag_reason: Optional[str] = None
    alternative: Optional[str] = None  # suggested alternative when flagged for gap mismatch
    dry_run: bool = True


@dataclass
class LineupSlot:
    """A single starting-lineup slot assignment."""

    slot: str  # e.g. "QB", "WR1", "RB2", "W/R", "K", "DEF"
    player: str
    team: str
    position: str
    projection: float
    vor: float
    status: str = ""
    flagged: bool = False
    bench_alternative: Optional[str] = None
    bench_alt_projection: Optional[float] = None
    bench_alt_status: str = ""


@dataclass
class BenchDepthWarning:
    """A bench-depth gap warning."""

    position: str
    usable_count: int
    starter_count: int
    minimum: int
    message: str
    ir_players: list[str] = field(default_factory=list)
    low_proj_players: list[str] = field(default_factory=list)


@dataclass
class RosterLimitWarning:
    """A warning that recommended moves would exceed the roster size limit."""

    current_size: int
    max_size: int
    excess: int
    message: str
    suggested_drops: list[dict] = field(default_factory=list)
    ir_players: list[str] = field(default_factory=list)
    low_proj_players: list[str] = field(default_factory=list)


@dataclass
class ByeWeekWarning:
    """A warning about an upcoming bye week for a roster player."""

    player: str
    position: str
    bye_week: int
    is_current_starter: bool
    message: str


@dataclass
class LineupRecommendation:
    """Full lineup recommendation returned by recommend_lineup()."""

    starters: list[LineupSlot] = field(default_factory=list)
    bench: list[LineupSlot] = field(default_factory=list)
    total_projection: float = 0.0
    total_vor: float = 0.0
    flagged_starters: list[LineupSlot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ActionPlan:
    """Action plan with moves to make, resulting lineup, and remaining warnings."""

    moves: list[AddDropRecommendation]
    skipped_recs: list[AddDropRecommendation]
    simulated_roster: Optional[pd.DataFrame] = None
    final_lineup: Optional[LineupRecommendation] = None
    remaining_warnings: list[BenchDepthWarning] = field(default_factory=list)
    comparison_notes: list[str] = field(default_factory=list)

@dataclass
class RecommendationReport:
    """Everything produced by the decision engine in one container."""

    my_roster: Optional[pd.DataFrame] = None
    depth_warnings: list[BenchDepthWarning] = field(default_factory=list)
    bye_week_warnings: list[ByeWeekWarning] = field(default_factory=list)
    lineup: Optional[LineupRecommendation] = None
    add_drop_recs: list[AddDropRecommendation] = field(default_factory=list)
    low_value_recs: list[AddDropRecommendation] = field(default_factory=list)
    action_plan: Optional[ActionPlan] = None
    week: Optional[int] = None
    hypothetical_drop: Optional[str] = None


# --------------------------------------------------------------------------- #
#  Position helpers                                                           #
# --------------------------------------------------------------------------- #

# Which NFL positions can fill each roster slot
SLOT_POSITION_MAP: dict[str, set[str]] = {
    "QB": {"QB"},
    "WR": {"WR"},
    "RB": {"RB"},
    "TE": {"TE"},
    "W/R": {"WR", "RB"},
    "W/R/T": {"WR", "RB", "TE"},
    "W/T": {"WR", "TE"},
    "FLEX": {"WR", "RB"},
    "SF": {"QB", "RB", "WR", "TE"},  # Super Flex
    "K": {"K"},
    "DEF": {"DEF"},
    "D/ST": {"DEF"},
}

# Slots that are "flexible" — filled after strict positions
FLEX_SLOTS = {"W/R", "W/R/T", "W/T", "FLEX", "SF"}

# Slots that are bench or IR
BENCH_SLOTS = {"BN"}
IR_SLOTS = {"IR"}


def parse_positions(positions_str: str) -> list[str]:
    """Parse the comma-separated positions string into a list of slot types."""
    return [p.strip() for p in positions_str.split(",")]


def get_starting_slots(positions: list[str]) -> list[str]:
    """Return only the starting (non-BN, non-IR) slot types."""
    return [s for s in positions if s not in BENCH_SLOTS and s not in IR_SLOTS]


def get_starters_count(positions: list[str]) -> dict[str, int]:
    """Count how many starters each NFL position needs across starting slots.

    W/R is treated as flexible — it does not count toward RB or WR starter needs.
    """
    slots = get_starting_slots(positions)
    counts: dict[str, int] = {}
    for slot in slots:
        if slot in FLEX_SLOTS:
            continue  # flex doesn't lock a specific position
        for pos in SLOT_POSITION_MAP.get(slot, set()):
            counts[pos] = counts.get(pos, 0) + 1
    return counts


def _week_col(current_week: int) -> str:
    return f"Week {current_week}"


def _parse_ir_statuses(config: dict) -> set[str]:
    return set(config.get("ir_statuses", ["IR", "IR-R", "NFI", "NFI-R", "NFI-A", "COVID", "PUP", "PUP-R", "O"]))


def _is_ir(status: Optional[str], ir_statuses: set[str]) -> bool:
    if pd.isna(status) or not status or str(status).strip() == "":
        return False
    status_str = str(status).strip()
    # Check exact match and prefix match (e.g. "IR-R" starts with "IR")
    for ir in ir_statuses:
        if status_str == ir or status_str.startswith(ir + "-"):
            return True
    return False


# --------------------------------------------------------------------------- #
#  1. get_my_roster                                                           #
# --------------------------------------------------------------------------- #


def get_my_roster(
    df: pd.DataFrame,
    team_name: str,
    current_week: Optional[int] = None,
) -> pd.DataFrame:
    """Filter the full df to only players on the user's team.

    Returns a DataFrame with columns:
    Name, Team, Position, Status, Week {current}, VOR
    """
    import ffbot

    if current_week is None:
        current_week = ffbot.current_week()

    week_col = _week_col(current_week)

    my_roster = df[df["Owner"] == team_name].copy()

    # Make sure all expected columns exist
    for col in ["Name", "Team", "Position", "Status", "VOR"]:
        if col not in my_roster.columns:
            my_roster[col] = None

    if week_col not in my_roster.columns:
        my_roster[week_col] = float("nan")

    week_columns = [
        col for col in my_roster.columns
        if col.startswith("Week ") and col[5:].isdigit()
    ]
    week_columns = [week_col] + [col for col in week_columns if col != week_col]
    cols = ["Name", "Team", "Position", "Status", *week_columns, "VOR"]
    cols = list(dict.fromkeys(cols))
    result = my_roster[cols].sort_values(by=week_col, ascending=False).reset_index(drop=True)
    return result


# --------------------------------------------------------------------------- #
#  2. flag_bench_depth_gaps                                                   #
# --------------------------------------------------------------------------- #


def flag_bench_depth_gaps(
    my_roster: pd.DataFrame,
    positions_config: dict,
) -> list[BenchDepthWarning]:
    """Check whether the roster has enough usable bench depth per position.

    A player on IR does NOT count toward bench depth.

    Parameters
    ----------
    my_roster
        DataFrame from get_my_roster() — must contain at least
        Name, Position, Status, and a Week column.
    positions_config
        Dict with keys: ``positions`` (comma-str), ``bench_depth_minimums``
        (dict), ``ir_statuses`` (list).
    """
    positions_str = positions_config["positions"]
    minimums: dict = positions_config.get("bench_depth_minimums", {})
    ir_statuses = _parse_ir_statuses(positions_config)
    min_proj = positions_config.get('min_usable_projection', 1.0)

    slots = parse_positions(positions_str)
    starter_counts = get_starters_count(slots)

    # Determine which week column is present
    week_cols = [c for c in my_roster.columns if c.startswith("Week ") and c[5:].isdigit()]
    week_col = week_cols[0] if week_cols else "VOR"

    warnings: list[BenchDepthWarning] = []

    for pos, minimum in minimums.items():
        # Find players whose Position field includes this position
        pos_players = []
        ir_players_names = []
        low_proj_names = []
        for _, row in my_roster.iterrows():
            player_positions = set(p.strip() for p in str(row["Position"]).split(","))
            if pos in player_positions:
                if _is_ir(row["Status"], ir_statuses):
                    ir_players_names.append(str(row["Name"]))
                else:
                    proj = _safe_float(row.get(week_col))
                    if proj is not None and proj >= min_proj:
                        pos_players.append(row)
                    else:
                        low_proj_names.append(str(row["Name"]))

        usable_count = len(pos_players)
        starter_need = starter_counts.get(pos, 0)
        bench_depth = usable_count - starter_need

        if bench_depth < minimum:
            ir_list = ", ".join(ir_players_names) if ir_players_names else "none"
            if bench_depth < 0:
                # Not enough players to even fill starters
                msg = (
                    f"CRITICAL: Not enough {pos} to fill starting slots "
                    f"(have {usable_count}, need {starter_need})"
                )
            elif bench_depth == 0:
                msg = (
                    f"No usable bench {pos} — only starters available"
                    + (f", {ir_list} on IR" if ir_players_names else "")
                )
            else:
                msg = (
                    f"Bench depth gap at {pos} — only {bench_depth} bench player(s), "
                    f"minimum is {minimum}"
                )

            warnings.append(
                BenchDepthWarning(
                    position=pos,
                    usable_count=usable_count,
                    starter_count=starter_need,
                    minimum=minimum,
                    message=msg,
                    ir_players=ir_players_names,
                    low_proj_players=low_proj_names,
                )
            )

    return warnings


def check_upcoming_byes(
    my_roster: pd.DataFrame,
    current_week: int,
    lookahead_weeks: int = 2,
    lineup: Optional[LineupRecommendation] = None,
) -> list[ByeWeekWarning]:
    """Flag players with upcoming bye weeks within the lookahead window.

    A bye week is detected when a player has a 0 or null projection for a week
    sandwiched between normal projections.  Starters are flagged as higher
    priority than bench players.
    """
    warnings: list[ByeWeekWarning] = []
    if my_roster is None or my_roster.empty:
        return warnings

    # Build set of starter names for cross-referencing
    starter_names: set[str] = set()
    if lineup is not None:
        starter_names = {s.player for s in lineup.starters}

    week_cols = []
    for w in range(current_week + 1, current_week + lookahead_weeks + 1):
        col = f"Week {w}"
        if col in my_roster.columns:
            week_cols.append((w, col))

    for _, row in my_roster.iterrows():
        player_name = str(row.get("Name", ""))
        player_pos = str(row.get("Position", ""))
        bye_week = None

        for week_num, col in week_cols:
            val = row.get(col)
            proj = _safe_float(val)
            if proj is None or proj == 0:
                # Confirm a genuine "sandwich" pattern: a normal (non-zero)
                # projection exists both before AND after this zero/null week
                # within the available data. This avoids false positives at
                # season boundaries (e.g. Week 18 with no Week 19 to confirm).
                week_cols_all = [
                    f"Week {w}" for w in range(1, 19) if f"Week {w}" in my_roster.columns
                ]
                week_idx = week_cols_all.index(col) if col in week_cols_all else -1
                has_prior = False
                has_after = False
                if week_idx > 0:
                    prior_cols = week_cols_all[:week_idx]
                    has_prior = any(
                        (_safe_float(row.get(prior_col)) or 0) > 0
                        for prior_col in prior_cols
                    )
                if week_idx >= 0 and week_idx < len(week_cols_all) - 1:
                    after_cols = week_cols_all[week_idx + 1 :]
                    has_after = any(
                        (_safe_float(row.get(after_col)) or 0) > 0
                        for after_col in after_cols
                    )
                if has_prior and has_after:
                    bye_week = week_num
                    break

        if bye_week is None:
            continue

        is_starter = player_name in starter_names
        priority = "starter" if is_starter else "bench"
        message = (
            f"{player_name} ({player_pos}, {priority}) has a bye in Week {bye_week}"
            f" — plan a replacement before then."
        )
        warnings.append(
            ByeWeekWarning(
                player=player_name,
                position=player_pos,
                bye_week=bye_week,
                is_current_starter=is_starter,
                message=message,
            )
        )

    return warnings


# --------------------------------------------------------------------------- #
#  3. rank_free_agents                                                        #
# --------------------------------------------------------------------------- #


def rank_free_agents(
    df: pd.DataFrame,
    position: str,
    current_week: int,
    exclude_status: list[str] | None = None,
) -> pd.DataFrame:
    """Filter free agents at *position*, sorted by this week's projection.

    VOR is used as a tiebreaker.  Players with injury statuses in
    *exclude_status* are removed entirely.  Players tagged ``Q`` are
    included but flagged (returned with a ``flagged`` column).
    """
    if exclude_status is None:
        exclude_status = ["IR", "IR-R", "PUP", "PUP-R", "O", "NFI", "NFI-R", "NFI-A", "COVID"]

    week_col = _week_col(current_week)

    # Free agents have no owner
    fa_mask = df["Owner"].fillna("").str.lower() == "free agent"
    # Also catch players with no owner ID
    fa_mask = fa_mask | (df["Owner ID"].isna() & (df["Owner"].fillna("").str.lower().isin(["free agent", ""])))

    pos_players = df[fa_mask].copy()

    # Filter by position
    pos_players = pos_players[
        pos_players["Position"].apply(
            lambda p: position in {x.strip() for x in str(p).split(",")}
        )
    ]

    # Exclude hard-to-use injury statuses
    def _is_excluded(s):
        if pd.isna(s) or not s:
            return False
        for exc in exclude_status:
            if str(s).strip() == exc or str(s).strip().startswith(exc + "-"):
                return True
        return False

    pos_players = pos_players[~pos_players["Status"].apply(_is_excluded)]

    cols = ["Name", "Team", "Position", "Status", "% Owned", week_col, "VOR", "flagged"]

    if len(pos_players) == 0:
        return pd.DataFrame(columns=cols)

    # Sort: by week projection desc, then VOR desc
    pos_players = pos_players.sort_values(
        by=[week_col, "VOR"], ascending=[False, False]
    )

    # Flag questionable players
    pos_players["flagged"] = pos_players["Status"].apply(
        lambda s: "Q" if (str(s).strip() if not pd.isna(s) else "") in ("Q", "Q-") or (
            str(s).strip().startswith("Q") if not pd.isna(s) else False
        ) else ""
    )

    available = [c for c in cols if c in pos_players.columns]
    return pos_players[available].reset_index(drop=True)


# --------------------------------------------------------------------------- #
#  4. recommend_adds_drops                                                     #
# --------------------------------------------------------------------------- #

# Regex to parse optimizer player strings like "Name (POS)" or "Name (POS) - Owner"
_OPTIMIZER_RE = re.compile(r"^(.+?)\s+\(([^)]+)\)(?:\s+-\s+(.+))?$")


def _parse_optimizer_player(text: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse a player string from the optimizer output.

    Returns (name, position, owner) or (None, None, None) for empty strings.
    """
    if text is None or str(text).strip() == "":
        return None, None, None

    m = _OPTIMIZER_RE.match(str(text).strip())
    if m:
        return m.group(1), m.group(2), m.group(3)

    # Fallback: just return the text as name
    return str(text).strip(), None, None


def _lookup_player(df: pd.DataFrame, name: str, position: Optional[str] = None) -> Optional[pd.Series]:
    """Look up a player in the df by name (and optionally position)."""
    query = str(name).strip()
    query_base = query.split("(")[0].strip()

    # Prefer an exact case-insensitive match over any substring match.
    matches = df[df["Name"].str.casefold() == query.casefold()]
    if position:
        matches = matches[matches["Position"].str.contains(position, na=False)]

    if len(matches) == 1:
        return matches.iloc[0]

    # Fall back to fuzzy substring matching.
    matches = df[df["Name"].str.contains(re.escape(query_base), na=False, regex=True)]
    if position:
        matches = matches[matches["Position"].str.contains(position, na=False)]

    if len(matches) == 0:
        return None

    # Among fuzzy matches, prefer an exact case-insensitive match.
    exact_ci = matches[matches["Name"].str.casefold() == query_base.casefold()]
    if len(exact_ci) == 1:
        return exact_ci.iloc[0]
    if len(exact_ci) > 1:
        matches = exact_ci

    # If multiple substring matches remain, prefer the closest string length
    # to the query as a proxy for the most likely actual player.
    if len(matches) > 1:
        matches = matches.copy()
        matches["_len_diff"] = (matches["Name"].str.len() - len(query_base)).abs()
        min_diff = matches["_len_diff"].min()
        matches = matches[matches["_len_diff"] == min_diff]

        if len(matches) > 1:
            logger.warning(
                f"Ambiguous player lookup for '{name}': {len(matches)} matches "
                f"({', '.join(matches['Name'].tolist())}); using first match"
            )

    return matches.iloc[0]


def recommend_adds_drops(
    df: pd.DataFrame,
    my_roster: pd.DataFrame,
    current_week: int,
    config: Optional[dict] = None,
) -> list[AddDropRecommendation]:
    """Combine ffbot.optimize() output with injury and depth-gap analysis.

    Returns a ranked list of AddDropRecommendation objects, each with a
    plain-English reason and confidence level.
    """
    import ffbot

    if config is None:
        from data_layer import load_config
        config = load_config()

    league_id = config["league_id"]
    team_id = config["team_id"]
    positions_str = config["positions"]
    min_vor_add_config = config.get("min_vor_gain_to_recommend_add", 5.0)
    if isinstance(min_vor_add_config, dict):
        min_vor_add_default = float(min_vor_add_config.get("default", 5.0))
    else:
        min_vor_add_default = float(min_vor_add_config)
        min_vor_add_config = {}
    positions_config = {
        "positions": positions_str,
        "bench_depth_minimums": config.get("bench_depth_minimums", {}),
        "ir_statuses": config.get("ir_statuses", []),
    }

    week_col = _week_col(current_week)
    ir_statuses = _parse_ir_statuses(config)

    # Check current bench depth
    depth_warnings = flag_bench_depth_gaps(my_roster, positions_config)
    depth_gap_positions = {w.position for w in depth_warnings}

    # Build current starter name set (used later in transparency check
    # to exclude starters from the "worst-VOR alternative" comparison pool,
    # matching the same exclusion logic used for suggested_drops)
    current_lineup = recommend_lineup(df, my_roster, positions_config, current_week)
    starter_names = {s.player for s in current_lineup.starters}

    # Run the optimizer
    logger.info("Running ffbot.optimize() ...")
    try:
        opt_df = ffbot.optimize(df, current_week, team_id, positions_str)
    except Exception as e:
        logger.error(f"Optimizer failed: {e}")
        return []

    recs: list[AddDropRecommendation] = []

    # Parse optimizer output
    for _, row in opt_df.iterrows():
        add_text = row.get("Add", "")
        drop_text = row.get("Drop", "")

        # Skip baseline row
        if str(add_text).strip() == "<current roster>":
            continue

        # Skip rows that are neither add nor drop
        if str(add_text).strip() == "" and str(drop_text).strip() == "":
            continue

        add_name, add_pos, add_owner = _parse_optimizer_player(add_text)
        drop_name, drop_pos, drop_owner = _parse_optimizer_player(drop_text)

        add_player = _lookup_player(df, add_name, add_pos) if add_name else None
        drop_player = _lookup_player(df, drop_name, drop_pos) if drop_name else None

        # Determine action type
        if add_name and drop_name:
            action = "waiver_claim" if add_owner else "add_drop"
        elif add_name and not drop_name:
            # Free agent pickup, no drop (waiver claim or FA add)
            action = "waiver_add" if add_owner else "fa_add"
        elif drop_name and not add_name:
            # Pure drop — skip, drops alone aren't recommendations
            continue
        else:
            continue

        # Build recommendation
        rec = AddDropRecommendation(action=action)
        flag_reasons: list[str] = []

        if add_player is not None:
            rec.add = str(add_player["Name"])
            rec.add_team = str(add_player["Team"])
            rec.add_position = str(add_player["Position"])
            rec.add_projection = _safe_float(add_player.get(week_col))
            rec.add_vor = _safe_float(add_player.get("VOR"))
            rec.add_status = _safe_status(add_player.get("Status"))
            rec.add_owned_pct = str(add_player.get("% Owned", ""))

        if drop_player is not None:
            rec.drop = str(drop_player["Name"])
            rec.drop_team = str(drop_player["Team"])
            rec.drop_position = str(drop_player["Position"])
            rec.drop_projection = _safe_float(drop_player.get(week_col))
            rec.drop_vor = _safe_float(drop_player.get("VOR"))
            rec.drop_status = _safe_status(drop_player.get("Status"))

        rec.vor_gain = _safe_float(row.get("VOR"))

        # --- Cross-reference: bench depth gaps (simulated post-drop) ---
        if drop_player is not None:
            drop_positions = {p.strip() for p in str(drop_player["Position"]).split(",")}

            # Simulate removing the drop player from the roster
            simulated_roster = my_roster[
                my_roster["Name"] != str(drop_player["Name"])
            ].copy()
            simulated_warnings = flag_bench_depth_gaps(simulated_roster, positions_config)
            simulated_gap_positions = {w.position for w in simulated_warnings}

            # Flag if this drop creates a NEW depth gap not present before
            new_gaps = simulated_gap_positions - depth_gap_positions
            for dp in drop_positions:
                if dp in new_gaps:
                    rec.flagged = True
                    flag_reasons.append(f"Dropping {dp} would create a bench depth gap")
                elif dp in depth_gap_positions:
                    rec.flagged = True
                    flag_reasons.append(f"Dropping {dp} exacerbates existing bench depth gap")

        # --- Cross-reference: injury statuses ---
        if add_player is not None and _is_ir(rec.add_status, ir_statuses):
            rec.flagged = True
            flag_reasons.append(f"Add target {add_name} has injury status {rec.add_status}")

        if drop_player is not None and _is_ir(rec.drop_status, ir_statuses):
            rec.flagged = True
            flag_reasons.append(f"Drop target {drop_name} has injury status {rec.drop_status}")

        # --- Cross-reference: bench depth gap awareness ---
        # If there is an existing bench depth gap at position X, and this
        # recommendation adds a player at a different position Y with
        # below-replacement individual VOR, flag it as deprioritized.
        if (
            add_player is not None
            and depth_gap_positions
            and rec.add_position not in depth_gap_positions
            and rec.add_vor is not None
            and rec.add_vor < 0
        ):
            rec.flagged = True
            gap_list = ", ".join(sorted(depth_gap_positions))
            flag_reasons.append(
                f"Uses a bench slot on {rec.add_position} depth while "
                f"your {gap_list} bench gap remains unaddressed — consider "
                f"whether a {gap_list} free agent better serves your need this week"
            )

            # Look up the best available non-flagged free agent at the gapped
            # position(s) and surface it as a concrete alternative.
            best_alt: Optional[pd.Series] = None
            best_alt_pos: Optional[str] = None
            for gap_pos in sorted(depth_gap_positions):
                fa_df = rank_free_agents(df, gap_pos, current_week)
                if fa_df.empty:
                    continue
                # Prefer non-flagged free agents; fall back to any if none are clean
                clean = fa_df[fa_df.get("flagged", "") == ""]
                candidates = clean if len(clean) > 0 else fa_df
                if len(candidates) > 0:
                    candidate = candidates.iloc[0]
                    proj = _safe_float(candidate.get(week_col)) or 0.0
                    if best_alt is None or proj > (_safe_float(best_alt.get(week_col)) or 0.0):
                        best_alt = candidate
                        best_alt_pos = gap_pos

            if best_alt is not None:
                alt_name = str(best_alt.get("Name", ""))
                alt_proj = _safe_float(best_alt.get(week_col)) or 0.0
                rec.alternative = (
                    f"Alternative: instead of this, consider adding "
                    f"{alt_name} ({best_alt_pos}, {alt_proj:.1f} pts) to address "
                    f"your {best_alt_pos} depth gap"
                )
                flag_reasons.append(rec.alternative)

        rec.flag_reason = " | ".join(flag_reasons) if flag_reasons else None

        # --- Build plain-English reason ---
        rec.reason = _build_add_drop_reason(
            rec, current_week, week_col, depth_gap_positions,
            min_vor_add_default=min_vor_add_default,
            min_vor_add_config=min_vor_add_config,
        )

        # --- Confidence ---
        rec.confidence = _assess_confidence(
            rec, min_vor_add_default, min_vor_add_config, ir_statuses, depth_gap_positions
        )

        # --- Transparency: ffbot drop vs worst-VOR bench player (appended AFTER reason build) ---
        # ffbot's optimizer maximizes starting-lineup points, not VOR, so it may
        # choose to drop a mid-VOR bench player while a worse-VOR bench player
        # at the same position is available. Surface that fact as information
        # for the user to weigh — no override, just transparency.
        if (
            drop_player is not None
            and add_player is not None
            and rec.add_position
            and rec.drop_position
            and rec.add_position == rec.drop_position
        ):
            threshold = float(config.get("worst_vor_drop_note_threshold", 3.0))
            same_pos_mask = (
                my_roster["Position"].apply(
                    lambda p: rec.drop_position in {x.strip() for x in str(p).split(",")}
                )
            )
            same_pos_roster = my_roster[same_pos_mask].copy()
            same_pos_roster = same_pos_roster[
                same_pos_roster["Name"] != str(drop_player["Name"])
            ]
            ir_statuses = _parse_ir_statuses(config)
            def _is_bench_row(row):
                name = str(row.get("Name", "")).strip()
                status = str(row.get("Status", "")).strip()
                if not status or status in ("", "nan"):
                    pass  # fall through to starter/name checks below
                if any(status.startswith(ir) or status == ir for ir in ir_statuses):
                    return False
                if name in starter_names:
                    return False
                return True

            bench_candidates = same_pos_roster[same_pos_roster.apply(_is_bench_row, axis=1)]
            if len(bench_candidates) > 0:
                bench_candidates = bench_candidates.copy()
                bench_candidates["_vor"] = bench_candidates["VOR"].apply(_safe_float).fillna(0.0)
                worst_row = bench_candidates.loc[bench_candidates["_vor"].idxmin()]
                worst_vor = _safe_float(worst_row.get("VOR")) or 0.0
                drop_vor = _safe_float(drop_player.get("VOR")) or 0.0
                vor_gap = drop_vor - worst_vor
                if vor_gap > threshold:
                    note = (
                        f"Note: ffbot's optimizer chose to drop {rec.drop} "
                        f"({drop_vor:+.1f}), but "
                        f"{worst_row['Name']} ({worst_vor:+.1f}) "
                        f"has lower individual VOR. ffbot optimizes full-season point "
                        f"projections, not VOR, so this may reflect factors VOR "
                        f"doesn't capture — use your judgment."
                    )
                    rec.reason = rec.reason.rstrip(".") + f". {note}"

        recs.append(rec)

    # Sort: non-flagged first by VOR gain descending, then flagged
    recs.sort(key=lambda r: (r.flagged, -(r.vor_gain if r.vor_gain is not None else 0)))

    return recs


def _safe_float(val) -> Optional[float]:
    if val is None or pd.isna(val):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_status(val) -> Optional[str]:
    if val is None or pd.isna(val):
        return None
    return str(val).strip()


def _build_add_drop_reason(
    rec: AddDropRecommendation,
    current_week: int,
    week_col: str,
    depth_gap_positions: set[str],
    min_vor_add_default: float = 5.0,
    min_vor_add_config: Optional[dict] = None,
) -> str:
    """Build a plain-English reason string for an add/drop recommendation."""
    parts: list[str] = []

    # Action description
    if rec.action in ("add_drop", "waiver_claim"):
        parts.append(f"Add {rec.add} ({rec.add_position}, {rec.add_team})")
        if rec.drop:
            parts.append(f"drop {rec.drop} ({rec.drop_position})")
    else:
        parts.append(f"Add {rec.add} ({rec.add_position}, {rec.add_team})")

    # Injury context
    if rec.add_status:
        if rec.add_status.strip() in ("Q", "Q-"):
            parts.append("questionable — monitor injury report")
        else:
            parts.append(f"({rec.add_status})")
    else:
        parts.append("no injury tag")

    # Projection
    if rec.add_projection is not None:
        parts.append(f"WK{current_week} proj {rec.add_projection:.1f} pts")

    # VOR / gain and streaming threshold
    if rec.vor_gain is not None and rec.vor_gain != 0:
        position = (rec.add_position or "").strip().upper()
        threshold = float((min_vor_add_config or {}).get(position, min_vor_add_default))
        if position and min_vor_add_config:
            parts.append(
                f"VOR gain {rec.vor_gain:+.1f} clears the streaming threshold "
                f"({threshold:.1f}) for {position}"
            )
        else:
            parts.append(f"VOR gain {rec.vor_gain:+.1f}")

    # Depth gap fill
    if rec.add_position and rec.add_position in depth_gap_positions:
        parts.append("fills a bench depth gap")

    # Drop context
    if rec.drop_projection is not None and rec.drop_vor is not None:
        parts.append(f"(dropping {rec.drop}: WK{current_week} proj {rec.drop_projection:.1f}, VOR {rec.drop_vor:+.1f})")

    return " — ".join(parts) + "."


def _assess_confidence(
    rec: AddDropRecommendation,
    min_vor_add_default: float,
    min_vor_add_config: dict,
    ir_statuses: set[str],
    depth_gap_positions: set[str],
) -> str:
    """Determine confidence level for a recommendation."""
    # Low confidence if flagged
    if rec.flagged:
        return "low"

    # Look up position-specific threshold
    position = (rec.add_position or "").strip().upper()
    min_vor_add = float(min_vor_add_config.get(position, min_vor_add_default))

    # High confidence if VOR gain is substantial and no flags
    if rec.vor_gain is not None and rec.vor_gain >= min_vor_add:
        return "high"

    # Medium otherwise
    return "medium"


# --------------------------------------------------------------------------- #
#  5. recommend_lineup                                                        #
# --------------------------------------------------------------------------- #


def recommend_lineup(
    df: pd.DataFrame,
    my_roster: pd.DataFrame,
    positions_config: dict,
    current_week: int,
) -> LineupRecommendation:
    """Assign the optimal starters to slots for the current week.

    Uses the Week {current} column (not season totals), respects the
    positions_config slots, and flags injured starters with bench
    alternatives.
    """
    positions_str = positions_config["positions"]
    ir_statuses = _parse_ir_statuses(positions_config)
    slots = parse_positions(positions_str)
    starting_slots = get_starting_slots(slots)
    week_col = _week_col(current_week)

    # Work with the full roster (need all positions, not just the team)
    team_name = None  # We don't need it; my_roster is already filtered

    # Get the full roster from df to access all columns
    # my_roster has: Name, Team, Position, Status, Week {n}, VOR
    players = my_roster.copy()

    # Fill IR slot with injured players
    ir_slots: list[LineupSlot] = []
    if "IR" in slots:
        ir_players = players[
            players["Status"].apply(lambda s: _is_ir(s, ir_statuses))
        ]
        for _, ir_row in ir_players.iterrows():
            ir_slots.append(
                LineupSlot(
                    slot="IR",
                    player=str(ir_row["Name"]),
                    team=str(ir_row["Team"]),
                    position=str(ir_row["Position"]),
                    projection=_safe_float(ir_row.get(week_col)) or 0.0,
                    vor=_safe_float(ir_row.get("VOR")) or 0.0,
                    status=_safe_status(ir_row.get("Status")) or "",
                )
            )
        players = players[
            ~players["Status"].apply(lambda s: _is_ir(s, ir_statuses))
        ]

    # Separate players by eligibility
    # A player is eligible for a slot if their Position includes any position
    # in the slot's compatible set.
    eligible_bench: list[pd.Series] = []

    # Assign slot order — least flexible first
    slot_order = _slot_order(starting_slots)

    assignments: dict[int, LineupSlot] = {}  # slot_index -> LineupSlot
    used_names: set[str] = set()
    slot_counters: dict[str, int] = {}

    # Make a working copy sorted by projection
    players = players.sort_values(by=week_col, ascending=False).reset_index(drop=True)

    for slot_idx, slot_type in enumerate(slot_order):
        compatible = SLOT_POSITION_MAP.get(slot_type, set())

        # Find best available player who can fill this slot
        best = None
        for _, row in players.iterrows():
            if row["Name"] in used_names:
                continue
            player_positions = {p.strip() for p in str(row["Position"]).split(",")}
            if player_positions & compatible:
                best = row
                break  # players are sorted by projection desc

        if best is not None:
            used_names.add(best["Name"])
            status_val = _safe_status(best.get("Status")) or ""
            is_flagged = status_val.strip() in ("Q", "Q-") or (
                status_val.strip().startswith("Q") if status_val else False
            )

            # Find bench alternative if flagged
            bench_alt: Optional[LineupSlot] = None
            if is_flagged:
                bench_alt = _find_bench_alternative(
                    players, used_names, compatible, week_col, ir_statuses
                )

            # Number repeated slots (WR1, WR2, RB1, RB2)
            if slot_type in ("WR", "RB"):
                slot_counters[slot_type] = slot_counters.get(slot_type, 0) + 1
                display_slot = f"{slot_type}{slot_counters[slot_type]}"
            else:
                display_slot = slot_type

            assignment = LineupSlot(
                slot=display_slot,
                player=str(best["Name"]),
                team=str(best["Team"]),
                position=str(best["Position"]),
                projection=_safe_float(best.get(week_col)) or 0.0,
                vor=_safe_float(best.get("VOR")) or 0.0,
                status=status_val,
                flagged=is_flagged,
                bench_alternative=bench_alt.player if bench_alt else None,
                bench_alt_projection=bench_alt.projection if bench_alt else None,
                bench_alt_status=bench_alt.status if bench_alt else "",
            )
            assignments[slot_idx] = assignment

    # Remaining players go to bench
    bench_slots: list[LineupSlot] = []
    for _, row in players.iterrows():
        if row["Name"] in used_names:
            continue
        bench_slots.append(
            LineupSlot(
                slot="BN",
                player=str(row["Name"]),
                team=str(row["Team"]),
                position=str(row["Position"]),
                projection=_safe_float(row.get(week_col)) or 0.0,
                vor=_safe_float(row.get("VOR")) or 0.0,
                status=_safe_status(row.get("Status")) or "",
            )
        )

    # Build result
    starters = [assignments[i] for i in sorted(assignments.keys())]
    total_proj = sum(s.projection for s in starters)
    total_vor = sum(s.vor for s in starters)
    flagged_starters = [s for s in starters if s.flagged]

    warnings: list[str] = []
    for s in flagged_starters:
        if s.bench_alternative:
            warnings.append(
                f"{s.player} ({s.slot}) is {s.status} — consider {s.bench_alternative} "
                f"(proj {s.bench_alt_projection:.1f})"
            )
        else:
            warnings.append(f"{s.player} ({s.slot}) is {s.status} — no bench alternative")

    result = LineupRecommendation(
        starters=starters,
        bench=bench_slots,
        total_projection=total_proj,
        total_vor=total_vor,
        flagged_starters=flagged_starters,
        warnings=warnings,
    )

    # Add IR players to the displayed bench without allowing them into starters.
    bench_slots.extend(ir_slots)

    return result


def _slot_order(starting_slots: list[str]) -> list[str]:
    """Return starting slots ordered from least to most flexible."""
    # Sort by flexibility: strict positions first, then flex
    strict_first = ["QB", "K", "DEF", "TE", "RB", "WR"]
    ordered: list[str] = []

    # Count occurrences
    remaining = list(starting_slots)

    for slot_type in strict_first:
        count = remaining.count(slot_type)
        for _ in range(count):
            ordered.append(slot_type)
        remaining = [s for s in remaining if s != slot_type]

    # Add remaining (flex slots like W/R)
    for s in remaining:
        ordered.append(s)

    return ordered


def _find_bench_alternative(
    players: pd.DataFrame,
    used_names: set[str],
    compatible: set[str],
    week_col: str,
    ir_statuses: set[str],
) -> Optional[LineupSlot]:
    """Find the best bench player who can fill a given slot type.

    Excludes players whose Status is in the configured IR-status set, and
    prefers candidates with a non-zero projection over zero/null projections.
    Returns None when no genuinely usable alternative exists.
    """
    candidates: list[tuple[pd.Series, float]] = []
    for _, row in players.iterrows():
        if row["Name"] in used_names:
            continue
        status_val = _safe_status(row.get("Status")) or ""
        if status_val.strip() in ir_statuses:
            continue
        player_positions = {p.strip() for p in str(row["Position"]).split(",")}
        if player_positions & compatible:
            proj = _safe_float(row.get(week_col))
            candidates.append((row, proj if proj is not None else 0.0))

    # Prefer a candidate with a real (non-zero) projection.
    for row, proj in candidates:
        if proj > 0:
            return LineupSlot(
                slot="BN",
                player=str(row["Name"]),
                team=str(row["Team"]),
                position=str(row["Position"]),
                projection=proj,
                vor=_safe_float(row.get("VOR")) or 0.0,
                status=_safe_status(row.get("Status")) or "",
            )

    # Fall back to a zero/null-projection candidate only if it's genuinely
    # usable (not IR-status). Otherwise there's no valid alternative.
    if candidates:
        row, proj = candidates[0]
        return LineupSlot(
            slot="BN",
            player=str(row["Name"]),
            team=str(row["Team"]),
            position=str(row["Position"]),
            projection=proj,
            vor=_safe_float(row.get("VOR")) or 0.0,
            status=_safe_status(row.get("Status")) or "",
        )

    return None



# --------------------------------------------------------------------------- #
#  Action Plan -- simulate post-move roster and compute resulting lineup        #
# --------------------------------------------------------------------------- #


def simulate_post_move_roster(
    df: pd.DataFrame,
    my_roster: pd.DataFrame,
    add_drop_recs: list[AddDropRecommendation],
    current_week: int,
) -> pd.DataFrame:
    """Apply non-flagged adds/drops to produce the post-move roster.

    Flagged recommendations are skipped (not applied).  Free-agent adds
    pull the player full data from df; drops remove the player from the
    roster copy.
    """
    week_col = _week_col(current_week)
    simulated = my_roster.copy()
    cols = list(my_roster.columns)

    for rec in add_drop_recs:
        if rec.flagged:
            continue

        player = None
        if rec.add:
            player = _lookup_player(df, rec.add, rec.add_position)
            if player is None:
                logger.warning(
                    f"Could not find '{rec.add}' in df for simulated roster; "
                    "skipping the recommendation"
                )
                continue

        # 1. Drop
        if rec.drop:
            simulated = simulated[simulated["Name"] != rec.drop]

        # 2. Add
        if rec.add:
            new_row = {}
            for col in cols:
                if col == week_col:
                    new_row[col] = _safe_float(player.get(week_col)) or 0.0
                elif col == "VOR":
                    new_row[col] = _safe_float(player.get("VOR")) or 0.0
                elif col == "Status":
                    new_row[col] = _safe_status(player.get("Status")) or None
                elif col in player.index:
                    new_row[col] = player[col]
                else:
                    new_row[col] = None
            simulated = pd.concat(
                [simulated, pd.DataFrame([new_row])], ignore_index=True
            )
            logger.debug(
                f"Simulated add: {rec.add} ({rec.add_position}, {rec.add_team})"
            )

    # Re-sort by week projection descending to match get_my_roster output
    if week_col in simulated.columns:
        simulated = simulated.sort_values(
            by=week_col, ascending=False
        ).reset_index(drop=True)

    return simulated


def build_action_plan(
    df: pd.DataFrame,
    my_roster: pd.DataFrame,
    add_drop_recs: list[AddDropRecommendation],
    positions_config: dict,
    current_week: int,
    original_lineup: Optional[LineupRecommendation] = None,
) -> ActionPlan:
    """Build a complete action plan.

    1. Separates flagged vs. non-flagged recommendations.
    2. Simulates the post-move roster (only non-flagged moves applied).
    3. Recomputes the optimal lineup from the simulated roster.
    4. Recomputes bench-depth warnings on the simulated roster.
    """
    moves = [r for r in add_drop_recs if not r.flagged]
    skipped = [r for r in add_drop_recs if r.flagged]

    logger.info(
        f"Action plan: {len(moves)} moves to make, "
        f"{len(skipped)} skipped (flagged)"
    )

    # Build sets of starter/IR names BEFORE any roster mutation so
    # suggested drops never target protected players
    starter_names: set[str] = set()
    if original_lineup is not None:
        starter_names = {s.player for s in original_lineup.starters}
    ir_statuses = _parse_ir_statuses(positions_config)
    ir_names: set[str] = set()
    for _, row in my_roster.iterrows():
        if _is_ir(row.get("Status"), ir_statuses):
            ir_names.add(str(row.get("Name", "")))

    max_roster_size = len(parse_positions(positions_config["positions"]))
    current_roster_size = len(my_roster)
    net_moves = (
        sum(1 for r in moves if r.add)
        - sum(1 for r in moves if r.drop)
    )
    projected_size = current_roster_size + net_moves

    suggested: list[dict] = []
    if projected_size > max_roster_size:
        excess = projected_size - max_roster_size
        # Identify lowest-VOR bench players in the CURRENT roster as drop candidates,
        # excluding starters and IR-status players
        bench_df = my_roster.copy()
        week_col = _week_col(current_week)
        bench_df["_vor"] = bench_df["VOR"].apply(lambda v: _safe_float(v) or 0.0)
        bench_df = bench_df.sort_values(by="_vor", ascending=True)
        for _, row in bench_df.iterrows():
            name = str(row.get("Name", ""))
            if name in starter_names or name in ir_names:
                continue
            suggested.append({
                "name": name,
                "position": str(row.get("Position", "")),
                "vor": _safe_float(row.get("VOR")),
            })
            if len(suggested) >= excess:
                break

    # Interleave mandatory drops so every numbered step is executable in Yahoo.
    # Walk the move list tracking roster size; when a pure add would overflow,
    # insert a drop for the next suggested candidate immediately before it.
    adjusted_moves: list[AddDropRecommendation] = []
    roster_size = current_roster_size
    suggested_iter = iter(suggested)
    drops_inserted = 0

    for move in moves:
        # If this is a pure add and roster is already full, insert a drop first
        if move.add and not move.drop and roster_size >= max_roster_size:
            try:
                drop_candidate = next(suggested_iter)
            except StopIteration:
                # No more candidates; keep the move as-is and let it overflow
                adjusted_moves.append(move)
                continue

            # Create a mandatory drop recommendation
            drop_rec = AddDropRecommendation(
                action="drop",
                drop=drop_candidate["name"],
                drop_position=drop_candidate["position"],
                drop_team="",
                drop_projection=None,
                drop_vor=drop_candidate["vor"],
                drop_status="",
                reason=(
                    f"Mandatory drop to make room for {move.add} "
                    f"(roster limit: {max_roster_size} players)"
                ),
            )
            adjusted_moves.append(drop_rec)
            roster_size -= 1
            drops_inserted += 1

        # Apply the original move
        if move.drop:
            roster_size -= 1
        if move.add:
            roster_size += 1
        adjusted_moves.append(move)

    moves = adjusted_moves

    # Re-simulate with the corrected move sequence
    simulated = simulate_post_move_roster(
        df, my_roster, moves, current_week
    )

    # Build warning describing what was auto-fixed
    if drops_inserted > 0:
        drop_names = ", ".join(
            f"{d['name']} ({d['position']})" for d in suggested[:drops_inserted]
        )
        original_excess = projected_size - max_roster_size
        roster_limit_warnings = [
            RosterLimitWarning(
                current_size=current_roster_size,
                max_size=max_roster_size,
                excess=original_excess,
                message=(
                    f"Roster limit enforced: {drops_inserted} additional drop(s) "
                    f"auto-inserted into the action plan to stay within the "
                    f"{max_roster_size}-player limit. "
                    f"Mandatory drops: {drop_names}."
                ),
                suggested_drops=suggested[:drops_inserted],
            )
        ]
    elif suggested:
        # Had excess but all moves were swaps (net 0) — shouldn't normally happen
        roster_limit_warnings = []
    else:
        roster_limit_warnings = []

    final_lineup = recommend_lineup(
        df, simulated, positions_config, current_week
    )
    remaining_warnings = flag_bench_depth_gaps(simulated, positions_config)

    # Generate comparison notes: for each add, compare its projection
    # against the starter at the same position in the final lineup
    comparison_notes = _generate_comparison_notes(moves, original_lineup, final_lineup)

    return ActionPlan(
        moves=moves,
        skipped_recs=skipped,
        simulated_roster=simulated,
        final_lineup=final_lineup,
        remaining_warnings=remaining_warnings + roster_limit_warnings,
        comparison_notes=comparison_notes,
    )


def _generate_comparison_notes(moves, original_lineup, final_lineup=None):
    notes = []
    if original_lineup is None:
        return notes

    # Build set of player names that are new adds in this round's moves,
    # so the bench-upgrade pass (pass 2) can skip them and avoid duplicating
    # notes already generated by pass 1.
    new_add_names = {
        str(rec.add).strip()
        for rec in moves
        if rec.add and str(rec.add).strip()
    }

    starter_by_pos = {}
    for s in original_lineup.starters:
        pos_set = {p.strip() for p in str(s.position).split(",")}
        for pos in pos_set:
            starter_by_pos[pos] = s

    # Pass 1: new adds vs original starters (existing behavior)
    for rec in moves:
        if rec.add is None or rec.add_projection is None:
            continue
        add_position = rec.add_position or ""
        add_proj = rec.add_projection
        add_pos_clean = add_position.strip()
        matching_starter = None
        for pos, starter in starter_by_pos.items():
            if pos == add_pos_clean or pos in add_pos_clean:
                matching_starter = starter
                break
        if matching_starter is not None:
            starter_proj = matching_starter.projection
            starter_name = matching_starter.player
            slot = matching_starter.slot
            if add_proj > starter_proj:
                notes.append(
                    f"{rec.add} ({add_pos_clean}, {add_proj}) replaces {starter_name} ({slot}, {starter_proj}) - higher projection"
                )
            else:
                notes.append(
                    f"{starter_name} ({slot}, {starter_proj}) starts over {rec.add} ({add_pos_clean}, {add_proj}) - higher projection, {rec.add} on bench"
                )
        else:
            notes.append(
                f"{rec.add} ({add_pos_clean}, {add_proj}) added to bench - no starter at same position to compete with"
            )

    # Pass 2: intra-roster bench upgrades — detect when an existing bench
    # player in the final lineup out-projects a starter at a position they
    # could fill, purely from week-to-week projection drift with no add/drop.
    if final_lineup is not None:
        final_starter_by_pos = {}
        for s in final_lineup.starters:
            pos_set = {p.strip() for p in str(s.position).split(",")}
            for pos in pos_set:
                final_starter_by_pos[pos] = s

        seen_pairs = set()  # (bench_name, starter_name) to avoid duplicates
        for bench_slot in (final_lineup.bench or []):
            bench_name = str(bench_slot.player).strip()
            bench_proj = bench_slot.projection
            bench_pos = str(bench_slot.position).strip()

            # Skip new adds — pass 1 already covered them
            if bench_name in new_add_names:
                continue
            if bench_proj is None:
                continue

            matching_starter = None
            for pos, starter in final_starter_by_pos.items():
                if pos == bench_pos or pos in bench_pos or bench_pos in pos:
                    matching_starter = starter
                    break

            if matching_starter is not None:
                starter_proj = matching_starter.projection
                starter_name = matching_starter.player
                starter_pos = str(matching_starter.position).strip()
                slot = matching_starter.slot
                pair_key = (bench_name, starter_name)
                if bench_proj > starter_proj and pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    notes.append(
                        bench_name + chr(32)+chr(40)+bench_pos+chr(44)+chr(32)+str(bench_proj)+chr(41)+chr(32)+chr(115)+chr(116)+chr(97)+chr(114)+chr(116)+chr(115)+chr(32)+chr(111)+chr(118)+chr(101)+chr(114)+chr(32)
                        + starter_name + chr(32)+chr(40)+starter_pos+chr(44)+chr(32)+str(starter_proj)+chr(41)+chr(32)+chr(45)+chr(32)+chr(104)+chr(105)+chr(103)+chr(104)+chr(101)+chr(114)+chr(32)+chr(112)+chr(114)+chr(111)+chr(106)+chr(101)+chr(99)+chr(116)+chr(105)+chr(111)+chr(110)
                    )

    return notes

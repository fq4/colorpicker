"""
Report generation — terminal output and markdown files.

Phase 1 of the dashboard: a clean terminal report plus a saved
Markdown record under reports/week_{n}.md.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from loguru import logger

from decision_engine import (
    AddDropRecommendation,
    ActionPlan,
    LineupRecommendation,
    LineupSlot,
    RecommendationReport,
    BenchDepthWarning,
    ByeWeekWarning,
    RosterLimitWarning,
)

REPORTS_DIR = "reports"

# ── Status display helpers ──────────────────────────────────────────────

_STATUS_MAP = {
    "Q": "⚠️ Q",
    "Q-": "⚠️ Q",
    "D": "⚠️ D",
    "O": "❌ O",
    "IR": "🦽 IR",
    "IR-R": "🦽 IR-R",
    "NFI": "🦽 NFI",
    "NFI-R": "🦽 NFI-R",
    "NFI-A": "🦽 NFI-A",
    "COVID": "🦽 COVID",
    "PUP": "PUP",
    "PUP-R": "PUP-R",
}


def _status_display(status) -> str:
    if status is None:
        return "✅"
    try:
        import pandas as pd
        if pd.isna(status):
            return "✅"
    except (TypeError, ValueError):
        pass
    s = str(status).strip()
    if s in ("", "nan", "None", "NA"):
        return "✅"
    for key, val in _STATUS_MAP.items():
        if s == key or s.startswith(key + "-"):
            return val
    return s


# ── Terminal report ─────────────────────────────────────────────────────


def build_terminal_report(
    report: RecommendationReport,
    current_week: int,
    season: int,
    config: dict,
    dry_run: bool = True,
) -> str:
    """Build a clean terminal report string.

    Sections:
      1. Current roster
      2. Recommended lineup
      3. Recommended adds/drops
      4. Bench depth warnings
    """
    lines: list[str] = []
    bar = "═" * 60

    lines.append(bar)
    lines.append(
        f"  Fantasy Football Decision Agent — Week {current_week} ({season})"
    )
    lines.append(bar)
    lines.append("")

    if report.hypothetical_drop:
        lines.append(f"  ⚠️  HYPOTHETICAL: roster shown assumes {report.hypothetical_drop} has already been dropped — this is a what-if simulation, no data was changed")
        lines.append("")

    locked = config.get("locked_positions", [])
    if locked:
        locked_str = ", ".join(locked)
        lines.append(f"  🔒 Locked positions (no add/drop suggestions): {locked_str}")
        lines.append("")

    # 1. Current roster (my players)
    if report.my_roster is not None:
        lines.append("--- Current Roster ---")
        lines.append(_format_my_roster_table(report.my_roster, current_week))
        lines.append("")

    # 2. Recommended lineup
    if report.lineup is not None:
        lines.append("--- Recommended Lineup ---")
        lines.append(_format_lineup_table(report.lineup, current_week))
        lines.append(f"  Total projection: {report.lineup.total_projection:.1f} pts")
        lines.append(f"  Total VOR: {report.lineup.total_vor:+.1f}")
        if report.lineup.flagged_starters:
            lines.append("")
            lines.append("  ⚠️  Injury flags:")
            for s in report.lineup.flagged_starters:
                alt_text = ""
                if s.bench_alternative:
                    alt_text = f" — alt: {s.bench_alternative} ({s.bench_alt_projection:.1f} pts)"
                lines.append(
                    f"    {s.player} ({s.slot}) — {s.status}{alt_text}"
                )
        lines.append("")

    # 3. Recommended adds/drops
    if report.add_drop_recs:
        lines.append("--- Recommended Adds/Drops ---")
        for i, rec in enumerate(report.add_drop_recs, 1):
            lines.append(_format_add_drop_rec_terminal(i, rec, current_week))
        lines.append("")

    # Negative VOR items are separated from main recommendations
    if report.low_value_recs:
        lines.append("--- Not Recommended (Negative VOR) ---")
        for i, rec in enumerate(report.low_value_recs, 1):
            lines.append(_format_add_drop_rec_terminal(i, rec, current_week))
        lines.append("")

    # 4. Bench depth warnings
    if report.depth_warnings:
        lines.append("--- Bench Depth Warnings ---")
        for w in report.depth_warnings:
            lines.append(f"  ⚠️ {w.message}")
        lines.append("")

    # 5. Upcoming Byes
    if report.bye_week_warnings:
        lines.append("--- Upcoming Byes ---")
        lines.append("")
        for w in report.bye_week_warnings:
            lines.append(f"  {w.message}")
        lines.append("")

    # 6. Action Plan (terminal)
    if report.action_plan is not None:
        lines.append(_format_action_plan_terminal(report.action_plan, current_week))
        lines.append("")

    # Footer
    waiver_priority = config.get("waiver_priority", "N/A")
    scoring = config.get("scoring_type", "unknown")
    waiver_type = config.get("waiver_type", "unknown")
    lines.append(bar)
    if dry_run:
        lines.append("  Dry-run mode — no actions submitted")
        lines.append("  Use --execute to submit recommendations")
    else:
        lines.append("  Execute mode requested — executor.py is NOT wired to Yahoo")
        lines.append("  No transactions were actually submitted.")
    lines.append(f"  Waiver priority: {waiver_priority} ({waiver_type})")
    lines.append(f"  Scoring: {scoring}")
    lines.append(bar)

    return "\n".join(lines)


def _format_my_roster_table(df, current_week: int) -> str:
    """Format the current roster as a terminal table."""
    week_col = f"Week {current_week}"
    headers = ["Player", "Team", "Position", "Status", "Proj", "VOR"]
    rows = []
    for _, row in df.iterrows():
        status = _status_display(row.get("Status"))
        proj = _format_float(row.get(week_col))
        vor = _format_float(row.get("VOR"), signed=True)
        rows.append([
            str(row.get("Name", "")),
            str(row.get("Team", "")),
            str(row.get("Position", "")),
            status,
            proj,
            vor,
        ])
    return _format_table(headers, rows, col_widths=[20, 6, 10, 8, 8, 8])


def _format_lineup_table(lineup: LineupRecommendation, current_week: int) -> str:
    """Format the recommended lineup as a terminal table."""
    headers = ["Slot", "Player", "Team", "Pos", "Proj", "VOR", "Status"]
    rows = []
    for s in lineup.starters:
        proj = _format_float(s.projection)
        vor = _format_float(s.vor, signed=True)
        rows.append([s.slot, s.player, s.team, s.position, proj, vor, _status_display(s.status)])
    header_row = _format_table(headers, rows, col_widths=[7, 20, 6, 8, 8, 8, 8])

    # Append flagged notes
    for s in lineup.starters:
        if s.flagged and s.bench_alternative:
            header_row += f"\n  ⚠️ {s.player} ({s.slot}) is {s.status} → alt: {s.bench_alternative} ({_format_float(s.bench_alt_projection)} pts)"

    return header_row


def _format_add_drop_rec_terminal(i: int, rec: AddDropRecommendation, week: int) -> str:
    """Format a single add/drop recommendation for the terminal."""
    flag = "⚠️ " if rec.flagged else "  "
    conf_label = rec.confidence.upper()[:3]

    lines = []
    lines.append(f"{flag}[{conf_label}] {i}. {rec.action.upper()}")

    if rec.add:
        add_proj = _format_float(rec.add_projection)
        add_vor = _format_float(rec.add_vor, signed=True)
        add_status = _status_display(rec.add_status)
        lines.append(f"     Add: {rec.add} ({rec.add_position}, {rec.add_team}) — proj {add_proj}, VOR {add_vor}, {add_status}")

    if rec.drop:
        drop_proj = _format_float(rec.drop_projection)
        drop_vor = _format_float(rec.drop_vor, signed=True)
        drop_status = _status_display(rec.drop_status)
        lines.append(f"     Drop: {rec.drop} ({rec.drop_position}, {rec.drop_team}) — proj {drop_proj}, VOR {drop_vor}, {drop_status}")

    if rec.vor_gain is not None:
        lines.append(f"     VOR gain: {_format_float(rec.vor_gain, signed=True)}")

    lines.append(f"     Reason: {rec.reason}")

    if rec.flagged and rec.flag_reason:
        lines.append(f"     ⚠️ FLAG: {rec.flag_reason}")

    return "\n".join(lines)


def _format_table(headers: list[str], rows: list[list[str]], col_widths: list[int]) -> str:
    """Format a simple fixed-width table for terminal output."""
    lines = []

    def _fmt_row(items):
        cells = []
        for idx, item in enumerate(items):
            w = col_widths[idx] if idx < len(col_widths) else 10
            cells.append(str(item).ljust(w)[:w])
        return "  ".join(cells).rstrip()

    lines.append(_fmt_row(headers))
    lines.append("  ".join("─" * w for w in col_widths))
    for row in rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)


def _format_float(val, signed: bool = False) -> str:
    if val is None:
        return "--"
    try:
        v = float(val)
        if signed:
            return f"{v:+.1f}"
        return f"{v:.1f}"
    except (TypeError, ValueError):
        return "--"


# ── Markdown report ─────────────────────────────────────────────────────


def build_markdown_report(
    report: RecommendationReport,
    current_week: int,
    season: int,
    config: dict,
    dry_run: bool = True,
) -> str:
    """Build a Markdown report string for saving to reports/week_{n}.md."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    lines.append(f"# Fantasy Football Decision Report — Week {current_week} ({season})")
    lines.append("")
    lines.append(f"_Generated: {now}_")
    lines.append("")

    if report.hypothetical_drop:
        lines.append(f"> ⚠️ **HYPOTHETICAL:** This report assumes **{report.hypothetical_drop}** has already been dropped. This is a what-if simulation; no data was changed.")
        lines.append("")

    locked = config.get("locked_positions", [])
    if locked:
        lines.append(f"> 🔒 **Locked positions** (no add/drop suggestions): {', '.join(locked)}")
        lines.append("")

    # Config summary
    lines.append("## Configuration")
    lines.append("")
    lines.append(f"| Setting | Value |")
    lines.append(f"|---------|-------|")
    lines.append(f"| Team | {config.get('team_name', 'N/A')} |")
    lines.append(f"| League ID | {config.get('league_id', 'N/A')} |")
    lines.append(f"| Scoring | {config.get('scoring_type', 'N/A')} |")
    lines.append(f"| Waiver type | {config.get('waiver_type', 'N/A')} |")
    lines.append(f"| Waiver priority | {config.get('waiver_priority', 'N/A')} |")
    mode_str = "Dry-run (no actions submitted)" if dry_run else "Execute requested — NOT actually sent (executor not implemented)"
    lines.append(f"| Mode | {mode_str} |")
    lines.append("")

    # 1. Current roster
    if report.my_roster is not None:
        lines.append("## Current Roster")
        lines.append("")
        lines.append(_md_my_roster_table(report.my_roster, current_week))
        lines.append("")

    # 2. Recommended lineup
    if report.lineup is not None:
        lines.append("## Recommended Lineup")
        lines.append("")
        lines.append(_md_lineup_table(report.lineup, current_week))
        lines.append("")
        lines.append(f"**Total projection:** {report.lineup.total_projection:.1f} pts")
        lines.append("")
        lines.append(f"**Total VOR:** {report.lineup.total_vor:+.1f}")
        lines.append("")

        if report.lineup.flagged_starters:
            lines.append("### Injury Flags")
            lines.append("")
            for s in report.lineup.flagged_starters:
                alt_text = ""
                if s.bench_alternative:
                    alt_text = f" — recommended alternative: **{s.bench_alternative}** ({s.bench_alt_projection:.1f} pts)"
                lines.append(f"- **{s.player}** ({s.slot}) is {s.status}{alt_text}")
            lines.append("")

    # 3. Recommended adds/drops
    if report.add_drop_recs:
        lines.append("## Recommended Adds/Drops")
        lines.append("")
        for i, rec in enumerate(report.add_drop_recs, 1):
            lines.append(_md_add_drop_rec(i, rec, current_week))
    else:
        lines.append("## Recommended Adds/Drops")
        lines.append("")
        lines.append("No add/drop recommendations at this time.")
        lines.append("")

    if report.low_value_recs:
        lines.append("## Not Recommended (Negative VOR)")
        lines.append("")
        lines.append("The following were excluded from main recommendations due to negative VOR gain:")
        lines.append("")
        for i, rec in enumerate(report.low_value_recs, 1):
            lines.append(_md_add_drop_rec(i, rec, current_week))

    # 4. Bench depth warnings
    if report.depth_warnings:
        lines.append("## Bench Depth Warnings")
        lines.append("")
        for w in report.depth_warnings:
            lines.append(f"- ⚠️ {w.message}")
            if w.ir_players:
                lines.append(f"  - IR players: {', '.join(w.ir_players)}")
        lines.append("")
    else:
        lines.append("## Bench Depth")
        lines.append("")
        lines.append("No bench depth warnings.")
        lines.append("")

    # 5. Upcoming Byes
    if report.bye_week_warnings:
        lines.append("## Upcoming Byes")
        lines.append("")
        lines.append("Players with bye weeks in the coming weeks:")
        lines.append("")
        for w in report.bye_week_warnings:
            lines.append(f"- {w.message}")
        lines.append("")

    # 6. Action Plan (markdown)
    if report.action_plan is not None:
        lines.append(_md_action_plan(report.action_plan, current_week))
        lines.append("")
        lines.append("---")
        lines.append("")

    # 6. Season-long tracking note
    lines.append("## Tracking")
    lines.append("")
    filename = f"league_{config.get('league_id', 'N/A')}_team_{config.get('team_id', 'N/A')}_week_{current_week}.md"
    lines.append(f"This report is saved as `reports/{filename}` for season-long accuracy tracking.")
    lines.append("")

    return "\n".join(lines)


def _md_my_roster_table(df, current_week: int) -> str:
    week_col = f"Week {current_week}"
    lines = ["| Player | Team | Position | Status | Proj | VOR |", "|--------|------|----------|--------|------|-----|"]
    for _, row in df.iterrows():
        status = _status_display(row.get("Status"))
        proj = _md_float(row.get(week_col))
        vor = _md_float(row.get("VOR"), signed=True)
        lines.append(f"| {row.get('Name','')} | {row.get('Team','')} | {row.get('Position','')} | {status} | {proj} | {vor} |")
    return "\n".join(lines)


def _md_lineup_table(lineup: LineupRecommendation, current_week: int) -> str:
    lines = ["| Slot | Player | Team | Position | Proj | VOR | Status |", "|------|--------|------|----------|------|-----|--------|"]
    for s in lineup.starters:
        proj = _md_float(s.projection)
        vor = _md_float(s.vor, signed=True)
        lines.append(
            f"| {s.slot} | {s.player} | {s.team} | {s.position} | {proj} | {vor} | {_status_display(s.status)} |"
        )
    return "\n".join(lines)


def _md_add_drop_rec(i: int, rec: AddDropRecommendation, week: int) -> str:
    lines = [f"### {i}. {rec.action.replace('_', ' ').title()} — Confidence: {rec.confidence.upper()}"]

    if rec.flagged:
        lines.append("")
        lines.append(f"> ⚠️ **FLAGGED**: {rec.flag_reason}")

    lines.append("")

    if rec.add:
        lines.append(f"- **Add:** {rec.add} ({rec.add_position}, {rec.add_team})")
        lines.append(f"  - WK{week} projection: {_md_float(rec.add_projection)} pts")
        lines.append(f"  - VOR: {_md_float(rec.add_vor, signed=True)}")
        lines.append(f"  - Status: {_status_display(rec.add_status) or 'Healthy'}")
        if rec.add_owned_pct:
            lines.append(f"  - % owned: {rec.add_owned_pct}")

    if rec.drop:
        lines.append(f"- **Drop:** {rec.drop} ({rec.drop_position}, {rec.drop_team})")
        lines.append(f"  - WK{week} projection: {_md_float(rec.drop_projection)} pts")
        lines.append(f"  - VOR: {_md_float(rec.drop_vor, signed=True)}")
        lines.append(f"  - Status: {_status_display(rec.drop_status) or 'Healthy'}")

    if rec.vor_gain is not None:
        lines.append(f"- **VOR gain:** {_md_float(rec.vor_gain, signed=True)}")

    lines.append("")
    lines.append(f"**Reason:** {rec.reason}")
    lines.append("")
    return "\n".join(lines)


def _md_float(val, signed: bool = False) -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if signed:
            return f"{v:+.1f}"
        return f"{v:.1f}"
    except (TypeError, ValueError):
        return "N/A"


# ── Save ──────────────────────────────────────────────────────────────────


def save_markdown_report(content: str, week: int, league_id: int, team_id: int, reports_dir: str = REPORTS_DIR) -> str:
    """Write the markdown report to reports/league_{league_id}_team_{team_id}_week_{n}.md."""
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, f"league_{league_id}_team_{team_id}_week_{week}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Report saved to {path}")
    return path


def save_both(
    terminal_report: str,
    markdown_report: str,
    week: int,
    league_id: int,
    team_id: int,
    reports_dir: str = REPORTS_DIR,
) -> str:
    """Save only the markdown version; the terminal version is printed to stdout."""
    return save_markdown_report(markdown_report, week, league_id, team_id, reports_dir)


# ── Action Plan formatting ──────────────────────────────────────────────


def _format_action_plan_terminal(plan: ActionPlan, week: int) -> str:
    lines: list[str] = []
    bar = "=" * 60

    lines.append(bar)
    lines.append("  YOUR ACTION PLAN")
    lines.append(bar)
    lines.append("")

    # Moves to make
    if plan.moves:
        lines.append("### Moves to Make (in order)")
        lines.append("")
        for i, rec in enumerate(plan.moves, 1):
            if rec.drop:
                lines.append(f"  {i}. Add {rec.add} ({rec.add_position}) - drop {rec.drop} ({rec.drop_position})")
            else:
                lines.append(f"  {i}. Add {rec.add} ({rec.add_position})")

            if rec.vor_gain is not None:
                lines.append(f"     VOR gain: {_format_float(rec.vor_gain, signed=True)}")

            # Injury flag note
            if rec.add_status and rec.add_status.strip() in ("Q", "Q-"):
                lines.append(f"     **Double-check injury status before game time**: {rec.add} is Q")
            elif rec.add_status:
                lines.append(f"     Status: {_status_display(rec.add_status)}")

            lines.append("")
    else:
        lines.append("### Moves to Make")
        lines.append("No non-flagged moves to make.")
        lines.append("")

    # Skipped
    if plan.skipped_recs:
        lines.append("### Skipped (Flagged)")
        lines.append("")
        for i, rec in enumerate(plan.skipped_recs, 1):
            if rec.drop:
                lines.append(f"  {i}. {rec.add} / {rec.drop} - **SKIPPED**")
            else:
                lines.append(f"  {i}. {rec.add} - **SKIPPED**")
            if rec.flag_reason:
                lines.append(f"     Reason: {rec.flag_reason}")
            lines.append("")

    # Resulting lineup
    if plan.final_lineup is not None:
        lines.append("### Your Resulting Starting Lineup")
        lines.append("")
        lines.append(_format_lineup_table(plan.final_lineup, week))
        lines.append(f"  Total projection: {_format_float(plan.final_lineup.total_projection)} pts")
        lines.append(f"  Total VOR: {_format_float(plan.final_lineup.total_vor, signed=True)}")
        lines.append("")

        # Position comparison notes
        if plan.comparison_notes:
            lines.append("### Position Comparison Notes")
            lines.append("")
            for note in plan.comparison_notes:
                lines.append(f"  - {note}")
            lines.append("")

        # Bench after these moves
        if plan.final_lineup.bench:
            lines.append("### Your Bench After These Moves")
            lines.append("")
            bench_headers = ["Slot", "Player", "Team", "Pos", "Proj", "VOR", "Status"]
            bench_rows = []
            for s in plan.final_lineup.bench:
                proj = _format_float(s.projection)
                vor = _format_float(s.vor, signed=True)
                bench_rows.append([s.slot, s.player, s.team, s.position, proj, vor, _status_display(s.status)])
            lines.append(_format_table(bench_headers, bench_rows, col_widths=[7, 20, 6, 8, 8, 8, 8]))
            lines.append("")

    # Injury reminders for added players in lineup
    injury_notes = []
    if plan.final_lineup:
        for s in plan.final_lineup.starters:
            if s.flagged:
                injury_notes.append(f"{s.player} ({s.slot}) is {s.status}")
    if injury_notes:
        lines.append("### Injury Reminder")
        lines.append("")
        for note in injury_notes:
            lines.append(f"  - Double-check before game time: {note}")
        lines.append("")

    # Remaining warnings
    if plan.remaining_warnings:
        lines.append("### Remaining Warnings")
        lines.append("")
        for w in plan.remaining_warnings:
            if isinstance(w, RosterLimitWarning):
                lines.append(f"  - {w.message}")
                for d in w.suggested_drops:
                    vor_str = f"{d['vor']:+.1f}" if d.get("vor") is not None else "N/A"
                    lines.append(f"    - {d['name']} ({d['position']}) — VOR {vor_str}")
            else:
                lines.append(f"  - {w.message}")
                if getattr(w, "ir_players", None):
                    lines.append(f"    - IR players: {', '.join(w.ir_players)}")
        lines.append("")

    return "\n".join(lines)


def _md_action_plan(plan: ActionPlan, week: int) -> str:
    lines: list[str] = []

    lines.append("## Your Action Plan")
    lines.append("")

    # Moves to make
    if plan.moves:
        lines.append("### Moves to Make (in order)")
        lines.append("")
        for i, rec in enumerate(plan.moves, 1):
            if rec.drop:
                lines.append(f"{i}. **Add:** {rec.add} ({rec.add_position}, {rec.add_team}) - **Drop:** {rec.drop} ({rec.drop_position})")
            else:
                lines.append(f"{i}. **Add:** {rec.add} ({rec.add_position}, {rec.add_team})")

            if rec.vor_gain is not None:
                lines.append(f"   - VOR gain: {_md_float(rec.vor_gain, signed=True)}")

            if rec.add_status and rec.add_status.strip() in ("Q", "Q-"):
                lines.append(f"   - **Double-check injury status before game time**: {rec.add} is Q")
            elif rec.add_status:
                lines.append(f"   - Status: {_status_display(rec.add_status)}")

            lines.append("")
    else:
        lines.append("### Moves to Make")
        lines.append("No non-flagged moves to make.")
        lines.append("")

    # Skipped
    if plan.skipped_recs:
        lines.append("### Skipped (Flagged)")
        lines.append("")
        for i, rec in enumerate(plan.skipped_recs, 1):
            if rec.drop:
                lines.append(f"{i}. {rec.add} / {rec.drop} - **SKIPPED**: {rec.flag_reason or 'depth gap warning'}")
            else:
                lines.append(f"{i}. {rec.add} - **SKIPPED**: {rec.flag_reason or 'flagged'}")
            lines.append("")

    # Resulting lineup
    if plan.final_lineup is not None:
        lines.append("### Your Resulting Starting Lineup")
        lines.append("")
        lines.append(_md_lineup_table(plan.final_lineup, week))
        lines.append("")
        lines.append(f"**Total projection:** {_md_float(plan.final_lineup.total_projection)} pts")
        lines.append("")
        lines.append(f"**Total VOR:** {_md_float(plan.final_lineup.total_vor, signed=True)}")
        lines.append("")

        # Position comparison notes
        if plan.comparison_notes:
            lines.append("### Position Comparison Notes")
            lines.append("")
            for note in plan.comparison_notes:
                lines.append(f"- **{note}**")
            lines.append("")

        # Bench after these moves
        if plan.final_lineup.bench:
            lines.append("### Your Bench After These Moves")
            lines.append("")
            lines.append("| Slot | Player | Team | Position | Proj | VOR | Status |")
            lines.append("|------|--------|------|----------|------|-----|--------|")
            for s in plan.final_lineup.bench:
                proj = _md_float(s.projection)
                vor = _md_float(s.vor, signed=True)
                lines.append(
                    f"| {s.slot} | {s.player} | {s.team} | {s.position} | {proj} | {vor} | {_status_display(s.status)} |"
                )
            lines.append("")

    # Injury reminders
    injury_notes = []
    if plan.final_lineup:
        for s in plan.final_lineup.starters:
            if s.flagged:
                injury_notes.append(f"**{s.player}** ({s.slot}) is {s.status}")
    if injury_notes:
        lines.append("### Injury Reminder")
        lines.append("")
        for note in injury_notes:
            lines.append(f"- Double-check before game time: {note}")
        lines.append("")

    # Remaining warnings
    if plan.remaining_warnings:
        lines.append("### Remaining Warnings")
        lines.append("")
        for w in plan.remaining_warnings:
            if isinstance(w, RosterLimitWarning):
                lines.append(f"- {w.message}")
                for d in w.suggested_drops:
                    vor_str = f"{d['vor']:+.1f}" if d.get("vor") is not None else "N/A"
                    lines.append(f"  - {d['name']} ({d['position']}) — VOR {vor_str}")
            else:
                lines.append(f"- {w.message}")
                if getattr(w, "ir_players", None):
                    lines.append(f"  - IR players: {', '.join(w.ir_players)}")
        lines.append("")

    return "\n".join(lines)

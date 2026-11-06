import csv
import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path


_LEAGUE_FILENAME = re.compile(
    r"league_(?P<league>[^_]+)_week_(?P<week>\d+)_(?P<stamp>\d{8}_\d{6})\.csv$"
)
_WEEK_FILENAME = re.compile(r"week_(?P<week>\d+)_(?P<stamp>\d{8}_\d{6})\.csv$")
_WEEK_COLUMN = re.compile(r"^Week\s+(\d+)$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    league_id TEXT NOT NULL,
    week_at_scrape INTEGER NOT NULL,
    snapshot_time TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    player_count INTEGER NOT NULL,
    is_pre_kickoff INTEGER NOT NULL,
    UNIQUE (league_id, week_at_scrape, snapshot_time, content_sha256)
);
CREATE TABLE IF NOT EXISTS projections (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    league_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position TEXT NOT NULL,
    target_week INTEGER NOT NULL,
    snapshot_time TEXT NOT NULL,
    value REAL NOT NULL,
    is_clean INTEGER NOT NULL,
    projection_age_hours REAL,
    PRIMARY KEY (snapshot_id, player_id, target_week)
);
CREATE INDEX IF NOT EXISTS idx_projections_target
    ON projections (league_id, target_week, is_clean, snapshot_time);
CREATE TABLE IF NOT EXISTS actuals (
    league_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position TEXT NOT NULL,
    target_week INTEGER NOT NULL,
    value REAL NOT NULL,
    is_bye INTEGER NOT NULL,
    engine_is_bye INTEGER NOT NULL DEFAULT 0,
    clean_projection_value REAL,
    nfl_team TEXT,
    is_rostered INTEGER NOT NULL DEFAULT 0,
    rostered_team_count INTEGER NOT NULL DEFAULT 0,
    source_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    source_snapshot_time TEXT NOT NULL,
    PRIMARY KEY (league_id, player_id, target_week)
);
CREATE INDEX IF NOT EXISTS idx_actuals_target
    ON actuals (league_id, target_week, player_id);
"""


def normalize_player_id(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return text


def parse_snapshot_filename(path, league_id):
    name = Path(path).name
    match = _LEAGUE_FILENAME.fullmatch(name)
    if match:
        if match.group("league") != str(league_id):
            return None
    else:
        match = _WEEK_FILENAME.fullmatch(name)
        if not match:
            return None

    snapshot_time = datetime.strptime(
        match.group("stamp"), "%Y%m%d_%H%M%S"
    ).astimezone()
    return int(match.group("week")), snapshot_time


def _as_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "nan", "NaN", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _lock_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _position(value):
    return str(value or "").split(",")[0].strip().upper()


def _is_rostered(value):
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {"", "nan", "none", "null"}:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


class AnalyticsStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self._ensure_column("projections", "projection_age_hours", "REAL")
        self._ensure_column(
            "actuals", "engine_is_bye", "INTEGER NOT NULL DEFAULT 0"
        )
        self._ensure_column("actuals", "clean_projection_value", "REAL")
        self._ensure_column("actuals", "nfl_team", "TEXT")
        self._ensure_column(
            "actuals", "is_rostered", "INTEGER NOT NULL DEFAULT 0"
        )
        self._ensure_column(
            "actuals", "rostered_team_count", "INTEGER NOT NULL DEFAULT 0"
        )
        self.connection.commit()

    def _ensure_column(self, table, column, definition):
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def close(self):
        self.connection.close()

    def ingest_file(self, path, league_id, kickoff_times):
        path = Path(path)
        parsed = parse_snapshot_filename(path, league_id)
        if parsed is None:
            raise ValueError(f"Unrecognized or mismatched snapshot filename: {path.name}")
        week_at_scrape, snapshot_time = parsed
        timestamp = snapshot_time.isoformat(timespec="seconds")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            columns = reader.fieldnames or []
        week_columns = {
            int(match.group(1)): column
            for column in columns
            if (match := _WEEK_COLUMN.fullmatch(column))
        }

        current_lock = _lock_time(kickoff_times.get(week_at_scrape))
        is_pre_kickoff = current_lock is not None and snapshot_time < current_lock
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO snapshots
                (league_id, week_at_scrape, snapshot_time, source_path,
                 content_sha256, player_count, is_pre_kickoff)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(league_id),
                week_at_scrape,
                timestamp,
                str(path.resolve()),
                digest,
                len(rows),
                int(is_pre_kickoff),
            ),
        )
        self.connection.commit()
        inserted = cursor.rowcount > 0
        if not inserted:
            existing = self.connection.execute(
                """
                SELECT id, player_count FROM snapshots
                WHERE league_id = ? AND week_at_scrape = ?
                  AND snapshot_time = ? AND content_sha256 = ?
                """,
                (str(league_id), week_at_scrape, timestamp, digest),
            ).fetchone()
            snapshot_id = existing["id"]
        else:
            snapshot_id = cursor.lastrowid

        roster_rows = []
        for row in rows:
            owner_id = row.get("Owner ID")
            team = str(row.get("Team") or "").strip().upper()
            if _is_rostered(owner_id) and team:
                roster_rows.append((row, team))
        team_stats = {}
        for target_week, column in week_columns.items():
            if target_week >= week_at_scrape:
                continue
            actuals_by_team = {}
            for row, team in roster_rows:
                actuals_by_team.setdefault(team, []).append(
                    _as_float(row.get(column))
                )
            for team, values in actuals_by_team.items():
                team_stats[(target_week, team)] = {
                    "rostered_team_count": len(values),
                    "engine_is_bye": int(
                        len(values) >= 3
                        and all(value is None or value == 0 for value in values)
                    ),
                }

        projection_values = []
        for row in rows:
            player_id = normalize_player_id(row.get("ID"))
            if not player_id:
                continue
            player_name = str(row.get("Name") or "").strip()
            position = _position(row.get("Position"))

            for target_week, column in week_columns.items():
                value = _as_float(row.get(column))
                if value is None:
                    continue
                if target_week >= week_at_scrape:
                    target_lock = _lock_time(kickoff_times.get(target_week))
                    is_clean = target_lock is not None and snapshot_time < target_lock
                    projection_age_hours = (
                        (target_lock - snapshot_time).total_seconds() / 3600
                        if target_lock is not None
                        else None
                    )
                    projection_values.append(
                        (
                            snapshot_id,
                            str(league_id),
                            player_id,
                            player_name,
                            position,
                            target_week,
                            timestamp,
                            value,
                            int(is_clean),
                            projection_age_hours,
                        )
                    )

        self.connection.executemany(
            """
            INSERT INTO projections
                (snapshot_id, league_id, player_id, player_name, position,
                 target_week, snapshot_time, value, is_clean, projection_age_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (snapshot_id, player_id, target_week) DO UPDATE SET
                player_name = excluded.player_name,
                position = excluded.position,
                value = excluded.value,
                is_clean = excluded.is_clean,
                projection_age_hours = excluded.projection_age_hours
            """,
            projection_values,
        )

        actual_values = []
        for row in rows:
            player_id = normalize_player_id(row.get("ID"))
            if not player_id:
                continue
            player_name = str(row.get("Name") or "").strip()
            position = _position(row.get("Position"))
            team = str(row.get("Team") or "").strip().upper()
            owner_id = row.get("Owner ID")
            is_rostered = _is_rostered(owner_id)

            for target_week, column in week_columns.items():
                if target_week >= week_at_scrape:
                    continue
                value = _as_float(row.get(column))
                if value is None:
                    continue
                clean_projection = self.connection.execute(
                    """
                    SELECT value FROM projections
                    WHERE league_id = ? AND player_id = ? AND target_week = ?
                      AND is_clean = 1
                    ORDER BY snapshot_time DESC
                    LIMIT 1
                    """,
                    (str(league_id), player_id, target_week),
                ).fetchone()
                clean_projection_value = (
                    clean_projection["value"] if clean_projection is not None else None
                )
                is_bye = (
                    clean_projection_value is None or clean_projection_value == 0
                )
                team_stats_for_week = team_stats.get((target_week, team), {})
                actual_values.append(
                    (
                        str(league_id),
                        player_id,
                        player_name,
                        position,
                        target_week,
                        value,
                        int(is_bye),
                        team_stats_for_week.get("engine_is_bye", 0),
                        clean_projection_value,
                        team,
                        int(is_rostered),
                        team_stats_for_week.get("rostered_team_count", 0),
                        snapshot_id,
                        timestamp,
                    )
                )

        self.connection.executemany(
            """
            INSERT INTO actuals
                (league_id, player_id, player_name, position, target_week,
                 value, is_bye, engine_is_bye, clean_projection_value, nfl_team,
                 is_rostered, rostered_team_count, source_snapshot_id,
                 source_snapshot_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (league_id, player_id, target_week) DO UPDATE SET
                player_name = excluded.player_name,
                position = excluded.position,
                value = excluded.value,
                is_bye = excluded.is_bye,
                engine_is_bye = excluded.engine_is_bye,
                clean_projection_value = excluded.clean_projection_value,
                nfl_team = excluded.nfl_team,
                is_rostered = excluded.is_rostered,
                rostered_team_count = excluded.rostered_team_count,
                source_snapshot_id = excluded.source_snapshot_id,
                source_snapshot_time = excluded.source_snapshot_time
            WHERE excluded.source_snapshot_time >= actuals.source_snapshot_time
            """,
            actual_values,
        )
        self.connection.commit()
        return {
            "inserted": inserted,
            "snapshot_id": snapshot_id,
            "player_count": len(rows),
            "week_at_scrape": week_at_scrape,
            "snapshot_time": timestamp,
        }

    def ingest_directory(self, directory, league_id, kickoff_times):
        directory = Path(directory)
        paths = []
        for path in directory.glob("*.csv"):
            if parse_snapshot_filename(path, league_id) is not None:
                paths.append(path)
        paths.sort(key=lambda path: parse_snapshot_filename(path, league_id)[1])
        return [
            (path, self.ingest_file(path, league_id, kickoff_times))
            for path in paths
        ]

    def snapshot_inventory(self, league_id):
        return self.connection.execute(
            """
            SELECT week_at_scrape, snapshot_time, player_count, source_path,
                   is_pre_kickoff
            FROM snapshots
            WHERE league_id = ?
            ORDER BY snapshot_time
            """,
            (str(league_id),),
        ).fetchall()

    def clean_projections(self, league_id, target_week):
        rows = self.connection.execute(
            """
            SELECT p.player_id, p.player_name, p.position, p.target_week,
                   p.snapshot_time, p.value, p.projection_age_hours
            FROM projections p
            JOIN snapshots s ON s.id = p.snapshot_id
            WHERE p.league_id = ? AND p.target_week = ? AND p.is_clean = 1
            ORDER BY p.snapshot_time DESC, s.id DESC
            """,
            (str(league_id), int(target_week)),
        ).fetchall()
        latest_by_player = {}
        for row in rows:
            latest_by_player.setdefault(row["player_id"], row)
        return list(latest_by_player.values())

    def actuals(self, league_id, before_week=None):
        query = """
            SELECT player_id, player_name, position, target_week, value, is_bye,
                   engine_is_bye, clean_projection_value, nfl_team, is_rostered,
                   rostered_team_count
            FROM actuals WHERE league_id = ?
        """
        params = [str(league_id)]
        if before_week is not None:
            query += " AND target_week < ?"
            params.append(int(before_week))
        query += " ORDER BY player_id, target_week"
        return self.connection.execute(query, params).fetchall()

    def snapshot_week(self, league_id, week_at_scrape):
        return self.connection.execute(
            """
            SELECT id, snapshot_time, player_count, is_pre_kickoff
            FROM snapshots
            WHERE league_id = ? AND week_at_scrape = ?
            ORDER BY snapshot_time DESC
            LIMIT 1
            """,
            (str(league_id), int(week_at_scrape)),
        ).fetchone()

    def has_actual(self, league_id, player_id, target_week):
        return self.connection.execute(
            """
            SELECT value FROM actuals
            WHERE league_id = ? AND player_id = ? AND target_week = ?
            """,
            (str(league_id), str(player_id), int(target_week)),
        ).fetchone()

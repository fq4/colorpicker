"""Run one-shot Windows scheduler jobs for the weekly fantasy-football workflow."""
from __future__ import annotations

import argparse
import base64
import html
import logging
import os
import shutil
import subprocess
import sys
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from data_layer import load_config
from scheduling import game_week, next_lock


LOGGER = logging.getLogger("scheduler")
ROOT = Path(__file__).resolve().parent


class JobLockError(RuntimeError):
    """Raised when another scheduler job holds the process lock."""


class LateSnapshotError(ValueError):
    """Raised when a snapshot would be taken at or after its kickoff."""


class FileLock(AbstractContextManager):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None
        self._locking_module = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                self._locking_module = msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                self._locking_module = fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise JobLockError(f"Another scheduler job holds {self.path}.") from exc
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name == "nt":
                self._locking_module.locking(
                    self.handle.fileno(), self._locking_module.LK_UNLCK, 1
                )
            else:
                self._locking_module.flock(
                    self.handle.fileno(), self._locking_module.LOCK_UN
                )
            self.handle.close()
            self.handle = None
        return False


@dataclass(frozen=True)
class JobPlan:
    job: str
    week: int
    commands: tuple[str, ...]
    opponent_id: int | None = None
    completed_week: int | None = None


def _week_value(mapping: Mapping, week: int):
    return mapping.get(week, mapping.get(str(week)))


def _config_path(value: str | Path, project_root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _python_command(*args: str, python: str | None = None) -> list[str]:
    return [python or sys.executable, *args]


def _format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


def _validate_snapshot_lock(week: int, now: datetime, kickoff_times: Mapping) -> datetime:
    value = _week_value(kickoff_times, week)
    if value is None:
        raise ValueError(f"No kickoff time configured for Week {week}.")
    kickoff = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if kickoff.tzinfo is None or kickoff.utcoffset() is None:
        raise ValueError(f"Kickoff time for Week {week} must include a timezone.")
    if now >= kickoff:
        raise LateSnapshotError(
            f"Week {week} kickoff has passed; refusing a stale clean snapshot."
        )
    return kickoff


def build_job_plan(
    job: str,
    now: datetime,
    config: Mapping,
    config_path: str | Path = "config.yaml",
    python: str | None = None,
    project_root: Path = ROOT,
) -> JobPlan:
    kickoff_times = config.get("kickoff_times", {})
    config_arg = str(_config_path(config_path, project_root))

    if job == "snapshot":
        week, _ = next_lock(now, kickoff_times)
        _validate_snapshot_lock(week, now, kickoff_times)
        command = _python_command(
            "-m", "analytics", "--config", config_arg,
            "snapshot", "--week", str(week), python=python,
        )
        return JobPlan(job, week, (_format_command(command),))

    if job == "weekly":
        week = game_week(now, kickoff_times)
        schedule = config.get("schedule", {})
        opponent_id = _week_value(schedule, week) if isinstance(schedule, Mapping) else None
        if opponent_id is None:
            raise ValueError(
                f"No scheduled opponent ID for Week {week}; update config.yaml schedule."
            )
        opponent_id = int(opponent_id)
        command = _python_command(
            "run_weekly.py", "--force-refresh", "--week", str(week), "--dry-run",
            "--opponent-id", str(opponent_id), "--config", config_arg, python=python,
        )
        return JobPlan(
            job,
            week,
            (
                _format_command(command),
                f"Copy reports/league_{config['league_id']}_team_{config['team_id']}_"
                f"week_{week}.md to reports/latest.md",
            ),
            opponent_id=opponent_id,
        )

    if job == "settle":
        week = game_week(now, kickoff_times)
        completed_week = week - 1
        if completed_week < 1:
            raise ValueError("There is no completed week to settle yet.")
        api_step = (
            "Python API: get_fresh_data() once, then capture_actual_results() "
            f"for Week {completed_week}"
        )
        commands = (
            api_step,
            _format_command(_python_command(
                "-m", "analytics", "--config", config_arg, "ingest", python=python
            )),
            _format_command(_python_command(
                "-m", "analytics", "--config", config_arg,
                "evaluate", "--week", str(completed_week), python=python,
            )),
            _format_command(_python_command(
                "-m", "analytics", "--config", config_arg,
                "predict", "--week", str(week), python=python,
            )),
        )
        return JobPlan(
            job, week, commands, completed_week=completed_week
        )

    raise ValueError(f"Unknown job: {job}")


def _show_toast(job: str, success: bool, log_path: Path) -> None:
    title = f"ffbot scheduler: {job} {'completed' if success else 'failed'}"
    message = (
        "The job completed successfully."
        if success
        else f"Check the scheduler log: {log_path}"
    )
    xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f"<text>{html.escape(title)}</text><text>{html.escape(message)}</text>"
        "</binding></visual></toast>"
    )
    encoded_xml = base64.b64encode(xml.encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$xmlText = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{encoded_xml}')); "
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        "[Windows.Data.Xml.Dom.XmlDocument, "
        "Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null; "
        "$document = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$document.LoadXml($xmlText); "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($document); "
        "[Windows.UI.Notifications.ToastNotificationManager]"
        ".CreateToastNotifier('Microsoft.Windows.Explorer').Show($toast)"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    ))
    LOGGER.addHandler(handler)


def _lock_status(lock_path: Path) -> str:
    try:
        with FileLock(lock_path):
            return "available"
    except JobLockError:
        return "busy"


def _print_plan(plan: JobPlan, lock_status: str) -> None:
    print(f"Job: {plan.job}")
    print(f"Week: {plan.week}")
    if plan.completed_week is not None:
        print(f"Completed week: {plan.completed_week}")
    if plan.opponent_id is not None:
        print(f"Scheduled opponent ID: {plan.opponent_id}")
    print(f"Lock status: {lock_status}")
    for index, command in enumerate(plan.commands, start=1):
        print(f"Command {index}: {command}")


def _run_command(command: list[str], project_root: Path) -> None:
    LOGGER.info("Running command: %s", _format_command(command))
    subprocess.run(command, cwd=project_root, check=True)


def _execute_plan(
    plan: JobPlan,
    config: Mapping,
    config_path: Path,
    project_root: Path,
    python: str | None = None,
) -> None:
    if plan.job == "snapshot":
        _validate_snapshot_lock(
            plan.week, datetime.now().astimezone(), config.get("kickoff_times", {})
        )
        _run_command(_python_command(
            "-m", "analytics", "--config", str(config_path),
            "snapshot", "--week", str(plan.week), python=python,
        ), project_root)
        return

    if plan.job == "weekly":
        command = _python_command(
            "run_weekly.py", "--force-refresh", "--week", str(plan.week),
            "--dry-run", "--opponent-id", str(plan.opponent_id),
            "--config", str(config_path), python=python,
        )
        _run_command(command, project_root)
        report_path = project_root / "reports" / (
            f"league_{config['league_id']}_team_{config['team_id']}_week_{plan.week}.md"
        )
        if not report_path.is_file():
            raise FileNotFoundError(f"Weekly report was not created: {report_path}")
        shutil.copy2(report_path, project_root / "reports" / "latest.md")
        LOGGER.info("Copied %s to reports/latest.md", report_path)
        return

    if plan.job == "settle":
        from data_layer import capture_actual_results, get_fresh_data

        LOGGER.info("Fetching one fresh scrape for settle.")
        df, _ = get_fresh_data(
            config["league_id"], config.get("is_idp", False),
            config_path=str(config_path),
        )
        capture_actual_results(
            df,
            league_id=config["league_id"],
            team_id=config["team_id"],
            week=plan.completed_week,
        )
        commands = (
            _python_command(
                "-m", "analytics", "--config", str(config_path), "ingest",
                python=python,
            ),
            _python_command(
                "-m", "analytics", "--config", str(config_path),
                "evaluate", "--week", str(plan.completed_week), python=python,
            ),
            _python_command(
                "-m", "analytics", "--config", str(config_path),
                "predict", "--week", str(plan.week), python=python,
            ),
        )
        for command in commands:
            _run_command(command, project_root)
        return

    raise ValueError(f"Unknown job: {plan.job}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one scheduled ffbot job.")
    parser.add_argument("job", choices=("snapshot", "weekly", "settle"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    return parser


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = ROOT
    config_path = _config_path(args.config, project_root)
    log_path = project_root / "logs" / "scheduler.log"
    lock_path = project_root / "logs" / "scheduler.lock"
    _configure_logging(log_path)

    success = False
    try:
        config = load_config(config_path)
        run_time = now or datetime.now().astimezone()
        plan = build_job_plan(
            args.job, run_time, config, config_path, project_root=project_root
        )
        if args.dry_run:
            _print_plan(plan, _lock_status(lock_path))
            LOGGER.info("Dry-run plan created for %s, Week %s.", plan.job, plan.week)
            success = True
            return 0

        with FileLock(lock_path):
            LOGGER.info("Starting %s, Week %s.", plan.job, plan.week)
            _execute_plan(plan, config, config_path, project_root)
            LOGGER.info("Finished %s, Week %s successfully.", plan.job, plan.week)
        success = True
        return 0
    except Exception as exc:
        LOGGER.exception("%s job failed: %s", args.job, exc)
        print(f"{args.job} job failed: {exc}. Log: {log_path}", file=sys.stderr)
        return 1
    finally:
        if not args.dry_run:
            try:
                _show_toast(args.job, success, log_path)
            except Exception as exc:
                LOGGER.warning("Could not display Windows toast: %s", exc)
                print(f"Windows toast notification failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

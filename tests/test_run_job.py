from datetime import datetime
import base64
from pathlib import Path

import pytest

from run_job import (
    FileLock,
    JobLockError,
    LateSnapshotError,
    _show_toast,
    _lock_status,
    _validate_snapshot_lock,
    build_job_plan,
)


KICKOFFS = {
    9: "2026-11-05T20:15:00-05:00",
    10: "2026-11-12T20:15:00-05:00",
}


def _config():
    return {
        "league_id": 492312,
        "team_id": 7,
        "kickoff_times": KICKOFFS,
        "schedule": {9: 5, 10: 8},
    }


def test_snapshot_plan_targets_next_kickoff_and_refuses_a_late_lock(tmp_path):
    now = datetime.fromisoformat("2026-11-06T09:00:00-05:00")
    plan = build_job_plan(
        "snapshot", now, _config(), project_root=tmp_path, python="python"
    )

    assert plan.week == 10
    assert "--week 10" in plan.commands[0]
    with pytest.raises(LateSnapshotError, match="stale clean snapshot"):
        _validate_snapshot_lock(
            10,
            datetime.fromisoformat("2026-11-12T20:15:00-05:00"),
            KICKOFFS,
        )


def test_weekly_plan_uses_scheduled_opponent_and_dry_run_command(tmp_path):
    now = datetime.fromisoformat("2026-11-06T09:00:00-05:00")
    plan = build_job_plan(
        "weekly", now, _config(), project_root=tmp_path, python="python"
    )

    assert plan.week == 9
    assert plan.opponent_id == 5
    assert "--force-refresh" in plan.commands[0]
    assert "--dry-run" in plan.commands[0]
    assert "--opponent-id 5" in plan.commands[0]
    assert "--execute" not in plan.commands[0]


def test_weekly_plan_requires_a_scheduled_opponent(tmp_path):
    config = _config()
    config["schedule"] = {}
    now = datetime.fromisoformat("2026-11-06T09:00:00-05:00")

    with pytest.raises(ValueError, match="No scheduled opponent"):
        build_job_plan("weekly", now, config, project_root=tmp_path)


def test_settle_plan_runs_one_scrape_then_ingests_evaluates_and_predicts(tmp_path):
    now = datetime.fromisoformat("2026-11-10T06:00:00-05:00")
    plan = build_job_plan(
        "settle", now, _config(), project_root=tmp_path, python="python"
    )

    assert plan.week == 10
    assert plan.completed_week == 9
    assert "get_fresh_data() once" in plan.commands[0]
    assert "capture_actual_results()" in plan.commands[0]
    assert "analytics" in plan.commands[1]
    assert "evaluate --week 9" in plan.commands[2]
    assert "predict --week 10" in plan.commands[3]


def test_file_lock_prevents_overlapping_jobs(tmp_path):
    path = tmp_path / "scheduler.lock"

    with FileLock(path):
        assert _lock_status(path) == "busy"
        with pytest.raises(JobLockError):
            FileLock(path).__enter__()

    assert _lock_status(path) == "available"


def test_dry_run_entrypoint_prints_plan_without_running_a_scrape(
    tmp_path, monkeypatch, capsys
):
    import run_job

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "league_id: 1\n"
        "team_id: 7\n"
        "schedule:\n  9: 5\n"
        "kickoff_times:\n"
        '  9: "2026-11-05T20:15:00-05:00"\n'
        '  10: "2026-11-12T20:15:00-05:00"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(run_job, "ROOT", tmp_path)
    monkeypatch.setattr(
        run_job,
        "_show_toast",
        lambda *args: pytest.fail("dry-run must not show a toast"),
    )

    result = run_job.main(
        ["snapshot", "--dry-run", "--config", str(config_path)],
        now=datetime.fromisoformat("2026-11-06T09:00:00-05:00"),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Job: snapshot" in output
    assert "Week: 10" in output
    assert "Lock status: available" in output
    assert "analytics" in output and "--week 10" in output
    assert not (tmp_path / "data").exists()


def test_toast_uses_native_windows_notifications_without_extra_packages(
    tmp_path, monkeypatch
):
    import run_job

    calls = []
    monkeypatch.setattr(run_job.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    _show_toast("weekly", False, tmp_path / "logs" / "scheduler.log")

    command = calls[0][0]
    script = command[-1]
    encoded_xml = script.split("FromBase64String('", 1)[1].split("')", 1)[0]
    xml = base64.b64decode(encoded_xml).decode("utf-8")
    assert command[0] == "powershell.exe"
    assert "ToastNotificationManager" in script
    assert "ffbot scheduler: weekly failed" in xml
    assert "scheduler.log" in xml

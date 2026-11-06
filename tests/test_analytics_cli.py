from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from analytics import cli


def _snapshot_config():
    return {
        "league_id": 123,
        "kickoff_times": {10: "2099-11-12T20:15:00-05:00"},
    }


def test_snapshot_accepts_fresh_scrape_from_immediately_previous_week(
    tmp_path, monkeypatch, capsys
):
    df = pd.DataFrame(
        [{"ID": 1, "Name": "Player", "Position": "RB", "Week 10": 12.0}]
    )

    def fake_scrape(league_id, is_idp=False):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = tmp_path / f"league_{league_id}_week_9_{timestamp}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        return df, 9

    monkeypatch.setattr(cli, "get_fresh_data", fake_scrape)

    class Store:
        def ingest_file(self, path, league_id, kickoff_times):
            assert Path(path).exists()
            assert Path(path).name.startswith("league_123_week_9_")
            return {"player_count": 1, "snapshot_time": "current"}

    cli._snapshot_command(
        SimpleNamespace(week=10, source=None, data_dir=str(tmp_path)),
        _snapshot_config(),
        {10: "2099-11-12T20:15:00-05:00"},
        Store(),
    )

    assert "Saved Week 10 snapshot" in capsys.readouterr().out


def test_snapshot_rejects_scrapes_more_than_one_week_behind(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "get_fresh_data", lambda *args: (pd.DataFrame(), 8))

    with pytest.raises(ValueError, match="immediately preceding scrape week"):
        cli._snapshot_command(
            SimpleNamespace(week=10, source=None, data_dir=str(tmp_path)),
            _snapshot_config(),
            {10: "2099-11-12T20:15:00-05:00"},
            object(),
        )


def test_snapshot_rejects_previous_week_scrape_without_target_values(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "get_fresh_data", lambda *args: (pd.DataFrame(), 9))

    with pytest.raises(ValueError, match="no Week 10 values"):
        cli._snapshot_command(
            SimpleNamespace(week=10, source=None, data_dir=str(tmp_path)),
            _snapshot_config(),
            {10: "2099-11-12T20:15:00-05:00"},
            object(),
        )

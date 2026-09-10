"""
Data layer — fetch, cache, and manage Yahoo Fantasy Football data via ffbot.
"""
import os
import re
import time
import glob
from datetime import datetime

import pandas as pd
import ffbot
from loguru import logger

DATA_DIR = "data"
DEFAULT_CONFIG_PATH = "config.yaml"


def load_config(config_path=DEFAULT_CONFIG_PATH):
    """Load YAML configuration file."""
    import yaml

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config


def save_weekly_cache(df, week, data_dir=DATA_DIR, league_id=None):
    """Cache scraped data to disk with the app's canonical filename.

    ffbot.save() hardcodes its own data/ directory and filename format, which
    is disconnected from the app's league-scoped cache layout. The app writes
    its own CSV directly to the correct location instead.
    """
    os.makedirs(data_dir, exist_ok=True)

    # Save with a week-prefixed, league-scoped name for programmatic lookup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"league_{league_id}_" if league_id is not None else ""
    filename = os.path.join(data_dir, f"{prefix}week_{week}_{timestamp}.csv")
    df.to_csv(filename, index=False)
    logger.info(f"Cached data to {filename}")
    return filename


def get_fresh_data(league_id, is_idp=False, config_path=DEFAULT_CONFIG_PATH):
    """Pull fresh data via ffbot.scrape() and cache it.

    Returns (df, current_week).
    """
    logger.info(f"Scraping league {league_id} (is_idp={is_idp}) ...")
    df = ffbot.scrape(league_id, is_IDP=is_idp)
    week = ffbot.current_week()

    try:
        save_weekly_cache(df, week, league_id=league_id)
    except Exception as e:
        logger.warning(f"Cache save failed: {e}")

    logger.info(f"Scrape complete — {len(df)} players, week {week}")
    return df, week


def get_latest_cached_or_fresh(league_id, is_idp=False, max_age_hours=12):
    """Use cache if recent enough, otherwise re-scrape.

    Returns (df, week).
    """
    data_dir = DATA_DIR
    if not os.path.isdir(data_dir):
        logger.info("No data directory — scraping fresh.")
        return get_fresh_data(league_id, is_idp)

    pattern = os.path.join(data_dir, f"league_{league_id}_week_*_*.csv")
    files = glob.glob(pattern)
    files = [
        f for f in files
        if os.path.getsize(f) > 0 and _cache_filename_parts(f) is not None
    ]

    if not files:
        logger.info("No cached data — scraping fresh.")
        return get_fresh_data(league_id, is_idp)

    latest = max(files, key=os.path.getmtime)
    age_hours = (time.time() - os.path.getmtime(latest)) / 3600

    if age_hours <= max_age_hours:
        logger.info(
            f"Using cached data: {os.path.basename(latest)} ({age_hours:.1f}h old)"
        )
        df, week = _load_cache(latest)
        return df, week

    logger.info(
        f"Cache is {age_hours:.1f}h old (max {max_age_hours}h) — scraping fresh."
    )
    return get_fresh_data(league_id, is_idp)


def _load_cache(filepath):
    """Load a cached CSV file and return (df, week)."""
    parts = _cache_filename_parts(filepath)
    if parts is None:
        raise ValueError(f"Invalid cache filename: {filepath}")

    df = pd.read_csv(filepath)

    week = parts[0]

    logger.info(f"Loaded {len(df)} players from cache (week {week})")
    return df, week


def _cache_filename_parts(filepath):
    """Return (week, timestamp) for a league-scoped cache filename."""
    basename = os.path.basename(filepath)
    match = re.fullmatch(r"league_[^_]+_week_(\d+)_(\d{8}_\d{6})\.csv", basename)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def ensure_data_columns(df):
    """Ensure expected columns exist after loading from CSV."""
    expected = [
        "ID",
        "Name",
        "Team",
        "Position",
        "Owner",
        "Owner ID",
        "Status",
        "% Owned",
        "Remaining",
        "VOR",
    ]
    for col in expected:
        if col not in df.columns:
            df[col] = None

    # Ensure Week columns exist for weeks 1-18
    for w in range(1, 19):
        col = f"Week {w}"
        if col not in df.columns:
            df[col] = float("nan")

    return df






def get_team_name_from_id(df, team_id):
    """Resolve the owner name for a given team_id from the scraped DataFrame.

    Builds an owner_id -> owner_name mapping from the df's "Owner ID" and "Owner"
    columns (skipping Free Agent rows), then returns the name associated with
    ``team_id``.

    Raises ValueError if team_id is not found in the data.
    """
    if team_id is None:
        raise ValueError("team_id is required to resolve team name.")

    id_to_name = {}
    for _, row in df.iterrows():
        owner_id = row.get("Owner ID")
        owner = row.get("Owner")
        if pd.notna(owner_id) and pd.notna(owner) and str(owner).strip() != "Free Agent":
            id_to_name[owner_id] = str(owner).strip()

    if team_id not in id_to_name:
        raise ValueError(
            f"team_id={team_id} not found in scraped data. "
            f"Available owner IDs: {sorted(set(id_to_name.keys()))}. "
            f"Check config.yaml and ensure the correct league is being scraped."
        )

    return id_to_name[team_id]

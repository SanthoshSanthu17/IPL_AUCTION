"""Past tournament winners, stored in a small JSON file (data/past_winners.json)."""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ipl_auction.paths import DATA_DIR


WINNERS_FILE = DATA_DIR / "past_winners.json"
_write_lock = threading.Lock()


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Return the stored records; a missing or corrupt file counts as empty."""
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [record for record in data if isinstance(record, dict)]


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    """Write atomically: temp file in the same folder, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.stem}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(records, file, indent=2)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _sort_key(record: dict[str, Any]) -> tuple[str, int]:
    season = record.get("season")
    return (
        str(record.get("recorded_at", "")),
        season if isinstance(season, int) else 0,
    )


def list_winners(path: Path | None = None) -> list[dict[str, Any]]:
    """All recorded winners, newest first. Creates an empty file if none exists."""
    target = path or WINNERS_FILE
    if not target.exists():
        try:
            _write_records(target, [])
        except OSError:
            pass
        return []
    return sorted(_read_records(target), key=_sort_key, reverse=True)


def record_winner(
    season: int,
    winner_team_id: str,
    runner_up_team_id: str,
    room_code: str,
    recorded_at: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Store one completed tournament. The same season and room replaces its old record."""
    if isinstance(season, bool) or not isinstance(season, int) or season < 1:
        raise ValueError("Season must be a positive whole number.")
    winner = str(winner_team_id or "").strip().lower()
    runner_up = str(runner_up_team_id or "").strip().lower()
    code = str(room_code or "").strip().upper()
    if not winner or not runner_up:
        raise ValueError("Winner and runner-up franchises are required.")
    if winner == runner_up:
        raise ValueError("Winner and runner-up must be different franchises.")
    if not code:
        raise ValueError("Room code is required.")

    record = {
        "season": season,
        "winner_team_id": winner,
        "runner_up_team_id": runner_up,
        "room_code": code,
        "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    target = path or WINNERS_FILE
    with _write_lock:
        records = [
            existing
            for existing in _read_records(target)
            if not (existing.get("season") == season and existing.get("room_code") == code)
        ]
        records.append(record)
        _write_records(target, records)
    return record
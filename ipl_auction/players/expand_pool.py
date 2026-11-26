"""Expand the auction pool from ``data/pool_roster.csv`` and refresh IPL stats.

The roster holds identity fields only (name, role, nationality, base price).
Statistics come from Cricsheet through the existing importer, and portraits come
from ``data/portrait_overrides.csv``; nothing is guessed. A player whose
Cricsheet name cannot be resolved uniquely keeps blank statistics and is listed
in the report so an alias can be added to ``data/ipl_stats_aliases.csv``. A
player without a portrait URL uses the initials avatar in the browser.

Run from the project folder:

    python -m ipl_auction.players.expand_pool --cutoff 2026-12-31
    python -m ipl_auction.players.expand_pool --archive ipl_male_json.zip
    python -m ipl_auction.players.expand_pool --check-images
"""

import argparse
import csv
import io
import json
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import BinaryIO
from urllib.request import Request, urlopen

from ipl_auction.paths import DATA_DIR
from ipl_auction.players.repository import PLAYER_FILE
from ipl_auction.players.update_stats import (
    AUCTION_INFO_FILE,
    CRICSHEET_URL,
    STAT_COLUMNS,
    PlayerStats,
    _load_name_aliases,
    calculate_ipl_stats,
    normalize_player_name,
)


ROSTER_FILE = DATA_DIR / "pool_roster.csv"
PORTRAIT_FILE = DATA_DIR / "portrait_overrides.csv"
ID_PREFIX = "ipl-2026-"
FIRST_NEW_ID = 90001
VALID_ROLES = {"Batter", "Bowler", "All-Rounder", "Wicketkeeper"}
ROSTER_COLUMNS = {"name", "role", "nationality", "base_price_crore"}


def load_roster(path: Path = ROSTER_FILE) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        missing = ROSTER_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Roster is missing columns: {', '.join(sorted(missing))}")
        roster = []
        seen: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            name = (row["name"] or "").strip()
            key = normalize_player_name(name)
            if not name or key in seen:
                raise ValueError(f"Roster row {row_number} has a blank or duplicate name.")
            seen.add(key)
            if (row["role"] or "").strip() not in VALID_ROLES:
                raise ValueError(f"Roster row {row_number} has an invalid role.")
            try:
                if float(row["base_price_crore"]) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"Roster row {row_number} has an invalid base price.")
            roster.append({key: (value or "").strip() for key, value in row.items()})
        return roster


def load_portrait_overrides(path: Path = PORTRAIT_FILE) -> dict[str, dict[str, str]]:
    """Optional file: name,image_url,portrait_profile_url (one row per player)."""
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return {
            normalize_player_name(row["name"]): {
                "image_url": (row.get("image_url") or "").strip(),
                "portrait_profile_url": (row.get("portrait_profile_url") or "").strip(),
            }
            for row in csv.DictReader(file)
            if (row.get("name") or "").strip()
        }


def _next_id_number(rows: list[dict[str, str]]) -> int:
    highest = FIRST_NEW_ID - 1
    for row in rows:
        match = re.fullmatch(re.escape(ID_PREFIX) + r"(\d+)", row["player_id"])
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def merge_roster(
    rows: list[dict[str, str]], roster: list[dict[str, str]], fieldnames: list[str]
) -> list[str]:
    """Append roster players that are not already in ``rows``; return their names."""
    existing = {normalize_player_name(row["name"]) for row in rows}
    next_id = _next_id_number(rows)
    added = []
    for entry in roster:
        if normalize_player_name(entry["name"]) in existing:
            continue
        row = {column: "" for column in fieldnames}
        row.update(
            player_id=f"{ID_PREFIX}{next_id}",
            name=entry["name"],
            role=entry["role"],
            nationality=entry["nationality"],
            base_price_crore=entry["base_price_crore"],
        )
        rows.append(row)
        added.append(entry["name"])
        next_id += 1
    return added


def _heuristic_source_name(player_name: str, source_names: set[str]) -> str | None:
    """Match 'Virat Kohli' to a single 'V Kohli'-style name, or return None."""
    tokens = normalize_player_name(player_name).split()
    if len(tokens) < 2:
        return None
    surname, initial = tokens[-1], tokens[0][0]
    candidates = [
        source
        for source in source_names
        if len(source.split()) >= 2
        and source.split()[-1] == surname
        and source.split()[0][0] == initial
    ]
    return candidates[0] if len(candidates) == 1 else None


def resolve_source_name(
    player_name: str,
    roster_source: str,
    aliases: dict[str, str],
    source_names: set[str],
) -> tuple[str | None, str]:
    """Return (cricsheet name key, how it was resolved)."""
    key = normalize_player_name(player_name)
    if roster_source:
        return normalize_player_name(roster_source), "roster"
    if key in aliases:
        return aliases[key], "alias"
    if key in source_names:
        return key, "exact"
    guess = _heuristic_source_name(player_name, source_names)
    if guess:
        return guess, "heuristic"
    return None, "unresolved"


def apply_stats(
    rows: list[dict[str, str]],
    stats_by_name: dict[str, PlayerStats],
    aliases: dict[str, str],
    roster: list[dict[str, str]],
) -> list[tuple[str, str, str]]:
    """Fill the stat columns; return (player, source name, method) for review."""
    roster_source = {
        normalize_player_name(entry["name"]): entry.get("cricsheet_name", "")
        for entry in roster
    }
    source_names = set(stats_by_name)
    resolutions = []
    for row in rows:
        key = normalize_player_name(row["name"])
        source, method = resolve_source_name(
            row["name"], roster_source.get(key, ""), aliases, source_names
        )
        stats = stats_by_name.get(source) if source else None
        for column in STAT_COLUMNS:
            value = getattr(stats, column) if stats else None
            row[column] = "" if value is None else str(value)
        resolutions.append(
            (row["name"], source or "", method if stats else "unresolved")
        )
    return resolutions


def apply_portraits(
    rows: list[dict[str, str]], overrides: dict[str, dict[str, str]]
) -> int:
    """Fill blank portrait columns from the overrides file; never replace one."""
    applied = 0
    for row in rows:
        override = overrides.get(normalize_player_name(row["name"]))
        if not override or not override["image_url"] or row.get("image_url"):
            continue
        row["image_url"] = override["image_url"]
        if override["portrait_profile_url"]:
            row["portrait_profile_url"] = override["portrait_profile_url"]
        applied += 1
    return applied


def check_image_urls(rows: list[dict[str, str]]) -> list[str]:
    """Return names whose image_url does not answer with an image."""
    broken = []
    for row in rows:
        url = row.get("image_url", "")
        if not url:
            continue
        try:
            request = Request(url, headers={"User-Agent": "ipl-auction-image-check"})
            with urlopen(request, timeout=20) as response:
                if not response.headers.get("Content-Type", "").startswith("image/"):
                    broken.append(row["name"])
        except OSError:
            broken.append(row["name"])
    return broken


def _write_rows(rows: list[dict[str, str]], fieldnames: list[str], path: Path) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8-sig", dir=path.parent, delete=False
        ) as file:
            temporary_path = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def build_pool(
    archive_file: BinaryIO,
    cutoff: date,
    *,
    player_file: Path = PLAYER_FILE,
    roster_file: Path = ROSTER_FILE,
    portrait_file: Path = PORTRAIT_FILE,
    info_file: Path = AUCTION_INFO_FILE,
    write: bool = True,
) -> dict:
    with player_file.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    roster = load_roster(roster_file)
    added = merge_roster(rows, roster, fieldnames)
    stats_by_name = calculate_ipl_stats(archive_file, cutoff)
    resolutions = apply_stats(rows, stats_by_name, _load_name_aliases(), roster)
    portraits = apply_portraits(rows, load_portrait_overrides(portrait_file))

    if write:
        _write_rows(rows, fieldnames, player_file)
        info = json.loads(info_file.read_text(encoding="utf-8"))
        info["registered_players"] = len(rows)
        info_file.write_text(
            json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return {
        "total": len(rows),
        "added": added,
        "resolutions": resolutions,
        "portraits_applied": portraits,
        "without_portrait": [row["name"] for row in rows if not row.get("image_url")],
    }


def _print_report(result: dict, cutoff: date) -> None:
    unresolved = [name for name, _, method in result["resolutions"] if method == "unresolved"]
    heuristic = [item for item in result["resolutions"] if item[2] == "heuristic"]
    print(f"Pool size: {result['total']} players ({len(result['added'])} added).")
    print(f"IPL stats through {cutoff.isoformat()}; {len(unresolved)} unresolved.")
    print(f"Portraits applied from overrides: {result['portraits_applied']}; "
          f"{len(result['without_portrait'])} players will show initials.")
    if heuristic:
        print("\nReview these automatic name matches (add an alias if any is wrong):")
        for name, source, _ in heuristic:
            print(f"  {name}  ->  {source}")
    if unresolved:
        print("\nNo unique Cricsheet match (stats left blank). Add a row to "
              "data/ipl_stats_aliases.csv, or a cricsheet_name in the roster:")
        for name in unresolved:
            print(f"  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add roster players to the pool and refresh IPL statistics."
    )
    parser.add_argument("--cutoff", type=date.fromisoformat,
                        help="Include matches on or before this date (default: season_year-12-31).")
    parser.add_argument("--archive", type=Path,
                        help="Local ipl_male_json.zip to use instead of downloading it.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the report without writing any file.")
    parser.add_argument("--check-images", action="store_true",
                        help="Only test every image_url in players.csv and list broken ones.")
    args = parser.parse_args()

    if args.check_images:
        with PLAYER_FILE.open("r", newline="", encoding="utf-8-sig") as file:
            broken = check_image_urls(list(csv.DictReader(file)))
        print("All image URLs returned an image." if not broken
              else "Image URL failed (the browser will show initials): " + ", ".join(broken))
        return

    if args.cutoff:
        cutoff = args.cutoff
    else:
        season = int(json.loads(AUCTION_INFO_FILE.read_text(encoding="utf-8"))["season_year"])
        cutoff = date(season, 12, 31)
    if args.archive:
        archive_data = args.archive.read_bytes()
    else:
        with urlopen(CRICSHEET_URL, timeout=60) as response:
            archive_data = response.read()
    result = build_pool(io.BytesIO(archive_data), cutoff, write=not args.dry_run)
    _print_report(result, cutoff)


if __name__ == "__main__":
    main()
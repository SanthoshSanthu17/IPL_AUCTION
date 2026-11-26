import argparse
import csv
import io
import json
import re
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import BinaryIO
from urllib.request import urlopen

from ipl_auction.paths import DATA_DIR
from ipl_auction.players.repository import PLAYER_FILE


CRICSHEET_URL = "https://cricsheet.org/downloads/ipl_male_json.zip"
NAME_ALIASES_FILE = DATA_DIR / "ipl_stats_aliases.csv"
AUCTION_INFO_FILE = DATA_DIR / "auction_info.json"
STAT_COLUMNS = (
    "matches",
    "runs",
    "wickets",
    "batting_average",
    "strike_rate",
    "economy",
)
BOWLER_WICKET_KINDS = {
    "bowled",
    "caught",
    "caught and bowled",
    "hit wicket",
    "lbw",
    "stumped",
}


@dataclass(frozen=True)
class PlayerStats:
    matches: int
    runs: int
    wickets: int
    batting_average: float | None
    strike_rate: float | None
    economy: float | None


@dataclass
class _PlayerTotals:
    matches: set[str] = field(default_factory=set)
    runs: int = 0
    balls_faced: int = 0
    dismissals: int = 0
    wickets: int = 0
    runs_conceded: int = 0
    legal_balls_bowled: int = 0

    def as_stats(self) -> PlayerStats:
        return PlayerStats(
            matches=len(self.matches),
            runs=self.runs,
            wickets=self.wickets,
            batting_average=(
                round(self.runs / self.dismissals, 2) if self.dismissals else None
            ),
            strike_rate=(
                round(self.runs / self.balls_faced * 100, 2)
                if self.balls_faced
                else None
            ),
            economy=(
                round(self.runs_conceded / self.legal_balls_bowled * 6, 2)
                if self.legal_balls_bowled
                else None
            ),
        )


def normalize_player_name(name: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def calculate_ipl_stats(archive_file: BinaryIO, cutoff: date) -> dict[str, PlayerStats]:
    totals: dict[str, _PlayerTotals] = defaultdict(_PlayerTotals)
    identities_by_name: dict[str, set[str]] = defaultdict(set)

    with zipfile.ZipFile(archive_file) as archive:
        for entry in archive.infolist():
            if not entry.filename.endswith(".json"):
                continue
            match = json.loads(archive.read(entry))
            info = match["info"]
            if date.fromisoformat(str(info["dates"][0])) > cutoff:
                continue

            registry = info.get("registry", {}).get("people", {})

            def identity_for(name: str) -> str:
                normalized = normalize_player_name(name)
                identity = registry.get(name) or f"name:{normalized}"
                identities_by_name[normalized].add(identity)
                return identity

            for team_players in info.get("players", {}).values():
                for name in set(team_players):
                    totals[identity_for(name)].matches.add(entry.filename)

            for innings in match.get("innings", []):
                for over in innings.get("overs", []):
                    for delivery in over.get("deliveries", []):
                        batter = identity_for(delivery["batter"])
                        bowler = identity_for(delivery["bowler"])
                        runs = delivery.get("runs", {})
                        extras = delivery.get("extras", {})
                        batter_totals = totals[batter]
                        bowler_totals = totals[bowler]
                        batter_totals.runs += int(runs.get("batter", 0))
                        if not extras.get("wides"):
                            batter_totals.balls_faced += 1

                        bowler_totals.runs_conceded += (
                            int(runs.get("total", 0))
                            - int(extras.get("byes", 0))
                            - int(extras.get("legbyes", 0))
                            - int(extras.get("penalty", 0))
                        )
                        if not extras.get("wides") and not extras.get("noballs"):
                            bowler_totals.legal_balls_bowled += 1

                        for wicket in delivery.get("wickets", []):
                            dismissed = totals[identity_for(wicket["player_out"])]
                            if wicket["kind"] != "retired hurt":
                                dismissed.dismissals += 1
                            if wicket["kind"] in BOWLER_WICKET_KINDS:
                                bowler_totals.wickets += 1

    stats_by_name: dict[str, PlayerStats] = {}
    for name, identities in identities_by_name.items():
        if len(identities) == 1:
            stats = totals[next(iter(identities))]
            if stats.matches:
                stats_by_name[name] = stats.as_stats()
    return stats_by_name


def _load_name_aliases() -> dict[str, str]:
    with NAME_ALIASES_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        aliases: dict[str, str] = {}
        for row in csv.DictReader(file):
            player_name = normalize_player_name(row["player_name"])
            source_name = normalize_player_name(row["cricsheet_name"])
            if player_name in aliases:
                raise ValueError(f"Duplicate IPL stats alias for '{row['player_name']}'.")
            aliases[player_name] = source_name
        return aliases


def _default_cutoff() -> date:
    with AUCTION_INFO_FILE.open("r", encoding="utf-8") as file:
        season_year = int(json.load(file)["season_year"])
    return date(season_year - 1, 12, 31)


def update_player_csv(archive_file: BinaryIO, cutoff: date) -> tuple[int, int]:
    stats_by_name = calculate_ipl_stats(archive_file, cutoff)
    aliases = _load_name_aliases()
    with PLAYER_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []
        missing_columns = set(STAT_COLUMNS) - set(fieldnames)
        if missing_columns:
            raise ValueError(
                "Player data is missing statistics columns: "
                + ", ".join(sorted(missing_columns))
            )
        players = list(reader)

    updated = 0
    for player in players:
        player_name = normalize_player_name(player["name"])
        source_name = aliases.get(player_name, player_name)
        stats = stats_by_name.get(source_name)
        if stats is not None:
            updated += 1
        for column in STAT_COLUMNS:
            value = getattr(stats, column) if stats else None
            player[column] = "" if value is None else str(value)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8-sig",
            dir=PLAYER_FILE.parent,
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(players)
        temporary_path.replace(PLAYER_FILE)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return updated, len(players)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh IPL career statistics from Cricsheet ball-by-ball data."
    )
    parser.add_argument(
        "--cutoff",
        type=date.fromisoformat,
        help="Include matches on or before this date (default: end of the prior year).",
    )
    args = parser.parse_args()
    cutoff = args.cutoff or _default_cutoff()
    with urlopen(CRICSHEET_URL, timeout=60) as response:
        archive_data = response.read()
    updated, total = update_player_csv(io.BytesIO(archive_data), cutoff)
    print(
        f"Updated IPL stats for {updated} of {total} players through "
        f"{cutoff.isoformat()}; unmapped or unavailable records remain blank."
    )


if __name__ == "__main__":
    main()

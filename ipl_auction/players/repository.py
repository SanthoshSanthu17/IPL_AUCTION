import csv
import math
from dataclasses import replace

from ipl_auction.paths import DATA_DIR
from ipl_auction.players.models import Player


PLAYER_FILE = DATA_DIR / "players.csv"
ELITE_FILE = DATA_DIR / "elite_players.csv"
REQUIRED_COLUMNS = {
    "player_id",
    "name",
    "role",
    "nationality",
    "base_price_crore",
}
def _optional_number(value: str | None, row_number: int, column: str) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(
            f"Player data row {row_number} has an invalid {column} value."
        ) from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(
            f"Player data row {row_number} has a negative {column} value."
        )
    return number


def _optional_integer(value: str | None, row_number: int, column: str) -> int | None:
    number = _optional_number(value, row_number, column)
    if number is None:
        return None
    if not number.is_integer():
        raise ValueError(
            f"Player data row {row_number} must use a whole number for {column}."
        )
    return int(number)


def load_elite_ids(known_ids: set[str]) -> set[str]:
    """Return the listed player_ids that exist in the pool (the file is optional)."""
    if not ELITE_FILE.exists():
        return set()
    with ELITE_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if "player_id" not in (reader.fieldnames or []):
            raise ValueError("Elite player data is missing the player_id column.")
        elite_ids = {(row["player_id"] or "").strip() for row in reader}
    return elite_ids & known_ids


def load_players() -> list[Player]:
    with PLAYER_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        columns = set(reader.fieldnames or [])
        missing_columns = REQUIRED_COLUMNS - columns
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Player data is missing required columns: {missing}")

        players = []
        seen_ids = set()
        for row_number, row in enumerate(reader, start=2):
            player_id = (row["player_id"] or "").strip()
            name = (row["name"] or "").strip()
            if not player_id or not name:
                raise ValueError(
                    f"Player data row {row_number} must include player_id and name."
                )
            if player_id in seen_ids:
                raise ValueError(f"Duplicate player_id '{player_id}' in player data.")
            seen_ids.add(player_id)

            try:
                base_price = float(row["base_price_crore"])
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Player data row {row_number} has an invalid base price."
                ) from error
            if not math.isfinite(base_price) or base_price < 0:
                raise ValueError(
                    f"Player data row {row_number} has a negative base price."
                )

            players.append(
                Player(
                    player_id=player_id,
                    name=name,
                    role=(row["role"] or "").strip(),
                    nationality=(row["nationality"] or "").strip(),
                    base_price_crore=base_price,
                    matches=_optional_integer(row.get("matches"), row_number, "matches"),
                    runs=_optional_integer(row.get("runs"), row_number, "runs"),
                    wickets=_optional_integer(row.get("wickets"), row_number, "wickets"),
                    batting_average=_optional_number(
                        row.get("batting_average"), row_number, "batting_average"
                    ),
                    strike_rate=_optional_number(
                        row.get("strike_rate"), row_number, "strike_rate"
                    ),
                    economy=_optional_number(row.get("economy"), row_number, "economy"),
                    image_url=(row.get("image_url") or "").strip() or None,
                )
            )
        elite_ids = load_elite_ids(seen_ids)
        return [
            replace(player, elite=player.player_id in elite_ids) for player in players
        ]
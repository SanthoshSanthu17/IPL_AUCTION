import csv
import math
from dataclasses import replace

from ipl_auction.paths import DATA_DIR
from ipl_auction.players.models import Player


PLAYER_FILE = DATA_DIR / "players.csv"
ELITE_FILE = DATA_DIR / "elite_players.csv"
PROFILE_FILE = DATA_DIR / "player_profiles.csv"
RATING_FILE = DATA_DIR / "player_ratings.csv"
DEFAULT_RATING = 50.0
BAT_ROLES = {"opener", "anchor", "middle", "finisher"}
BOWL_TYPES = {"pace", "spin"}
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


def _data_lines(path) -> list[str]:
    """CSV lines of a file, skipping # comment lines (used for review notes)."""
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return [line for line in file if not line.lstrip().startswith("#")]


def load_player_profiles() -> dict[str, tuple[str | None, str | None]]:
    """Return {player_id: (bat_role, bowl_type)} (the file is optional)."""
    if not PROFILE_FILE.exists():
        return {}
    profiles: dict[str, tuple[str | None, str | None]] = {}
    reader = csv.DictReader(_data_lines(PROFILE_FILE))
    if "player_id" not in (reader.fieldnames or []):
        raise ValueError("Player profile data is missing the player_id column.")
    for row in reader:
        player_id = (row.get("player_id") or "").strip()
        if not player_id:
            continue
        bat_role = (row.get("bat_role") or "").strip().casefold() or None
        bowl_type = (row.get("bowl_type") or "").strip().casefold() or None
        if bat_role not in BAT_ROLES:
            bat_role = None
        if bowl_type not in BOWL_TYPES:
            bowl_type = None
        profiles[player_id] = (bat_role, bowl_type)
    return profiles


def load_player_ratings() -> dict[str, float]:
    """Return {player_id: rating} from data/player_ratings.csv (optional file).

    Lines starting with # are ignored so the file can carry a review note.
    """
    if not RATING_FILE.exists():
        return {}
    ratings: dict[str, float] = {}
    reader = csv.DictReader(_data_lines(RATING_FILE))
    fields = reader.fieldnames or []
    if "player_id" not in fields or "rating" not in fields:
        raise ValueError("Player rating data needs player_id and rating columns.")
    for row in reader:
        player_id = (row.get("player_id") or "").strip()
        if not player_id:
            continue
        try:
            value = float(row.get("rating") or "")
        except ValueError:
            continue
        if math.isfinite(value):
            ratings[player_id] = max(0.0, min(100.0, value))
    return ratings


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
        profiles = load_player_profiles()
        ratings = load_player_ratings()
        return [
            replace(
                player,
                elite=player.player_id in elite_ids,
                bat_role=profiles.get(player.player_id, (None, None))[0],
                bowl_type=profiles.get(player.player_id, (None, None))[1],
                rating=ratings.get(player.player_id, DEFAULT_RATING),
            )
            for player in players
        ]
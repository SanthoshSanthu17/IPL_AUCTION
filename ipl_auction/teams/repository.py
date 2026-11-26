import json
import math

from ipl_auction.paths import DATA_DIR
from ipl_auction.teams.models import Team


TEAM_FILE = DATA_DIR / "teams.json"
SETTINGS_FILE = DATA_DIR / "auction_settings.json"


def load_teams() -> list[Team]:
    with TEAM_FILE.open("r", encoding="utf-8") as file:
        records = json.load(file)

    teams = []
    seen_ids = set()
    for record in records:
        team_id = str(record.get("team_id", "")).strip()
        name = str(record.get("name", "")).strip()
        short_name = str(record.get("short_name", team_id.upper())).strip()
        logo_url = str(record.get("logo_url", "")).strip()
        if not team_id or not name:
            raise ValueError("Each team must have a team_id and name.")
        if team_id in seen_ids:
            raise ValueError(f"Duplicate team_id '{team_id}' in team data.")
        seen_ids.add(team_id)
        teams.append(
            Team(
                team_id=team_id,
                name=name,
                short_name=short_name,
                logo_url=logo_url,
            )
        )
    return teams


def load_default_purse() -> float:
    with SETTINGS_FILE.open("r", encoding="utf-8") as file:
        settings = json.load(file)

    try:
        purse = float(settings["default_purse_crore"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "Auction settings must define a numeric default_purse_crore."
        ) from error
    if not math.isfinite(purse) or purse < 0:
        raise ValueError("The default purse must be a finite, non-negative amount.")
    return purse

"""Build a DRAFT hidden rating (0-100) for every player.

Run from the project folder:

    python -m ipl_auction.players.build_ratings

It reads data/players.csv (IPL stats) and data/elite_players.csv and writes
data/player_ratings.csv. The numbers are a starting point only: open the CSV,
review it and edit any rating you disagree with. Ratings are never shown to
users during the auction. Blank stats are treated as neutral-low, never guessed.
ELITE players are always placed above every non-ELITE player.

Use --keep-existing to leave a file you have already edited untouched.
"""

import argparse
import csv

from ipl_auction.paths import DATA_DIR
from ipl_auction.players.models import Player
from ipl_auction.players.repository import load_players

RATING_FILE = DATA_DIR / "player_ratings.csv"

NEUTRAL_LOW = 0.30  # used for a blank stat
FLOOR_RATING = 40.0  # lowest possible draft rating
NON_ELITE_TOP = 89.0  # highest non-ELITE rating
ELITE_BOTTOM = 90.0  # lowest ELITE rating
ELITE_TOP = 99.0  # highest ELITE rating
FULL_CREDIT_MATCHES = 20  # fewer IPL matches pull the score toward neutral


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _credibility(player: Player, impact: float) -> float:
    matches = player.matches or 0
    weight = min(1.0, matches / FULL_CREDIT_MATCHES)
    return weight * impact + (1.0 - weight) * NEUTRAL_LOW


def batting_impact(player: Player) -> float:
    """0..1 from batting average, strike rate and career runs."""
    average = (
        NEUTRAL_LOW
        if player.batting_average is None
        else _clamp((player.batting_average - 15) / (45 - 15))
    )
    strike = (
        NEUTRAL_LOW
        if player.strike_rate is None
        else _clamp((player.strike_rate - 110) / (180 - 110))
    )
    volume = _clamp((player.runs or 0) / 4000)
    return _credibility(player, 0.35 * average + 0.35 * strike + 0.30 * volume)


def bowling_impact(player: Player) -> float:
    """0..1 from economy, wickets per match and career wickets."""
    economy = (
        NEUTRAL_LOW
        if player.economy is None
        else _clamp((10.5 - player.economy) / (10.5 - 7.0))
    )
    matches = player.matches or 0
    per_match = _clamp(((player.wickets or 0) / matches) / 1.2) if matches else 0.0
    volume = _clamp((player.wickets or 0) / 150)
    return _credibility(player, 0.40 * economy + 0.30 * per_match + 0.30 * volume)


def raw_score(player: Player) -> float:
    """Role-aware 0..1 score before it is mapped onto the rating scale."""
    category = player.role.strip().casefold()
    if category == "bowler":
        return bowling_impact(player)
    if category == "all-rounder":
        wickets = player.wickets or 0
        bowl_weight = min(1.0, wickets / 30) * 0.5
        blended = (1 - bowl_weight) * batting_impact(player) + bowl_weight * (
            bowling_impact(player)
        )
        return _clamp(blended + 0.06)  # small bonus for doing both jobs
    return batting_impact(player)  # batters and wicketkeepers


def compute_ratings(players: list[Player]) -> dict[str, float]:
    raw = {player.player_id: raw_score(player) for player in players}
    ratings: dict[str, float] = {}
    elite = [player for player in players if player.elite]
    others = [player for player in players if not player.elite]

    for player in others:
        value = FLOOR_RATING + (NON_ELITE_TOP - FLOOR_RATING) * raw[player.player_id]
        ratings[player.player_id] = round(min(NON_ELITE_TOP, value), 1)

    if elite:
        low = min(raw[player.player_id] for player in elite)
        high = max(raw[player.player_id] for player in elite)
        span = (high - low) or 1.0
        for player in elite:
            share = (raw[player.player_id] - low) / span
            ratings[player.player_id] = round(
                ELITE_BOTTOM + (ELITE_TOP - ELITE_BOTTOM) * share, 1
            )
    return ratings


def write_ratings(players: list[Player], ratings: dict[str, float]) -> None:
    ordered = sorted(players, key=lambda p: (-ratings[p.player_id], p.name))
    with RATING_FILE.open("w", newline="", encoding="utf-8") as file:
        file.write(
            "# DRAFT - owner to review and edit. Hidden from users during the "
            "auction. Scale 0-100; ELITE players are 90+.\n"
        )
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["player_id", "name", "rating"])
        for player in ordered:
            writer.writerow(
                [player.player_id, player.name, f"{ratings[player.player_id]:.1f}"]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="do nothing if data/player_ratings.csv already exists",
    )
    args = parser.parse_args()
    if args.keep_existing and RATING_FILE.exists():
        print(f"{RATING_FILE} already exists; left unchanged.")
        return
    players = load_players()
    ratings = compute_ratings(players)
    write_ratings(players, ratings)
    print(f"Wrote {len(players)} draft ratings to {RATING_FILE}")
    print("Review and edit the file before your first real auction.")


if __name__ == "__main__":
    main()
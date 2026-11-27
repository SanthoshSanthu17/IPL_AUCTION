"""Player sets (Set 1 to Set 4) for one mega auction.

The 200-player pool is split into sets by hidden rating (ELITE players first),
so Set 1 holds the best players and Set 4 the least rated. Each auction calls
Set 1, then Set 2, then Set 3, then Set 4, in random order inside a set.
Only some players of every set take part in one auction; the rest sit out.
Tweak the constants below to change the rules.

Ratings are used here on the server only. They are never sent to browsers.
"""

import random
from dataclasses import dataclass, field

from ipl_auction.players.models import Player
from ipl_auction.teams.lineup import MAX_OVERSEAS_SQUAD, SQUAD_SIZE

# ---- Set rules (tweak here) -----------------------------------------------
SET_COUNT = 4
SET_SIZE = 50  # players in each set (SET_COUNT x SET_SIZE = the whole pool)
PLAYERS_PER_SET_IN_AUCTION = 40  # SET_SIZE - this = players sitting out per set
SET_BREAK_SECONDS = 3.0  # extra pause (the "Set 2 begins" banner) between sets
MAX_DRAW_ATTEMPTS = 500  # redraws of the sit-outs before the closest draw is used

# ---- Role minimums for the players that take part --------------------------
AUCTION_FRANCHISES = 10
# Minimum per franchise (a squad of SQUAD_SIZE) x AUCTION_FRANCHISES.
ROLE_MINIMUMS_PER_FRANCHISE = {
    "wicketkeepers": 1,
    "openers": 2,  # players whose bat_role is "opener"
    "all_rounders": 3,
    "pace_options": 4,  # Bowlers + All-Rounders with bowl_type "pace"
    "spin_options": 2,  # Bowlers + All-Rounders with bowl_type "spin"
}
ROLE_MINIMUMS = {
    name: count * AUCTION_FRANCHISES
    for name, count in ROLE_MINIMUMS_PER_FRANCHISE.items()
}
# A squad holds at most MAX_OVERSEAS_SQUAD overseas players, so the rest must be Indian.
ROLE_MINIMUMS["indian_players"] = AUCTION_FRANCHISES * (SQUAD_SIZE - MAX_OVERSEAS_SQUAD)
# Rating used for a player that has none.
DEFAULT_RATING = 50.0
_BOWLING_ROLES = {"bowler", "all-rounder"}


def _role(player: Player) -> str:
    return player.role.strip().casefold()


ROLE_CHECKS = {
    "wicketkeepers": lambda p: _role(p) == "wicketkeeper",
    "openers": lambda p: p.bat_role == "opener",
    "all_rounders": lambda p: _role(p) == "all-rounder",
    "pace_options": lambda p: p.bowl_type == "pace" and _role(p) in _BOWLING_ROLES,
    "spin_options": lambda p: p.bowl_type == "spin" and _role(p) in _BOWLING_ROLES,
    "indian_players": lambda p: not p.is_overseas,
}


@dataclass
class AuctionPlan:
    players: list[Player]  # the auction order: Set 1 shuffled, Set 2 shuffled, ...
    player_sets: dict[str, int]  # player_id -> set number (players taking part)
    sat_out: dict[int, list[str]]  # set number -> player_ids sitting out
    attempts: int = 1
    used_fallback: bool = False  # True when no draw met the role minimums
    shortfalls: dict[str, int] = field(default_factory=dict)  # only when fallback


def set_label(number: int) -> str:
    return f"SET {number}"


def split_into_sets(
    players: list[Player],
    set_count: int = SET_COUNT,
    set_size: int = SET_SIZE,
) -> list[list[Player]]:
    """Split the pool into sets: ELITE first, then by rating (best first).

    With exactly set_count x set_size players every set has set_size players;
    any other pool size is split as evenly as possible (earlier sets larger).
    """
    ordered = sorted(
        players,
        key=lambda p: (
            not p.elite,
            -(DEFAULT_RATING if p.rating is None else p.rating),
            p.name.casefold(),
            p.player_id,
        ),
    )
    if len(ordered) == set_count * set_size:
        sizes = [set_size] * set_count
    else:
        base, extra = divmod(len(ordered), set_count)
        sizes = [base + (1 if index < extra else 0) for index in range(set_count)]
    sets, start = [], 0
    for size in sizes:
        sets.append(ordered[start : start + size])
        start += size
    return sets


def set_numbers(players: list[Player]) -> dict[str, int]:
    """player_id -> set number for the whole pool (used by the catalogue)."""
    return {
        player.player_id: number
        for number, members in enumerate(split_into_sets(players), start=1)
        for player in members
    }


def role_counts(players: list[Player]) -> dict[str, int]:
    return {name: sum(1 for p in players if check(p)) for name, check in ROLE_CHECKS.items()}


def role_shortfalls(
    players: list[Player], minimums: dict[str, int] | None = None
) -> dict[str, int]:
    """{role: how many more are needed}; empty when every minimum is met."""
    minimums = ROLE_MINIMUMS if minimums is None else minimums
    counts = role_counts(players)
    return {
        name: needed - counts.get(name, 0)
        for name, needed in minimums.items()
        if counts.get(name, 0) < needed
    }


def draw_sit_outs(sets: list[list[Player]], rng: random.Random) -> dict[int, list[str]]:
    """Pick the players sitting out of each set. ELITE players never sit out."""
    sat_out: dict[int, list[str]] = {}
    for number, members in enumerate(sets, start=1):
        count = max(0, len(members) - PLAYERS_PER_SET_IN_AUCTION)
        candidates = [player for player in members if not player.elite]
        chosen = rng.sample(candidates, min(count, len(candidates)))
        sat_out[number] = [player.player_id for player in chosen]
    return sat_out


def _taking_part(sets: list[list[Player]], sat_out: dict[int, list[str]]) -> list[list[Player]]:
    return [
        [player for player in members if player.player_id not in set(sat_out[number])]
        for number, members in enumerate(sets, start=1)
    ]


def build_auction_plan(
    players: list[Player],
    rng: random.Random | None = None,
    minimums: dict[str, int] | None = None,
    max_attempts: int | None = None,
) -> AuctionPlan:
    """Choose who sits out, check the role minimums and fix the calling order.

    A draw that misses a minimum is redrawn, up to max_attempts times. If none
    passes, the closest draw is used and the plan says so (used_fallback).
    """
    rng = rng or random.Random()
    minimums = ROLE_MINIMUMS if minimums is None else minimums
    attempts_allowed = max(1, MAX_DRAW_ATTEMPTS if max_attempts is None else max_attempts)
    sets = split_into_sets(players)

    best_out: dict[int, list[str]] | None = None
    best_gaps: dict[str, int] = {}
    best_total = 0
    attempts = 0
    for attempts in range(1, attempts_allowed + 1):
        sat_out = draw_sit_outs(sets, rng)
        taking_part = [p for members in _taking_part(sets, sat_out) for p in members]
        gaps = role_shortfalls(taking_part, minimums)
        total = sum(gaps.values())
        if best_out is None or total < best_total:
            best_out, best_gaps, best_total = sat_out, gaps, total
        if total == 0:
            break

    assert best_out is not None
    ordered: list[Player] = []
    player_sets: dict[str, int] = {}
    for number, members in enumerate(_taking_part(sets, best_out), start=1):
        members = list(members)
        rng.shuffle(members)
        ordered.extend(members)
        for player in members:
            player_sets[player.player_id] = number
    return AuctionPlan(
        players=ordered,
        player_sets=player_sets,
        sat_out=best_out,
        attempts=attempts,
        used_fallback=best_total > 0,
        shortfalls=best_gaps if best_total > 0 else {},
    )
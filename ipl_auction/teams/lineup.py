"""Squad rules, Playing XI rules and the hidden franchise rating.

Every franchise builds a squad of 15 players in any mix of roles. After the
auction each franchise picks and locks a Playing XI; the rest are bench. The
franchise rating is calculated from hidden player ratings, so it is only shown
after the lock. Tweak the constants below to change the rules.
"""

from ipl_auction.players.models import Player
from ipl_auction.teams.models import TeamState

# ---- Squad rules (during the auction) -------------------------------------
SQUAD_SIZE = 15
XI_SIZE = 11
BENCH_SIZE = SQUAD_SIZE - XI_SIZE
MAX_OVERSEAS_SQUAD = 6
MIN_BASE_PRICE_CRORE = 0.30  # cheapest base price, used for the purse reserve

# ---- Playing XI rules (hard rules only) -----------------------------------
MAX_OVERSEAS_XI = 4

# ---- Franchise rating constants -------------------------------------------
BENCH_BONUS_WEIGHT = 0.10
PENALTY_NO_WICKETKEEPER = 8.0
MIN_BOWLING_OPTIONS = 5  # Bowlers + All-Rounders in the XI
PENALTY_PER_MISSING_BOWLING_OPTION = 4.0
MAX_PURE_BATTERS = 5
PENALTY_PER_EXTRA_BATTER = 2.0
MAX_OPENERS = 2
PENALTY_PER_EXTRA_OPENER = 3.0
PENALTY_NO_FINISHER = 3.0
PENALTY_NO_PACE = 2.0
PENALTY_NO_SPIN = 2.0
MIN_BATTING_OPTIONS = 6  # Batters + Wicketkeepers + All-Rounders in the XI
PENALTY_PER_MISSING_BATTING_OPTION = 3.0
DEFAULT_PLAYER_RATING = 50.0

STYLE_LABELS = {
    "opener": "Opener",
    "anchor": "Anchor",
    "middle": "Middle order",
    "finisher": "Finisher",
    "pace": "Pace",
    "spin": "Spin",
}


def _category(player: Player) -> str:
    return player.role.strip().casefold()


def style_label(player: Player) -> str:
    """Public tag such as 'Opener' or 'Pace' shown next to a player's role."""
    parts = []
    if player.bat_role:
        parts.append(STYLE_LABELS[player.bat_role])
    if player.bowl_type and _category(player) in {"bowler", "all-rounder"}:
        parts.append(STYLE_LABELS[player.bowl_type])
    return " · ".join(parts)


def _money(value: float) -> str:
    return f"₹{value:.2f} Cr"


# ---------------------------------------------------------------------------
# Squad rules
# ---------------------------------------------------------------------------
def squad_size(team_state: TeamState) -> int:
    return len(team_state.players)


def overseas_count(team_state: TeamState) -> int:
    return sum(1 for player in team_state.players if player.is_overseas)


def squad_is_full(team_state: TeamState) -> bool:
    return squad_size(team_state) >= SQUAD_SIZE


def squad_block_reason(
    player: Player, team_state: TeamState, bid_crore: float
) -> str | None:
    """Why this franchise may not win `player` at `bid_crore`, or None if it may."""
    if squad_is_full(team_state):
        return f"Your squad is full ({SQUAD_SIZE}/{SQUAD_SIZE})."
    if player.is_overseas and overseas_count(team_state) >= MAX_OVERSEAS_SQUAD:
        return (
            f"Overseas limit reached: a squad can have at most "
            f"{MAX_OVERSEAS_SQUAD} overseas players."
        )
    spots_after = SQUAD_SIZE - squad_size(team_state) - 1
    reserve = round(spots_after * MIN_BASE_PRICE_CRORE, 2)
    purse_after = round(team_state.purse_remaining_crore - bid_crore, 2)
    if purse_after + 1e-9 < reserve:
        return (
            f"This bid would leave {_money(max(purse_after, 0))}, below the "
            f"{_money(reserve)} needed to fill your remaining {spots_after} "
            f"spots at {_money(MIN_BASE_PRICE_CRORE)} each."
        )
    return None


def bid_block_reason(
    player: Player, team_state: TeamState, bid_crore: float
) -> str | None:
    """Full check used for the Raise bid button (purse first, then squad rules)."""
    if bid_crore > team_state.purse_remaining_crore + 1e-9:
        return "Your franchise does not have enough purse for this bid."
    return squad_block_reason(player, team_state, bid_crore)


def can_still_buy(team_state: TeamState, remaining_players: list[Player]) -> bool:
    """True if this franchise could win at least one of the remaining players."""
    if squad_is_full(team_state):
        return False
    return any(
        bid_block_reason(player, team_state, player.base_price_crore) is None
        for player in remaining_players
    )


# ---------------------------------------------------------------------------
# Playing XI
# ---------------------------------------------------------------------------
def xi_capacity(team_state: TeamState) -> int:
    """How many players the XI must contain for this squad (normally 11)."""
    overseas = overseas_count(team_state)
    indians = squad_size(team_state) - overseas
    return min(XI_SIZE, indians + min(overseas, MAX_OVERSEAS_XI))


def validate_xi(team_state: TeamState, player_ids: object) -> list[Player]:
    """Return the chosen players, or raise ValueError with a clear message.

    Hard rules only: the right number of players from your own squad and at
    most 4 overseas players. Everything else is allowed (and may cost rating).
    """
    if not isinstance(player_ids, list) or not all(
        isinstance(item, str) for item in player_ids
    ):
        raise ValueError("Send your Playing XI as a list of player ids.")
    if len(set(player_ids)) != len(player_ids):
        raise ValueError("Each player can only be picked once.")
    squad = {player.player_id: player for player in team_state.players}
    unknown = [item for item in player_ids if item not in squad]
    if unknown:
        raise ValueError("You can only pick players from your own squad.")
    required = xi_capacity(team_state)
    if len(player_ids) != required:
        raise ValueError(f"Pick exactly {required} players for your Playing XI.")
    chosen = [squad[item] for item in player_ids]
    if sum(1 for player in chosen if player.is_overseas) > MAX_OVERSEAS_XI:
        raise ValueError(
            f"A Playing XI can have at most {MAX_OVERSEAS_XI} overseas players."
        )
    return chosen


def auto_pick_xi(team_state: TeamState) -> list[Player]:
    """Best XI by hidden rating within the hard rules (used by the host's auto-lock)."""
    required = xi_capacity(team_state)
    ranked = sorted(
        team_state.players,
        key=lambda player: -(
            player.rating if player.rating is not None else DEFAULT_PLAYER_RATING
        ),
    )
    chosen: list[Player] = []
    overseas = 0
    for player in ranked:
        if len(chosen) == required:
            break
        if player.is_overseas:
            if overseas >= MAX_OVERSEAS_XI:
                continue
            overseas += 1
        chosen.append(player)
    return chosen


# ---------------------------------------------------------------------------
# Franchise rating (hidden player ratings; shown only after the XI lock)
# ---------------------------------------------------------------------------
def _rating_of(player: Player) -> float:
    return player.rating if player.rating is not None else DEFAULT_PLAYER_RATING


def franchise_rating(xi: list[Player], bench: list[Player]) -> dict:
    """Return {"rating": float, "breakdown": [{"label", "points"}, ...]}.

    rating = average of the 11 XI ratings
             + 10% of the bench average
             - balance penalties, clamped to 0..100.
    """
    breakdown: list[dict] = []

    base = sum(_rating_of(player) for player in xi) / XI_SIZE
    breakdown.append({"label": "Playing XI strength", "points": round(base, 1)})

    if bench:
        bench_average = sum(_rating_of(player) for player in bench) / len(bench)
        bench_bonus = BENCH_BONUS_WEIGHT * bench_average
        breakdown.append({"label": "Bench depth", "points": round(bench_bonus, 1)})
    else:
        bench_bonus = 0.0

    categories = [_category(player) for player in xi]
    penalties: list[tuple[str, float]] = []

    if "wicketkeeper" not in categories:
        penalties.append(("No wicketkeeper in the XI", PENALTY_NO_WICKETKEEPER))

    bowling_options = [
        player for player in xi if _category(player) in {"bowler", "all-rounder"}
    ]
    missing_bowling = MIN_BOWLING_OPTIONS - len(bowling_options)
    if missing_bowling > 0:
        penalties.append(
            (
                f"Only {len(bowling_options)} bowling options "
                f"(need {MIN_BOWLING_OPTIONS})",
                missing_bowling * PENALTY_PER_MISSING_BOWLING_OPTION,
            )
        )

    batting_options = [
        player
        for player in xi
        if _category(player) in {"batter", "wicketkeeper", "all-rounder"}
    ]
    missing_batting = MIN_BATTING_OPTIONS - len(batting_options)
    if missing_batting > 0:
        penalties.append(
            (
                f"Only {len(batting_options)} batting options "
                f"(need {MIN_BATTING_OPTIONS})",
                missing_batting * PENALTY_PER_MISSING_BATTING_OPTION,
            )
        )

    pure_batters = sum(1 for category in categories if category == "batter")
    if pure_batters > MAX_PURE_BATTERS:
        extra = pure_batters - MAX_PURE_BATTERS
        penalties.append(
            (
                f"{pure_batters} pure batters (more than {MAX_PURE_BATTERS})",
                extra * PENALTY_PER_EXTRA_BATTER,
            )
        )

    openers = sum(1 for player in xi if player.bat_role == "opener")
    if openers > MAX_OPENERS:
        penalties.append(
            (
                f"{openers} openers in the XI (more than {MAX_OPENERS})",
                (openers - MAX_OPENERS) * PENALTY_PER_EXTRA_OPENER,
            )
        )

    if not any(player.bat_role == "finisher" for player in xi):
        penalties.append(("No finisher in the XI", PENALTY_NO_FINISHER))

    if not any(player.bowl_type == "pace" for player in bowling_options):
        penalties.append(("No pace bowler in the XI", PENALTY_NO_PACE))
    if not any(player.bowl_type == "spin" for player in bowling_options):
        penalties.append(("No spinner in the XI", PENALTY_NO_SPIN))

    total = base + bench_bonus
    for label, amount in penalties:
        breakdown.append({"label": label, "points": -round(amount, 1)})
        total -= amount

    return {
        "rating": round(max(0.0, min(100.0, total)), 1),
        "breakdown": breakdown,
    }
from collections import Counter

from ipl_auction.players.models import Player
from ipl_auction.teams.models import TeamState


ROLE_SLOTS = (
    {"key": "wicketkeeper", "label": "Wicketkeeper", "capacity": 1},
    {"key": "opener", "label": "Openers", "capacity": 2},
    {"key": "anchor", "label": "Anchor", "capacity": 1},
    {"key": "middle_order", "label": "Middle order", "capacity": 1},
    {"key": "finisher", "label": "Finisher", "capacity": 1},
    {"key": "allrounder", "label": "All-rounders", "capacity": 2},
    {"key": "fast_bowler", "label": "Fast bowler", "capacity": 1},
    {"key": "spinner", "label": "Spinner", "capacity": 1},
    {"key": "bowler", "label": "Other bowler", "capacity": 1},
)

ROLE_LABELS = {
    "wicketkeeper": "Wicketkeeper",
    "opener": "Opener",
    "anchor": "Anchor",
    "middle_order": "Middle order",
    "finisher": "Finisher",
    "spin_allrounder": "Spin all-rounder",
    "fast_allrounder": "Fast all-rounder",
    "fast_bowler": "Fast bowler",
    "spinner": "Spinner",
    "bowler": "Other bowler",
}

PLAYER_ROLE_ASSIGNMENTS = {
    "wicketkeeper": ("wicketkeeper",),
    "batter": ("opener", "anchor", "middle_order", "finisher"),
    "all-rounder": ("spin_allrounder", "fast_allrounder"),
    "bowler": ("fast_bowler", "spinner", "bowler"),
}

ALL_ROUNDER_ROLES = frozenset({"spin_allrounder", "fast_allrounder"})
LINEUP_SIZE = sum(slot["capacity"] for slot in ROLE_SLOTS)


def _role_counts(team_state: TeamState) -> Counter[str]:
    assigned = Counter(team_state.player_roles.values())
    counts = Counter(assigned)
    counts["allrounder"] = sum(assigned[role] for role in ALL_ROUNDER_ROLES)
    return counts


def role_progress(team_state: TeamState) -> list[dict[str, int | str | bool]]:
    counts = _role_counts(team_state)
    return [
        {
            "key": slot["key"],
            "label": slot["label"],
            "count": min(counts[slot["key"]], slot["capacity"]),
            "capacity": slot["capacity"],
            "complete": counts[slot["key"]] >= slot["capacity"],
        }
        for slot in ROLE_SLOTS
    ]


def lineup_score(team_state: TeamState) -> int:
    filled = sum(int(slot["count"]) for slot in role_progress(team_state))
    return round(filled * 100 / LINEUP_SIZE)


def eligible_roles(player: Player) -> list[dict[str, str]]:
    options = PLAYER_ROLE_ASSIGNMENTS.get(player.role.strip().casefold(), ())
    return [{"key": role, "label": ROLE_LABELS[role]} for role in options]


def available_roles(player: Player, team_state: TeamState) -> list[dict[str, str]]:
    player_category = player.role.strip().casefold()
    counts = _role_counts(team_state)

    if player_category == "wicketkeeper" and any(
        squad_player.role.strip().casefold() == "wicketkeeper"
        for squad_player in team_state.players
    ):
        return []

    available = []
    for option in eligible_roles(player):
        role = option["key"]
        if role in ALL_ROUNDER_ROLES:
            is_available = counts["allrounder"] < 2
        else:
            capacity = next(
                (slot["capacity"] for slot in ROLE_SLOTS if slot["key"] == role),
                0,
            )
            is_available = counts[role] < capacity
        if is_available:
            available.append(option)
    return available


def role_label(role: str | None) -> str | None:
    return ROLE_LABELS.get(role) if role else None

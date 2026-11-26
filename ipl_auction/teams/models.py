from dataclasses import dataclass, field

from ipl_auction.players.models import Player


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    short_name: str = ""
    logo_url: str = ""


@dataclass
class TeamState:
    team: Team
    purse_remaining_crore: float
    players: list[Player] = field(default_factory=list)
    player_roles: dict[str, str] = field(default_factory=dict)

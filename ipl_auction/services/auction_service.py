from ipl_auction.auction.engine import AuctionResult, mark_unsold, sell_player
from ipl_auction.players.models import Player
from ipl_auction.teams.models import TeamState


def record_sale(
    player: Player,
    team_state: TeamState,
    bid_crore: float,
    squad_role: str | None = None,
) -> AuctionResult:
    return sell_player(player, team_state, bid_crore, squad_role)


def record_unsold(player: Player) -> AuctionResult:
    return mark_unsold(player)

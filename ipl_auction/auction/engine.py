from dataclasses import dataclass

from ipl_auction.players.models import Player
from ipl_auction.teams.lineup import squad_block_reason
from ipl_auction.teams.models import TeamState


@dataclass(frozen=True)
class AuctionResult:
    player: Player
    team_id: str | None
    sold_price_crore: float | None


def validate_bid(player: Player, team_state: TeamState, bid_crore: float) -> None:
    if bid_crore < player.base_price_crore:
        raise ValueError("Bid cannot be lower than the player's base price.")
    if bid_crore > team_state.purse_remaining_crore:
        raise ValueError("Bid exceeds the team's remaining purse.")
    reason = squad_block_reason(player, team_state, bid_crore)
    if reason:
        raise ValueError(reason)


def sell_player(
    player: Player,
    team_state: TeamState,
    bid_crore: float,
    squad_role: str | None = None,
) -> AuctionResult:
    validate_bid(player, team_state, bid_crore)
    team_state.purse_remaining_crore = round(
        team_state.purse_remaining_crore - bid_crore, 2
    )
    team_state.players.append(player)
    if squad_role:
        team_state.player_roles[player.player_id] = squad_role
    return AuctionResult(
        player=player,
        team_id=team_state.team.team_id,
        sold_price_crore=bid_crore,
    )


def mark_unsold(player: Player) -> AuctionResult:
    return AuctionResult(player=player, team_id=None, sold_price_crore=None)
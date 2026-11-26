import unittest

from ipl_auction.auction.engine import mark_unsold, sell_player, validate_bid
from ipl_auction.players.models import Player
from ipl_auction.teams.models import Team, TeamState


class AuctionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.player = Player(
            player_id="p1",
            name="Sample Player",
            role="Batter",
            nationality="India",
            base_price_crore=1.0,
        )
        self.team_state = TeamState(
            team=Team(team_id="sample", name="Sample Team"),
            purse_remaining_crore=10.0,
        )

    def test_sale_updates_purse_and_squad(self) -> None:
        result = sell_player(self.player, self.team_state, 2.5)

        self.assertEqual(self.team_state.purse_remaining_crore, 7.5)
        self.assertEqual(self.team_state.players, [self.player])
        self.assertEqual(result.sold_price_crore, 2.5)

    def test_bid_below_base_price_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "base price"):
            validate_bid(self.player, self.team_state, 0.5)

    def test_bid_over_purse_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "remaining purse"):
            validate_bid(self.player, self.team_state, 10.1)

    def test_unsold_result_has_no_team_or_price(self) -> None:
        result = mark_unsold(self.player)

        self.assertIsNone(result.team_id)
        self.assertIsNone(result.sold_price_crore)


if __name__ == "__main__":
    unittest.main()

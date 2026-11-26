import unittest

from ipl_auction.auction.engine import sell_player
from ipl_auction.players.models import Player
from ipl_auction.teams.lineup import available_roles, lineup_score, role_progress
from ipl_auction.teams.models import Team, TeamState


class LineupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.team_state = TeamState(
            team=Team(team_id="sample", name="Sample Team"),
            purse_remaining_crore=20.0,
        )

    def test_wicketkeeper_slot_is_limited_to_one(self) -> None:
        player = Player(
            player_id="wk-1",
            name="Keeper",
            role="Wicketkeeper",
            nationality="India",
            base_price_crore=1.0,
        )

        self.assertEqual(
            available_roles(player, self.team_state),
            [{"key": "wicketkeeper", "label": "Wicketkeeper"}],
        )
        sell_player(player, self.team_state, 1.0, "wicketkeeper")

        self.assertEqual(available_roles(player, self.team_state), [])
        self.assertEqual(lineup_score(self.team_state), 9)
        wicketkeeper_progress = role_progress(self.team_state)[0]
        self.assertEqual(wicketkeeper_progress["count"], 1)
        self.assertTrue(wicketkeeper_progress["complete"])

    def test_only_two_batters_can_fill_the_opener_slots(self) -> None:
        batters = [
            Player(
                player_id=f"batter-{index}",
                name=f"Batter {index}",
                role="Batter",
                nationality="India",
                base_price_crore=1.0,
            )
            for index in range(3)
        ]

        for player in batters[:2]:
            self.assertIn(
                {"key": "opener", "label": "Opener"},
                available_roles(player, self.team_state),
            )
            sell_player(player, self.team_state, 1.0, "opener")

        self.assertNotIn(
            {"key": "opener", "label": "Opener"},
            available_roles(batters[2], self.team_state),
        )
        self.assertEqual(lineup_score(self.team_state), 18)


if __name__ == "__main__":
    unittest.main()

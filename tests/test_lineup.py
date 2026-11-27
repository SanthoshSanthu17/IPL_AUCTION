import unittest

from ipl_auction.auction.engine import sell_player, validate_bid
from ipl_auction.players.models import Player
from ipl_auction.teams.lineup import (
    MAX_OVERSEAS_SQUAD,
    SQUAD_SIZE,
    auto_pick_xi,
    bid_block_reason,
    can_still_buy,
    franchise_rating,
    validate_xi,
    xi_capacity,
)
from ipl_auction.teams.models import Team, TeamState


def make_player(
    index: int,
    role: str = "Batter",
    nationality: str = "India",
    rating: float = 60.0,
    bat_role: str | None = None,
    bowl_type: str | None = None,
    base: float = 0.3,
) -> Player:
    return Player(
        player_id=f"p{index}",
        name=f"Player {index}",
        role=role,
        nationality=nationality,
        base_price_crore=base,
        bat_role=bat_role,
        bowl_type=bowl_type,
        rating=rating,
    )


def make_team(purse: float = 125.0) -> TeamState:
    return TeamState(team=Team(team_id="t", name="Team"), purse_remaining_crore=purse)


def balanced_xi(rating: float = 60.0) -> list[Player]:
    """A well balanced XI that triggers no penalty."""
    return [
        make_player(1, "Wicketkeeper", rating=rating, bat_role="opener"),
        make_player(2, "Batter", rating=rating, bat_role="opener"),
        make_player(3, "Batter", rating=rating, bat_role="middle"),
        make_player(4, "Batter", rating=rating, bat_role="middle"),
        make_player(5, "All-Rounder", rating=rating, bat_role="finisher", bowl_type="pace"),
        make_player(6, "All-Rounder", rating=rating, bat_role="middle", bowl_type="spin"),
        make_player(7, "Bowler", rating=rating, bowl_type="pace"),
        make_player(8, "Bowler", rating=rating, bowl_type="pace"),
        make_player(9, "Bowler", rating=rating, bowl_type="spin"),
        make_player(10, "Bowler", rating=rating, bowl_type="pace"),
        make_player(11, "Batter", rating=rating, bat_role="anchor"),
    ]


class SquadRuleTests(unittest.TestCase):
    def test_any_mix_of_roles_is_allowed_up_to_fifteen(self) -> None:
        team = make_team()
        for index in range(SQUAD_SIZE):
            player = make_player(index, "Batter", bat_role="opener")
            self.assertIsNone(bid_block_reason(player, team, 0.3))
            sell_player(player, team, 0.3)
        self.assertEqual(len(team.players), 15)

    def test_sixteenth_player_is_rejected(self) -> None:
        team = make_team()
        for index in range(SQUAD_SIZE):
            sell_player(make_player(index), team, 0.3)
        extra = make_player(99)
        self.assertIn("squad is full", bid_block_reason(extra, team, 0.3))
        with self.assertRaisesRegex(ValueError, "squad is full"):
            validate_bid(extra, team, 0.3)

    def test_overseas_cap_blocks_the_seventh_overseas_player(self) -> None:
        team = make_team()
        for index in range(MAX_OVERSEAS_SQUAD):
            sell_player(make_player(index, nationality="Australia"), team, 0.3)
        overseas = make_player(50, nationality="England")
        indian = make_player(51)
        self.assertIn("Overseas limit", bid_block_reason(overseas, team, 0.3))
        self.assertIsNone(bid_block_reason(indian, team, 0.3))

    def test_purse_reserve_blocks_bids_that_leave_too_little_money(self) -> None:
        team = make_team(purse=10.0)
        sell_player(make_player(0), team, 0.3)  # purse 9.7, 13 spots after the next buy
        player = make_player(1, base=1.0)
        # reserve = 13 x 0.30 = 3.90 Cr
        self.assertIsNone(bid_block_reason(player, team, 5.8))  # leaves 3.90
        message = bid_block_reason(player, team, 6.0)  # leaves 3.70
        self.assertIn("needed to fill your remaining 13 spots", message)

    def test_purse_message_comes_first_when_bid_exceeds_purse(self) -> None:
        team = make_team(purse=5.0)
        self.assertIn(
            "enough purse", bid_block_reason(make_player(1), team, 6.0)
        )

    def test_can_still_buy_is_false_when_full_or_broke(self) -> None:
        pool = [make_player(100, base=1.0)]
        team = make_team(purse=0.5)
        self.assertFalse(can_still_buy(team, pool))
        self.assertTrue(can_still_buy(make_team(), pool))
        full = make_team()
        for index in range(SQUAD_SIZE):
            sell_player(make_player(index), full, 0.3)
        self.assertFalse(can_still_buy(full, pool))


class PlayingXiTests(unittest.TestCase):
    def squad(self) -> TeamState:
        team = make_team()
        for player in balanced_xi() + [
            make_player(12, "Bowler", bowl_type="pace"),
            make_player(13, "Batter", bat_role="middle"),
            make_player(14, "Bowler", bowl_type="spin"),
            make_player(15, "All-Rounder", bat_role="middle", bowl_type="spin"),
        ]:
            sell_player(player, team, 0.3)
        return team

    def test_xi_must_have_exactly_eleven_players(self) -> None:
        team = self.squad()
        ids = [player.player_id for player in team.players]
        self.assertEqual(len(validate_xi(team, ids[:11])), 11)
        with self.assertRaisesRegex(ValueError, "exactly 11"):
            validate_xi(team, ids[:10])
        with self.assertRaisesRegex(ValueError, "exactly 11"):
            validate_xi(team, ids[:12])

    def test_xi_rejects_duplicates_and_outsiders(self) -> None:
        team = self.squad()
        ids = [player.player_id for player in team.players][:11]
        with self.assertRaisesRegex(ValueError, "only be picked once"):
            validate_xi(team, ids[:10] + [ids[0]])
        with self.assertRaisesRegex(ValueError, "your own squad"):
            validate_xi(team, ids[:10] + ["someone-else"])
        with self.assertRaisesRegex(ValueError, "list of player ids"):
            validate_xi(team, "p1")

    def test_xi_allows_at_most_four_overseas(self) -> None:
        team = make_team()
        for index in range(6):
            sell_player(make_player(index, nationality="Australia"), team, 0.3)
        for index in range(6, 15):
            sell_player(make_player(index), team, 0.3)
        ids = [player.player_id for player in team.players]
        five_overseas = ids[:5] + ids[6:12]
        with self.assertRaisesRegex(ValueError, "at most 4 overseas"):
            validate_xi(team, five_overseas)
        four_overseas = ids[:4] + ids[6:13]
        self.assertEqual(len(validate_xi(team, four_overseas)), 11)

    def test_small_squad_xi_capacity_respects_overseas_cap(self) -> None:
        team = make_team()
        for index in range(6):
            sell_player(make_player(index, nationality="Australia"), team, 0.3)
        sell_player(make_player(6), team, 0.3)
        self.assertEqual(xi_capacity(team), 5)  # 1 Indian + 4 overseas

    def test_auto_pick_takes_best_rated_within_the_overseas_cap(self) -> None:
        team = make_team()
        for index in range(6):
            sell_player(
                make_player(index, nationality="Australia", rating=90 - index), team, 0.3
            )
        for index in range(6, 15):
            sell_player(make_player(index, rating=50), team, 0.3)
        chosen = auto_pick_xi(team)
        self.assertEqual(len(chosen), 11)
        self.assertEqual(sum(player.is_overseas for player in chosen), 4)


class FranchiseRatingTests(unittest.TestCase):
    def bench(self, rating: float = 60.0) -> list[Player]:
        return [make_player(100 + index, rating=rating) for index in range(4)]

    def test_balanced_xi_has_no_penalty(self) -> None:
        result = franchise_rating(balanced_xi(60), self.bench(60))
        self.assertEqual(result["rating"], 66.0)  # 60 + 10% of 60
        self.assertTrue(all(line["points"] >= 0 for line in result["breakdown"]))

    def test_three_openers_lowers_the_rating(self) -> None:
        xi = balanced_xi(60)
        xi[2] = make_player(3, "Batter", rating=60, bat_role="opener")
        result = franchise_rating(xi, self.bench(60))
        self.assertEqual(result["rating"], 63.0)
        self.assertIn(
            {"label": "3 openers in the XI (more than 2)", "points": -3.0},
            result["breakdown"],
        )

    def test_no_wicketkeeper_costs_eight_points(self) -> None:
        xi = balanced_xi(60)
        xi[0] = make_player(1, "Batter", rating=60, bat_role="opener")
        result = franchise_rating(xi, self.bench(60))
        self.assertEqual(result["rating"], 58.0)

    def test_too_few_bowlers_costs_four_per_missing_option(self) -> None:
        xi = balanced_xi(60)
        xi[7] = make_player(8, "Batter", rating=60, bat_role="middle")
        xi[9] = make_player(10, "Batter", rating=60, bat_role="middle")
        result = franchise_rating(xi, self.bench(60))
        labels = [line["label"] for line in result["breakdown"]]
        self.assertTrue(any("bowling options" in label for label in labels))
        # 60 + 6 bench - 4 (one bowling option short) - 2 (6 pure batters)
        self.assertEqual(result["rating"], 60.0)

    def test_missing_finisher_pace_and_spin_are_penalised(self) -> None:
        xi = [
            make_player(index, "Wicketkeeper" if index == 0 else "Bowler", rating=60)
            for index in range(11)
        ]
        labels = [line["label"] for line in franchise_rating(xi, [])["breakdown"]]
        self.assertIn("No finisher in the XI", labels)
        self.assertIn("No pace bowler in the XI", labels)
        self.assertIn("No spinner in the XI", labels)

    def test_better_players_give_a_higher_rating(self) -> None:
        strong = franchise_rating(balanced_xi(80), self.bench(80))["rating"]
        weak = franchise_rating(balanced_xi(50), self.bench(50))["rating"]
        self.assertGreater(strong, weak)

    def test_rating_is_clamped_between_zero_and_one_hundred(self) -> None:
        self.assertEqual(franchise_rating(balanced_xi(100), self.bench(100))["rating"], 100.0)
        awful = [make_player(index, "Batter", rating=0) for index in range(11)]
        self.assertEqual(franchise_rating(awful, [])["rating"], 0.0)


if __name__ == "__main__":
    unittest.main()
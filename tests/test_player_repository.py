import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ipl_auction.players.models import Player
from ipl_auction.players.repository import load_players


class PlayerRepositoryTests(unittest.TestCase):
    def test_auction_player_id_does_not_infer_a_portrait(self) -> None:
        player = Player(
            player_id="ipl-2026-148",
            name="Cameron Green",
            role="Batter",
            nationality="Australia",
            base_price_crore=2.0,
        )

        self.assertIsNone(player.image_url)

    def test_player_uses_explicit_profile_portrait_url(self) -> None:
        image_url = (
            "https://www.iplt20.com/_next/image?url=%2Fapi%2Fteam-assets"
            "%3Fpathname%3Dplayer%252F8903%252F1558%252F70430.png"
            "%26brand%3Dipl%26fallback%3Dfallback-player-men&w=1080&q=75"
        )
        player = Player(
            player_id="ipl-2026-57",
            name="Ravi Bishnoi",
            role="Bowler",
            nationality="India",
            base_price_crore=1.0,
            image_url=image_url,
        )

        self.assertEqual(player.image_url, image_url)

    def test_loads_explicit_player_portrait_url(self) -> None:
        image_url = "https://www.iplt20.com/player-image.png"
        csv_content = (
            "player_id,name,role,nationality,base_price_crore,image_url\n"
            f"p1,Test Player,Batter,India,1.5,{image_url}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "players.csv"
            path.write_text(csv_content, encoding="utf-8")
            with patch("ipl_auction.players.repository.PLAYER_FILE", path):
                player = load_players()[0]

        self.assertEqual(player.image_url, image_url)

    def test_loads_optional_ipl_statistics(self) -> None:
        csv_content = (
            "player_id,name,role,nationality,base_price_crore,matches,runs,"
            "wickets,batting_average,strike_rate,economy\n"
            "p1,Test Player,Batter,India,1.5,20,500,2,31.25,140.5,7.2\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "players.csv"
            path.write_text(csv_content, encoding="utf-8")
            with patch("ipl_auction.players.repository.PLAYER_FILE", path):
                player = load_players()[0]

        self.assertEqual(player.matches, 20)
        self.assertEqual(player.runs, 500)
        self.assertEqual(player.batting_average, 31.25)
        self.assertEqual(player.strike_rate, 140.5)

    def test_blank_statistics_are_optional(self) -> None:
        csv_content = (
            "player_id,name,role,nationality,base_price_crore,matches,runs,"
            "wickets,batting_average,strike_rate,economy\n"
            "p1,Test Player,Batter,India,1.5,,,,,,\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "players.csv"
            path.write_text(csv_content, encoding="utf-8")
            with patch("ipl_auction.players.repository.PLAYER_FILE", path):
                player = load_players()[0]

        self.assertIsNone(player.matches)
        self.assertIsNone(player.economy)

    def test_rejects_non_finite_statistics(self) -> None:
        csv_content = (
            "player_id,name,role,nationality,base_price_crore,matches,runs,"
            "wickets,batting_average,strike_rate,economy\n"
            "p1,Test Player,Batter,India,1.5,,,,,NaN,\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "players.csv"
            path.write_text(csv_content, encoding="utf-8")
            with patch("ipl_auction.players.repository.PLAYER_FILE", path):
                with self.assertRaisesRegex(ValueError, "strike_rate"):
                    load_players()


if __name__ == "__main__":
    unittest.main()

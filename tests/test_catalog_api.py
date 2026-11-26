import asyncio
import unittest

from ipl_auction.server.app import catalog


class CatalogApiTests(unittest.TestCase):
    def test_catalog_exposes_official_auction_sections(self) -> None:
        result = asyncio.run(catalog())
        players = result["player_records"]

        self.assertEqual(result["players"], len(players))
        self.assertEqual(result["auction"]["registered_players"], len(players))
        self.assertEqual(result["players_with_ipl_stats"], len(players))
        self.assertTrue(
            all(player["matches"] > 0 for player in players)
        )
        self.assertNotIn("official_status", players[0])
        self.assertEqual(result["auction"]["target_squad_size"], 11)
        self.assertEqual(len(result["teams"]), 10)
        self.assertTrue(all(team["logo_url"] for team in result["teams"]))
        ravi_bishnoi = next(player for player in players if player["name"] == "Ravi Bishnoi")
        self.assertIn(
            "player%252F8903%252F1558%252F70430.png",
            ravi_bishnoi["image_url"],
        )
        self.assertIn("matches", players[0])
        self.assertIn("economy", players[0])


if __name__ == "__main__":
    unittest.main()

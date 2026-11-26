import io
import json
import unittest
import zipfile
from datetime import date

from ipl_auction.players.update_stats import (
    _load_name_aliases,
    calculate_ipl_stats,
    normalize_player_name,
)


def _match(match_date: str, deliveries: list[dict]) -> dict:
    return {
        "info": {
            "dates": [match_date],
            "players": {
                "Team A": ["A Batter", "C Non-striker"],
                "Team B": ["B Bowler"],
            },
            "registry": {
                "people": {
                    "A Batter": "player-a",
                    "B Bowler": "player-b",
                    "C Non-striker": "player-c",
                }
            },
        },
        "innings": [
            {
                "team": "Team A",
                "overs": [{"over": 0, "deliveries": deliveries}],
            }
        ],
    }


def _archive_bytes(matches: list[dict]) -> io.BytesIO:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as file:
        for index, match in enumerate(matches):
            file.writestr(f"match-{index}.json", json.dumps(match))
    archive.seek(0)
    return archive


class CricsheetStatsTests(unittest.TestCase):
    def test_normalizes_player_names_for_source_matching(self) -> None:
        self.assertEqual(normalize_player_name("Quinton De Kock"), "quinton de kock")

    def test_uses_verified_aliases_without_misattributing_nahids_stats(self) -> None:
        aliases = _load_name_aliases()

        self.assertEqual(
            aliases[normalize_player_name("Wanindu Hasaranga")],
            normalize_player_name("PWH de Silva"),
        )
        self.assertEqual(
            aliases[normalize_player_name("Dasun Shanaka")],
            normalize_player_name("MD Shanaka"),
        )
        self.assertEqual(
            aliases[normalize_player_name("Sandeep Warrier")],
            normalize_player_name("S Sandeep Warrier"),
        )
        self.assertNotIn(normalize_player_name("Nahid Rana"), aliases)

    def test_calculates_stats_and_excludes_matches_after_cutoff(self) -> None:
        deliveries = [
            {
                "batter": "A Batter",
                "bowler": "B Bowler",
                "runs": {"batter": 4, "total": 4},
            },
            {
                "batter": "A Batter",
                "bowler": "B Bowler",
                "extras": {"noballs": 1},
                "runs": {"batter": 1, "extras": 1, "total": 2},
            },
            {
                "batter": "A Batter",
                "bowler": "B Bowler",
                "runs": {"batter": 0, "total": 0},
                "wickets": [{"kind": "bowled", "player_out": "A Batter"}],
            },
            {
                "batter": "C Non-striker",
                "bowler": "B Bowler",
                "extras": {"wides": 1},
                "runs": {"batter": 0, "extras": 1, "total": 1},
                "wickets": [{"kind": "run out", "player_out": "C Non-striker"}],
            },
        ]
        archive = _archive_bytes(
            [
                _match("2025-05-01", deliveries),
                _match(
                    "2026-04-01",
                    [
                        {
                            "batter": "A Batter",
                            "bowler": "B Bowler",
                            "runs": {"batter": 20, "total": 20},
                        }
                    ],
                ),
            ]
        )

        stats = calculate_ipl_stats(archive, date(2025, 12, 31))

        batter = stats["a batter"]
        self.assertEqual(batter.matches, 1)
        self.assertEqual(batter.runs, 5)
        self.assertEqual(batter.batting_average, 5.0)
        self.assertEqual(batter.strike_rate, 166.67)
        bowler = stats["b bowler"]
        self.assertEqual(bowler.wickets, 1)
        self.assertEqual(bowler.economy, 21.0)
        non_striker = stats["c non striker"]
        self.assertEqual(non_striker.runs, 0)
        self.assertEqual(non_striker.batting_average, 0.0)
        self.assertIsNone(non_striker.strike_rate)


if __name__ == "__main__":
    unittest.main()

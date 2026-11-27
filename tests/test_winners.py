import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from ipl_auction.server import winners
from ipl_auction.server.app import past_winners


class PastWinnersStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "past_winners.json"

    def test_missing_file_returns_empty_list(self) -> None:
        self.assertFalse(self.path.exists())
        self.assertEqual(winners.list_winners(self.path), [])
        self.assertTrue(self.path.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [])

    def test_corrupt_file_is_treated_as_empty(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(winners.list_winners(self.path), [])
        self.path.write_text('{"a": 1}', encoding="utf-8")
        self.assertEqual(winners.list_winners(self.path), [])

    def test_record_two_winners_and_read_back_newest_first(self) -> None:
        winners.record_winner(1, "csk", "mi", "abc123", recorded_at="2026-01-01T10:00:00+00:00", path=self.path)
        winners.record_winner(2, "rcb", "kkr", "xyz789", recorded_at="2026-02-01T10:00:00+00:00", path=self.path)

        result = winners.list_winners(self.path)

        self.assertEqual([item["season"] for item in result], [2, 1])
        self.assertEqual(result[0]["winner_team_id"], "rcb")
        self.assertEqual(result[0]["runner_up_team_id"], "kkr")
        self.assertEqual(result[0]["room_code"], "XYZ789")
        self.assertEqual(result[1]["winner_team_id"], "csk")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_same_season_and_room_is_replaced_not_duplicated(self) -> None:
        winners.record_winner(1, "csk", "mi", "ABC123", path=self.path)
        winners.record_winner(1, "gt", "mi", "ABC123", path=self.path)
        result = winners.list_winners(self.path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["winner_team_id"], "gt")

    def test_invalid_input_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            winners.record_winner(0, "csk", "mi", "ABC123", path=self.path)
        with self.assertRaises(ValueError):
            winners.record_winner(1, "csk", "csk", "ABC123", path=self.path)
        with self.assertRaises(ValueError):
            winners.record_winner(1, "csk", "mi", "", path=self.path)


class PastWinnersApiTests(unittest.TestCase):
    def test_endpoint_adds_team_names_and_logos(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "past_winners.json"
        original = winners.WINNERS_FILE
        winners.WINNERS_FILE = path
        self.addCleanup(setattr, winners, "WINNERS_FILE", original)

        empty = asyncio.run(past_winners())
        self.assertEqual(empty, {"winners": []})

        winners.record_winner(1, "csk", "mi", "ABC123", path=path)
        result = asyncio.run(past_winners())["winners"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["season"], 1)
        self.assertTrue(result[0]["winner"]["name"])
        self.assertTrue(result[0]["winner"]["logo_url"])
        self.assertTrue(result[0]["runner_up"]["name"])
        self.assertEqual(result[0]["date"], result[0]["recorded_at"][:10])


if __name__ == "__main__":
    unittest.main()
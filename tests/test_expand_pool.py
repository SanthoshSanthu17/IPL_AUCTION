import csv
import io
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from ipl_auction.players.expand_pool import (
    ROSTER_FILE,
    VALID_ROLES,
    apply_portraits,
    build_pool,
    load_roster,
    merge_roster,
)
from ipl_auction.players.repository import PLAYER_FILE, load_players
from ipl_auction.players.update_stats import normalize_player_name

FIELDS = [
    "player_id", "name", "role", "nationality", "base_price_crore", "matches",
    "runs", "wickets", "batting_average", "strike_rate", "economy",
    "image_url", "portrait_profile_url",
]
PRICE_TIERS = {"2", "1.5", "1", "0.75", "0.5", "0.3"}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def _archive() -> io.BytesIO:
    def match(day: str, people: dict, deliveries: list[dict]) -> dict:
        return {
            "info": {
                "dates": [day],
                "players": {"A": list(people), "B": ["B Bowler"]},
                "registry": {"people": {**people, "B Bowler": "p-bowler"}},
            },
            "innings": [{"team": "A", "overs": [{"over": 0, "deliveries": deliveries}]}],
        }

    ball = {"batter": "V Kohli", "bowler": "B Bowler", "non_striker": "V Kohli",
            "runs": {"batter": 4, "extras": 0, "total": 4}}
    out = {"batter": "V Kohli", "bowler": "B Bowler", "non_striker": "V Kohli",
           "runs": {"batter": 0, "extras": 0, "total": 0},
           "wickets": [{"player_out": "V Kohli", "kind": "bowled"}]}
    people = {"V Kohli": "p-kohli"}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr("1.json", json.dumps(match("2026-04-01", people, [ball, out])))
        file.writestr("2.json", json.dumps(match("2027-04-01", people, [ball])))
    archive.seek(0)
    return archive


class RosterTests(unittest.TestCase):
    def test_roster_is_valid_and_large_enough(self) -> None:
        roster = load_roster()
        self.assertGreaterEqual(len(roster), 65)

    def test_combined_pool_is_balanced(self) -> None:
        roster = load_roster()
        combined_roles = {p.role for p in load_players()} | {r["role"] for r in roster}
        self.assertEqual(combined_roles, VALID_ROLES)
        pool = {normalize_player_name(p.name) for p in load_players()} | {
            normalize_player_name(r["name"]) for r in roster
        }
        self.assertGreaterEqual(len(pool), 100)
        nationalities = {r["nationality"] for r in roster}
        self.assertIn("India", nationalities)
        self.assertGreater(len(nationalities - {"India"}), 3)
        self.assertEqual({r["base_price_crore"] for r in roster}, PRICE_TIERS)


class BuildPoolTests(unittest.TestCase):
    def _build(self, roster_rows: list[dict], portraits: list[dict] | None = None):
        directory = Path(tempfile.mkdtemp())
        players = directory / "players.csv"
        _write_csv(players, FIELDS, [{
            **{c: "" for c in FIELDS}, "player_id": "ipl-2026-57", "name": "Existing Guy",
            "role": "Bowler", "nationality": "India", "base_price_crore": "2",
        }])
        roster = directory / "roster.csv"
        _write_csv(roster, ["name", "role", "nationality", "base_price_crore", "cricsheet_name"],
                   roster_rows)
        portrait = directory / "portraits.csv"
        if portraits:
            _write_csv(portrait, ["name", "image_url", "portrait_profile_url"], portraits)
        info = directory / "info.json"
        info.write_text(json.dumps({"season_year": 2026, "registered_players": 1}))
        result = build_pool(_archive(), date(2026, 12, 31), player_file=players,
                            roster_file=roster, portrait_file=portrait, info_file=info)
        with players.open(encoding="utf-8-sig") as file:
            return result, list(csv.DictReader(file)), json.loads(info.read_text())

    def test_adds_players_with_new_ids_and_cutoff_stats(self) -> None:
        result, rows, info = self._build([
            {"name": "Virat Kohli", "role": "Batter", "nationality": "India",
             "base_price_crore": "2", "cricsheet_name": ""},
            {"name": "Nobody Known", "role": "Bowler", "nationality": "India",
             "base_price_crore": "0.3", "cricsheet_name": ""},
        ])
        kohli = next(r for r in rows if r["name"] == "Virat Kohli")
        self.assertEqual(kohli["player_id"], "ipl-2026-90001")
        self.assertEqual((kohli["matches"], kohli["runs"]), ("1", "4"))  # 2027 match excluded
        self.assertEqual(kohli["image_url"], "")
        unknown = next(r for r in rows if r["name"] == "Nobody Known")
        self.assertEqual(unknown["player_id"], "ipl-2026-90002")
        self.assertEqual(unknown["matches"], "")
        self.assertIn(("Nobody Known", "", "unresolved"), result["resolutions"])
        self.assertIn(("Virat Kohli", "v kohli", "heuristic"), result["resolutions"])
        self.assertEqual(info["registered_players"], 3)

    def test_rerun_does_not_duplicate_players(self) -> None:
        roster = [{"name": "Virat Kohli", "role": "Batter", "nationality": "India",
                   "base_price_crore": "2", "cricsheet_name": ""}]
        _, rows, _ = self._build(roster)
        self.assertEqual([r["name"] for r in rows].count("Virat Kohli"), 1)
        again: list[dict] = list(rows)
        self.assertEqual(merge_roster(again, load_roster_rows(roster), FIELDS), [])

    def test_portrait_override_fills_blanks_only(self) -> None:
        rows = [
            {"name": "A One", "image_url": "", "portrait_profile_url": ""},
            {"name": "B Two", "image_url": "https://keep.me/x.png", "portrait_profile_url": ""},
        ]
        overrides = {
            normalize_player_name("A One"): {"image_url": "https://new/a.png", "portrait_profile_url": "p"},
            normalize_player_name("B Two"): {"image_url": "https://new/b.png", "portrait_profile_url": "q"},
        }
        self.assertEqual(apply_portraits(rows, overrides), 1)
        self.assertEqual(rows[0]["image_url"], "https://new/a.png")
        self.assertEqual(rows[1]["image_url"], "https://keep.me/x.png")


def load_roster_rows(rows: list[dict]) -> list[dict]:
    return rows


if __name__ == "__main__":
    unittest.main()
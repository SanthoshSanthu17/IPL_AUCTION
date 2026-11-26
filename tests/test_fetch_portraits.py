import unittest

from ipl_auction.players.fetch_portraits import add_found, canonical_image_url, match_names

EXISTING = (
    "https://www.iplt20.com/_next/image?url=%2Fapi%2Fteam-assets%3Fpathname%3D"
    "player%252F8903%252F1873%252F65397.png%26brand%3Dipl%26fallback%3Dfallback-player-men"
    "&w=1080&q=75"
)
SQUAD_SRC = (
    "https://www.iplt20.com/_next/image?url=%2Fapi%2Fteam-assets%3Fpathname%3D"
    "player%252F8903%252F1873%252F65397.png%26brand%3Dipl&w=750&q=75"
)


class PortraitUrlTests(unittest.TestCase):
    def test_squad_image_is_rebuilt_in_the_existing_csv_format(self) -> None:
        self.assertEqual(canonical_image_url(SQUAD_SRC), EXISTING)

    def test_already_canonical_url_is_unchanged(self) -> None:
        self.assertEqual(canonical_image_url(EXISTING), EXISTING)

    def test_direct_asset_url_and_non_player_images_are_handled(self) -> None:
        direct = "https://www.iplt20.com/api/team-assets?pathname=player%2F8903%2F1873%2F65397.png&brand=ipl"
        self.assertEqual(canonical_image_url(direct), EXISTING)
        for src in (
            "https://www.iplt20.com/api/team-assets?pathname=static%2Fteams%2F2138%2Fplayer-bg.svg&brand=ipl",
            "https://www.iplt20.com/_next/image?url=https%3A%2F%2Fimages.ctfassets.net%2Fx.png&w=3840&q=75",
            "",
        ):
            self.assertIsNone(canonical_image_url(src))


class MatchTests(unittest.TestCase):
    def _found(self, *names: str) -> dict:
        found: dict = {}
        add_found(found, [
            {"alt": name, "src": SQUAD_SRC,
             "href": "https://www.iplt20.com/players/14c3c843-ed5a-4e29-985b-d54e349bf411/slug"}
            for name in names
        ])
        return found

    def test_exact_match_ignores_case_and_keeps_profile_link(self) -> None:
        found = self._found("Virat KOHLI")
        matches, heuristic, unmatched = match_names(["Virat Kohli"], found)
        self.assertEqual(matches["Virat Kohli"]["portrait_profile_url"],
                         "https://www.iplt20.com/players/14c3c843-ed5a-4e29-985b-d54e349bf411/slug")
        self.assertEqual((heuristic, unmatched), ([], []))

    def test_unique_initial_and_surname_match_is_flagged_for_review(self) -> None:
        matches, heuristic, _ = match_names(["Mohammed Siraj"], self._found("Mohd Siraj"))
        self.assertIn("Mohammed Siraj", matches)
        self.assertEqual(heuristic, [("Mohammed Siraj", "Mohd Siraj")])

    def test_ambiguous_or_missing_names_stay_unmatched(self) -> None:
        found = self._found("Jitesh Sharma", "Jayant Sharma")
        _, _, unmatched = match_names(["Jaideep Sharma", "Nobody Here"], found)
        self.assertEqual(unmatched, ["Jaideep Sharma", "Nobody Here"])


if __name__ == "__main__":
    unittest.main()
import asyncio
import tempfile
import unittest
from pathlib import Path

from ipl_auction.match.engine import T20MatchSimulator, simulate_full_match
from ipl_auction.players.models import Player
from ipl_auction.server import winners
from ipl_auction.server.rooms import RoomManager
from ipl_auction.teams.lineup import franchise_rating
from ipl_auction.teams.models import Team, TeamState
from ipl_auction.tournament.constants import (
    STAGE_ELIMINATOR,
    STAGE_FINAL,
    STAGE_LEAGUE_R1,
    STAGE_LEAGUE_R2,
    STAGE_QUALIFIER_1,
    STAGE_QUALIFIER_2,
    STAGE_SEMI_FINAL,
    STATUS_COMPLETED,
    STATUS_LIVE,
)
from ipl_auction.tournament.manager import build_tournament


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)


def make_xi(prefix: str, rating: float = 70.0) -> list[Player]:
    return [
        Player(f"{prefix}-1", f"{prefix} WK", "Wicketkeeper", "India", 1.0, bat_role="opener", rating=rating),
        Player(f"{prefix}-2", f"{prefix} Opener", "Batter", "India", 1.0, bat_role="opener", rating=rating),
        Player(f"{prefix}-3", f"{prefix} Anchor", "Batter", "India", 1.0, bat_role="anchor", rating=rating),
        Player(f"{prefix}-4", f"{prefix} Middle", "Batter", "Australia", 1.0, bat_role="middle", rating=rating),
        Player(f"{prefix}-5", f"{prefix} Finisher", "All-Rounder", "India", 1.0, bat_role="finisher", bowl_type="pace", rating=rating),
        Player(f"{prefix}-6", f"{prefix} AR Spin", "All-Rounder", "England", 1.0, bat_role="middle", bowl_type="spin", rating=rating),
        Player(f"{prefix}-7", f"{prefix} AR Pace", "All-Rounder", "India", 1.0, bat_role="finisher", bowl_type="pace", rating=rating),
        Player(f"{prefix}-8", f"{prefix} Pace 1", "Bowler", "South Africa", 1.0, bowl_type="pace", rating=rating),
        Player(f"{prefix}-9", f"{prefix} Pace 2", "Bowler", "India", 1.0, bowl_type="pace", rating=rating),
        Player(f"{prefix}-10", f"{prefix} Spin 1", "Bowler", "Afghanistan", 1.0, bowl_type="spin", rating=rating),
        Player(f"{prefix}-11", f"{prefix} Pace 3", "Bowler", "India", 1.0, bowl_type="pace", rating=rating),
    ]


class T20MatchEngineTests(unittest.TestCase):
    def test_deterministic_simulation_and_constraints(self) -> None:
        xi_a = make_xi("A", 78.0)
        xi_b = make_xi("B", 72.0)

        res1 = simulate_full_match("M1", "a", "Team A", xi_a, "b", "Team B", xi_b, seed=12345)
        res2 = simulate_full_match("M1", "a", "Team A", xi_a, "b", "Team B", xi_b, seed=12345)

        self.assertEqual(res1.winner_team_id, res2.winner_team_id)
        self.assertEqual(res1.margin, res2.margin)
        self.assertEqual(res1.innings[0].runs, res2.innings[0].runs)
        self.assertEqual(res1.innings[1].runs, res2.innings[1].runs)

        # Verify max 4 overs (24 balls) per bowler & no consecutive overs by same bowler
        for inn in res1.innings:
            for bcard in inn.bowling_card.values():
                self.assertLessEqual(bcard.legal_balls, 24)
            prev_bowler = None
            last_over = -1
            for ev in inn.deliveries:
                if ev.over_number != last_over:
                    if prev_bowler is not None:
                        self.assertNotEqual(ev.bowler_id, prev_bowler)
                    prev_bowler = ev.bowler_id
                    last_over = ev.over_number

    def test_super_over_triggered_on_tie(self) -> None:
        xi_a = make_xi("A", 70.0)
        xi_b = make_xi("B", 70.0)
        sim = T20MatchSimulator("M-TIE", "a", "Team A", xi_a, "b", "Team B", xi_b, seed=7)

        # Advance until end of 1st innings
        while len(sim.regular_innings) == 1:
            sim.step_ball()

        # Set 2nd innings to tie 1st innings at 20.0 overs and trigger innings completion
        first_runs = sim.regular_innings[0].runs
        inn2 = sim.regular_innings[1]
        inn2.runs = first_runs
        inn2.legal_balls = 120
        inn2.current_over_legal_balls = 6
        transition = sim._check_innings_completion(inn2)

        self.assertTrue(transition.get("super_over_started"))
        self.assertEqual(sim.super_over_round, 1)

        # Complete Super Over(s) until a winner is decided
        res = sim.run_to_completion()
        self.assertTrue(res.tied_regular)
        self.assertGreaterEqual(res.super_overs_played, 1)
        self.assertGreaterEqual(len(res.super_over_innings), 2)
        self.assertIn("Super Over", res.margin)


class TournamentStructureTests(unittest.TestCase):
    def _build_teams(self, ratings: list[float]):
        team_states = {}
        xi_locks = {}
        f_ratings = {}
        ids = []
        for idx, r in enumerate(ratings):
            tid = f"t{idx + 1}"
            ids.append(tid)
            xi = make_xi(tid.upper(), rating=r)
            bench = [
                Player(f"{tid}-b{i}", f"{tid} Bench {i}", "Batter", "India", 0.5, rating=r - 5)
                for i in range(4)
            ]
            team_states[tid] = TeamState(
                team=Team(tid, f"Team {idx + 1}", short_name=f"T{idx + 1}"),
                purse_remaining_crore=10.0,
                players=xi + bench,
            )
            xi_locks[tid] = [p.player_id for p in xi]
            f_ratings[tid] = franchise_rating(xi, bench)
        return ids, team_states, xi_locks, f_ratings

    def test_two_and_three_team_edge_cases(self) -> None:
        # 2 teams -> direct Final
        ids2, ts2, locks2, fr2 = self._build_teams([80.0, 75.0])
        t2 = build_tournament("R2", ids2, ts2, locks2, fr2, seed=10)
        self.assertEqual(t2.format_type, "knockout_2")
        self.assertEqual(len(t2.fixtures), 1)
        self.assertEqual(t2.fixtures[0].stage, STAGE_FINAL)

        # 3 teams -> Semi-Final (#2 vs #3) + Final (#1 vs SF Winner)
        ids3, ts3, locks3, fr3 = self._build_teams([85.0, 80.0, 75.0])
        t3 = build_tournament("R3", ids3, ts3, locks3, fr3, seed=10)
        self.assertEqual(t3.format_type, "knockout_3")
        self.assertEqual([f.stage for f in t3.fixtures], [STAGE_SEMI_FINAL, STAGE_FINAL])
        self.assertEqual(t3.fixtures[0].team_a_id, "t2")
        self.assertEqual(t3.fixtures[0].team_b_id, "t3")
        self.assertEqual(t3.fixtures[1].team_a_id, "t1")
        self.assertIsNone(t3.fixtures[1].team_b_id)

    def test_ten_team_league_swiss_r2_nrr_and_playoffs(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        winners_path = Path(folder.name) / "past_winners.json"
        original_file = winners.WINNERS_FILE
        winners.WINNERS_FILE = winners_path
        self.addCleanup(setattr, winners, "WINNERS_FILE", original_file)

        ratings = [90, 88, 86, 84, 82, 80, 78, 76, 74, 72]
        ids, ts, locks, fr = self._build_teams(ratings)
        t = build_tournament("IPL26", ids, ts, locks, fr, seed=99)

        self.assertEqual(t.format_type, "league_playoffs")
        # Top 2 (t1, t2) skip league stage to Qualifier 1
        q1 = next(f for f in t.fixtures if f.stage == STAGE_QUALIFIER_1)
        self.assertEqual((q1.team_a_id, q1.team_b_id), ("t1", "t2"))

        # Round 1 pairs: t3 vs t10, t4 vs t9, t5 vs t8, t6 vs t7
        r1 = [f for f in t.fixtures if f.stage == STAGE_LEAGUE_R1]
        self.assertEqual(len(r1), 4)
        self.assertEqual((r1[0].team_a_id, r1[0].team_b_id), ("t3", "t10"))
        self.assertEqual((r1[3].team_a_id, r1[3].team_b_id), ("t6", "t7"))

        # Complete all Round 1 matches -> triggers Swiss Round 2 generation
        for fx in r1:
            xi_a = [p for p in ts[fx.team_a_id].players if p.player_id in locks[fx.team_a_id]]
            xi_b = [p for p in ts[fx.team_b_id].players if p.player_id in locks[fx.team_b_id]]
            res = simulate_full_match(
                fx.match_id, fx.team_a_id, ts[fx.team_a_id].team.name, xi_a,
                fx.team_b_id, ts[fx.team_b_id].team.name, xi_b, seed=fx.seed
            )
            t.record_match_completion(fx, res, res.innings[0].deliveries + res.innings[1].deliveries)

        self.assertTrue(t.round2_generated)
        r2 = [f for f in t.fixtures if f.stage == STAGE_LEAGUE_R2]
        self.assertEqual(len(r2), 4)

        # Complete Round 2 -> populates Eliminator with Top 2 from Points Table
        for fx in r2:
            xi_a = [p for p in ts[fx.team_a_id].players if p.player_id in locks[fx.team_a_id]]
            xi_b = [p for p in ts[fx.team_b_id].players if p.player_id in locks[fx.team_b_id]]
            res = simulate_full_match(
                fx.match_id, fx.team_a_id, ts[fx.team_a_id].team.name, xi_a,
                fx.team_b_id, ts[fx.team_b_id].team.name, xi_b, seed=fx.seed
            )
            t.record_match_completion(fx, res, res.innings[0].deliveries + res.innings[1].deliveries)

        self.assertTrue(t.playoffs_initialized)
        table = t.sorted_standings()
        self.assertEqual(len(table), 8)
        for row in table:
            self.assertEqual(row["played"], 2)

        elim = next(f for f in t.fixtures if f.stage == STAGE_ELIMINATOR)
        self.assertEqual((elim.team_a_id, elim.team_b_id), (table[0]["team_id"], table[1]["team_id"]))

        # Play Qualifier 1, Eliminator, Qualifier 2, and Final
        for stage in (STAGE_QUALIFIER_1, STAGE_ELIMINATOR, STAGE_QUALIFIER_2, STAGE_FINAL):
            fx = next(f for f in t.fixtures if f.stage == stage)
            self.assertTrue(fx.is_ready)
            xi_a = [p for p in ts[fx.team_a_id].players if p.player_id in locks[fx.team_a_id]]
            xi_b = [p for p in ts[fx.team_b_id].players if p.player_id in locks[fx.team_b_id]]
            res = simulate_full_match(
                fx.match_id, fx.team_a_id, ts[fx.team_a_id].team.name, xi_a,
                fx.team_b_id, ts[fx.team_b_id].team.name, xi_b, seed=fx.seed
            )
            t.record_match_completion(fx, res, [])

        self.assertIsNotNone(t.champion_team_id)
        self.assertIsNotNone(t.runner_up_team_id)
        saved = winners.list_winners(winners_path)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["winner_team_id"], t.champion_team_id)
        self.assertEqual(saved[0]["runner_up_team_id"], t.runner_up_team_id)
        self.assertEqual(saved[0]["room_code"], "IPL26")


class LiveMatchRoomWebSocketTests(unittest.TestCase):
    def test_tournament_auto_starts_after_xi_lock_and_supports_live_spectator_and_skip(self) -> None:
        async def run_test() -> None:
            manager = RoomManager()
            teams = {
                "a": TeamState(Team("a", "Team A", short_name="TA"), 10.0, players=make_xi("A", 80)),
                "b": TeamState(Team("b", "Team B", short_name="TB"), 10.0, players=make_xi("B", 75)),
                "c": TeamState(Team("c", "Team C", short_name="TC"), 10.0, players=make_xi("C", 70)),
            }
            pool = [Player("pool1", "Pool Player", "Batter", "India", 1.0)]
            room, admin_token = await manager.create_room(pool, teams, 10, 0.25)

            ws_a, ws_b, ws_c = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
            await manager.join(room, ws_a, "Owner A", "a", admin_token)
            await manager.join(room, ws_b, "Owner B", "b", None)
            await manager.join(room, ws_c, "Owner C", "c", None)

            room.status = "complete"
            await manager.handle(room, ws_a, {"type": "auto_lock"})

            self.assertIsNotNone(room.tournament)
            assert room.tournament is not None
            sf = room.tournament.fixtures[0]  # Team B vs Team C

            # Team A is not in Semi-Final -> cannot start_match
            await manager.handle(room, ws_a, {"type": "start_match", "match_id": sf.match_id})
            self.assertEqual(ws_a.messages[-1]["type"], "error")

            # Team B starts the match (sends match_started followed by room state broadcast)
            await manager.handle(room, ws_b, {"type": "start_match", "match_id": sf.match_id})
            self.assertEqual(sf.status, STATUS_LIVE)
            self.assertIn("match_started", [m["type"] for m in ws_b.messages])

            # Team A joins as spectator (read-only) -> cannot change match speed
            await manager.handle(room, ws_a, {"type": "join_spectator", "match_id": sf.match_id})
            self.assertEqual(ws_a.messages[-1]["type"], "match_snapshot")
            self.assertEqual(ws_a.messages[-1]["role"], "spectator")

            await manager.handle(
                room, ws_a, {"type": "set_match_speed", "match_id": sf.match_id, "speed": "skip"}
            )
            self.assertEqual(ws_a.messages[-1]["type"], "error")

            # Team B skips to result -> completes Semi-Final & unlocks Final
            await manager.handle(
                room, ws_b, {"type": "set_match_speed", "match_id": sf.match_id, "speed": "skip"}
            )
            self.assertEqual(sf.status, STATUS_COMPLETED)
            final_fx = room.tournament.fixtures[1]
            self.assertTrue(final_fx.is_ready)
            self.assertEqual(final_fx.team_b_id, sf.winner_team_id)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
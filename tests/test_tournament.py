import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from ipl_auction.match.constants import (
    MAX_SAME_DELIVERY_PER_OVER,
    OVERS_RULES,
    PHASE_BAT_BALL,
    PHASE_BOWL_BALL,
    PHASE_SELECT_BOWLER,
    PHASE_SELECT_NEXT_BATTER,
    PHASE_SELECT_OPENERS,
    PHASE_TOSS_CALL,
    PHASE_TOSS_DECISION,
)
from ipl_auction.match.deliveries import (
    ALL_DELIVERY_CODES,
    DELIVERIES_BY_STYLE,
    LEFT_ARM_ORTHODOX_DELIVERIES,
    LEG_SPIN_DELIVERIES,
    OFF_SPIN_DELIVERIES,
    PACE_DELIVERIES,
    STYLE_LEFT_ARM_ORTHODOX,
    STYLE_LEG_SPIN,
    STYLE_OFF_SPIN,
    STYLE_PACE,
    bowler_style,
    classify_spin_style,
    deliveries_for,
    get_delivery,
)
from ipl_auction.match.engine import (
    T20MatchSimulator,
    shot_probabilities,
    simulate_full_match,
    tactical_modifiers,
)
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
        while len(sim.regular_innings) < 2:
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

        # 3 teams -> Eliminator (#2 vs #3) + Final (#1 vs Eliminator winner)
        ids3, ts3, locks3, fr3 = self._build_teams([85.0, 80.0, 75.0])
        t3 = build_tournament("R3", ids3, ts3, locks3, fr3, seed=10)
        self.assertEqual(t3.format_type, "knockout_3")
        self.assertEqual([f.stage for f in t3.fixtures], [STAGE_ELIMINATOR, STAGE_FINAL])
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


# ---------------------------------------------------------------------------
# Interactive ball-by-ball engine
# ---------------------------------------------------------------------------
def make_sim(overs: int = 20, seed: int = 11, rating_a: float = 70.0, rating_b: float = 70.0):
    return T20MatchSimulator(
        "M-I", "a", "Team A", make_xi("A", rating_a), "b", "Team B", make_xi("B", rating_b),
        seed=seed, total_overs=overs,
    )


def to_first_delivery(sim: T20MatchSimulator, bat_first: bool = True) -> None:
    """Drive toss, openers and the first bowler by hand."""
    sim.call_toss("heads")
    sim.decide_toss("bat")
    batters = sim.available_batters()
    sim.select_openers(batters[0].player_id, batters[1].player_id)
    sim.select_bowler(sim.eligible_bowlers_now()[0].player_id)


def stub_random(sim: T20MatchSimulator, *values: float, then: float = 0.99) -> None:
    queue = list(values)
    sim.rng.random = lambda: queue.pop(0) if queue else then  # type: ignore[method-assign]


class InteractiveMatchFlowTests(unittest.TestCase):
    def test_full_interactive_phase_sequence_and_actors(self) -> None:
        sim = make_sim()
        self.assertEqual(sim.phase, PHASE_TOSS_CALL)
        self.assertEqual(sim.actor_team_id(), "b")  # Team B / away side calls
        with self.assertRaises(ValueError):
            sim.decide_toss("bat")  # wrong phase
        with self.assertRaises(ValueError):
            sim.call_toss("edge")

        sim.call_toss("heads")
        self.assertEqual(sim.phase, PHASE_TOSS_DECISION)
        self.assertEqual(sim.actor_team_id(), sim.toss_winner_id)
        winner = sim.toss_winner_id
        sim.decide_toss("bowl")
        self.assertNotEqual(sim.bat_first_id, winner)
        self.assertEqual(sim.phase, PHASE_SELECT_OPENERS)
        self.assertEqual(sim.actor_team_id(), sim.bat_first_id)

        batters = sim.available_batters()
        self.assertEqual(len(batters), 11)
        with self.assertRaises(ValueError):
            sim.select_openers(batters[0].player_id, batters[0].player_id)
        with self.assertRaises(ValueError):
            sim.select_openers(batters[0].player_id, "not-a-player")
        # Any two distinct batters, even #4 and #9
        sim.select_openers(batters[3].player_id, batters[8].player_id)
        inn = sim.current_innings
        self.assertEqual((inn.striker_id, inn.non_striker_id), (batters[3].player_id, batters[8].player_id))
        self.assertEqual(sim.phase, PHASE_SELECT_BOWLER)
        self.assertEqual(sim.actor_team_id(), sim.bowl_first_id)

        sim.select_bowler(sim.eligible_bowlers_now()[0].player_id)
        self.assertEqual(sim.phase, PHASE_BOWL_BALL)
        stub_random(sim)  # no wide, no wicket, shot fails -> dot
        event, _ = sim.select_delivery(sim.delivery_options()[0]["code"])
        self.assertIsNone(event)
        self.assertEqual(sim.phase, PHASE_BAT_BALL)
        self.assertEqual(sim.actor_team_id(), sim.bat_first_id)
        ball, _ = sim.play_shot("4")
        self.assertEqual(ball.runs_off_bat, 0)
        self.assertEqual(sim.current_innings.legal_balls, 1)
        self.assertEqual(sim.phase, PHASE_BOWL_BALL)

    def test_invalid_shot_and_delivery_codes_rejected(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        with self.assertRaises(ValueError):
            sim.select_delivery("not_a_delivery")
        sim_pace = sim.current_innings.current_bowler_id
        bowler = sim.teams[sim.bowl_first_id]["by_id"][sim_pace]
        wrong = next(c for c in ALL_DELIVERY_CODES if get_delivery(bowler, c) is None)
        with self.assertRaises(ValueError):
            sim.select_delivery(wrong)
        stub_random(sim)
        sim.select_delivery(sim.delivery_options()[0]["code"])
        with self.assertRaises(ValueError):
            sim.play_shot("3")

    def test_wicket_lets_batting_side_choose_any_next_batter(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        # no wide (0.99), wicket roll succeeds (0.0), dismissal type roll -> caught
        stub_random(sim, 0.99, 0.0, 0.0)
        sim.select_delivery(sim.delivery_options()[0]["code"])
        ball, _ = sim.play_shot("6")
        self.assertTrue(ball.is_wicket)
        inn = sim.current_innings
        self.assertEqual(inn.wickets, 1)
        self.assertEqual(sim.phase, PHASE_SELECT_NEXT_BATTER)
        self.assertIsNone(inn.striker_id)
        options = sim.available_batters()
        self.assertEqual(len(options), 9)
        with self.assertRaises(ValueError):
            sim.select_next_batter(inn.non_striker_id)  # already batting
        promoted = options[-1]  # send the last man in, not the next in order
        sim.select_next_batter(promoted.player_id)
        self.assertEqual(inn.striker_id, promoted.player_id)
        self.assertEqual(sim.phase, PHASE_BOWL_BALL)  # same bowler continues the over

    def test_wicket_on_last_ball_of_over_swaps_ends_then_asks_for_bowler(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        stub_random(sim)
        for _ in range(5):
            sim.select_delivery(next(o["code"] for o in sim.delivery_options() if not o["disabled"]))
            sim.play_shot("0")
        stub_random(sim, 0.99, 0.0, 0.0)
        sim.select_delivery(next(o["code"] for o in sim.delivery_options() if not o["disabled"]))
        sim.play_shot("6")
        inn = sim.current_innings
        self.assertEqual(sim.phase, PHASE_SELECT_NEXT_BATTER)
        non_striker_before = inn.non_striker_id
        newcomer = sim.available_batters()[0]
        sim.select_next_batter(newcomer.player_id)
        self.assertEqual(inn.striker_id, non_striker_before)
        self.assertEqual(inn.non_striker_id, newcomer.player_id)
        self.assertEqual(sim.phase, PHASE_SELECT_BOWLER)

    def test_innings_break_sets_target_and_asks_for_openers(self) -> None:
        sim = make_sim(overs=5)
        while len(sim.regular_innings) < 2:
            sim.step_ball()
        first, second = sim.regular_innings
        self.assertTrue(first.completed)
        self.assertEqual(second.target, first.runs + 1)
        self.assertEqual(sim.phase, PHASE_SELECT_OPENERS)
        self.assertEqual(sim.actor_team_id(), second.batting_team_id)
        self.assertEqual(second.max_overs, 5)

    def test_super_over_is_one_over_two_wickets_second_batter_bats_first(self) -> None:
        sim = make_sim(overs=5, seed=3)
        while len(sim.regular_innings) < 2:
            sim.step_ball()
        second = sim.regular_innings[1]
        second.runs = sim.regular_innings[0].runs
        second.legal_balls = second.max_overs * 6
        transition = sim._check_innings_completion(second)
        self.assertTrue(transition["super_over_started"])
        so = sim.current_innings
        self.assertTrue(so.is_super_over)
        self.assertEqual(so.max_overs, 1)
        self.assertEqual(so.max_wickets, 2)
        self.assertEqual(so.batting_team_id, second.batting_team_id)
        self.assertEqual(sim.phase, PHASE_SELECT_OPENERS)
        res = sim.run_to_completion()
        self.assertTrue(res.tied_regular)
        self.assertIn("Super Over", res.margin)

    def test_same_seed_and_same_choices_are_deterministic(self) -> None:
        for overs in (5, 10, 20):
            r1 = simulate_full_match(
                "D", "a", "Team A", make_xi("A", 75), "b", "Team B", make_xi("B", 68),
                seed=99, total_overs=overs,
            )
            r2 = simulate_full_match(
                "D", "a", "Team A", make_xi("A", 75), "b", "Team B", make_xi("B", 68),
                seed=99, total_overs=overs,
            )
            self.assertEqual(r1.margin, r2.margin)
            self.assertEqual(
                [(i.runs, i.wickets, i.legal_balls) for i in r1.innings],
                [(i.runs, i.wickets, i.legal_balls) for i in r2.innings],
            )

        def scripted() -> tuple:
            sim = make_sim(overs=5, seed=21)
            to_first_delivery(sim)
            for _ in range(12):
                if sim.phase == PHASE_BOWL_BALL:
                    sim.select_delivery(next(o["code"] for o in sim.delivery_options() if not o["disabled"]))
                elif sim.phase == PHASE_BAT_BALL:
                    sim.play_shot("2")
                elif sim.phase == PHASE_SELECT_BOWLER:
                    sim.select_bowler(sim.eligible_bowlers_now()[0].player_id)
                elif sim.phase == PHASE_SELECT_NEXT_BATTER:
                    sim.select_next_batter(sim.available_batters()[0].player_id)
            inn = sim.current_innings
            return inn.runs, inn.wickets, inn.legal_balls, [e.commentary for e in inn.deliveries]

        self.assertEqual(scripted(), scripted())

    def test_snapshot_hides_ratings_and_has_elite_boost_metadata(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        snap = sim.snapshot()
        for key in ("striker_is_elite", "bowler_is_elite", "over_boost_used"):
            self.assertIn(key, snap)
        self.assertFalse(snap["over_boost_used"])
        text = json.dumps(snap)
        self.assertNotIn("rating", text)
        sim.select_delivery(next(o["code"] for o in sim.delivery_options()))
        self.assertNotIn("rating", json.dumps(sim.snapshot()))

    def test_commentary_varies_and_never_repeats_back_to_back(self) -> None:
        sim = make_sim(overs=10, seed=5)
        sim.run_to_completion()
        lines = [ev.commentary for inn in sim.regular_innings for ev in inn.deliveries]
        self.assertGreater(len(lines), 60)
        self.assertGreater(len(set(lines)) / len(lines), 0.6)
        for first, second in zip(lines, lines[1:]):
            self.assertNotEqual(first, second)


class OversConfigurationTests(unittest.TestCase):
    def test_overs_rules_for_5_10_20(self) -> None:
        self.assertEqual(OVERS_RULES[5], {"powerplay_end": 2, "death_start": 4, "max_bowler_overs": 1})
        self.assertEqual(OVERS_RULES[10], {"powerplay_end": 3, "death_start": 8, "max_bowler_overs": 2})
        self.assertEqual(OVERS_RULES[20], {"powerplay_end": 6, "death_start": 16, "max_bowler_overs": 4})
        for overs, rules in OVERS_RULES.items():
            sim = make_sim(overs=overs)
            snap = sim.snapshot()
            self.assertEqual(snap["total_overs"], overs)
            self.assertEqual(snap["powerplay_end"], rules["powerplay_end"])
            self.assertEqual(snap["death_start"], rules["death_start"])
            self.assertEqual(snap["max_bowler_overs"], rules["max_bowler_overs"])
        with self.assertRaises(ValueError):
            make_sim(overs=7)

    def test_matches_respect_selected_overs_and_bowler_limits(self) -> None:
        for overs in (5, 10, 20):
            cap = OVERS_RULES[overs]["max_bowler_overs"]
            res = simulate_full_match(
                "O", "a", "Team A", make_xi("A"), "b", "Team B", make_xi("B"),
                seed=4, total_overs=overs,
            )
            for inn in res.innings:
                self.assertLessEqual(inn.legal_balls, overs * 6)
                for card in inn.bowling_card.values():
                    self.assertLessEqual(card.legal_balls, cap * 6)


class BowlerSelectionRuleTests(unittest.TestCase):
    def test_cannot_bowl_consecutive_overs_but_can_alternate(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        inn = sim.current_innings
        first = inn.current_bowler_id
        stub_random(sim)
        for _ in range(6):
            sim.select_delivery(next(o["code"] for o in sim.delivery_options() if not o["disabled"]))
            sim.play_shot("0")
        self.assertEqual(sim.phase, PHASE_SELECT_BOWLER)
        self.assertNotIn(first, {p.player_id for p in sim.eligible_bowlers_now()})
        with self.assertRaises(ValueError):
            sim.select_bowler(first)
        other = sim.eligible_bowlers_now()[0]
        sim.select_bowler(other.player_id)
        for _ in range(6):
            sim.select_delivery(next(o["code"] for o in sim.delivery_options() if not o["disabled"]))
            sim.play_shot("0")
        # Alternate overs are fine: the first bowler is allowed again.
        self.assertIn(first, {p.player_id for p in sim.eligible_bowlers_now()})
        sim.select_bowler(first)
        self.assertEqual(inn.bowler_overs[first], 2)

    def test_max_overs_per_bowler_and_last_bowler_exception(self) -> None:
        sim = make_sim(overs=5)  # 1 over per bowler
        to_first_delivery(sim)
        inn = sim.current_innings
        first = inn.current_bowler_id
        stub_random(sim)
        for _ in range(6):
            sim.select_delivery(next(o["code"] for o in sim.delivery_options() if not o["disabled"]))
            sim.play_shot("0")
        with self.assertRaises(ValueError):
            sim.select_bowler(first)  # 1-over cap already used
        # Everyone else has used their quota: the previous bowler is the only one left,
        # but he has also used his quota, so the engine falls back safely.
        for pid in list(inn.bowling_card):
            if pid != first:
                inn.bowler_overs[pid] = 1
        self.assertTrue(sim.eligible_bowlers_now())
        # With a 2-over cap the previous bowler is allowed when nobody else is left.
        inn.max_bowler_overs = 2
        for pid in list(inn.bowling_card):
            if pid != first:
                inn.bowler_overs[pid] = 2
        self.assertEqual([p.player_id for p in sim.eligible_bowlers_now()], [first])
        sim.select_bowler(first)

    def test_bowler_cannot_exceed_max_overs(self) -> None:
        sim = make_sim(overs=10)
        to_first_delivery(sim)
        inn = sim.current_innings
        stub_random(sim)
        for _ in range(6):
            sim.select_delivery(next(o["code"] for o in sim.delivery_options() if not o["disabled"]))
            sim.play_shot("0")
        target = next(p for p in sim.eligible_bowlers_now())
        inn.bowler_overs[target.player_id] = inn.max_bowler_overs
        with self.assertRaises(ValueError) as ctx:
            sim.select_bowler(target.player_id)
        self.assertIn("maximum", str(ctx.exception))


class DeliveryRuleTests(unittest.TestCase):
    def test_max_two_of_same_delivery_per_over(self) -> None:
        self.assertEqual(MAX_SAME_DELIVERY_PER_OVER, 2)
        sim = make_sim()
        to_first_delivery(sim)
        stub_random(sim)
        code = sim.delivery_options()[0]["code"]
        for _ in range(2):
            sim.select_delivery(code)
            sim.play_shot("0")
        options = {o["code"]: o for o in sim.delivery_options()}
        self.assertEqual(options[code]["remaining"], 0)
        self.assertTrue(options[code]["disabled"])
        with self.assertRaises(ValueError):
            sim.select_delivery(code)
        other = next(c for c, o in options.items() if not o["disabled"])
        sim.select_delivery(other)  # a different delivery is fine
        sim.play_shot("0")

    def test_limit_resets_for_the_next_over(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        stub_random(sim)
        codes = [o["code"] for o in sim.delivery_options()]
        order = [codes[0], codes[0], codes[1], codes[1], codes[2], codes[2]]
        for code in order:
            sim.select_delivery(code)
            sim.play_shot("0")
        sim.select_bowler(sim.eligible_bowlers_now()[0].player_id)
        self.assertTrue(all(o["remaining"] == 2 for o in sim.delivery_options()))

    def test_wide_adds_run_keeps_ball_and_counts_toward_limit(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        stub_random(sim, then=0.0)  # every wide roll hits
        code = "wide_yorker" if sim.delivery_options()[0]["code"] == "yorker" else sim.delivery_options()[0]["code"]
        bowler = sim.teams[sim.bowl_first_id]["by_id"][sim.current_innings.current_bowler_id]
        code = next(d.code for d in deliveries_for(bowler))
        for expected_runs in (1, 2):
            event, _ = sim.select_delivery(code)
            self.assertIsNotNone(event)
            self.assertEqual(event.extra_type, "wide")
            self.assertFalse(event.is_legal)
            self.assertEqual(event.extras, 1)
            self.assertEqual(sim.current_innings.runs, expected_runs)
            self.assertEqual(sim.current_innings.legal_balls, 0)
            self.assertEqual(sim.phase, PHASE_BOWL_BALL)
        with self.assertRaises(ValueError):
            sim.select_delivery(code)  # wides used up the two allowed

    def test_high_risk_defensive_deliveries_have_the_specified_numbers(self) -> None:
        wide_codes = {
            "wide_yorker": (0.21, 0.015, -0.28),
            "wide_googly": (0.21, 0.022, -0.26),
            "wide_flatter": (0.20, 0.018, -0.25),
            "wide_flighted": (0.20, 0.020, -0.25),
        }
        by_code = {d.code: d for group in DELIVERIES_BY_STYLE.values() for d in group}
        for code, (wide, wicket, run) in wide_codes.items():
            delivery = by_code[code]
            self.assertAlmostEqual(delivery.wide_risk, wide)
            self.assertAlmostEqual(delivery.wicket_mod, wicket)
            self.assertAlmostEqual(delivery.run_mod, run)
        yorker = by_code["yorker"]
        self.assertEqual((yorker.wide_risk, yorker.wicket_mod, yorker.run_mod), (0.03, 0.035, -0.22))
        self.assertEqual(len(PACE_DELIVERIES), 8)
        self.assertEqual(len(LEG_SPIN_DELIVERIES), 6)
        self.assertEqual(len(LEFT_ARM_ORTHODOX_DELIVERIES), 6)
        self.assertEqual(len(OFF_SPIN_DELIVERIES), 6)


class SpinSubStyleTests(unittest.TestCase):
    def _bowler(self, name: str, bowl_type: str) -> Player:
        return Player(f"x-{name}", name, "Bowler", "India", 1.0, bowl_type=bowl_type, rating=60.0)

    def test_classification_and_delivery_sets(self) -> None:
        leg = self._bowler("Rashid Khan", "spin")
        left = self._bowler("Ravindra Jadeja", "spin")
        off = self._bowler("Ravichandran Ashwin", "spin")
        pace = self._bowler("Jasprit Bumrah", "pace")
        self.assertEqual(classify_spin_style(leg), STYLE_LEG_SPIN)
        self.assertEqual(classify_spin_style(left), STYLE_LEFT_ARM_ORTHODOX)
        self.assertEqual(classify_spin_style(off), STYLE_OFF_SPIN)
        self.assertEqual(bowler_style(pace), STYLE_PACE)
        self.assertEqual(deliveries_for(leg), LEG_SPIN_DELIVERIES)
        self.assertEqual(deliveries_for(left), LEFT_ARM_ORTHODOX_DELIVERIES)
        self.assertEqual(deliveries_for(off), OFF_SPIN_DELIVERIES)
        self.assertEqual(deliveries_for(pace), PACE_DELIVERIES)

    def test_no_pace_deliveries_for_spinners_and_no_off_spin_for_leg_or_left_arm(self) -> None:
        pace_codes = {d.code for d in PACE_DELIVERIES}
        off_codes = {d.code for d in OFF_SPIN_DELIVERIES}
        for name in ("Rashid Khan", "Yuzvendra Chahal", "Ravindra Jadeja", "Axar Patel", "Sunil Narine"):
            spinner = self._bowler(name, "spin")
            codes = {d.code for d in deliveries_for(spinner)}
            self.assertFalse(codes & pace_codes, name)
            if classify_spin_style(spinner) != STYLE_OFF_SPIN:
                self.assertFalse(codes & off_codes, name)
        pacer = self._bowler("Mohammed Siraj", "pace")
        self.assertFalse({d.code for d in deliveries_for(pacer)} & ({d.code for d in OFF_SPIN_DELIVERIES} | {d.code for d in LEG_SPIN_DELIVERIES}))
        self.assertIsNone(get_delivery(pacer, "googly"))
        self.assertIsNone(get_delivery(self._bowler("Rashid Khan", "spin"), "yorker"))
        self.assertIsNone(get_delivery(self._bowler("Rashid Khan", "spin"), "off_break"))

    def test_snapshot_offers_only_the_bowlers_own_deliveries(self) -> None:
        sim = make_sim()
        sim.call_toss("heads")
        sim.decide_toss("bat")
        batters = sim.available_batters()
        sim.select_openers(batters[0].player_id, batters[1].player_id)
        spinner = next(p for p in sim.eligible_bowlers_now() if p.bowl_type == "spin")
        sim.select_bowler(spinner.player_id)
        codes = {o["code"] for o in sim.snapshot()["available_deliveries"]}
        self.assertEqual(codes, {d.code for d in OFF_SPIN_DELIVERIES})
        self.assertTrue(all(o["max"] == 2 for o in sim.snapshot()["available_deliveries"]))


class TacticalModifierTests(unittest.TestCase):
    def _p(self, bat_role: str | None = None, bowl_type: str | None = None, rating: float = 70.0) -> Player:
        return Player("p", "P", "Batter", "India", 1.0, bat_role=bat_role, bowl_type=bowl_type, rating=rating)

    def _mods(self, **overrides):
        args = dict(
            striker=self._p("anchor"), bowler=self._p(bowl_type="pace"), shot=1, over_number=10,
            total_overs=20, powerplay_end=6, death_start=16, wickets=3, is_opener=False, bowler_wickets=0,
        )
        args.update(overrides)
        return tactical_modifiers(**args)

    def test_neutral_situation_has_no_modifiers(self) -> None:
        self.assertEqual(self._mods(), (0.0, 0.0))

    def test_out_of_position_opener_penalty_only_in_powerplay(self) -> None:
        for role in ("middle", "finisher"):
            wicket, run = self._mods(striker=self._p(role), is_opener=True, over_number=2)
            self.assertAlmostEqual(wicket, 0.045)
            self.assertAlmostEqual(run, -0.08)
        self.assertEqual(self._mods(striker=self._p("opener"), is_opener=True, over_number=2), (0.0, 0.0))
        self.assertEqual(self._mods(striker=self._p("middle"), is_opener=True, over_number=7), (0.0, 0.0))
        self.assertEqual(self._mods(striker=self._p("middle"), is_opener=False, over_number=2), (0.0, 0.0))

    def test_finisher_promotion_bonus_needs_late_overs_and_wickets_in_hand(self) -> None:
        fin = self._p("finisher")
        for shot in (4, 6):
            _, run = self._mods(striker=fin, shot=shot, over_number=17, wickets=2)
            self.assertAlmostEqual(run, 0.07)
        # last 25% of the innings also counts (over 16 of 20 is not yet "late" by fraction alone)
        _, run = self._mods(striker=fin, shot=6, over_number=16, wickets=0, death_start=18)
        self.assertAlmostEqual(run, 0.07)
        self.assertEqual(self._mods(striker=fin, shot=6, over_number=17, wickets=3)[1], 0.0)
        self.assertEqual(self._mods(striker=fin, shot=1, over_number=17, wickets=0)[1], 0.0)
        self.assertEqual(self._mods(striker=fin, shot=6, over_number=10, wickets=0)[1], 0.0)

    def test_death_over_spin_helps_batters(self) -> None:
        spinner = self._p(bowl_type="spin")
        wicket, run = self._mods(bowler=spinner, shot=6, over_number=17)
        self.assertAlmostEqual(wicket, -0.02)
        self.assertAlmostEqual(run, 0.09)
        wicket, run = self._mods(bowler=spinner, shot=1, over_number=17)
        self.assertAlmostEqual(wicket, -0.02)
        self.assertAlmostEqual(run, 0.0)
        self.assertEqual(self._mods(bowler=self._p(bowl_type="pace"), shot=6, over_number=17), (0.0, 0.0))
        self.assertEqual(self._mods(bowler=spinner, shot=6, over_number=10), (0.0, 0.0))

    def test_bowler_rhythm_grows_then_caps(self) -> None:
        wicket, run = self._mods(bowler_wickets=1)
        self.assertAlmostEqual(wicket, 0.015)
        self.assertAlmostEqual(run, -0.03)
        wicket, run = self._mods(bowler_wickets=2)
        self.assertAlmostEqual(wicket, 0.03)
        self.assertAlmostEqual(run, -0.06)
        wicket, run = self._mods(bowler_wickets=6)
        self.assertAlmostEqual(wicket, 0.045)
        self.assertAlmostEqual(run, -0.09)

    def test_probability_formulas_and_clamps(self) -> None:
        yorker = get_delivery(self._p(bowl_type="pace"), "yorker")
        p_w, p_s = shot_probabilities(
            shot=6, delivery=yorker, striker=self._p(), bowler=self._p(),
            momentum=0.0, tactical_wicket_mod=0.0, tactical_run_mod=0.0,
        )
        self.assertAlmostEqual(p_w, 0.17 + 0.035)
        self.assertAlmostEqual(p_s, 0.38 - 0.22)
        # momentum helps scoring and lowers dismissal risk
        p_w2, p_s2 = shot_probabilities(
            shot=6, delivery=yorker, striker=self._p(), bowler=self._p(),
            momentum=1.0, tactical_wicket_mod=0.0, tactical_run_mod=0.0,
        )
        self.assertAlmostEqual(p_w - p_w2, 0.03)
        self.assertAlmostEqual(p_s2 - p_s, 0.08)
        # skill difference of (90-40)/250 = 0.2 and clamps
        p_w3, p_s3 = shot_probabilities(
            shot=0, delivery=yorker, striker=self._p(rating=90), bowler=self._p(rating=40),
            momentum=0.0, tactical_wicket_mod=0.0, tactical_run_mod=0.0,
        )
        self.assertAlmostEqual(p_w3, 0.015)
        self.assertAlmostEqual(p_s3, 0.85 + 0.2 - 0.22)
        _, p_s4 = shot_probabilities(
            shot=0, delivery=yorker, striker=self._p(rating=100), bowler=self._p(rating=0),
            momentum=1.0, tactical_wicket_mod=0.0, tactical_run_mod=0.5,
        )
        self.assertAlmostEqual(p_s4, 0.92)

    def test_momentum_builds_on_ones_and_twos_and_drops_on_boundaries(self) -> None:
        sim = make_sim()
        to_first_delivery(sim)
        inn = sim.current_innings
        stub_random(sim, 0.99, 0.99, 0.0)  # no wide, no wicket, success -> 1 run
        sim.select_delivery(sim.delivery_options()[0]["code"])
        sim.play_shot("1")
        self.assertAlmostEqual(inn.momentum, 0.18)
        stub_random(sim, 0.99, 0.99, 0.0)
        sim.select_delivery(sim.delivery_options()[0]["code"])
        sim.play_shot("2")
        self.assertAlmostEqual(inn.momentum, 0.36)
        stub_random(sim, 0.99, 0.99, 0.0)
        sim.select_delivery(sim.delivery_options()[1]["code"])
        sim.play_shot("4")
        self.assertAlmostEqual(inn.momentum, 0.26)


class TournamentBracketAndOversTests(TournamentStructureTests):
    def test_overs_option_validated_and_propagated(self) -> None:
        ids, ts, locks, fr = self._build_teams([80.0, 75.0])
        t = build_tournament("RO", ids, ts, locks, fr, seed=1, total_overs=10)
        self.assertEqual(t.total_overs, 10)
        with self.assertRaises(ValueError):
            build_tournament("RO", ids, ts, locks, fr, seed=1, total_overs=15)
        self.assertEqual(build_tournament("RO", ids, ts, locks, fr, seed=1).total_overs, 20)

    def test_six_team_bracket_is_league_then_ipl_playoffs(self) -> None:
        ids, ts, locks, fr = self._build_teams([90, 85, 80, 75, 70, 65])
        t = build_tournament("R6", ids, ts, locks, fr, seed=5, total_overs=5)
        self.assertEqual(t.format_type, "league_playoffs")
        stages = [f.stage for f in t.fixtures]
        self.assertEqual(stages.count(STAGE_LEAGUE_R1), 2)
        self.assertEqual(stages[-4:], [STAGE_QUALIFIER_1, STAGE_ELIMINATOR, STAGE_QUALIFIER_2, STAGE_FINAL])
        self.assertEqual(sorted(t.league_team_ids), ["t3", "t4", "t5", "t6"])
        q1 = next(f for f in t.fixtures if f.stage == STAGE_QUALIFIER_1)
        self.assertEqual((q1.team_a_id, q1.team_b_id), ("t1", "t2"))

    def test_three_team_eliminator_winner_reaches_final(self) -> None:
        ids, ts, locks, fr = self._build_teams([85, 80, 75])
        t = build_tournament("R3", ids, ts, locks, fr, seed=2)
        elim, final = t.fixtures
        xi = lambda tid: [p for p in ts[tid].players if p.player_id in locks[tid]]
        res = simulate_full_match(
            elim.match_id, elim.team_a_id, "Team 2", xi(elim.team_a_id),
            elim.team_b_id, "Team 3", xi(elim.team_b_id), seed=elim.seed, total_overs=t.total_overs,
        )
        t.record_match_completion(elim, res, [])
        self.assertEqual(final.team_a_id, "t1")
        self.assertEqual(final.team_b_id, res.winner_team_id)


class InteractiveRoomWebSocketTests(unittest.TestCase):
    async def _room(self):
        manager = RoomManager()
        teams = {
            "a": TeamState(Team("a", "Team A", short_name="TA"), 10.0, players=make_xi("A", 80)),
            "b": TeamState(Team("b", "Team B", short_name="TB"), 10.0, players=make_xi("B", 75)),
            "c": TeamState(Team("c", "Team C", short_name="TC"), 10.0, players=make_xi("C", 70)),
        }
        pool = [Player("pool1", "Pool Player", "Batter", "India", 1.0)]
        room, admin_token = await manager.create_room(pool, teams, 10, 0.25)
        sockets = {k: FakeWebSocket() for k in "abc"}
        await manager.join(room, sockets["a"], "Owner A", "a", admin_token)
        await manager.join(room, sockets["b"], "Owner B", "b", None)
        await manager.join(room, sockets["c"], "Owner C", "c", None)
        return manager, room, sockets

    def test_host_sets_overs_and_auto_lock_initialises_tournament(self) -> None:
        async def run_test() -> None:
            manager, room, ws = await self._room()
            self.assertEqual(room.tournament_overs, 20)

            await manager.handle(room, ws["b"], {"type": "set_tournament_overs", "total_overs": 10})
            self.assertEqual(ws["b"].messages[-1]["type"], "error")  # not the host
            await manager.handle(room, ws["a"], {"type": "set_tournament_overs", "total_overs": 7})
            self.assertEqual(ws["a"].messages[-1]["type"], "error")
            await manager.handle(room, ws["a"], {"type": "set_tournament_overs", "total_overs": 10})
            self.assertEqual(room.tournament_overs, 10)
            self.assertEqual(ws["a"].messages[-1]["state"]["tournament_overs"], 10)

            room.status = "complete"
            await manager.handle(room, ws["a"], {"type": "auto_lock"})
            self.assertIsNotNone(room.tournament)
            self.assertEqual(room.tournament.total_overs, 10)
            self.assertEqual(room.tournament.format_type, "knockout_3")
            self.assertEqual(room.tournament.fixtures[0].stage, STAGE_ELIMINATOR)

            await manager.handle(room, ws["a"], {"type": "set_tournament_overs", "total_overs": 5})
            self.assertEqual(room.tournament.total_overs, 5)

        asyncio.run(run_test())

    def test_overs_locked_once_a_match_has_started(self) -> None:
        async def run_test() -> None:
            manager, room, ws = await self._room()
            room.status = "complete"
            await manager.handle(room, ws["a"], {"type": "auto_lock"})
            fx = room.tournament.fixtures[0]
            await manager.handle(room, ws["b"], {"type": "start_match", "match_id": fx.match_id})
            await manager.handle(room, ws["a"], {"type": "set_tournament_overs", "total_overs": 5})
            self.assertEqual(ws["a"].messages[-1]["type"], "error")
            self.assertEqual(room.tournament.total_overs, 20)

        asyncio.run(run_test())

    def test_two_owners_play_live_and_spectator_watches_read_only(self) -> None:
        async def run_test() -> None:
            manager, room, ws = await self._room()
            room.status = "complete"
            await manager.handle(room, ws["a"], {"type": "auto_lock"})
            fx = room.tournament.fixtures[0]  # Team B vs Team C
            self.assertEqual((fx.team_a_id, fx.team_b_id), ("b", "c"))

            await manager.handle(room, ws["b"], {"type": "start_match", "match_id": fx.match_id})
            started = [m for m in ws["b"].messages if m["type"] == "match_started"][-1]
            self.assertEqual(started["match"]["phase"], "toss_call")
            self.assertEqual(started["match"]["actor_team_id"], "c")  # Team B side of the fixture calls
            mid = fx.match_id

            await manager.handle(room, ws["a"], {"type": "join_spectator", "match_id": mid})
            self.assertEqual(ws["a"].messages[-1]["role"], "spectator")

            # Spectators cannot play; the wrong franchise cannot act out of turn.
            await manager.handle(room, ws["a"], {"type": "call_toss", "match_id": mid, "call": "heads"})
            self.assertEqual(ws["a"].messages[-1]["type"], "error")
            await manager.handle(room, ws["b"], {"type": "call_toss", "match_id": mid, "call": "heads"})
            self.assertEqual(ws["b"].messages[-1]["type"], "error")
            self.assertIn("not your turn", ws["b"].messages[-1]["message"])

            await manager.handle(room, ws["c"], {"type": "call_toss", "match_id": mid, "call": "heads"})
            session = room.tournament.sessions[mid]
            self.assertEqual(session.simulator.phase, PHASE_TOSS_DECISION)
            update_b = [m for m in ws["b"].messages if m["type"] == "match_update"][-1]
            self.assertEqual(update_b["role"], "participant")
            self.assertEqual(update_b["event"], "toss")
            update_a = [m for m in ws["a"].messages if m["type"] == "match_update"][-1]
            self.assertEqual(update_a["role"], "spectator")

            winner_ws = ws["b"] if session.simulator.toss_winner_id == "b" else ws["c"]
            await manager.handle(room, winner_ws, {"type": "decide_toss", "match_id": mid, "decision": "bat"})
            self.assertEqual(session.simulator.phase, PHASE_SELECT_OPENERS)
            batting_ws = ws[session.simulator.bat_first_id]
            bowling_ws = ws[session.simulator.bowl_first_id]
            batters = session.simulator.available_batters()

            await manager.handle(room, bowling_ws, {
                "type": "select_openers", "match_id": mid,
                "striker_id": batters[0].player_id, "non_striker_id": batters[1].player_id})
            self.assertEqual(bowling_ws.messages[-1]["type"], "error")
            await manager.handle(room, batting_ws, {
                "type": "select_openers", "match_id": mid,
                "striker_id": batters[2].player_id, "non_striker_id": batters[5].player_id})
            self.assertEqual(session.simulator.phase, PHASE_SELECT_BOWLER)

            bowler = session.simulator.eligible_bowlers_now()[0]
            await manager.handle(room, bowling_ws, {"type": "select_bowler", "match_id": mid, "bowler_id": bowler.player_id})
            code = session.simulator.delivery_options()[0]["code"]
            await manager.handle(room, bowling_ws, {"type": "select_delivery", "match_id": mid, "delivery_code": code})
            if session.simulator.phase == PHASE_BAT_BALL:
                await manager.handle(room, batting_ws, {"type": "play_shot", "match_id": mid, "shot_code": "1"})
                last = [m for m in batting_ws.messages if m["type"] == "match_update"][-1]
                self.assertIn(last["event"], {"ball", "wicket", "innings_completed"})
                self.assertIsNotNone(last["ball"])
                self.assertEqual(last["match"]["legal_balls"], 1)

            # Host can auto-advance a step even when not playing; spectators still read-only.
            before = session.simulator.revision
            await manager.handle(room, ws["a"], {"type": "simulate_ball", "match_id": mid})
            self.assertGreater(session.simulator.revision, before)
            self.assertNotEqual(ws["a"].messages[-1]["type"], "error")

        asyncio.run(run_test())

    def test_autoplay_speed_toggle_and_manual_return(self) -> None:
        async def run_test() -> None:
            manager, room, ws = await self._room()
            room.status = "complete"
            await manager.handle(room, ws["a"], {"type": "auto_lock"})
            fx = room.tournament.fixtures[0]
            await manager.handle(room, ws["b"], {"type": "start_match", "match_id": fx.match_id})
            session = room.tournament.sessions[fx.match_id]
            self.assertEqual(session.speed, "manual")
            self.assertIsNone(session.task)

            await manager.handle(room, ws["b"], {"type": "set_match_speed", "match_id": fx.match_id, "speed": "normal"})
            self.assertIsNotNone(session.task)
            await manager.handle(room, ws["c"], {"type": "call_toss", "match_id": fx.match_id, "call": "heads"})
            self.assertEqual(ws["c"].messages[-1]["type"], "error")  # auto-play owns the match

            await manager.handle(room, ws["b"], {"type": "set_match_speed", "match_id": fx.match_id, "speed": "manual"})
            self.assertIsNone(session.task)
            self.assertEqual(session.speed, "manual")
            await manager.handle(room, ws["c"], {"type": "call_toss", "match_id": fx.match_id, "call": "tails"})
            self.assertEqual(session.simulator.phase, PHASE_TOSS_DECISION)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
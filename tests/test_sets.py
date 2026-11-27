import asyncio
import random
import time
import unittest
from unittest.mock import patch

from ipl_auction.players import sets
from ipl_auction.players.models import Player
from ipl_auction.players.repository import load_players
from ipl_auction.players.sets import (
    MAX_DRAW_ATTEMPTS,
    PLAYERS_PER_SET_IN_AUCTION,
    ROLE_MINIMUMS,
    SET_COUNT,
    SET_SIZE,
    build_auction_plan,
    role_counts,
    split_into_sets,
)
from ipl_auction.server.app import catalog
from ipl_auction.server.rooms import RoomManager
from ipl_auction.teams.models import Team, TeamState
from ipl_auction.teams.repository import load_teams


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, message) -> None:
        self.messages.append(message)


def make_pool(count=200, elite=0, **overrides) -> list[Player]:
    """A synthetic pool: rating falls with the index, the first `elite` are ELITE."""
    roles = ["Batter", "Bowler", "All-Rounder", "Wicketkeeper"]
    pool = []
    for index in range(count):
        role = roles[index % 4]
        pool.append(
            Player(
                player_id=f"s{index}",
                name=f"Set Player {index}",
                role=role,
                nationality="India" if index % 3 else "Australia",
                base_price_crore=0.3,
                elite=index < elite,
                bat_role="opener" if index % 5 == 0 else "middle",
                bowl_type=("pace" if index % 2 else "spin") if role in {"Bowler", "All-Rounder"} else None,
                rating=100.0 - index * 0.25,
                **overrides,
            )
        )
    return pool


class SplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.players = load_players()
        cls.sets = split_into_sets(cls.players)

    def test_pool_splits_into_four_sets_of_fifty(self) -> None:
        self.assertEqual(len(self.sets), SET_COUNT)
        self.assertEqual([len(members) for members in self.sets], [SET_SIZE] * SET_COUNT)
        ids = [player.player_id for members in self.sets for player in members]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {player.player_id for player in self.players})

    def test_every_elite_player_is_in_set_one(self) -> None:
        elite_ids = {player.player_id for player in self.players if player.elite}
        self.assertEqual(len(elite_ids), 16)
        self.assertTrue(elite_ids <= {player.player_id for player in self.sets[0]})

    def test_sets_fall_in_rating_order(self) -> None:
        for higher, lower in zip(self.sets, self.sets[1:]):
            self.assertGreaterEqual(
                min(player.rating for player in higher),
                max(player.rating for player in lower),
            )

    def test_elite_players_go_to_set_one_even_with_a_low_rating(self) -> None:
        pool = make_pool(elite=0)
        weak_elite = pool[-1].__class__(**{**pool[-1].__dict__, "elite": True, "rating": 1.0})
        pool[-1] = weak_elite
        self.assertIn(weak_elite.player_id, {p.player_id for p in split_into_sets(pool)[0]})

    def test_uneven_pool_is_split_as_evenly_as_possible(self) -> None:
        parts = split_into_sets(make_pool(count=10))
        self.assertEqual([len(part) for part in parts], [3, 3, 2, 2])

    def test_catalog_exposes_set_numbers_and_the_set_rules(self) -> None:
        result = asyncio.run(catalog())
        records = result["player_records"]
        self.assertEqual(
            result["sets"],
            {"count": SET_COUNT, "size": SET_SIZE, "taking_part": PLAYERS_PER_SET_IN_AUCTION},
        )
        self.assertTrue(all(record["set_number"] in range(1, SET_COUNT + 1) for record in records))
        self.assertTrue(all(r["set_number"] == 1 for r in records if r["elite"]))
        self.assertTrue(all("rating" not in record for record in records))


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.players = load_players()
        cls.elite_ids = {player.player_id for player in cls.players if player.elite}

    def test_plan_has_forty_per_set_and_ten_sitting_out(self) -> None:
        plan = build_auction_plan(self.players, random.Random(7))
        self.assertEqual(len(plan.players), SET_COUNT * PLAYERS_PER_SET_IN_AUCTION)
        for number in range(1, SET_COUNT + 1):
            taking_part = [p for p in plan.players if plan.player_sets[p.player_id] == number]
            self.assertEqual(len(taking_part), PLAYERS_PER_SET_IN_AUCTION)
            self.assertEqual(len(plan.sat_out[number]), SET_SIZE - PLAYERS_PER_SET_IN_AUCTION)
        sat_out_ids = {pid for ids in plan.sat_out.values() for pid in ids}
        self.assertFalse(sat_out_ids & {p.player_id for p in plan.players})
        self.assertEqual(len(sat_out_ids) + len(plan.players), len(self.players))

    def test_elite_players_never_sit_out(self) -> None:
        for seed in range(25):
            plan = build_auction_plan(self.players, random.Random(seed))
            sat_out_ids = {pid for ids in plan.sat_out.values() for pid in ids}
            self.assertFalse(self.elite_ids & sat_out_ids)
            self.assertTrue(self.elite_ids <= {p.player_id for p in plan.players})

    def test_role_minimums_hold_for_every_draw(self) -> None:
        for seed in range(25):
            plan = build_auction_plan(self.players, random.Random(seed))
            self.assertFalse(plan.used_fallback)
            counts = role_counts(plan.players)
            for name, minimum in ROLE_MINIMUMS.items():
                self.assertGreaterEqual(counts[name], minimum, name)
            self.assertGreaterEqual(counts["wicketkeepers"], 10)
            self.assertGreaterEqual(counts["openers"], 20)

    def test_sets_are_called_in_order_and_shuffled_inside_a_set(self) -> None:
        plan = build_auction_plan(self.players, random.Random(3))
        numbers = [plan.player_sets[p.player_id] for p in plan.players]
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(set(numbers), set(range(1, SET_COUNT + 1)))
        first_set_ids = [p.player_id for p in plan.players if plan.player_sets[p.player_id] == 1]
        by_rating = sorted(first_set_ids, key=lambda pid: -next(p.rating for p in self.players if p.player_id == pid))
        self.assertNotEqual(first_set_ids, by_rating)
        other = build_auction_plan(self.players, random.Random(4))
        self.assertNotEqual([p.player_id for p in plan.players], [p.player_id for p in other.players])

    def test_a_failed_draw_is_redrawn(self) -> None:
        real = sets.role_shortfalls
        calls = []

        def fail_once(players, minimums=None):
            calls.append(1)
            return {"openers": 3} if len(calls) == 1 else real(players, minimums)

        with patch("ipl_auction.players.sets.role_shortfalls", fail_once):
            plan = build_auction_plan(self.players, random.Random(1))
        self.assertEqual(plan.attempts, 2)
        self.assertFalse(plan.used_fallback)

    def test_fallback_uses_the_closest_draw_after_the_retry_limit(self) -> None:
        pool = make_pool(elite=16)
        impossible = {"wicketkeepers": 500}
        with patch.object(sets, "MAX_DRAW_ATTEMPTS", 7):
            plan = build_auction_plan(pool, random.Random(2), minimums=impossible)
        self.assertEqual(plan.attempts, 7)
        self.assertTrue(plan.used_fallback)
        self.assertIn("wicketkeepers", plan.shortfalls)
        self.assertEqual(len(plan.players), SET_COUNT * PLAYERS_PER_SET_IN_AUCTION)
        sat_out_ids = {pid for ids in plan.sat_out.values() for pid in ids}
        self.assertFalse({p.player_id for p in pool if p.elite} & sat_out_ids)

    def test_default_retry_limit_is_set(self) -> None:
        self.assertGreater(MAX_DRAW_ATTEMPTS, 1)


class SetRoomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RoomManager()
        self.players = load_players()
        self.teams = {
            team.team_id: TeamState(team=team, purse_remaining_crore=125.0)
            for team in load_teams()
        }

    async def stop_timers(self, room) -> None:
        for task in (room.timer_task, room.advance_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def expire_current_lot(self, room) -> None:
        if room.timer_task:
            room.timer_task.cancel()
            try:
                await room.timer_task
            except asyncio.CancelledError:
                pass
            room.timer_task = None
        deadline_at = time.time() - 0.01
        room.deadline_at = deadline_at
        await self.manager._expire_player(room, deadline_at)

    def test_room_stores_sets_and_hides_sit_outs_from_the_live_lists(self) -> None:
        async def run_test() -> None:
            plan = build_auction_plan(self.players, random.Random(11))
            room, token = await self.manager.create_room(
                plan.players, self.teams, 10, 0.1, plan=plan
            )
            self.assertEqual(room.player_sets, plan.player_sets)
            self.assertEqual(room.sat_out, plan.sat_out)
            admin = FakeWebSocket()
            team_id = next(iter(self.teams))
            await self.manager.join(room, admin, "Host", team_id, token)
            await self.manager.handle(room, admin, {"type": "start"})
            state = self.manager.snapshot(room)

            sat_out_ids = {pid for ids in plan.sat_out.values() for pid in ids}
            listed = {player["player_id"] for player in state["players"]}
            self.assertEqual(state["player_count"], 160)
            self.assertEqual(len(listed), 160)
            self.assertFalse(listed & sat_out_ids)
            self.assertEqual(state["current_player"]["set_number"], 1)
            self.assertTrue(all(player["set_number"] in (1, 2, 3, 4) for player in state["players"]))
            self.assertTrue(state["sets"]["enabled"])
            self.assertEqual(state["sets"]["current"], 1)
            self.assertEqual(
                [(row["number"], row["total"], row["called"]) for row in state["sets"]["rows"]],
                [(n, 40, 0) for n in range(1, 5)],
            )
            # who sat out stays on the server (only counts are sent); no ratings either
            self.assertFalse(any(pid in str(state["sets"]) for pid in sat_out_ids))
            for player in state["players"]:
                self.assertNotIn("rating", player)
            await self.stop_timers(room)

        asyncio.run(run_test())

    def test_a_room_without_a_plan_has_no_sets(self) -> None:
        async def run_test() -> None:
            teams = {"a": TeamState(Team("a", "Team A"), 10.0)}
            room, _ = await self.manager.create_room(self.players[:3], teams, 10, 0.1)
            state = self.manager.snapshot(room)
            self.assertFalse(state["sets"]["enabled"])
            self.assertIsNone(state["set_break"])
            self.assertTrue(all(player["set_number"] is None for player in state["players"]))

        asyncio.run(run_test())

    def test_set_break_is_announced_between_sets_only(self) -> None:
        async def run_test() -> None:
            pool = make_pool(count=8)
            # a small pool: 2 players per set, 4 sets (all take part)
            with patch.object(sets, "PLAYERS_PER_SET_IN_AUCTION", 2):
                plan = build_auction_plan(pool, random.Random(5), minimums={})
            teams = {"a": TeamState(Team("a", "Team A"), 125.0)}
            room, token = await self.manager.create_room(plan.players, teams, 10, 0.1, plan=plan)
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", token)
            await self.manager.handle(room, admin, {"type": "start"})
            breaks = []
            with patch("ipl_auction.server.rooms.AUTO_ADVANCE_DELAY_SECONDS", 0.01), patch(
                "ipl_auction.server.rooms.SET_BREAK_SECONDS", 0.01
            ):
                while room.status == "live":
                    await self.expire_current_lot(room)
                    info = self.manager.snapshot(room)["set_break"]
                    breaks.append(info["next_set"] if info else None)
                    if room.advance_task:
                        await room.advance_task
            self.assertEqual(breaks[:7], [None, 2, None, 3, None, 4, None])
            await self.stop_timers(room)

        asyncio.run(run_test())

    def test_full_auction_with_sets_ends_cleanly(self) -> None:
        async def run_test() -> None:
            plan = build_auction_plan(self.players, random.Random(21))
            room, token = await self.manager.create_room(
                plan.players, self.teams, 10, 0.1, plan=plan
            )
            team_ids = list(self.teams)
            sockets = {}
            for index, team_id in enumerate(team_ids):
                sockets[team_id] = FakeWebSocket()
                await self.manager.join(
                    room, sockets[team_id], f"Owner {index}", team_id,
                    token if index == 0 else None,
                )
            with patch("ipl_auction.server.rooms.AUTO_ADVANCE_DELAY_SECONDS", 0.001), patch(
                "ipl_auction.server.rooms.SET_BREAK_SECONDS", 0.001
            ):
                await self.manager.handle(room, sockets[team_ids[0]], {"type": "start"})
                lot = 0
                while room.status == "live":
                    for offset in range(len(team_ids)):
                        team_id = team_ids[(lot + offset) % len(team_ids)]
                        try:
                            await self.manager.handle(room, sockets[team_id], {"type": "bid"})
                        except Exception:
                            continue
                        if room.highest_team_id == team_id:
                            break
                    lot += 1
                    await self.expire_current_lot(room)
                    if room.advance_task:
                        await room.advance_task
            self.assertEqual(room.status, "complete")
            self.assertIn(room.complete_reason, {"squads_full", "pool_exhausted", "no_buyers"})
            called_sets = [room.player_sets[r["player_id"]] for r in room.results]
            self.assertEqual(called_sets, sorted(called_sets))
            for state in self.teams.values():
                self.assertLessEqual(len(state.players), 15)
                self.assertLessEqual(sum(p.is_overseas for p in state.players), 6)
            sat_out_ids = {pid for ids in room.sat_out.values() for pid in ids}
            self.assertFalse({r["player_id"] for r in room.results} & sat_out_ids)
            snapshot = self.manager.snapshot(room)
            self.assertIsNone(snapshot["set_break"])
            self.assertEqual(sum(row["called"] for row in snapshot["sets"]["rows"]), len(room.results))
            await self.stop_timers(room)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
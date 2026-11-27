import asyncio
import time
import unittest
from unittest.mock import patch

from ipl_auction.players.models import Player
from ipl_auction.server.rooms import RoomManager
from ipl_auction.teams.models import Team, TeamState


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, message) -> None:
        self.messages.append(message)


class RoomManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RoomManager()
        self.player = Player(
            player_id="p1",
            name="Sample Player",
            role="Batter",
            nationality="India",
            base_price_crore=1.0,
        )
        self.teams = {
            "a": TeamState(Team("a", "Team A"), 10.0),
            "b": TeamState(Team("b", "Team B"), 10.0),
        }

    def test_room_snapshot_includes_player_image_and_ipl_stats(self) -> None:
        async def run_test() -> None:
            player = Player(
                player_id="ipl-2026-148",
                name="Cameron Green",
                role="Batter",
                nationality="Australia",
                base_price_crore=2.0,
                matches=16,
                runs=452,
                image_url=(
                    "https://www.iplt20.com/_next/image?url=%2Fapi%2Fteam-assets"
                    "%3Fpathname%3Dplayer%252F8903%252F1873%252F65397.png"
                    "%26brand%3Dipl%26fallback%3Dfallback-player-men&w=1080&q=75"
                ),
            )
            room, admin_token = await self.manager.create_room(
                [player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.handle(room, admin, {"type": "start"})

            snapshot = self.manager.snapshot(room)

            self.assertEqual(
                snapshot["current_player"]["image_url"],
                player.image_url,
            )
            self.assertEqual(snapshot["current_player"]["matches"], 16)
            self.assertEqual(
                snapshot["players"][0]["image_url"],
                player.image_url,
            )
            if room.timer_task:
                room.timer_task.cancel()
                try:
                    await room.timer_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_bid_uses_base_then_configured_increment(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            owner_b = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.join(room, owner_b, "Owner B", "b", None)

            await self.manager.handle(room, admin, {"type": "start"})
            await self.manager.handle(room, admin, {"type": "bid"})
            self.assertEqual(room.current_bid_crore, 1.0)
            self.assertEqual(room.highest_team_id, "a")

            await self.manager.handle(room, owner_b, {"type": "bid"})
            self.assertEqual(room.current_bid_crore, 1.25)
            self.assertEqual(room.highest_team_id, "b")

            if room.timer_task:
                room.timer_task.cancel()
                try:
                    await room.timer_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_bid_is_accepted_for_any_player_role(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.handle(room, admin, {"type": "start"})

            await self.manager.handle(room, admin, {"type": "bid"})
            self.assertEqual(room.highest_team_id, "a")
            self.assertFalse(any(message["type"] == "error" for message in admin.messages))

            if room.timer_task:
                room.timer_task.cancel()
                try:
                    await room.timer_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_bid_is_rejected_when_the_squad_is_full(self) -> None:
        for index in range(15):
            self.teams["a"].players.append(
                Player(f"owned-{index}", f"Owned {index}", "Batter", "India", 0.3)
            )

        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.join(room, FakeWebSocket(), "Owner B", "b", None)
            await self.manager.handle(room, admin, {"type": "start"})

            await self.manager.handle(room, admin, {"type": "bid"})

            self.assertIsNone(room.highest_team_id)
            self.assertIn("squad is full", admin.messages[-1]["message"])
            if room.timer_task:
                room.timer_task.cancel()
                try:
                    await room.timer_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_admin_can_own_a_franchise_and_bid(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)

            await self.manager.handle(room, admin, {"type": "start"})
            await self.manager.handle(room, admin, {"type": "bid"})

            self.assertEqual(room.team_members["a"], "Host")
            self.assertEqual(room.highest_team_id, "a")
            if room.timer_task:
                room.timer_task.cancel()
                try:
                    await room.timer_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_admin_can_pause_and_resume_a_live_lot(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            owner = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.join(room, owner, "Owner", "b", None)
            await self.manager.handle(room, admin, {"type": "start"})

            await self.manager.handle(room, owner, {"type": "pause"})
            self.assertEqual(room.status, "live")
            self.assertIn("Only the auction admin", owner.messages[-1]["message"])

            await self.manager.handle(room, admin, {"type": "pause"})
            remaining = room.paused_remaining_seconds
            self.assertEqual(room.status, "paused")
            self.assertEqual(room.paused_from_status, "live")
            self.assertIsNotNone(remaining)
            self.assertIsNone(room.timer_task)

            await self.manager.handle(
                room, owner, {"type": "bid"}
            )
            self.assertEqual(room.status, "paused")
            self.assertIn(
                "Bidding is not open",
                owner.messages[-1]["message"],
            )

            await self.manager.handle(room, admin, {"type": "resume"})
            self.assertEqual(room.status, "live")
            self.assertIsNone(room.paused_from_status)
            self.assertAlmostEqual(
                room.deadline_at - time.time(),
                remaining,
                delta=0.1,
            )
            if room.timer_task:
                room.timer_task.cancel()
                try:
                    await room.timer_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_admin_can_pause_and_resume_between_lots(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            room.status = "between"

            await self.manager.handle(room, admin, {"type": "pause"})
            self.assertEqual(room.status, "paused")
            self.assertEqual(room.paused_from_status, "between")
            self.assertIsNotNone(room.paused_remaining_seconds)

            await self.manager.handle(room, admin, {"type": "resume"})
            self.assertEqual(room.status, "between")
            self.assertIsNone(room.paused_from_status)
            self.assertIsNotNone(room.advance_task)
            room.advance_task.cancel()
            try:
                await room.advance_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_test())

    def test_sold_and_unsold_lots_advance_automatically(self) -> None:
        async def run_test() -> None:
            with patch("ipl_auction.server.rooms.AUTO_ADVANCE_DELAY_SECONDS", 0.01):
                second_player = Player(
                    player_id="p2",
                    name="Next Player",
                    role="Batter",
                    nationality="India",
                    base_price_crore=1.0,
                )
                room, admin_token = await self.manager.create_room(
                    [self.player, second_player], self.teams, 10, 0.25
                )
                admin = FakeWebSocket()
                await self.manager.join(room, admin, "Host", "a", admin_token)
                await self.manager.handle(room, admin, {"type": "start"})

                async def expire_current_lot() -> None:
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

                await expire_current_lot()
                self.assertEqual(room.results[0]["status"], "unsold")
                self.assertEqual(room.status, "between")
                await room.advance_task
                self.assertEqual(room.status, "live")
                self.assertEqual(room.current_index, 1)

                await self.manager.handle(room, admin, {"type": "bid"})
                await expire_current_lot()
                self.assertEqual(room.results[1]["status"], "sold")
                self.assertEqual(room.status, "between")
                await room.advance_task
                self.assertEqual(room.status, "complete")

        asyncio.run(run_test())

    def test_admin_can_change_timer_between_lots_only(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            owner = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.join(room, owner, "Owner", "b", None)

            await self.manager.handle(
                room, admin, {"type": "set_timer", "timer_seconds": 5}
            )
            self.assertEqual(room.timer_seconds, 5)

            await self.manager.handle(
                room, owner, {"type": "set_timer", "timer_seconds": 10}
            )
            self.assertEqual(room.timer_seconds, 5)
            self.assertIn("Only the auction admin", owner.messages[-1]["message"])

            room.status = "between"
            await self.manager.handle(
                room, admin, {"type": "set_timer", "timer_seconds": 10}
            )
            self.assertEqual(room.timer_seconds, 10)

            await self.manager.handle(
                room, admin, {"type": "set_timer", "timer_seconds": 7}
            )
            self.assertEqual(room.timer_seconds, 10)
            self.assertIn("must be 5 or 10", admin.messages[-1]["message"])

        asyncio.run(run_test())

    def test_only_admin_can_start_the_auction(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 5, 0.1
            )
            owner = FakeWebSocket()
            await self.manager.join(room, owner, "Owner", "a", None)

            await self.manager.handle(room, owner, {"type": "start"})

            error_messages = [
                message for message in owner.messages if message["type"] == "error"
            ]
            self.assertEqual(error_messages[0]["message"], "Only the auction admin can do that.")
            self.assertEqual(room.status, "lobby")

        asyncio.run(run_test())

    def test_admin_cannot_start_without_a_franchise_owner(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 5, 0.1
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", None, admin_token)

            await self.manager.handle(room, admin, {"type": "start"})

            error_messages = [
                message for message in admin.messages if message["type"] == "error"
            ]
            self.assertIn("Choose a franchise", error_messages[0]["message"])
            self.assertEqual(room.status, "lobby")

        asyncio.run(run_test())

    def test_franchise_cannot_be_claimed_twice(self) -> None:
        async def run_test() -> None:
            room, _ = await self.manager.create_room([self.player], self.teams, 5, 0.1)
            await self.manager.join(room, FakeWebSocket(), "Owner A", "a", None)

            with self.assertRaisesRegex(ValueError, "already been selected"):
                await self.manager.join(room, FakeWebSocket(), "Owner B", "a", None)

        asyncio.run(run_test())

    def test_snapshot_reports_player_statistics_coverage(self) -> None:
        player_with_stats = Player(
            player_id="p2",
            name="Player with stats",
            role="Batter",
            nationality="India",
            base_price_crore=1.0,
            matches=12,
        )

        async def run_test() -> None:
            room, _ = await self.manager.create_room(
                [self.player, player_with_stats], self.teams, 5, 0.1
            )

            snapshot = self.manager.snapshot(room)

            self.assertEqual(snapshot["players_with_ipl_stats"], 1)

        asyncio.run(run_test())

    def test_snapshot_uses_only_room_results_for_player_status(self) -> None:
        async def run_test() -> None:
            room, _ = await self.manager.create_room(
                [
                    self.player,
                    Player(
                        player_id="p2",
                        name="Next Player",
                        role="Batter",
                        nationality="India",
                        base_price_crore=1.0,
                    ),
                ],
                self.teams,
                5,
                0.1,
            )
            room.results.append(
                {
                    "player_id": self.player.player_id,
                    "player_name": self.player.name,
                    "status": "sold",
                    "team_id": "a",
                    "team_name": "Team A",
                    "price_crore": 2.5,
                }
            )

            snapshot = self.manager.snapshot(room)

            self.assertEqual(snapshot["players"][0]["live_status"], "sold")
            self.assertEqual(snapshot["players"][0]["sale_price_crore"], 2.5)
            self.assertIn("style_label", snapshot["players"][0])
            self.assertNotIn("eligible_squad_roles", snapshot["players"][0])
            self.assertEqual(snapshot["players"][1]["live_status"], "upcoming")

        asyncio.run(run_test())



class SquadAndXiRoomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RoomManager()
        self.teams = {
            "a": TeamState(Team("a", "Team A"), 125.0),
            "b": TeamState(Team("b", "Team B"), 125.0),
        }

    @staticmethod
    def make_player(index, role="Batter", nationality="India", rating=60.0, base=0.3):
        return Player(
            player_id=f"p{index}",
            name=f"Player {index}",
            role=role,
            nationality=nationality,
            base_price_crore=base,
            rating=rating,
        )

    @staticmethod
    async def stop_timers(room) -> None:
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

    async def sell_next(self, room, bidder) -> None:
        await self.manager.handle(room, bidder, {"type": "bid"})
        await self.expire_current_lot(room)
        if room.advance_task:
            await room.advance_task

    def fill(self, team_id: str, count: int, **kwargs) -> None:
        start = len(self.teams[team_id].players)
        for index in range(count):
            owned = self.make_player(f"{team_id}{start + index}", **kwargs)
            self.teams[team_id].players.append(owned)

    def test_auction_ends_when_every_joined_franchise_has_fifteen(self) -> None:
        async def run_test() -> None:
            with patch("ipl_auction.server.rooms.AUTO_ADVANCE_DELAY_SECONDS", 0.01):
                self.fill("a", 14)
                pool = [self.make_player(1), self.make_player(2)]
                room, token = await self.manager.create_room(pool, self.teams, 10, 0.25)
                admin = FakeWebSocket()
                await self.manager.join(room, admin, "Host", "a", token)
                await self.manager.handle(room, admin, {"type": "start"})
                await self.sell_next(room, admin)
                self.assertEqual(room.status, "complete")
                self.assertEqual(room.complete_reason, "squads_full")
                self.assertEqual(len(self.teams["a"].players), 15)
                # franchise b never joined, so it does not hold the auction open
                self.assertEqual(self.teams["b"].players, [])
                await self.stop_timers(room)

        asyncio.run(run_test())

    def test_auction_ends_when_the_pool_is_exhausted(self) -> None:
        async def run_test() -> None:
            with patch("ipl_auction.server.rooms.AUTO_ADVANCE_DELAY_SECONDS", 0.01):
                room, token = await self.manager.create_room(
                    [self.make_player(1)], self.teams, 10, 0.25
                )
                admin = FakeWebSocket()
                await self.manager.join(room, admin, "Host", "a", token)
                await self.manager.handle(room, admin, {"type": "start"})
                await self.expire_current_lot(room)
                await room.advance_task
                self.assertEqual(room.status, "complete")
                self.assertEqual(room.complete_reason, "pool_exhausted")

        asyncio.run(run_test())

    def test_auction_ends_when_nobody_can_buy_the_remaining_players(self) -> None:
        async def run_test() -> None:
            with patch("ipl_auction.server.rooms.AUTO_ADVANCE_DELAY_SECONDS", 0.01):
                self.teams["a"].purse_remaining_crore = 4.5  # reserve 3.9 for 13 spots
                pool = [self.make_player(1, base=0.5), self.make_player(2, base=2.0)]
                room, token = await self.manager.create_room(pool, self.teams, 10, 0.25)
                admin = FakeWebSocket()
                await self.manager.join(room, admin, "Host", "a", token)
                await self.manager.handle(room, admin, {"type": "start"})
                await self.sell_next(room, admin)  # buys player 1 at 0.5 Cr
                self.assertEqual(room.status, "complete")
                self.assertEqual(room.complete_reason, "no_buyers")
                await self.stop_timers(room)

        asyncio.run(run_test())

    def test_overseas_cap_is_enforced_on_bids(self) -> None:
        async def run_test() -> None:
            self.fill("a", 6, nationality="Australia")
            overseas = self.make_player(1, nationality="England")
            room, token = await self.manager.create_room([overseas], self.teams, 10, 0.25)
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", token)
            await self.manager.join(room, FakeWebSocket(), "Owner B", "b", None)
            await self.manager.handle(room, admin, {"type": "start"})
            await self.manager.handle(room, admin, {"type": "bid"})
            self.assertIsNone(room.highest_team_id)
            self.assertIn("Overseas limit", admin.messages[-1]["message"])
            snapshot = self.manager.snapshot(room)
            own = next(team for team in snapshot["teams"] if team["team_id"] == "a")
            self.assertIn("Overseas limit", own["bid_block_reason"])
            await self.stop_timers(room)

        asyncio.run(run_test())

    def test_purse_reserve_rejects_a_bid_that_strands_the_squad(self) -> None:
        async def run_test() -> None:
            self.teams["a"].purse_remaining_crore = 4.0  # 14 spots after -> 4.2 needed
            room, token = await self.manager.create_room(
                [self.make_player(1, base=0.3)], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", token)
            await self.manager.join(room, FakeWebSocket(), "Owner B", "b", None)
            await self.manager.handle(room, admin, {"type": "start"})
            await self.manager.handle(room, admin, {"type": "bid"})
            self.assertIsNone(room.highest_team_id)
            self.assertIn("needed to fill your remaining", admin.messages[-1]["message"])
            await self.stop_timers(room)

        asyncio.run(run_test())

    async def finished_room(self):
        """A completed auction where A and B each hold 15 players."""
        self.fill("a", 15, role="Batter")
        self.fill("b", 15, role="Bowler")
        room, token = await self.manager.create_room(
            [self.make_player(1)], self.teams, 10, 0.25
        )
        admin, other = FakeWebSocket(), FakeWebSocket()
        await self.manager.join(room, admin, "Host", "a", token)
        await self.manager.join(room, other, "Owner B", "b", None)
        await self.manager.handle(room, admin, {"type": "start"})
        self.assertEqual(room.status, "complete")
        return room, admin, other

    def test_xi_lock_flow_reveals_ratings_only_after_everyone_locks(self) -> None:
        async def run_test() -> None:
            room, admin, other = await self.finished_room()
            a_ids = [p.player_id for p in self.teams["a"].players]
            b_ids = [p.player_id for p in self.teams["b"].players]

            await self.manager.handle(room, admin, {"type": "lock_xi", "player_ids": a_ids[:10]})
            self.assertIn("exactly 11", admin.messages[-1]["message"])

            await self.manager.handle(room, admin, {"type": "lock_xi", "player_ids": a_ids[:11]})
            self.assertIn("a", room.xi_locks)
            snapshot = self.manager.snapshot(room)
            self.assertFalse(snapshot["xi"]["all_locked"])
            self.assertIsNone(snapshot["xi"]["rankings"])
            private = self.manager._private_view(room, room.connections[admin])
            self.assertIn("rating", private)
            self.assertIsNone(self.manager._private_view(room, room.connections[other]))

            await self.manager.handle(room, admin, {"type": "lock_xi", "player_ids": a_ids[:11]})
            self.assertIn("already locked", admin.messages[-1]["message"])

            await self.manager.handle(room, other, {"type": "lock_xi", "player_ids": b_ids[:11]})
            snapshot = self.manager.snapshot(room)
            self.assertTrue(snapshot["xi"]["all_locked"])
            self.assertEqual(len(snapshot["xi"]["rankings"]), 2)
            self.assertEqual(snapshot["xi"]["rankings"][0]["rank"], 1)

        asyncio.run(run_test())

    def test_xi_lock_rejects_more_than_four_overseas(self) -> None:
        async def run_test() -> None:
            self.fill("a", 5, nationality="Australia")
            self.fill("a", 10)
            self.fill("b", 15)
            room, token = await self.manager.create_room(
                [self.make_player(1)], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", token)
            await self.manager.join(room, FakeWebSocket(), "Owner B", "b", None)
            await self.manager.handle(room, admin, {"type": "start"})
            ids = [p.player_id for p in self.teams["a"].players]
            await self.manager.handle(room, admin, {"type": "lock_xi", "player_ids": ids[:11]})
            self.assertIn("at most 4 overseas", admin.messages[-1]["message"])
            self.assertNotIn("a", room.xi_locks)

        asyncio.run(run_test())

    def test_only_admin_can_auto_lock_and_it_locks_everyone(self) -> None:
        async def run_test() -> None:
            room, admin, other = await self.finished_room()
            await self.manager.handle(room, other, {"type": "auto_lock"})
            self.assertIn("Only the auction admin", other.messages[-1]["message"])
            await self.manager.handle(room, admin, {"type": "auto_lock"})
            self.assertEqual(set(room.xi_locks), {"a", "b"})
            self.assertTrue(self.manager.snapshot(room)["xi"]["all_locked"])

        asyncio.run(run_test())

    def test_lock_is_not_available_during_the_auction(self) -> None:
        async def run_test() -> None:
            room, token = await self.manager.create_room(
                [self.make_player(1)], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", token)
            await self.manager.handle(room, admin, {"type": "start"})
            await self.manager.handle(room, admin, {"type": "lock_xi", "player_ids": []})
            self.assertIn("opens when the auction is complete", admin.messages[-1]["message"])
            await self.stop_timers(room)

        asyncio.run(run_test())

    def test_hidden_ratings_never_reach_the_browser_payloads(self) -> None:
        async def run_test() -> None:
            room, admin, other = await self.finished_room()
            ids = [p.player_id for p in self.teams["a"].players][:11]
            await self.manager.handle(room, admin, {"type": "lock_xi", "player_ids": ids})

            def has_player_rating(value) -> bool:
                if isinstance(value, dict):
                    if "player_id" in value and (
                        "rating" in value or "player_rating" in value
                    ):
                        return True
                    return any(has_player_rating(item) for item in value.values())
                if isinstance(value, list):
                    return any(has_player_rating(item) for item in value)
                return False

            for socket in (admin, other):
                state = [m for m in socket.messages if m["type"] == "state"][-1]["state"]
                self.assertFalse(has_player_rating(state["players"]))
                self.assertFalse(has_player_rating(state["teams"]))
                self.assertFalse(has_player_rating(state["current_player"]))
            other_state = [m for m in other.messages if m["type"] == "state"][-1]["state"]
            self.assertNotIn("my_xi", other_state)
            self.assertIsNone(other_state["xi"]["rankings"])
            admin_state = [m for m in admin.messages if m["type"] == "state"][-1]["state"]
            self.assertIn("rating", admin_state["my_xi"])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
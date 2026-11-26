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
            self.assertEqual(room.highest_squad_role, "opener")

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

    def test_bid_automatically_assigns_an_eligible_squad_role(self) -> None:
        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.handle(room, admin, {"type": "start"})

            await self.manager.handle(room, admin, {"type": "bid"})
            self.assertEqual(room.highest_team_id, "a")
            self.assertEqual(room.highest_squad_role, "opener")
            self.assertFalse(any(message["type"] == "error" for message in admin.messages))

            if room.timer_task:
                room.timer_task.cancel()
                try:
                    await room.timer_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_bid_is_rejected_when_no_compatible_lineup_role_is_open(self) -> None:
        self.teams["a"].player_roles.update(
            {
                "opener-1": "opener",
                "opener-2": "opener",
                "anchor": "anchor",
                "middle": "middle_order",
                "finisher": "finisher",
            }
        )

        async def run_test() -> None:
            room, admin_token = await self.manager.create_room(
                [self.player], self.teams, 10, 0.25
            )
            admin = FakeWebSocket()
            await self.manager.join(room, admin, "Host", "a", admin_token)
            await self.manager.handle(room, admin, {"type": "start"})

            await self.manager.handle(room, admin, {"type": "bid"})

            self.assertIsNone(room.highest_team_id)
            self.assertIn(
                "no open lineup role",
                admin.messages[-1]["message"],
            )
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
            self.assertEqual(room.highest_squad_role, "opener")
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
                    "squad_role": "opener",
                    "squad_role_label": "Opener",
                }
            )

            snapshot = self.manager.snapshot(room)

            self.assertEqual(snapshot["players"][0]["live_status"], "sold")
            self.assertEqual(snapshot["players"][0]["sale_price_crore"], 2.5)
            self.assertEqual(
                snapshot["players"][0]["eligible_squad_roles"],
                [
                    {"key": "opener", "label": "Opener"},
                    {"key": "anchor", "label": "Anchor"},
                    {"key": "middle_order", "label": "Middle order"},
                    {"key": "finisher", "label": "Finisher"},
                ],
            )
            self.assertEqual(snapshot["players"][1]["live_status"], "upcoming")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()

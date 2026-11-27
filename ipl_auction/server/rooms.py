import asyncio
import hmac
import math
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ipl_auction.match.constants import DEFAULT_MATCH_SPEED, MATCH_SPEED_DELAYS
from ipl_auction.match.engine import T20MatchSimulator
from ipl_auction.players.models import Player
from ipl_auction.players.sets import SET_BREAK_SECONDS, AuctionPlan
from ipl_auction.services.auction_service import record_sale, record_unsold
from ipl_auction.teams.lineup import (
    MAX_OVERSEAS_SQUAD,
    MAX_OVERSEAS_XI,
    SQUAD_SIZE,
    XI_SIZE,
    auto_pick_xi,
    bid_block_reason,
    can_still_buy,
    franchise_rating,
    overseas_count,
    squad_is_full,
    style_label,
    validate_xi,
    xi_capacity,
)
from ipl_auction.teams.models import TeamState
from ipl_auction.tournament.constants import STATUS_COMPLETED, STATUS_LIVE, STATUS_UPCOMING
from ipl_auction.tournament.manager import (
    Fixture,
    LiveMatchSession,
    TournamentState,
    build_tournament,
)

AUTO_ADVANCE_DELAY_SECONDS = 3.2


@dataclass
class Connection:
    name: str
    team_id: str | None
    is_admin: bool


@dataclass
class AuctionRoom:
    code: str
    admin_token: str
    timer_seconds: int
    bid_increment_crore: float
    players: list[Player]
    team_states: dict[str, TeamState]
    team_names: dict[str, str]
    connections: dict[WebSocket, Connection] = field(default_factory=dict)
    team_members: dict[str, str] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    status: str = "lobby"
    current_index: int = -1
    current_bid_crore: float | None = None
    highest_team_id: str | None = None
    complete_reason: str | None = None
    xi_locks: dict[str, list[str]] = field(default_factory=dict)
    franchise_ratings: dict[str, dict[str, Any]] = field(default_factory=dict)
    tournament: TournamentState | None = None
    tournament_seed: int = field(default_factory=lambda: secrets.randbelow(1_000_000) + 1)
    # Player sets: player_id -> set number for the players taking part, and the
    # player_ids that sat out of each set (kept so a later feature can rotate them).
    player_sets: dict[str, int] = field(default_factory=dict)
    sat_out: dict[int, list[str]] = field(default_factory=dict)
    sets_fallback: bool = False
    deadline_at: float | None = None
    advance_deadline_at: float | None = None
    paused_from_status: str | None = None
    paused_remaining_seconds: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer_task: asyncio.Task[None] | None = None
    advance_task: asyncio.Task[None] | None = None

    @property
    def current_player(self) -> Player | None:
        if 0 <= self.current_index < len(self.players):
            return self.players[self.current_index]
        return None


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, AuctionRoom] = {}
        self._lock = asyncio.Lock()

    async def create_room(
        self,
        players: list[Player],
        team_states: dict[str, TeamState],
        timer_seconds: int,
        bid_increment_crore: float,
        plan: AuctionPlan | None = None,
    ) -> tuple[AuctionRoom, str]:
        if plan is not None:
            players = plan.players
        if not players:
            raise ValueError("The player pool is empty.")
        if timer_seconds not in (5, 10):
            raise ValueError("The bid timer must be 5 or 10 seconds.")
        if not math.isfinite(bid_increment_crore) or bid_increment_crore <= 0:
            raise ValueError("The bid increment must be greater than zero.")

        async with self._lock:
            code = self._new_code()
            admin_token = secrets.token_urlsafe(32)
            room = AuctionRoom(
                code=code,
                admin_token=admin_token,
                timer_seconds=timer_seconds,
                bid_increment_crore=bid_increment_crore,
                players=players,
                team_states=team_states,
                team_names={key: state.team.name for key, state in team_states.items()},
                player_sets=dict(plan.player_sets) if plan else {},
                sat_out={n: list(ids) for n, ids in plan.sat_out.items()} if plan else {},
                sets_fallback=plan.used_fallback if plan else False,
            )
            self.rooms[code] = room
            return room, admin_token

    def get_room(self, code: str) -> AuctionRoom | None:
        return self.rooms.get(code.upper())

    async def join(
        self,
        room: AuctionRoom,
        websocket: WebSocket,
        name: str,
        team_id: str | None,
        admin_token: str | None,
    ) -> Connection:
        name = name.strip()
        if not name or len(name) > 32:
            raise ValueError("Enter a display name between 1 and 32 characters.")
        if team_id is not None and not isinstance(team_id, str):
            raise ValueError("Select a valid franchise.")
        if admin_token is not None and not isinstance(admin_token, str):
            raise ValueError("The admin key is not valid.")

        is_admin = bool(
            admin_token
            and hmac.compare_digest(admin_token, room.admin_token)
        )
        if admin_token and not is_admin:
            raise ValueError("The admin key is not valid.")

        async with room.lock:
            if room.status != "lobby" and not is_admin and team_id not in room.team_members:
                raise ValueError("This auction has already started.")
            if team_id is not None:
                if team_id not in room.team_states:
                    raise ValueError("Select a valid franchise.")
                current_owner = room.team_members.get(team_id)
                if current_owner and current_owner.casefold() != name.casefold():
                    raise ValueError("That franchise has already been selected.")
                room.team_members[team_id] = name

            connection = Connection(name=name, team_id=team_id, is_admin=is_admin)
            room.connections[websocket] = connection
            return connection

    async def leave(self, room: AuctionRoom, websocket: WebSocket) -> None:
        async with room.lock:
            room.connections.pop(websocket, None)
            tournament = room.tournament
        if tournament is not None:
            for session in list(tournament.sessions.values()):
                async with session.lock:
                    session.participants.discard(websocket)
                    session.spectators.discard(websocket)

    async def handle(
        self, room: AuctionRoom, websocket: WebSocket, message: dict[str, Any]
    ) -> None:
        connection = room.connections.get(websocket)
        if connection is None:
            await self._send_error(websocket, "Join the room before sending actions.")
            return

        action = message.get("type")

        # Tournament / Live Match actions
        if action in {
            "start_match",
            "join_match",
            "join_spectator",
            "leave_match",
            "set_match_speed",
            "get_match_scorecard",
        }:
            try:
                await self._handle_tournament_action(room, websocket, connection, message)
            except ValueError as error:
                await self._send_error(websocket, str(error))
            return

        try:
            async with room.lock:
                if action == "start":
                    self._require_admin(connection)
                    if room.status != "lobby":
                        raise ValueError("The auction has already started.")
                    if connection.team_id is None:
                        raise ValueError(
                            "Choose a franchise before starting so you can bid as the host."
                        )
                    if not room.team_members:
                        raise ValueError("At least one franchise must join before starting.")
                    self._open_next_player(room)
                elif action == "set_timer":
                    self._require_admin(connection)
                    self._set_timer(room, message)
                elif action == "pause":
                    self._require_admin(connection)
                    self._pause(room)
                elif action == "resume":
                    self._require_admin(connection)
                    self._resume(room)
                elif action == "bid":
                    self._place_bid(room, connection)
                elif action == "lock_xi":
                    self._lock_xi(room, connection, message)
                    self._maybe_init_tournament(room)
                elif action == "auto_lock":
                    self._require_admin(connection)
                    self._auto_lock(room)
                    self._maybe_init_tournament(room)
                else:
                    raise ValueError("Unknown auction action.")
        except ValueError as error:
            await self._send_error(websocket, str(error))
            return

        await self.broadcast(room)

    def _maybe_init_tournament(self, room: AuctionRoom) -> None:
        if room.tournament is not None:
            return
        active_ids = [t for t in room.team_members if t in room.team_states]
        if (
            room.status == "complete"
            and len(active_ids) >= 2
            and all(t in room.xi_locks for t in active_ids)
        ):
            room.tournament = build_tournament(
                room_code=room.code,
                participating_team_ids=active_ids,
                team_states=room.team_states,
                xi_locks=room.xi_locks,
                franchise_ratings=room.franchise_ratings,
                seed=room.tournament_seed,
            )

    async def _handle_tournament_action(
        self,
        room: AuctionRoom,
        websocket: WebSocket,
        connection: Connection,
        message: dict[str, Any],
    ) -> None:
        tournament = room.tournament
        if tournament is None:
            raise ValueError("The tournament has not started yet.")

        action = message.get("type")
        match_id = str(message.get("match_id") or "").strip()

        if action == "leave_match":
            for session in list(tournament.sessions.values()):
                if not match_id or session.match_id == match_id:
                    async with session.lock:
                        session.participants.discard(websocket)
                        session.spectators.discard(websocket)
            return

        if not match_id:
            raise ValueError("Select a valid fixture.")

        if action == "get_match_scorecard":
            result_data = tournament.completed_results.get(match_id)
            if result_data is None:
                session = tournament.sessions.get(match_id)
                if session is not None:
                    async with session.lock:
                        snap = session.simulator.snapshot()
                        await websocket.send_json(
                            {"type": "match_snapshot", "match": snap, "role": "spectator"}
                        )
                        return
                raise ValueError("Scorecard is not available for this match yet.")
            await websocket.send_json(
                {
                    "type": "match_scorecard",
                    "match_id": match_id,
                    "result": result_data,
                }
            )
            return

        if action == "start_match":
            await self._start_or_join_match(room, tournament, websocket, connection, match_id)
            return

        if action == "join_match":
            session = tournament.sessions.get(match_id)
            if session is None:
                fixture = tournament.get_fixture(match_id)
                if fixture is None:
                    raise ValueError("Match not found.")
                if fixture.status == STATUS_COMPLETED and match_id in tournament.completed_results:
                    await websocket.send_json(
                        {
                            "type": "match_completed",
                            "match_id": match_id,
                            "result": tournament.completed_results[match_id],
                        }
                    )
                    return
                raise ValueError("This match has not started yet.")

            fixture = session.fixture
            if not fixture.involves_team(connection.team_id):
                raise ValueError("Only the two assigned franchises can join as match players.")

            await self._detach_from_other_sessions(tournament, websocket, keep_match_id=match_id)
            async with session.lock:
                session.spectators.discard(websocket)
                session.participants.add(websocket)
                snap = session.simulator.snapshot()
                snap["speed"] = session.speed
                await websocket.send_json(
                    {
                        "type": "match_snapshot",
                        "match": snap,
                        "role": "participant",
                    }
                )
            return

        if action == "join_spectator":
            session = tournament.sessions.get(match_id)
            if session is None:
                fixture = tournament.get_fixture(match_id)
                if fixture is None:
                    raise ValueError("Match not found.")
                if fixture.status == STATUS_COMPLETED and match_id in tournament.completed_results:
                    await websocket.send_json(
                        {
                            "type": "match_completed",
                            "match_id": match_id,
                            "result": tournament.completed_results[match_id],
                        }
                    )
                    return
                raise ValueError("This match is not live right now.")

            await self._detach_from_other_sessions(tournament, websocket, keep_match_id=match_id)
            async with session.lock:
                if session.fixture.involves_team(connection.team_id):
                    session.participants.add(websocket)
                    role = "participant"
                else:
                    session.spectators.add(websocket)
                    role = "spectator"
                snap = session.simulator.snapshot()
                snap["speed"] = session.speed
                await websocket.send_json(
                    {
                        "type": "match_snapshot",
                        "match": snap,
                        "role": role,
                    }
                )
            return

        if action == "set_match_speed":
            session = tournament.sessions.get(match_id)
            if session is None:
                raise ValueError("Match is not currently live.")
            if not session.fixture.involves_team(connection.team_id):
                raise ValueError("Only the two competing franchises can change match speed.")

            speed = str(message.get("speed") or "").strip().casefold()
            if speed not in MATCH_SPEED_DELAYS:
                raise ValueError("Invalid match speed. Choose normal, fast, or skip.")

            if speed == "skip":
                await self._skip_match_to_result(room, tournament, session)
            else:
                async with session.lock:
                    session.speed = speed
                    snap = session.simulator.snapshot()
                    snap["speed"] = session.speed
                    watchers = session.all_watchers()
                for watcher in watchers:
                    try:
                        await watcher.send_json(
                            {
                                "type": "score_update",
                                "match_id": session.match_id,
                                "match": snap,
                            }
                        )
                    except (WebSocketDisconnect, RuntimeError):
                        pass
            return

    @staticmethod
    async def _detach_from_other_sessions(
        tournament: TournamentState, websocket: WebSocket, keep_match_id: str
    ) -> None:
        for session in list(tournament.sessions.values()):
            if session.match_id == keep_match_id:
                continue
            async with session.lock:
                session.participants.discard(websocket)
                session.spectators.discard(websocket)

    async def _start_or_join_match(
        self,
        room: AuctionRoom,
        tournament: TournamentState,
        websocket: WebSocket,
        connection: Connection,
        match_id: str,
    ) -> None:
        await self._detach_from_other_sessions(tournament, websocket, keep_match_id=match_id)

        created_new = False
        async with tournament.lock:
            fixture = tournament.get_fixture(match_id)
            if fixture is None:
                raise ValueError("Fixture not found.")
            if not fixture.is_ready:
                raise ValueError("Both teams for this playoff match are not determined yet.")
            if not fixture.involves_team(connection.team_id):
                raise ValueError("Only the two assigned franchises can start this match.")
            if fixture.status == STATUS_COMPLETED:
                raise ValueError("This match has already finished.")

            session = tournament.sessions.get(match_id)
            if session is None:
                team_a_id = fixture.team_a_id
                team_b_id = fixture.team_b_id
                assert team_a_id is not None and team_b_id is not None

                team_a_state = room.team_states[team_a_id]
                team_b_state = room.team_states[team_b_id]
                a_xi_ids = set(room.xi_locks[team_a_id])
                b_xi_ids = set(room.xi_locks[team_b_id])
                team_a_xi = [p for p in team_a_state.players if p.player_id in a_xi_ids]
                team_b_xi = [p for p in team_b_state.players if p.player_id in b_xi_ids]

                simulator = T20MatchSimulator(
                    match_id=match_id,
                    team_a_id=team_a_id,
                    team_a_name=team_a_state.team.name,
                    team_a_xi=team_a_xi,
                    team_b_id=team_b_id,
                    team_b_name=team_b_state.team.name,
                    team_b_xi=team_b_xi,
                    seed=fixture.seed,
                )
                session = LiveMatchSession(
                    match_id=match_id,
                    fixture=fixture,
                    simulator=simulator,
                    speed=DEFAULT_MATCH_SPEED,
                )
                fixture.status = STATUS_LIVE
                tournament.sessions[match_id] = session
                created_new = True

        async with session.lock:
            session.spectators.discard(websocket)
            session.participants.add(websocket)
            snap = session.simulator.snapshot()
            snap["speed"] = session.speed
            await websocket.send_json(
                {
                    "type": "match_started" if created_new else "match_snapshot",
                    "match": snap,
                    "role": "participant",
                }
            )

        if created_new:
            session.task = asyncio.create_task(
                self._run_match_loop(room, tournament, session)
            )
            await self.broadcast(room)

    async def _skip_match_to_result(
        self,
        room: AuctionRoom,
        tournament: TournamentState,
        session: LiveMatchSession,
    ) -> None:
        if session.task and not session.task.done():
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
            session.task = None

        async with session.lock:
            if not session.simulator.completed:
                session.simulator.run_to_completion()
            result = session.simulator.result
            assert result is not None
            deliveries = (
                [ev for inn in result.innings for ev in inn.deliveries]
                + [ev for inn in result.super_over_innings for ev in inn.deliveries]
            )
            snap = session.simulator.snapshot()
            snap["speed"] = "skip"
            watchers = session.all_watchers()

        await self._finalize_live_session(
            room=room,
            tournament=tournament,
            session=session,
            result=result,
            deliveries=deliveries,
            snap=snap,
            watchers=watchers,
        )

    async def _run_match_loop(
        self,
        room: AuctionRoom,
        tournament: TournamentState,
        session: LiveMatchSession,
    ) -> None:
        try:
            while True:
                async with session.lock:
                    delay = MATCH_SPEED_DELAYS.get(session.speed, 1.2)
                if delay > 0:
                    await asyncio.sleep(delay)

                async with session.lock:
                    if session.simulator.completed:
                        break
                    ball_event, transition = session.simulator.step_ball()
                    snap = session.simulator.snapshot()
                    snap["speed"] = session.speed
                    watchers = session.all_watchers()
                    is_done = session.simulator.completed
                    result = session.simulator.result
                    deliveries = (
                        (
                            [ev for inn in result.innings for ev in inn.deliveries]
                            + [ev for inn in result.super_over_innings for ev in inn.deliveries]
                        )
                        if is_done and result
                        else []
                    )

                if ball_event is not None:
                    payload = {
                        "type": "ball_update",
                        "match_id": session.match_id,
                        "ball": ball_event.to_public_dict(),
                        "match": snap,
                    }
                    for watcher in watchers:
                        try:
                            await watcher.send_json(payload)
                        except (WebSocketDisconnect, RuntimeError):
                            pass

                if transition.get("super_over_started"):
                    so_payload = {
                        "type": "super_over_started",
                        "match_id": session.match_id,
                        "super_over_round": transition.get("super_over_round", 1),
                        "match": snap,
                    }
                    for watcher in watchers:
                        try:
                            await watcher.send_json(so_payload)
                        except (WebSocketDisconnect, RuntimeError):
                            pass
                elif transition.get("innings_completed") and not is_done:
                    inn_payload = {
                        "type": "innings_completed",
                        "match_id": session.match_id,
                        "match": snap,
                    }
                    for watcher in watchers:
                        try:
                            await watcher.send_json(inn_payload)
                        except (WebSocketDisconnect, RuntimeError):
                            pass

                if is_done and result is not None:
                    await self._finalize_live_session(
                        room=room,
                        tournament=tournament,
                        session=session,
                        result=result,
                        deliveries=deliveries,
                        snap=snap,
                        watchers=watchers,
                    )
                    break
        except asyncio.CancelledError:
            return

    async def _finalize_live_session(
        self,
        room: AuctionRoom,
        tournament: TournamentState,
        session: LiveMatchSession,
        result: Any,
        deliveries: list[Any],
        snap: dict[str, Any],
        watchers: list[WebSocket],
    ) -> None:
        async with tournament.lock:
            if session.fixture.status == STATUS_COMPLETED:
                return
            updates = tournament.record_match_completion(
                fixture=session.fixture,
                result=result,
                deliveries=deliveries,
            )
            tournament.sessions.pop(session.match_id, None)

        completed_payload = {
            "type": "match_completed",
            "match_id": session.match_id,
            "match": snap,
            "result": result.to_public_dict(),
        }
        for watcher in watchers:
            try:
                await watcher.send_json(completed_payload)
            except (WebSocketDisconnect, RuntimeError):
                pass

        if updates.get("standings_updated"):
            await self._broadcast_room_event(
                room,
                {
                    "type": "tournament_standings_updated",
                    "standings": tournament.sorted_standings(),
                },
            )
        if updates.get("bracket_updated"):
            await self._broadcast_room_event(
                room,
                {
                    "type": "playoff_bracket_updated",
                    "tournament": self._tournament_snapshot(room, tournament),
                },
            )

        await self.broadcast(room)

    async def _broadcast_room_event(self, room: AuctionRoom, payload: dict[str, Any]) -> None:
        async with room.lock:
            sockets = list(room.connections.keys())
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                pass

    async def broadcast(self, room: AuctionRoom) -> None:
        async with room.lock:
            state = self.snapshot(room)
            recipients = [
                (websocket, self._private_view(room, connection))
                for websocket, connection in room.connections.items()
            ]

        disconnected = []
        for websocket, private in recipients:
            payload = {**state, "my_xi": private} if private else state
            try:
                await websocket.send_json({"type": "state", "state": payload})
            except (WebSocketDisconnect, RuntimeError):
                disconnected.append(websocket)

        if disconnected:
            async with room.lock:
                for websocket in disconnected:
                    room.connections.pop(websocket, None)

    def _tournament_snapshot(
        self, room: AuctionRoom, tournament: TournamentState
    ) -> dict[str, Any]:
        def team_meta(team_id: str | None, fallback_label: str = "TBD") -> dict[str, Any] | None:
            if not team_id or team_id not in room.team_states:
                return None
            t = room.team_states[team_id].team
            return {
                "team_id": t.team_id,
                "name": t.name,
                "short_name": t.short_name,
                "logo_url": t.logo_url,
            }

        fixtures_data = []
        live_count = 0
        completed_count = 0
        for fx in tournament.fixtures:
            if fx.status == STATUS_LIVE:
                live_count += 1
            elif fx.status == STATUS_COMPLETED:
                completed_count += 1

            live_score = None
            session = tournament.sessions.get(fx.match_id)
            if session is not None:
                sim = session.simulator
                inn = sim.current_innings
                live_score = {
                    "batting_team_id": inn.batting_team_id,
                    "batting_team_name": inn.batting_team_name,
                    "runs": inn.runs,
                    "wickets": inn.wickets,
                    "overs": inn.overs_display,
                    "target": inn.target,
                    "speed": session.speed,
                }

            fixtures_data.append(
                {
                    "match_id": fx.match_id,
                    "fixture_number": fx.fixture_number,
                    "stage": fx.stage,
                    "is_playoff": fx.is_playoff,
                    "status": fx.status,
                    "is_ready": fx.is_ready,
                    "team_a": team_meta(fx.team_a_id),
                    "team_b": team_meta(fx.team_b_id),
                    "tbd_a_label": fx.tbd_a_label,
                    "tbd_b_label": fx.tbd_b_label,
                    "winner_team_id": fx.winner_team_id,
                    "loser_team_id": fx.loser_team_id,
                    "result_summary": fx.result_summary,
                    "live_score": live_score,
                }
            )

        return {
            "active": True,
            "format_type": tournament.format_type,
            "total_teams": len(tournament.rankings),
            "current_stage": tournament.current_stage_label(),
            "rankings": list(tournament.rankings),
            "fixtures": fixtures_data,
            "standings": tournament.sorted_standings(),
            "counts": {
                "total": len(tournament.fixtures),
                "upcoming": len(tournament.fixtures) - live_count - completed_count,
                "live": live_count,
                "completed": completed_count,
            },
            "champion": team_meta(tournament.champion_team_id),
            "runner_up": team_meta(tournament.runner_up_team_id),
            "final_result": tournament.final_result,
        }

    def snapshot(self, room: AuctionRoom) -> dict[str, Any]:
        player = room.current_player
        results_by_player = {result["player_id"]: result for result in room.results}
        players = []
        for auction_player in room.players:
            result = results_by_player.get(auction_player.player_id)
            live_status = result["status"] if result else "upcoming"
            if (
                result is None
                and room.status == "live"
                and player is not None
                and auction_player.player_id == player.player_id
            ):
                live_status = "live"
            elif (
                result is None
                and room.status == "paused"
                and room.paused_from_status == "live"
                and player is not None
                and auction_player.player_id == player.player_id
            ):
                live_status = "live"
            players.append(
                {
                    **self._player_data(auction_player),
                    "set_number": room.player_sets.get(auction_player.player_id),
                    "live_status": live_status,
                    "sale_price_crore": result["price_crore"] if result else None,
                    "sold_team_id": result["team_id"] if result else None,
                    "sold_team_name": result["team_name"] if result else None,
                }
            )

        teams = []
        for team_id, team_state in room.team_states.items():
            roster = []
            for bought_player in team_state.players:
                sale = results_by_player.get(bought_player.player_id, {})
                roster.append(
                    {
                        "player_id": bought_player.player_id,
                        "name": bought_player.name,
                        "role": bought_player.role,
                        "nationality": bought_player.nationality,
                        "overseas": bought_player.is_overseas,
                        "style_label": style_label(bought_player),
                        "image_url": bought_player.image_url,
                        "elite": bought_player.elite,
                        "base_price_crore": bought_player.base_price_crore,
                        "price_crore": sale.get("price_crore"),
                    }
                )
            teams.append(
                {
                    "team_id": team_id,
                    "name": team_state.team.name,
                    "short_name": team_state.team.short_name,
                    "logo_url": team_state.team.logo_url,
                    "purse_remaining_crore": team_state.purse_remaining_crore,
                    "players_bought": len(team_state.players),
                    "squad_size": len(team_state.players),
                    "squad_capacity": SQUAD_SIZE,
                    "overseas_count": overseas_count(team_state),
                    "captain": room.team_members.get(team_id),
                    "xi_locked": team_id in room.xi_locks,
                    "bid_block_reason": self._bid_block_reason(
                        room, team_id, team_state
                    ),
                    "roster": roster,
                }
            )

        set_break = self._set_break_info(room)
        return {
            "room_code": room.code,
            "status": room.status,
            "timer_seconds": room.timer_seconds,
            "bid_increment_crore": room.bid_increment_crore,
            "demo_data": all(player.player_id.startswith("demo-") for player in room.players),
            "current_index": room.current_index,
            "player_count": len(room.players),
            "players_with_ipl_stats": sum(
                player.has_ipl_stats for player in room.players
            ),
            "players": players,
            "current_player": (
                {
                    **self._player_data(player),
                    "set_number": room.player_sets.get(player.player_id),
                }
                if player
                else None
            ),
            "sets": self._set_summary(room),
            "set_break": set_break,
            "current_bid_crore": room.current_bid_crore,
            "highest_team_id": room.highest_team_id,
            "squad_size": SQUAD_SIZE,
            "xi_size": XI_SIZE,
            "max_overseas_squad": MAX_OVERSEAS_SQUAD,
            "max_overseas_xi": MAX_OVERSEAS_XI,
            "complete_reason": room.complete_reason,
            "xi": self._xi_summary(room),
            "tournament": (
                self._tournament_snapshot(room, room.tournament)
                if room.tournament is not None
                else None
            ),
            "highest_team_name": (
                room.team_names.get(room.highest_team_id)
                if room.highest_team_id
                else None
            ),
            "deadline_at": room.deadline_at,
            "advance_deadline_at": room.advance_deadline_at,
            "advance_delay_seconds": AUTO_ADVANCE_DELAY_SECONDS
            + (SET_BREAK_SECONDS if set_break else 0),
            "paused_from_status": room.paused_from_status,
            "paused_remaining_seconds": room.paused_remaining_seconds,
            "teams": teams,
            "results": list(room.results),
            "members": [
                {"name": member.name, "team_id": member.team_id, "is_admin": member.is_admin}
                for member in room.connections.values()
            ],
        }

    def _open_next_player(self, room: AuctionRoom) -> None:
        next_index = room.current_index + 1
        room.advance_deadline_at = None
        room.advance_task = None
        room.paused_from_status = None
        room.paused_remaining_seconds = None
        reason = self._completion_reason(room, next_index)
        if reason:
            room.status = "complete"
            room.complete_reason = reason
            room.current_bid_crore = None
            room.highest_team_id = None
            room.deadline_at = None
            room.timer_task = None
            return

        room.current_index = next_index
        room.status = "live"
        room.current_bid_crore = None
        room.highest_team_id = None
        deadline_at = time.time() + room.timer_seconds
        room.deadline_at = deadline_at
        room.timer_task = asyncio.create_task(
            self._expire_player(room, deadline_at)
        )

    @staticmethod
    def _set_timer(room: AuctionRoom, message: dict[str, Any]) -> None:
        if room.status not in {"lobby", "between"}:
            raise ValueError("Change the lot timer before the auction or between players.")
        timer_seconds = message.get("timer_seconds")
        if (
            not isinstance(timer_seconds, int)
            or isinstance(timer_seconds, bool)
            or timer_seconds not in (5, 10)
        ):
            raise ValueError("The lot timer must be 5 or 10 seconds.")
        room.timer_seconds = timer_seconds

    @staticmethod
    def _pause(room: AuctionRoom) -> None:
        if room.status not in {"live", "between"}:
            raise ValueError("The auction can only be paused during or between lots.")

        if room.status == "live":
            if room.deadline_at is None:
                raise ValueError("The active lot has no timer to pause.")
            remaining_seconds = max(
                0.0, room.deadline_at - time.time()
            )
            room.paused_remaining_seconds = remaining_seconds
            if room.timer_task:
                room.timer_task.cancel()
                room.timer_task = None
        else:
            remaining_seconds = (
                max(0.0, room.advance_deadline_at - time.time())
                if room.advance_deadline_at is not None
                else AUTO_ADVANCE_DELAY_SECONDS
            )
            room.paused_remaining_seconds = remaining_seconds
            if room.advance_task:
                room.advance_task.cancel()
                room.advance_task = None
            room.advance_deadline_at = None
        room.paused_from_status = room.status
        room.status = "paused"

    def _resume(self, room: AuctionRoom) -> None:
        if room.status != "paused" or room.paused_from_status not in {"live", "between"}:
            raise ValueError("The auction is not paused.")

        resume_status = room.paused_from_status
        if resume_status == "live":
            if room.paused_remaining_seconds is None:
                raise ValueError("The paused lot's remaining time is unavailable.")
            room.status = "live"
            deadline_at = time.time() + room.paused_remaining_seconds
            room.deadline_at = deadline_at
            room.timer_task = asyncio.create_task(
                self._expire_player(room, deadline_at)
            )
        else:
            room.status = "between"
            room.deadline_at = None
            remaining_seconds = room.paused_remaining_seconds
            advance_deadline_at = time.time() + (
                remaining_seconds
                if remaining_seconds is not None
                else AUTO_ADVANCE_DELAY_SECONDS
            )
            room.advance_deadline_at = advance_deadline_at
            room.advance_task = asyncio.create_task(
                self._advance_after_settlement(room, advance_deadline_at)
            )

        room.paused_from_status = None
        room.paused_remaining_seconds = None

    def _place_bid(
        self,
        room: AuctionRoom,
        connection: Connection,
    ) -> None:
        if room.status != "live" or room.current_player is None:
            raise ValueError("Bidding is not open right now.")
        if connection.team_id is None:
            raise ValueError("Join a franchise to place bids.")
        if room.highest_team_id == connection.team_id:
            raise ValueError("Your franchise is already the highest bidder.")
        if room.deadline_at is None or time.time() >= room.deadline_at:
            raise ValueError("The bidding timer has expired.")

        player = room.current_player
        next_bid = (
            player.base_price_crore
            if room.current_bid_crore is None
            else round(room.current_bid_crore + room.bid_increment_crore, 2)
        )
        team_state = room.team_states[connection.team_id]
        reason = bid_block_reason(player, team_state, next_bid)
        if reason:
            raise ValueError(reason)

        room.current_bid_crore = next_bid
        room.highest_team_id = connection.team_id
        deadline_at = time.time() + room.timer_seconds
        room.deadline_at = deadline_at
        if room.timer_task:
            room.timer_task.cancel()
        room.timer_task = asyncio.create_task(
            self._expire_player(room, deadline_at)
        )

    async def _expire_player(self, room: AuctionRoom, deadline_at: float) -> None:
        try:
            await asyncio.sleep(max(0, deadline_at - time.time()))
        except asyncio.CancelledError:
            return

        async with room.lock:
            if room.status != "live" or room.deadline_at != deadline_at:
                return

            player = room.current_player
            if player is None:
                return
            if room.highest_team_id is None or room.current_bid_crore is None:
                result = record_unsold(player)
            else:
                try:
                    result = record_sale(
                        player,
                        room.team_states[room.highest_team_id],
                        room.current_bid_crore,
                    )
                except ValueError:
                    # Safety net: never leave the room stuck on a bad sale.
                    result = record_unsold(player)

            sold_team = room.team_states.get(result.team_id) if result.team_id else None
            room.results.append(
                {
                    "player_id": player.player_id,
                    "player_name": player.name,
                    "status": "unsold" if result.team_id is None else "sold",
                    "team_id": result.team_id,
                    "team_name": room.team_names.get(result.team_id),
                    "price_crore": result.sold_price_crore,
                    "squad_count": len(sold_team.players) if sold_team else None,
                    "squad_capacity": SQUAD_SIZE,
                }
            )
            room.status = "between"
            room.deadline_at = None
            room.timer_task = None
            advance_deadline_at = (
                time.time() + AUTO_ADVANCE_DELAY_SECONDS + self._set_break_seconds(room)
            )
            room.advance_deadline_at = advance_deadline_at
            room.advance_task = asyncio.create_task(
                self._advance_after_settlement(room, advance_deadline_at)
            )

        await self.broadcast(room)

    async def _advance_after_settlement(
        self, room: AuctionRoom, deadline_at: float
    ) -> None:
        try:
            await asyncio.sleep(max(0, deadline_at - time.time()))
        except asyncio.CancelledError:
            return

        async with room.lock:
            if room.status != "between" or room.advance_deadline_at != deadline_at:
                return
            room.advance_task = None
            room.advance_deadline_at = None
            self._open_next_player(room)

        await self.broadcast(room)

    def _next_set_number(self, room: AuctionRoom) -> int | None:
        """The set number of the next lot when it starts a new set, else None."""
        player = room.current_player
        next_index = room.current_index + 1
        if not room.player_sets or player is None or next_index >= len(room.players):
            return None
        next_set = room.player_sets.get(room.players[next_index].player_id)
        if next_set is None or next_set == room.player_sets.get(player.player_id):
            return None
        if self._completion_reason(room, next_index):
            return None  # the auction ends instead of starting the next set
        return next_set

    def _set_break_seconds(self, room: AuctionRoom) -> float:
        return SET_BREAK_SECONDS if self._next_set_number(room) is not None else 0.0

    def _set_break_info(self, room: AuctionRoom) -> dict[str, Any] | None:
        """The "Set N begins" banner details while waiting for the next set."""
        waiting = room.status == "between" or (
            room.status == "paused" and room.paused_from_status == "between"
        )
        number = self._next_set_number(room) if waiting else None
        if number is None:
            return None
        return {
            "next_set": number,
            "label": f"SET {number}",
            "players": sum(
                1 for item in room.players if room.player_sets.get(item.player_id) == number
            ),
            "seconds": SET_BREAK_SECONDS,
        }

    @staticmethod
    def _set_summary(room: AuctionRoom) -> dict[str, Any]:
        """Set progress for the live room (counts only, no player names or ratings)."""
        if not room.player_sets:
            return {"enabled": False, "current": None, "rows": [], "used_fallback": False}
        totals: dict[int, int] = {}
        called: dict[int, int] = {}
        for item in room.players:
            number = room.player_sets.get(item.player_id)
            totals[number] = totals.get(number, 0) + 1
        for result in room.results:
            number = room.player_sets.get(result["player_id"])
            called[number] = called.get(number, 0) + 1
        player = room.current_player
        return {
            "enabled": True,
            "current": (
                room.player_sets.get(player.player_id)
                if player and room.status != "complete"
                else None
            ),
            "rows": [
                {
                    "number": number,
                    "label": f"SET {number}",
                    "total": totals[number],
                    "called": called.get(number, 0),
                    "sat_out": len(room.sat_out.get(number, [])),
                }
                for number in sorted(totals)
            ],
            "used_fallback": room.sets_fallback,
        }

    @staticmethod
    def _active_team_states(room: AuctionRoom) -> list[TeamState]:
        """Franchises that a person has joined; empty franchises never bid."""
        return [
            room.team_states[team_id]
            for team_id in room.team_members
            if team_id in room.team_states
        ]

    def _completion_reason(self, room: AuctionRoom, next_index: int) -> str | None:
        active = self._active_team_states(room)
        if active and all(squad_is_full(state) for state in active):
            return "squads_full"
        if next_index >= len(room.players):
            return "pool_exhausted"
        remaining = room.players[next_index:]
        if not any(can_still_buy(state, remaining) for state in active):
            return "no_buyers"
        return None

    @staticmethod
    def _next_bid(room: AuctionRoom, player: Player) -> float:
        return (
            player.base_price_crore
            if room.current_bid_crore is None
            else round(room.current_bid_crore + room.bid_increment_crore, 2)
        )

    def _bid_block_reason(
        self, room: AuctionRoom, team_id: str, team_state: TeamState
    ) -> str | None:
        player = room.current_player
        active_lot = room.status == "live" or (
            room.status == "paused" and room.paused_from_status == "live"
        )
        if player is None or not active_lot:
            return None
        return bid_block_reason(player, team_state, self._next_bid(room, player))

    def _store_xi_lock(
        self, room: AuctionRoom, team_id: str, chosen: list[Player]
    ) -> None:
        team_state = room.team_states[team_id]
        chosen_ids = {player.player_id for player in chosen}
        bench = [
            player for player in team_state.players if player.player_id not in chosen_ids
        ]
        room.xi_locks[team_id] = [player.player_id for player in chosen]
        room.franchise_ratings[team_id] = franchise_rating(chosen, bench)

    def _lock_xi(
        self, room: AuctionRoom, connection: Connection, message: dict[str, Any]
    ) -> None:
        if room.status != "complete":
            raise ValueError("The Playing XI opens when the auction is complete.")
        team_id = connection.team_id
        if team_id is None:
            raise ValueError("Join a franchise to lock a Playing XI.")
        if team_id in room.xi_locks:
            raise ValueError("Your Playing XI is already locked.")
        chosen = validate_xi(room.team_states[team_id], message.get("player_ids"))
        self._store_xi_lock(room, team_id, chosen)

    def _auto_lock(self, room: AuctionRoom) -> None:
        if room.status != "complete":
            raise ValueError("The Playing XI opens when the auction is complete.")
        for team_id in room.team_members:
            if team_id in room.xi_locks or team_id not in room.team_states:
                continue
            self._store_xi_lock(
                room, team_id, auto_pick_xi(room.team_states[team_id])
            )

    def _xi_summary(self, room: AuctionRoom) -> dict[str, Any]:
        active_ids = [t for t in room.team_members if t in room.team_states]
        locked = [t for t in active_ids if t in room.xi_locks]
        waiting = [t for t in active_ids if t not in room.xi_locks]
        all_locked = room.status == "complete" and bool(active_ids) and not waiting
        rankings = None
        if all_locked:
            if room.tournament is not None:
                rankings = list(room.tournament.rankings)
            else:
                ordered = sorted(
                    active_ids, key=lambda t: -room.franchise_ratings[t]["rating"]
                )
                rankings = [
                    {
                        "rank": index + 1,
                        "team_id": team_id,
                        "name": room.team_states[team_id].team.name,
                        "short_name": room.team_states[team_id].team.short_name,
                        "logo_url": room.team_states[team_id].team.logo_url,
                        "rating": room.franchise_ratings[team_id]["rating"],
                    }
                    for index, team_id in enumerate(ordered)
                ]
        return {
            "open": room.status == "complete",
            "required": {
                team_id: xi_capacity(room.team_states[team_id]) for team_id in active_ids
            },
            "locked_team_ids": locked,
            "waiting_team_ids": waiting,
            "all_locked": all_locked,
            "rankings": rankings,
        }

    def _private_view(
        self, room: AuctionRoom, connection: Connection
    ) -> dict[str, Any] | None:
        """What only this franchise may see: its own rating and breakdown."""
        team_id = connection.team_id
        if team_id is None or team_id not in room.xi_locks:
            return None
        rating = room.franchise_ratings[team_id]
        rank = None
        summary = self._xi_summary(room)
        if summary["rankings"]:
            rank = next(
                (row["rank"] for row in summary["rankings"] if row["team_id"] == team_id),
                None,
            )
        return {
            "locked": True,
            "player_ids": list(room.xi_locks[team_id]),
            "rating": rating["rating"],
            "breakdown": rating["breakdown"],
            "rank": rank,
        }

    @staticmethod
    def _player_data(player: Player) -> dict[str, Any]:
        return {
            "player_id": player.player_id,
            "name": player.name,
            "role": player.role,
            "style_label": style_label(player),
            "overseas": player.is_overseas,
            "nationality": player.nationality,
            "image_url": player.image_url,
            "elite": player.elite,
            "base_price_crore": player.base_price_crore,
            "matches": player.matches,
            "runs": player.runs,
            "wickets": player.wickets,
            "batting_average": player.batting_average,
            "strike_rate": player.strike_rate,
            "economy": player.economy,
        }

    @staticmethod
    def _require_admin(connection: Connection) -> None:
        if not connection.is_admin:
            raise ValueError("Only the auction admin can do that.")

    @staticmethod
    async def _send_error(websocket: WebSocket, message: str) -> None:
        try:
            await websocket.send_json({"type": "error", "message": message})
        except (WebSocketDisconnect, RuntimeError):
            return

    send_error = _send_error

    def _new_code(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(6))
            if code not in self.rooms:
                return code
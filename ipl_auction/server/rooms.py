import asyncio
import hmac
import math
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ipl_auction.players.models import Player
from ipl_auction.services.auction_service import record_sale, record_unsold
from ipl_auction.teams.lineup import (
    available_roles,
    eligible_roles,
    lineup_score,
    role_label,
    role_progress,
)
from ipl_auction.teams.models import TeamState

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
    highest_squad_role: str | None = None
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
    ) -> tuple[AuctionRoom, str]:
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

    async def handle(
        self, room: AuctionRoom, websocket: WebSocket, message: dict[str, Any]
    ) -> None:
        connection = room.connections.get(websocket)
        if connection is None:
            await self._send_error(websocket, "Join the room before sending actions.")
            return

        action = message.get("type")
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
                else:
                    raise ValueError("Unknown auction action.")
        except ValueError as error:
            await self._send_error(websocket, str(error))
            return

        await self.broadcast(room)

    async def broadcast(self, room: AuctionRoom) -> None:
        async with room.lock:
            connections = list(room.connections)
            state = self.snapshot(room)

        disconnected = []
        for websocket in connections:
            try:
                await websocket.send_json({"type": "state", "state": state})
            except (WebSocketDisconnect, RuntimeError):
                disconnected.append(websocket)

        if disconnected:
            async with room.lock:
                for websocket in disconnected:
                    room.connections.pop(websocket, None)

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
                    "live_status": live_status,
                    "sale_price_crore": result["price_crore"] if result else None,
                    "sold_team_id": result["team_id"] if result else None,
                    "sold_team_name": result["team_name"] if result else None,
                    "squad_role": result.get("squad_role") if result else None,
                }
            )

        teams = []
        for team_id, team_state in room.team_states.items():
            roster = []
            for bought_player in team_state.players:
                sale = results_by_player.get(bought_player.player_id, {})
                assigned_role = team_state.player_roles.get(bought_player.player_id)
                roster.append(
                    {
                        "player_id": bought_player.player_id,
                        "name": bought_player.name,
                        "role": bought_player.role,
                        "image_url": bought_player.image_url,
                        "base_price_crore": bought_player.base_price_crore,
                        "price_crore": sale.get("price_crore"),
                        "squad_role": assigned_role,
                        "squad_role_label": role_label(assigned_role),
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
                    "captain": room.team_members.get(team_id),
                    "lineup_score": lineup_score(team_state),
                    "role_progress": role_progress(team_state),
                    "available_roles": (
                        available_roles(player, team_state) if player else []
                    ),
                    "roster": roster,
                }
            )

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
            "current_player": self._player_data(player) if player else None,
            "current_bid_crore": room.current_bid_crore,
            "highest_team_id": room.highest_team_id,
            "highest_squad_role": room.highest_squad_role,
            "highest_squad_role_label": role_label(room.highest_squad_role),
            "highest_team_name": (
                room.team_names.get(room.highest_team_id)
                if room.highest_team_id
                else None
            ),
            "deadline_at": room.deadline_at,
            "advance_deadline_at": room.advance_deadline_at,
            "advance_delay_seconds": AUTO_ADVANCE_DELAY_SECONDS,
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
        if next_index >= len(room.players):
            room.status = "complete"
            room.current_bid_crore = None
            room.highest_team_id = None
            room.highest_squad_role = None
            room.deadline_at = None
            room.timer_task = None
            return

        room.current_index = next_index
        room.status = "live"
        room.current_bid_crore = None
        room.highest_team_id = None
        room.highest_squad_role = None
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
        if next_bid > team_state.purse_remaining_crore:
            raise ValueError("Your franchise does not have enough purse for this bid.")
        available_squad_roles = available_roles(player, team_state)
        if not available_squad_roles:
            raise ValueError("Your franchise has no open lineup role that fits this player.")
        squad_role = available_squad_roles[0]["key"]

        room.current_bid_crore = next_bid
        room.highest_team_id = connection.team_id
        room.highest_squad_role = squad_role
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
                result = record_sale(
                    player,
                    room.team_states[room.highest_team_id],
                    room.current_bid_crore,
                    room.highest_squad_role,
                )

            room.results.append(
                {
                    "player_id": player.player_id,
                    "player_name": player.name,
                    "status": "unsold" if result.team_id is None else "sold",
                    "team_id": result.team_id,
                    "team_name": room.team_names.get(result.team_id),
                    "price_crore": result.sold_price_crore,
                    "squad_role": room.highest_squad_role,
                    "squad_role_label": role_label(room.highest_squad_role),
                }
            )
            room.status = "between"
            room.deadline_at = None
            room.timer_task = None
            advance_deadline_at = time.time() + AUTO_ADVANCE_DELAY_SECONDS
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

    @staticmethod
    def _player_data(player: Player) -> dict[str, Any]:
        return {
            "player_id": player.player_id,
            "name": player.name,
            "role": player.role,
            "eligible_squad_roles": eligible_roles(player),
            "nationality": player.nationality,
            "image_url": player.image_url,
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

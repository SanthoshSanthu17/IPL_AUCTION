import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ipl_auction.paths import DATA_DIR
from ipl_auction.players.repository import load_players
from ipl_auction.players.sets import (
    PLAYERS_PER_SET_IN_AUCTION,
    SET_COUNT,
    SET_SIZE,
    build_auction_plan,
    set_numbers,
)
from ipl_auction.server.rooms import RoomManager
from ipl_auction.teams.models import TeamState
from ipl_auction.teams.repository import load_default_purse, load_teams


STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "web"
room_manager = RoomManager()
app = FastAPI(title="IPL Auction")
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
AUCTION_INFO_FILE = DATA_DIR / "auction_info.json"


class CreateRoomRequest(BaseModel):
    timer_seconds: Literal[5, 10] = 10
    bid_increment_crore: float = Field(default=0.1, gt=0, le=10)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/catalog")
async def catalog() -> dict:
    try:
        players = load_players()
        teams = load_teams()
        with AUCTION_INFO_FILE.open("r", encoding="utf-8") as file:
            auction_info = json.load(file)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    pool_sets = set_numbers(players)
    return {
        "players": len(players),
        "players_with_ipl_stats": sum(player.has_ipl_stats for player in players),
        "auction": auction_info,
        "sets": {
            "count": SET_COUNT,
            "size": SET_SIZE,
            "taking_part": PLAYERS_PER_SET_IN_AUCTION,
        },
        "player_records": [
            {
                "player_id": player.player_id,
                "name": player.name,
                "role": player.role,
                "nationality": player.nationality,
                "base_price_crore": player.base_price_crore,
                "image_url": player.image_url,
                "elite": player.elite,
                "set_number": pool_sets.get(player.player_id),
                "matches": player.matches,
                "runs": player.runs,
                "wickets": player.wickets,
                "batting_average": player.batting_average,
                "strike_rate": player.strike_rate,
                "economy": player.economy,
            }
            for player in players
        ],
        "teams": [
            {
                "team_id": team.team_id,
                "name": team.name,
                "short_name": team.short_name,
                "logo_url": team.logo_url,
            }
            for team in teams
        ],
    }


@app.post("/api/rooms")
async def create_room(request: CreateRoomRequest) -> dict:
    try:
        players = load_players()
        teams = load_teams()
        purse = load_default_purse()
        team_states = {
            team.team_id: TeamState(team=team, purse_remaining_crore=purse)
            for team in teams
        }
        plan = build_auction_plan(players)
        room, admin_token = await room_manager.create_room(
            players=plan.players,
            team_states=team_states,
            timer_seconds=request.timer_seconds,
            bid_increment_crore=round(request.bid_increment_crore, 2),
            plan=plan,
        )
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "room_code": room.code,
        "admin_token": admin_token,
        "timer_seconds": room.timer_seconds,
    }


@app.websocket("/ws/{room_code}")
async def auction_socket(websocket: WebSocket, room_code: str) -> None:
    await websocket.accept()
    room = room_manager.get_room(room_code)
    if room is None:
        await websocket.send_json({"type": "error", "message": "Auction room not found."})
        await websocket.close(code=4404)
        return

    connection = None
    try:
        join_message = await websocket.receive_json()
        if not isinstance(join_message, dict):
            raise ValueError("Invalid join message.")
        if join_message.get("type") != "join":
            raise ValueError("Join the room before sending actions.")
        connection = await room_manager.join(
            room=room,
            websocket=websocket,
            name=str(join_message.get("name", "")),
            team_id=join_message.get("team_id") or None,
            admin_token=join_message.get("admin_token") or None,
        )
        await room_manager.broadcast(room)

        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await room_manager.send_error(websocket, "Invalid message format.")
                continue
            await room_manager.handle(room, websocket, message)
    except ValueError as error:
        await room_manager.send_error(websocket, str(error))
        await websocket.close(code=4400)
    except WebSocketDisconnect:
        pass
    finally:
        if connection is not None:
            await room_manager.leave(room, websocket)
            await room_manager.broadcast(room)
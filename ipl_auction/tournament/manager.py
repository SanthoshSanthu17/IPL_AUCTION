"""Tournament ranking, Swiss league fixture generation, NRR standings, playoffs, and live sessions."""

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from ipl_auction.match.constants import (
    DEFAULT_MATCH_SPEED,
    DEFAULT_TOURNAMENT_OVERS,
    TOURNAMENT_OVERS_OPTIONS,
)
from ipl_auction.match.engine import T20MatchSimulator
from ipl_auction.match.models import BallEvent, MatchResult
from ipl_auction.players.models import Player
from ipl_auction.teams.lineup import DEFAULT_PLAYER_RATING
from ipl_auction.teams.models import TeamState
from ipl_auction.tournament.constants import (
    DEFAULT_TOURNAMENT_SEASON,
    LEAGUE_MIN_TEAMS,
    MAX_TOURNAMENT_TEAMS,
    MIN_TOURNAMENT_TEAMS,
    POINTS_LOSS,
    POINTS_WIN,
    STAGE_ELIMINATOR,
    STAGE_ELIMINATOR_PLAYIN,
    STAGE_FINAL,
    STAGE_LEAGUE_R1,
    STAGE_LEAGUE_R2,
    STAGE_QUALIFIER_1,
    STAGE_QUALIFIER_2,
    STATUS_COMPLETED,
    STATUS_LIVE,
    STATUS_UPCOMING,
)

try:
    from ipl_auction.server.winners import record_winner as _record_winner
except ImportError:
    _record_winner = None


def _rating_of(player: Player) -> float:
    return player.rating if player.rating is not None else DEFAULT_PLAYER_RATING


def compute_initial_rankings(
    participating_team_ids: list[str],
    team_states: dict[str, TeamState],
    xi_locks: dict[str, list[str]],
    franchise_ratings: dict[str, dict[str, Any]],
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Rank participating franchises by Franchise Rating -> XI Strength -> Bench Avg -> Coin Toss."""
    rng = random.Random(seed)
    coin_toss_keys = {tid: rng.random() for tid in participating_team_ids}

    entries: list[dict[str, Any]] = []
    for tid in participating_team_ids:
        state = team_states[tid]
        locked_ids = set(xi_locks.get(tid, []))
        xi_players = [p for p in state.players if p.player_id in locked_ids]
        bench_players = [p for p in state.players if p.player_id not in locked_ids]

        xi_strength = (
            round(sum(_rating_of(p) for p in xi_players) / len(xi_players), 4)
            if xi_players
            else 0.0
        )
        bench_avg = (
            round(sum(_rating_of(p) for p in bench_players) / len(bench_players), 4)
            if bench_players
            else 0.0
        )
        f_rating = float(franchise_ratings[tid]["rating"])
        entries.append(
            {
                "team_id": tid,
                "name": state.team.name,
                "short_name": state.team.short_name,
                "logo_url": state.team.logo_url,
                "rating": f_rating,
                "_xi_strength": xi_strength,
                "_bench_avg": bench_avg,
                "_coin": coin_toss_keys[tid],
            }
        )

    entries.sort(
        key=lambda item: (
            -item["rating"],
            -item["_xi_strength"],
            -item["_bench_avg"],
            -item["_coin"],
        )
    )

    finalized: list[dict[str, Any]] = []
    for idx, item in enumerate(entries, start=1):
        finalized.append(
            {
                "rank": idx,
                "team_id": item["team_id"],
                "name": item["name"],
                "short_name": item["short_name"],
                "logo_url": item["logo_url"],
                "rating": item["rating"],
            }
        )
    return finalized


@dataclass
class Fixture:
    match_id: str
    fixture_number: int
    stage: str
    is_playoff: bool
    team_a_id: str | None
    team_b_id: str | None
    status: str = STATUS_UPCOMING
    winner_team_id: str | None = None
    loser_team_id: str | None = None
    result_summary: str | None = None
    seed: int = 100
    tbd_a_label: str = "TBD"
    tbd_b_label: str = "TBD"

    def involves_team(self, team_id: str | None) -> bool:
        if not team_id:
            return False
        return self.team_a_id == team_id or self.team_b_id == team_id

    @property
    def is_ready(self) -> bool:
        return bool(self.team_a_id and self.team_b_id)


@dataclass
class StandingRow:
    team_id: str
    team_name: str
    short_name: str
    logo_url: str
    initial_rank: int
    played: int = 0
    won: int = 0
    lost: int = 0
    points: int = 0
    runs_scored: int = 0
    overs_faced: float = 0.0
    runs_conceded: int = 0
    overs_bowled: float = 0.0

    @property
    def nrr(self) -> float:
        for_rate = (self.runs_scored / self.overs_faced) if self.overs_faced > 0 else 0.0
        against_rate = (
            (self.runs_conceded / self.overs_bowled) if self.overs_bowled > 0 else 0.0
        )
        return round(for_rate - against_rate, 3)

    def to_public_dict(self, position: int) -> dict[str, Any]:
        return {
            "position": position,
            "team_id": self.team_id,
            "name": self.team_name,
            "short_name": self.short_name,
            "logo_url": self.logo_url,
            "initial_rank": self.initial_rank,
            "played": self.played,
            "won": self.won,
            "lost": self.lost,
            "points": self.points,
            "nrr": self.nrr,
        }


@dataclass
class LiveMatchSession:
    match_id: str
    fixture: Fixture
    simulator: T20MatchSimulator
    speed: str = DEFAULT_MATCH_SPEED
    participants: set[WebSocket] = field(default_factory=set)
    spectators: set[WebSocket] = field(default_factory=set)
    task: asyncio.Task[None] | None = None  # auto-play loop (computer plays both sides)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ball_history: list[dict[str, Any]] = field(default_factory=list)

    def all_watchers(self) -> list[WebSocket]:
        return list(self.participants | self.spectators)


@dataclass
class TournamentState:
    room_code: str
    rankings: list[dict[str, Any]]
    fixtures: list[Fixture]
    standings: dict[str, StandingRow]
    league_team_ids: list[str]
    format_type: str  # "knockout_2", "knockout_3", "league_playoffs"
    base_seed: int
    total_overs: int = DEFAULT_TOURNAMENT_OVERS
    round2_generated: bool = False
    playoffs_initialized: bool = False
    champion_team_id: str | None = None
    runner_up_team_id: str | None = None
    final_result: dict[str, Any] | None = None
    winner_recorded: bool = False
    sessions: dict[str, LiveMatchSession] = field(default_factory=dict)
    completed_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_deliveries: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def get_fixture(self, match_id: str) -> Fixture | None:
        for fx in self.fixtures:
            if fx.match_id == match_id:
                return fx
        return None

    def sorted_standings(self) -> list[dict[str, Any]]:
        ordered = sorted(
            self.standings.values(),
            key=lambda row: (-row.points, -row.nrr, row.initial_rank),
        )
        return [row.to_public_dict(idx + 1) for idx, row in enumerate(ordered)]

    def current_stage_label(self) -> str:
        if self.champion_team_id:
            return "Tournament Complete"
        live = [fx for fx in self.fixtures if fx.status == STATUS_LIVE]
        if live:
            return live[0].stage
        upcoming = [fx for fx in self.fixtures if fx.status == STATUS_UPCOMING and fx.is_ready]
        if upcoming:
            return upcoming[0].stage
        return "Awaiting Next Stage"

    def record_match_completion(
        self,
        fixture: Fixture,
        result: MatchResult,
        deliveries: list[BallEvent],
    ) -> dict[str, bool]:
        """Update standings, generate Round 2 or playoff progression, and record Past Winners."""
        fixture.status = STATUS_COMPLETED
        fixture.winner_team_id = result.winner_team_id
        fixture.loser_team_id = result.loser_team_id
        fixture.result_summary = result.margin

        public_result = result.to_public_dict()
        self.completed_results[fixture.match_id] = public_result
        self.completed_deliveries[fixture.match_id] = [
            ev.to_public_dict() for ev in deliveries
        ]

        standings_updated = False
        bracket_updated = False

        # Update league NRR & points (regular innings only; Super Over excluded)
        if not fixture.is_playoff and len(result.innings) >= 2:
            inn1, inn2 = result.innings[0], result.innings[1]
            row1 = self.standings.get(inn1.batting_team_id)
            row2 = self.standings.get(inn2.batting_team_id)
            if row1 and row2:
                row1.played += 1
                row1.runs_scored += inn1.runs
                row1.overs_faced += inn1.nrr_overs
                row1.runs_conceded += inn2.runs
                row1.overs_bowled += inn2.nrr_overs

                row2.played += 1
                row2.runs_scored += inn2.runs
                row2.overs_faced += inn2.nrr_overs
                row2.runs_conceded += inn1.runs
                row2.overs_bowled += inn1.nrr_overs

                if result.winner_team_id == row1.team_id:
                    row1.won += 1
                    row1.points += POINTS_WIN
                    row2.lost += 1
                    row2.points += POINTS_LOSS
                else:
                    row2.won += 1
                    row2.points += POINTS_WIN
                    row1.lost += 1
                    row1.points += POINTS_LOSS
                standings_updated = True

        # Handle tournament progression
        if self.format_type == "knockout_2":
            if fixture.stage == STAGE_FINAL:
                self._crown_champion(result.winner_team_id, result.loser_team_id, public_result)
                bracket_updated = True

        elif self.format_type == "knockout_3":
            if fixture.stage == STAGE_ELIMINATOR:
                final_fx = next((f for f in self.fixtures if f.stage == STAGE_FINAL), None)
                if final_fx and final_fx.team_b_id is None:
                    final_fx.team_b_id = result.winner_team_id
                    bracket_updated = True
            elif fixture.stage == STAGE_FINAL:
                self._crown_champion(result.winner_team_id, result.loser_team_id, public_result)
                bracket_updated = True

        elif self.format_type == "league_playoffs":
            if not fixture.is_playoff:
                r1_fixtures = [f for f in self.fixtures if f.stage == STAGE_LEAGUE_R1]
                if (
                    not self.round2_generated
                    and r1_fixtures
                    and all(f.status == STATUS_COMPLETED for f in r1_fixtures)
                ):
                    self._generate_round_2(r1_fixtures)
                    bracket_updated = True

                league_fixtures = [f for f in self.fixtures if not f.is_playoff]
                if (
                    self.round2_generated
                    and not self.playoffs_initialized
                    and all(f.status == STATUS_COMPLETED for f in league_fixtures)
                ):
                    self._populate_eliminator_from_standings()
                    bracket_updated = True
            else:
                bracket_updated = self._advance_playoffs(fixture, result, public_result)

        return {
            "standings_updated": standings_updated,
            "bracket_updated": bracket_updated,
        }

    def _generate_round_2(self, r1_fixtures: list[Fixture]) -> None:
        self.round2_generated = True
        rank_by_id = {r["team_id"]: r["rank"] for r in self.rankings}
        r1_pairs = {
            frozenset((f.team_a_id, f.team_b_id))
            for f in r1_fixtures
            if f.team_a_id and f.team_b_id
        }

        winners = sorted(
            [f.winner_team_id for f in r1_fixtures if f.winner_team_id],
            key=lambda tid: rank_by_id[tid],
        )
        losers = sorted(
            [f.loser_team_id for f in r1_fixtures if f.loser_team_id],
            key=lambda tid: rank_by_id[tid],
        )

        # Handle odd-sized league groups (e.g., 5-team or 7-team tournament -> 3 or 5 league teams)
        carry_team: str | None = None
        if len(winners) % 2 == 1:
            carry_team = winners.pop(-1)
        if carry_team is not None:
            losers.append(carry_team)
            losers.sort(key=lambda tid: rank_by_id[tid])

        # Also check if a team had an R1 bye (when total league teams was odd)
        r1_played = {
            tid
            for f in r1_fixtures
            for tid in (f.team_a_id, f.team_b_id)
            if tid is not None
        }
        bye_teams = [tid for tid in self.league_team_ids if tid not in r1_played]
        if bye_teams:
            # Give bye team(s) 2 matches in Round 2 by pairing them cleanly
            for b_tid in bye_teams:
                if len(losers) % 2 == 1:
                    losers.append(b_tid)
                    losers.sort(key=lambda tid: rank_by_id[tid])
                else:
                    winners.append(b_tid)
                    winners.sort(key=lambda tid: rank_by_id[tid])

        def pair_group(group: list[str]) -> list[tuple[str, str]]:
            pairs: list[tuple[str, str]] = []
            remaining = list(group)
            while len(remaining) >= 2:
                first = remaining.pop(0)
                partner_idx = 0
                for idx, candidate in enumerate(remaining):
                    if frozenset((first, candidate)) not in r1_pairs:
                        partner_idx = idx
                        break
                second = remaining.pop(partner_idx)
                pairs.append((first, second))
            return pairs

        r2_pairs = pair_group(winners) + pair_group(losers)

        # If any bye team only played 1 match so far (because odd league count means total slots = N*2),
        # pair remaining 1-match teams together so every league team plays exactly 2 matches.
        scheduled_counts = {tid: 0 for tid in self.league_team_ids}
        for f in r1_fixtures:
            if f.team_a_id:
                scheduled_counts[f.team_a_id] += 1
            if f.team_b_id:
                scheduled_counts[f.team_b_id] += 1
        for a, b in r2_pairs:
            scheduled_counts[a] += 1
            scheduled_counts[b] += 1

        under_scheduled = sorted(
            [tid for tid, cnt in scheduled_counts.items() if cnt < 2],
            key=lambda tid: rank_by_id[tid],
        )
        while len(under_scheduled) >= 2:
            a = under_scheduled.pop(0)
            b = under_scheduled.pop(0)
            r2_pairs.append((a, b))

        # Insert Round 2 fixtures right before playoff fixtures
        playoff_fixtures = [f for f in self.fixtures if f.is_playoff]
        non_playoff = [f for f in self.fixtures if not f.is_playoff]

        for a, b in r2_pairs:
            num = len(non_playoff) + 1
            non_playoff.append(
                Fixture(
                    match_id=f"{self.room_code}-M{num}",
                    fixture_number=num,
                    stage=STAGE_LEAGUE_R2,
                    is_playoff=False,
                    team_a_id=a,
                    team_b_id=b,
                    seed=self.base_seed + num * 17,
                )
            )

        # Renumber playoff fixtures cleanly
        for idx, pf in enumerate(playoff_fixtures, start=len(non_playoff) + 1):
            pf.fixture_number = idx
            pf.match_id = f"{self.room_code}-M{idx}"

        self.fixtures = non_playoff + playoff_fixtures

    def _populate_eliminator_from_standings(self) -> None:
        self.playoffs_initialized = True
        table = self.sorted_standings()
        elim = next((f for f in self.fixtures if f.stage == STAGE_ELIMINATOR), None)
        if elim and len(table) >= 2:
            elim.team_a_id = table[0]["team_id"]
            elim.team_b_id = table[1]["team_id"]

    def _advance_playoffs(
        self,
        fixture: Fixture,
        result: MatchResult,
        public_result: dict[str, Any],
    ) -> bool:
        q1 = next((f for f in self.fixtures if f.stage == STAGE_QUALIFIER_1), None)
        elim = next((f for f in self.fixtures if f.stage == STAGE_ELIMINATOR), None)
        q2 = next((f for f in self.fixtures if f.stage == STAGE_QUALIFIER_2), None)
        final_fx = next((f for f in self.fixtures if f.stage == STAGE_FINAL), None)

        if fixture.stage == STAGE_QUALIFIER_1:
            if final_fx:
                final_fx.team_a_id = result.winner_team_id
            if q2:
                q2.team_a_id = result.loser_team_id
            return True

        if fixture.stage == STAGE_ELIMINATOR:
            if q2:
                q2.team_b_id = result.winner_team_id
            return True

        if fixture.stage == STAGE_QUALIFIER_2:
            if final_fx:
                final_fx.team_b_id = result.winner_team_id
            return True

        if fixture.stage == STAGE_FINAL:
            self._crown_champion(result.winner_team_id, result.loser_team_id, public_result)
            return True

        return False

    def _crown_champion(
        self,
        winner_team_id: str,
        runner_up_team_id: str,
        public_result: dict[str, Any],
    ) -> None:
        self.champion_team_id = winner_team_id
        self.runner_up_team_id = runner_up_team_id
        self.final_result = public_result

        if not self.winner_recorded and _record_winner is not None:
            self.winner_recorded = True
            try:
                _record_winner(
                    season=DEFAULT_TOURNAMENT_SEASON,
                    winner_team_id=winner_team_id,
                    runner_up_team_id=runner_up_team_id,
                    room_code=self.room_code,
                )
            except Exception:
                pass


def build_tournament(
    room_code: str,
    participating_team_ids: list[str],
    team_states: dict[str, TeamState],
    xi_locks: dict[str, list[str]],
    franchise_ratings: dict[str, dict[str, Any]],
    seed: int = 42,
    total_overs: int = DEFAULT_TOURNAMENT_OVERS,
) -> TournamentState:
    """Create initial tournament rankings, standings, and Round 1 / bracket fixtures."""
    if total_overs not in TOURNAMENT_OVERS_OPTIONS:
        raise ValueError("Tournament overs must be 5, 10 or 20.")
    count = len(participating_team_ids)
    if count < MIN_TOURNAMENT_TEAMS or count > MAX_TOURNAMENT_TEAMS:
        raise ValueError(
            f"Tournament requires between {MIN_TOURNAMENT_TEAMS} and {MAX_TOURNAMENT_TEAMS} franchises."
        )

    rankings = compute_initial_rankings(
        participating_team_ids=participating_team_ids,
        team_states=team_states,
        xi_locks=xi_locks,
        franchise_ratings=franchise_ratings,
        seed=seed,
    )
    ranked_ids = [row["team_id"] for row in rankings]

    if count == 2:
        fixtures = [
            Fixture(
                match_id=f"{room_code}-M1",
                fixture_number=1,
                stage=STAGE_FINAL,
                is_playoff=True,
                team_a_id=ranked_ids[0],
                team_b_id=ranked_ids[1],
                seed=seed + 101,
            )
        ]
        return TournamentState(
            room_code=room_code,
            rankings=rankings,
            fixtures=fixtures,
            standings={},
            league_team_ids=[],
            format_type="knockout_2",
            base_seed=seed,
            total_overs=total_overs,
        )

    if count == 3:
        fixtures = [
            Fixture(
                match_id=f"{room_code}-M1",
                fixture_number=1,
                stage=STAGE_ELIMINATOR,
                is_playoff=True,
                team_a_id=ranked_ids[1],
                team_b_id=ranked_ids[2],
                seed=seed + 101,
            ),
            Fixture(
                match_id=f"{room_code}-M2",
                fixture_number=2,
                stage=STAGE_FINAL,
                is_playoff=True,
                team_a_id=ranked_ids[0],
                team_b_id=None,
                seed=seed + 202,
                tbd_b_label="Eliminator Winner",
            ),
        ]
        return TournamentState(
            room_code=room_code,
            rankings=rankings,
            fixtures=fixtures,
            standings={},
            league_team_ids=[],
            format_type="knockout_3",
            base_seed=seed,
            total_overs=total_overs,
        )

    # 4 to 10 teams: Top 2 skip league stage to Qualifier 1; Ranks 3..N enter Swiss league
    league_ids = ranked_ids[2:]
    standings: dict[str, StandingRow] = {}
    for row in rankings[2:]:
        tid = row["team_id"]
        standings[tid] = StandingRow(
            team_id=tid,
            team_name=row["name"],
            short_name=row["short_name"],
            logo_url=row["logo_url"],
            initial_rank=row["rank"],
        )

    fixtures: list[Fixture] = []
    # Pair highest vs lowest inside league_ids: e.g. Rank 3 vs Rank 10, Rank 4 vs Rank 9...
    pair_count = len(league_ids) // 2
    for idx in range(pair_count):
        team_a = league_ids[idx]
        team_b = league_ids[-(idx + 1)]
        num = len(fixtures) + 1
        fixtures.append(
            Fixture(
                match_id=f"{room_code}-M{num}",
                fixture_number=num,
                stage=STAGE_LEAGUE_R1,
                is_playoff=False,
                team_a_id=team_a,
                team_b_id=team_b,
                seed=seed + num * 13,
            )
        )

    # Pre-create playoff bracket placeholders
    q1_num = len(fixtures) + 1
    fixtures.append(
        Fixture(
            match_id=f"{room_code}-M{q1_num}",
            fixture_number=q1_num,
            stage=STAGE_QUALIFIER_1,
            is_playoff=True,
            team_a_id=ranked_ids[0],
            team_b_id=ranked_ids[1],
            seed=seed + 501,
        )
    )
    elim_num = len(fixtures) + 1
    fixtures.append(
        Fixture(
            match_id=f"{room_code}-M{elim_num}",
            fixture_number=elim_num,
            stage=STAGE_ELIMINATOR,
            is_playoff=True,
            team_a_id=None,
            team_b_id=None,
            seed=seed + 502,
            tbd_a_label="League #1",
            tbd_b_label="League #2",
        )
    )
    q2_num = len(fixtures) + 1
    fixtures.append(
        Fixture(
            match_id=f"{room_code}-M{q2_num}",
            fixture_number=q2_num,
            stage=STAGE_QUALIFIER_2,
            is_playoff=True,
            team_a_id=None,
            team_b_id=None,
            seed=seed + 503,
            tbd_a_label="Qualifier 1 Loser",
            tbd_b_label="Eliminator Winner",
        )
    )
    final_num = len(fixtures) + 1
    fixtures.append(
        Fixture(
            match_id=f"{room_code}-M{final_num}",
            fixture_number=final_num,
            stage=STAGE_FINAL,
            is_playoff=True,
            team_a_id=None,
            team_b_id=None,
            seed=seed + 504,
            tbd_a_label="Qualifier 1 Winner",
            tbd_b_label="Qualifier 2 Winner",
        )
    )

    return TournamentState(
        room_code=room_code,
        rankings=rankings,
        fixtures=fixtures,
        standings=standings,
        league_team_ids=league_ids,
        format_type="league_playoffs",
        base_seed=seed,
        total_overs=total_overs,
    )
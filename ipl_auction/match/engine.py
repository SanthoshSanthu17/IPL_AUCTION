"""Deterministic pure-Python T20 match engine independent of UI/WebSockets."""

import random
from typing import Any

from ipl_auction.match.constants import (
    BALLS_PER_OVER,
    BASE_OUTCOME_WEIGHTS,
    BAT_ROLE_PRIORITY,
    BOWLER_DISMISSAL_TYPES,
    BOWLER_OR_AR_BAT_PRIORITY,
    DEFAULT_PLAYER_RATING,
    FALLBACK_BAT_PRIORITY,
    MAX_OVERS_PER_BOWLER,
    MAX_OVERS_REGULAR,
    MAX_SUPER_OVERS_SAFETY_CAP,
    MAX_WICKETS_REGULAR,
    NO_BALL_PROBABILITY_BASE,
    RUN_OUT_SHARE_OF_WICKETS,
    SUPER_OVER_MAX_WICKETS,
    SUPER_OVER_OVERS,
    WIDE_PROBABILITY_BASE,
)
from ipl_auction.match.models import (
    BallEvent,
    BatterScorecard,
    BowlerScorecard,
    InningsState,
    MatchResult,
)
from ipl_auction.players.models import Player


def _player_rating(player: Player) -> float:
    return player.rating if player.rating is not None else DEFAULT_PLAYER_RATING


def _role_lower(player: Player) -> str:
    return player.role.strip().casefold()


def _bat_priority(player: Player) -> int:
    if player.bat_role and player.bat_role.strip().casefold() in BAT_ROLE_PRIORITY:
        return BAT_ROLE_PRIORITY[player.bat_role.strip().casefold()]
    if _role_lower(player) in {"bowler", "all-rounder"}:
        return BOWLER_OR_AR_BAT_PRIORITY
    return FALLBACK_BAT_PRIORITY


def build_batting_order(xi: list[Player]) -> list[Player]:
    """Order Playing XI by bat_role priority while preserving original order on ties."""
    indexed = list(enumerate(xi))
    indexed.sort(key=lambda pair: (_bat_priority(pair[1]), pair[0]))
    return [player for _, player in indexed]


def eligible_bowlers(xi: list[Player]) -> list[Player]:
    """Return Bowlers and All-Rounders from the locked XI (fallback to all XI if none)."""
    bowlers = [
        player
        for player in xi
        if _role_lower(player) in {"bowler", "all-rounder"}
    ]
    return bowlers if bowlers else list(xi)


def _select_super_over_batters(ordered_xi: list[Player]) -> list[Player]:
    """Select top 3 batters/finishers/all-rounders for Super Over (max 2 wickets)."""
    ranked = sorted(
        enumerate(ordered_xi),
        key=lambda pair: (
            0
            if (pair[1].bat_role or "").casefold() in {"finisher", "opener", "middle", "anchor"}
            else 1,
            -_player_rating(pair[1]),
            pair[0],
        ),
    )
    return [player for _, player in ranked[:3]]


def _select_super_over_bowler(xi: list[Player]) -> Player:
    """Select highest-rated eligible bowler for the Super Over."""
    pool = eligible_bowlers(xi)
    return max(pool, key=lambda p: _player_rating(p))


class T20MatchSimulator:
    """Step-by-step deterministic T20 simulator supporting live ball streaming."""

    def __init__(
        self,
        match_id: str,
        team_a_id: str,
        team_a_name: str,
        team_a_xi: list[Player],
        team_b_id: str,
        team_b_name: str,
        team_b_xi: list[Player],
        seed: int = 42,
    ) -> None:
        if len(team_a_xi) < 2 or len(team_b_xi) < 2:
            raise ValueError("Both teams must have at least 2 players in their Playing XI.")
        self.match_id = match_id
        self.seed = seed
        self.rng = random.Random(seed)

        self.teams = {
            team_a_id: {
                "team_id": team_a_id,
                "name": team_a_name,
                "xi": list(team_a_xi),
                "by_id": {p.player_id: p for p in team_a_xi},
                "bat_order": build_batting_order(team_a_xi),
                "bowlers": eligible_bowlers(team_a_xi),
            },
            team_b_id: {
                "team_id": team_b_id,
                "name": team_b_name,
                "xi": list(team_b_xi),
                "by_id": {p.player_id: p for p in team_b_xi},
                "bat_order": build_batting_order(team_b_xi),
                "bowlers": eligible_bowlers(team_b_xi),
            },
        }
        self.team_a_id = team_a_id
        self.team_b_id = team_b_id

        # Toss decides who bats first deterministically
        toss_winner = team_a_id if self.rng.random() < 0.5 else team_b_id
        bat_first = toss_winner if self.rng.random() < 0.6 else (
            team_b_id if toss_winner == team_a_id else team_a_id
        )
        bowl_first = team_b_id if bat_first == team_a_id else team_a_id
        self.toss_winner_id = toss_winner
        self.bat_first_id = bat_first
        self.bowl_first_id = bowl_first

        self.sequence = 0
        self.regular_innings: list[InningsState] = []
        self.super_over_innings: list[InningsState] = []
        self.super_over_round = 0
        self.completed = False
        self.result: MatchResult | None = None

        self.current_innings = self._create_innings(
            innings_number=1,
            batting_team_id=self.bat_first_id,
            bowling_team_id=self.bowl_first_id,
            max_overs=MAX_OVERS_REGULAR,
            max_wickets=MAX_WICKETS_REGULAR,
            target=None,
            is_super_over=False,
        )
        self.regular_innings.append(self.current_innings)

    def _create_innings(
        self,
        innings_number: int,
        batting_team_id: str,
        bowling_team_id: str,
        max_overs: int,
        max_wickets: int,
        target: int | None,
        is_super_over: bool,
    ) -> InningsState:
        bat_team = self.teams[batting_team_id]
        bowl_team = self.teams[bowling_team_id]

        bat_players = (
            _select_super_over_batters(bat_team["bat_order"])
            if is_super_over
            else bat_team["bat_order"]
        )
        bowl_players = (
            [_select_super_over_bowler(bowl_team["xi"])]
            if is_super_over
            else bowl_team["bowlers"]
        )

        batting_card = {
            p.player_id: BatterScorecard(player_id=p.player_id, player_name=p.name)
            for p in bat_players
        }
        bowling_card = {
            p.player_id: BowlerScorecard(player_id=p.player_id, player_name=p.name)
            for p in bowl_players
        }

        striker = bat_players[0]
        non_striker = bat_players[1]
        batting_card[striker.player_id].batted = True
        batting_card[non_striker.player_id].batted = True

        effective_max_wickets = min(max_wickets, len(bat_players) - 1)

        inn = InningsState(
            innings_number=innings_number,
            batting_team_id=batting_team_id,
            batting_team_name=bat_team["name"],
            bowling_team_id=bowling_team_id,
            bowling_team_name=bowl_team["name"],
            max_overs=max_overs,
            max_wickets=effective_max_wickets,
            target=target,
            is_super_over=is_super_over,
            striker_id=striker.player_id,
            non_striker_id=non_striker.player_id,
            next_batter_index=2,
            batting_order=[p.player_id for p in bat_players],
            batting_card=batting_card,
            bowling_card=bowling_card,
        )
        self._select_bowler_for_over(inn)
        return inn

    def _select_bowler_for_over(self, inn: InningsState) -> None:
        bowl_team = self.teams[inn.bowling_team_id]
        pool: list[Player] = (
            [_select_super_over_bowler(bowl_team["xi"])]
            if inn.is_super_over
            else bowl_team["bowlers"]
        )
        max_balls = (
            SUPER_OVER_OVERS * BALLS_PER_OVER
            if inn.is_super_over
            else MAX_OVERS_PER_BOWLER * BALLS_PER_OVER
        )

        candidates = [
            p
            for p in pool
            if inn.bowling_card[p.player_id].legal_balls < max_balls
            and p.player_id != inn.last_bowler_id
        ]
        if not candidates:
            # Relax consecutive rule only if pool size == 1
            candidates = [
                p
                for p in pool
                if inn.bowling_card[p.player_id].legal_balls < max_balls
            ]
        if not candidates:
            # Extreme edge case where fewer than 5 bowlers exist in XI: allow any eligible bowler
            candidates = [p for p in pool if p.player_id != inn.last_bowler_id] or pool

        # Weight towards higher-rated bowlers while keeping variety
        weights = [max(10.0, _player_rating(p)) for p in candidates]
        chosen = self.rng.choices(candidates, weights=weights, k=1)[0]
        inn.current_bowler_id = chosen.player_id
        inn.current_over_runs = 0
        inn.current_over_legal_balls = 0

    def step_ball(self) -> tuple[BallEvent | None, dict[str, Any]]:
        """Simulate one delivery and return (BallEvent, transition_info)."""
        if self.completed:
            return None, {"match_completed": True}

        inn = self.current_innings
        if inn.completed:
            transition = self._advance_innings_or_finish()
            return None, transition

        bat_team = self.teams[inn.batting_team_id]
        bowl_team = self.teams[inn.bowling_team_id]

        striker: Player = bat_team["by_id"][inn.striker_id]
        bowler: Player = bowl_team["by_id"][inn.current_bowler_id]

        bat_rating = _player_rating(striker)
        bowl_rating = _player_rating(bowler)
        diff = (bat_rating - bowl_rating) / 100.0  # -1.0 .. +1.0

        over_number = inn.legal_balls // BALLS_PER_OVER
        ball_in_over = (inn.legal_balls % BALLS_PER_OVER) + 1

        # Phase & chase pressure adjustments
        phase_aggression = 1.0
        if inn.is_super_over:
            phase_aggression = 1.35
        elif over_number < 6:
            phase_aggression = 1.12
        elif over_number >= 16:
            phase_aggression = 1.28

        if inn.target is not None:
            runs_needed = max(0, inn.target - inn.runs)
            balls_left = max(1, inn.max_overs * BALLS_PER_OVER - inn.legal_balls)
            req_rr = (runs_needed * 6.0) / balls_left
            wickets_in_hand = max(1, inn.max_wickets - inn.wickets)
            if req_rr > 12.0:
                phase_aggression *= 1.25
            elif req_rr > 9.5:
                phase_aggression *= 1.12
            elif req_rr < 6.0 and wickets_in_hand <= 4:
                phase_aggression *= 0.85

        role_mod = 1.0
        b_role = (striker.bat_role or "").casefold()
        if b_role == "finisher" and (over_number >= 15 or inn.is_super_over):
            role_mod = 1.15
        elif b_role == "opener" and over_number < 6:
            role_mod = 1.10
        elif b_role == "anchor" and inn.wickets >= 3 and over_number < 15:
            role_mod = 0.92
        elif _role_lower(striker) == "bowler":
            role_mod = 0.72

        # Check extras (wide / no-ball)
        wide_prob = max(0.01, WIDE_PROBABILITY_BASE * (1.05 - bowl_rating / 200.0))
        noball_prob = max(0.004, NO_BALL_PROBABILITY_BASE * (1.05 - bowl_rating / 200.0))
        extra_roll = self.rng.random()

        self.sequence += 1
        batter_card = inn.batting_card[striker.player_id]
        bowler_card = inn.bowling_card[bowler.player_id]

        if extra_roll < wide_prob:
            # Wide delivery: +1 extra, illegal delivery, strike stays unless extra runs
            inn.runs += 1
            inn.extras_total += 1
            inn.wides += 1
            inn.current_over_runs += 1
            bowler_card.runs_conceded += 1
            event = BallEvent(
                match_id=self.match_id,
                innings_number=inn.innings_number,
                over_number=over_number,
                ball_number=ball_in_over,
                batter_id=striker.player_id,
                batter_name=striker.name,
                bowler_id=bowler.player_id,
                bowler_name=bowler.name,
                runs_off_bat=0,
                extras=1,
                extra_type="wide",
                total_runs=1,
                is_wicket=False,
                dismissed_player_id=None,
                dismissed_player_name=None,
                dismissal_type=None,
                is_legal=False,
                sequence=self.sequence,
                score_after=inn.runs,
                wickets_after=inn.wickets,
                overs_after=inn.overs_display,
                commentary=f"{bowler.name} to {striker.name}, WIDE! 1 extra run.",
                is_super_over=inn.is_super_over,
            )
            inn.deliveries.append(event)
            transition = self._check_innings_completion(inn)
            return event, transition

        is_noball = extra_roll < (wide_prob + noball_prob)
        extra_runs = 1 if is_noball else 0

        # Compute legal-ball outcome weights
        aggression = phase_aggression * role_mod * (1.0 + diff * 0.45)
        w_dot = max(12.0, BASE_OUTCOME_WEIGHTS["dot"] / (aggression ** 0.55))
        w_1 = BASE_OUTCOME_WEIGHTS["1"] * (1.0 + diff * 0.12)
        w_2 = BASE_OUTCOME_WEIGHTS["2"] * (1.0 + diff * 0.18)
        w_3 = BASE_OUTCOME_WEIGHTS["3"]
        w_4 = BASE_OUTCOME_WEIGHTS["4"] * (aggression ** 0.75)
        w_6 = BASE_OUTCOME_WEIGHTS["6"] * (aggression ** 0.90)
        w_wicket = (
            BASE_OUTCOME_WEIGHTS["wicket"]
            * (1.0 - diff * 0.40)
            * (1.0 + max(0.0, phase_aggression - 1.0) * 0.45)
        )
        if _role_lower(striker) == "bowler":
            w_wicket *= 1.35
        if is_noball:
            # On a no-ball, bowler dismissals cannot happen; only run-out is possible
            w_wicket *= RUN_OUT_SHARE_OF_WICKETS

        outcomes = ["dot", "1", "2", "3", "4", "6", "wicket"]
        weights = [
            max(1.0, w_dot),
            max(1.0, w_1),
            max(0.5, w_2),
            max(0.1, w_3),
            max(1.0, w_4),
            max(0.5, w_6),
            max(0.5, w_wicket),
        ]
        outcome = self.rng.choices(outcomes, weights=weights, k=1)[0]

        runs_off_bat = 0
        is_wicket = False
        dismissal_type: str | None = None
        dismissed_player: Player | None = None

        if outcome == "wicket":
            is_wicket = True
            dismissed_player = striker
            if is_noball or self.rng.random() < RUN_OUT_SHARE_OF_WICKETS:
                dismissal_type = "run out"
            else:
                dismissal_type = self.rng.choice(BOWLER_DISMISSAL_TYPES)
                bowler_card.wickets += 1
        elif outcome != "dot":
            runs_off_bat = int(outcome)

        total_runs = runs_off_bat + extra_runs

        # Update innings and scorecards
        inn.runs += total_runs
        inn.current_over_runs += total_runs
        if is_noball:
            inn.extras_total += 1
            inn.noballs += 1
            bowler_card.runs_conceded += total_runs
            batter_card.balls += 1
        else:
            inn.legal_balls += 1
            inn.current_over_legal_balls += 1
            bowler_card.legal_balls += 1
            bowler_card.runs_conceded += runs_off_bat
            batter_card.balls += 1

        if runs_off_bat > 0:
            batter_card.runs += runs_off_bat
            if runs_off_bat == 4:
                batter_card.fours += 1
            elif runs_off_bat == 6:
                batter_card.sixes += 1

        if is_wicket and dismissed_player is not None:
            inn.wickets += 1
            if dismissal_type == "run out":
                batter_card.dismissal = "run out"
            elif dismissal_type == "bowled":
                batter_card.dismissal = f"b {bowler.name}"
            elif dismissal_type == "lbw":
                batter_card.dismissal = f"lbw b {bowler.name}"
            elif dismissal_type == "stumped":
                batter_card.dismissal = f"st b {bowler.name}"
            elif dismissal_type == "caught & bowled":
                batter_card.dismissal = f"c & b {bowler.name}"
            else:
                batter_card.dismissal = f"c sub b {bowler.name}"

            inn.fall_of_wickets.append(
                {
                    "wicket": inn.wickets,
                    "runs": inn.runs,
                    "overs": inn.overs_display,
                    "player_id": dismissed_player.player_id,
                    "player_name": dismissed_player.name,
                }
            )
            if inn.wickets < inn.max_wickets and inn.next_batter_index < len(inn.batting_order):
                new_batter_id = inn.batting_order[inn.next_batter_index]
                inn.next_batter_index += 1
                inn.striker_id = new_batter_id
                inn.batting_card[new_batter_id].batted = True
            else:
                inn.striker_id = None
        else:
            # Strike rotation on odd runs off the bat
            if runs_off_bat % 2 == 1 and inn.striker_id and inn.non_striker_id:
                inn.striker_id, inn.non_striker_id = inn.non_striker_id, inn.striker_id

        # End of over handling (only on 6th legal ball)
        over_finished = not is_noball and inn.current_over_legal_balls == BALLS_PER_OVER
        if over_finished:
            if inn.current_over_runs == 0:
                bowler_card.maidens += 1
            if inn.striker_id and inn.non_striker_id:
                inn.striker_id, inn.non_striker_id = inn.non_striker_id, inn.striker_id
            inn.last_bowler_id = bowler.player_id

        commentary = self._build_commentary(
            bowler.name,
            striker.name,
            runs_off_bat,
            is_noball,
            is_wicket,
            dismissal_type,
        )

        event = BallEvent(
            match_id=self.match_id,
            innings_number=inn.innings_number,
            over_number=over_number,
            ball_number=ball_in_over,
            batter_id=striker.player_id,
            batter_name=striker.name,
            bowler_id=bowler.player_id,
            bowler_name=bowler.name,
            runs_off_bat=runs_off_bat,
            extras=extra_runs,
            extra_type="noball" if is_noball else None,
            total_runs=total_runs,
            is_wicket=is_wicket,
            dismissed_player_id=dismissed_player.player_id if dismissed_player else None,
            dismissed_player_name=dismissed_player.name if dismissed_player else None,
            dismissal_type=dismissal_type,
            is_legal=not is_noball,
            sequence=self.sequence,
            score_after=inn.runs,
            wickets_after=inn.wickets,
            overs_after=inn.overs_display,
            commentary=commentary,
            is_super_over=inn.is_super_over,
        )
        inn.deliveries.append(event)

        transition = self._check_innings_completion(inn)
        if not inn.completed and over_finished:
            self._select_bowler_for_over(inn)

        return event, transition

    @staticmethod
    def _build_commentary(
        bowler_name: str,
        striker_name: str,
        runs_off_bat: int,
        is_noball: bool,
        is_wicket: bool,
        dismissal_type: str | None,
    ) -> str:
        prefix = f"{bowler_name} to {striker_name}"
        if is_wicket:
            nb = " (No-ball!)" if is_noball else ""
            return f"{prefix}, OUT! {striker_name} is {dismissal_type}{nb}."
        if is_noball:
            return f"{prefix}, NO BALL! {runs_off_bat + 1} runs added."
        if runs_off_bat == 0:
            return f"{prefix}, dot ball."
        if runs_off_bat == 4:
            return f"{prefix}, FOUR! Cracked away to the boundary."
        if runs_off_bat == 6:
            return f"{prefix}, SIX! Clean strike over the ropes."
        return f"{prefix}, {runs_off_bat} run{'s' if runs_off_bat > 1 else ''}."

    def _check_innings_completion(self, inn: InningsState) -> dict[str, Any]:
        target_reached = inn.target is not None and inn.runs >= inn.target
        all_out = inn.wickets >= inn.max_wickets
        overs_done = inn.legal_balls >= inn.max_overs * BALLS_PER_OVER

        if not (target_reached or all_out or overs_done):
            return {"innings_completed": False, "match_completed": False}

        inn.completed = True
        inn.all_out = all_out and not target_reached
        return self._advance_innings_or_finish()

    def _advance_innings_or_finish(self) -> dict[str, Any]:
        # 1. Regular 1st innings just finished -> start 2nd innings
        if len(self.regular_innings) == 1 and self.regular_innings[0].completed:
            first = self.regular_innings[0]
            second = self._create_innings(
                innings_number=2,
                batting_team_id=self.bowl_first_id,
                bowling_team_id=self.bat_first_id,
                max_overs=MAX_OVERS_REGULAR,
                max_wickets=MAX_WICKETS_REGULAR,
                target=first.runs + 1,
                is_super_over=False,
            )
            self.current_innings = second
            self.regular_innings.append(second)
            return {
                "innings_completed": True,
                "match_completed": False,
                "super_over_started": False,
            }

        # 2. Regular 2nd innings finished -> check result or trigger Super Over
        if len(self.regular_innings) == 2 and self.regular_innings[1].completed and not self.super_over_innings:
            first, second = self.regular_innings
            if first.runs != second.runs:
                self._finalize_match(tied_regular=False)
                return {
                    "innings_completed": True,
                    "match_completed": True,
                    "super_over_started": False,
                }
            # Tied regular match -> start Super Over 1 (team batting 2nd bats 1st in SO)
            self.super_over_round = 1
            so_first = self._create_innings(
                innings_number=3,
                batting_team_id=second.batting_team_id,
                bowling_team_id=first.batting_team_id,
                max_overs=SUPER_OVER_OVERS,
                max_wickets=SUPER_OVER_MAX_WICKETS,
                target=None,
                is_super_over=True,
            )
            self.current_innings = so_first
            self.super_over_innings.append(so_first)
            return {
                "innings_completed": True,
                "match_completed": False,
                "super_over_started": True,
                "super_over_round": self.super_over_round,
            }

        # 3. Super Over progression
        if self.super_over_innings:
            last_so = self.super_over_innings[-1]
            if not last_so.completed:
                return {"innings_completed": False, "match_completed": False}

            # Odd number of SO innings -> start chase innings of current Super Over
            if len(self.super_over_innings) % 2 == 1:
                so_second = self._create_innings(
                    innings_number=len(self.regular_innings) + len(self.super_over_innings) + 1,
                    batting_team_id=last_so.bowling_team_id,
                    bowling_team_id=last_so.batting_team_id,
                    max_overs=SUPER_OVER_OVERS,
                    max_wickets=SUPER_OVER_MAX_WICKETS,
                    target=last_so.runs + 1,
                    is_super_over=True,
                )
                self.current_innings = so_second
                self.super_over_innings.append(so_second)
                return {
                    "innings_completed": True,
                    "match_completed": False,
                    "super_over_started": False,
                }

            # Even number of SO innings -> compare latest pair
            so_a = self.super_over_innings[-2]
            so_b = self.super_over_innings[-1]
            if so_a.runs != so_b.runs or self.super_over_round >= MAX_SUPER_OVERS_SAFETY_CAP:
                self._finalize_match(tied_regular=True)
                return {
                    "innings_completed": True,
                    "match_completed": True,
                    "super_over_started": False,
                }

            # Still tied -> start another Super Over
            self.super_over_round += 1
            next_so = self._create_innings(
                innings_number=len(self.regular_innings) + len(self.super_over_innings) + 1,
                batting_team_id=so_b.batting_team_id,
                bowling_team_id=so_a.batting_team_id,
                max_overs=SUPER_OVER_OVERS,
                max_wickets=SUPER_OVER_MAX_WICKETS,
                target=None,
                is_super_over=True,
            )
            self.current_innings = next_so
            self.super_over_innings.append(next_so)
            return {
                "innings_completed": True,
                "match_completed": False,
                "super_over_started": True,
                "super_over_round": self.super_over_round,
            }

        return {"innings_completed": False, "match_completed": False}

    def _finalize_match(self, tied_regular: bool) -> None:
        first, second = self.regular_innings
        if not tied_regular:
            if first.runs > second.runs:
                winner_id = first.batting_team_id
                loser_id = second.batting_team_id
                margin = f"{first.batting_team_name} won by {first.runs - second.runs} runs"
            else:
                winner_id = second.batting_team_id
                loser_id = first.batting_team_id
                wickets_left = second.max_wickets - second.wickets
                margin = f"{second.batting_team_name} won by {wickets_left} wicket{'s' if wickets_left != 1 else ''}"
        else:
            so_a, so_b = self.super_over_innings[-2], self.super_over_innings[-1]
            if so_a.runs > so_b.runs:
                winner_id = so_a.batting_team_id
                loser_id = so_b.batting_team_id
            elif so_b.runs > so_a.runs:
                winner_id = so_b.batting_team_id
                loser_id = so_a.batting_team_id
            else:
                # Safety cap fallback: higher boundary count in regular innings
                a_boundaries = sum(
                    c.fours + c.sixes for c in first.batting_card.values()
                )
                b_boundaries = sum(
                    c.fours + c.sixes for c in second.batting_card.values()
                )
                if a_boundaries >= b_boundaries:
                    winner_id, loser_id = first.batting_team_id, second.batting_team_id
                else:
                    winner_id, loser_id = second.batting_team_id, first.batting_team_id
            winner_name = self.teams[winner_id]["name"]
            so_label = (
                "Super Over"
                if self.super_over_round == 1
                else f"Super Over #{self.super_over_round}"
            )
            margin = f"{winner_name} won via {so_label}"

        potm = self._compute_player_of_the_match(winner_id)
        self.completed = True
        self.result = MatchResult(
            match_id=self.match_id,
            winner_team_id=winner_id,
            winner_team_name=self.teams[winner_id]["name"],
            loser_team_id=loser_id,
            loser_team_name=self.teams[loser_id]["name"],
            margin=margin,
            tied_regular=tied_regular,
            super_overs_played=self.super_over_round,
            player_of_the_match=potm,
            innings=list(self.regular_innings),
            super_over_innings=list(self.super_over_innings),
        )

    def _compute_player_of_the_match(self, winner_id: str) -> dict[str, Any]:
        scores: dict[str, dict[str, Any]] = {}
        for inn in self.regular_innings:
            bat_bonus = 1.15 if inn.batting_team_id == winner_id else 1.0
            bowl_bonus = 1.15 if inn.bowling_team_id == winner_id else 1.0
            for pid, bcard in inn.batting_card.items():
                entry = scores.setdefault(
                    pid,
                    {
                        "player_id": pid,
                        "player_name": bcard.player_name,
                        "team_id": inn.batting_team_id,
                        "team_name": inn.batting_team_name,
                        "points": 0.0,
                        "runs": 0,
                        "balls": 0,
                        "wickets": 0,
                        "runs_conceded": 0,
                    },
                )
                entry["runs"] += bcard.runs
                entry["balls"] += bcard.balls
                entry["points"] += (bcard.runs * 1.2 + bcard.sixes * 2.0 + bcard.fours * 1.0) * bat_bonus

            for pid, wcard in inn.bowling_card.items():
                entry = scores.setdefault(
                    pid,
                    {
                        "player_id": pid,
                        "player_name": wcard.player_name,
                        "team_id": inn.bowling_team_id,
                        "team_name": inn.bowling_team_name,
                        "points": 0.0,
                        "runs": 0,
                        "balls": 0,
                        "wickets": 0,
                        "runs_conceded": 0,
                    },
                )
                entry["wickets"] += wcard.wickets
                entry["runs_conceded"] += wcard.runs_conceded
                entry["points"] += (wcard.wickets * 25.0 + wcard.maidens * 10.0) * bowl_bonus

        best = max(scores.values(), key=lambda item: item["points"])
        summary_parts = []
        if best["runs"] > 0 or best["balls"] > 0:
            summary_parts.append(f"{best['runs']} ({best['balls']})")
        if best["wickets"] > 0:
            summary_parts.append(f"{best['wickets']}/{best['runs_conceded']}")
        return {
            "player_id": best["player_id"],
            "player_name": best["player_name"],
            "team_id": best["team_id"],
            "team_name": best["team_name"],
            "summary": " & ".join(summary_parts) if summary_parts else "Match impact",
        }

    def run_to_completion(self) -> MatchResult:
        while not self.completed:
            self.step_ball()
        assert self.result is not None
        return self.result

    def snapshot(self) -> dict[str, Any]:
        inn = self.current_innings
        recent_balls = [ev.to_public_dict() for ev in inn.deliveries[-6:]]
        striker_card = inn.batting_card.get(inn.striker_id) if inn.striker_id else None
        non_striker_card = (
            inn.batting_card.get(inn.non_striker_id) if inn.non_striker_id else None
        )
        bowler_card = (
            inn.bowling_card.get(inn.current_bowler_id)
            if inn.current_bowler_id
            else None
        )

        req_rr = None
        runs_needed = None
        balls_remaining = None
        if inn.target is not None and not inn.completed:
            runs_needed = max(0, inn.target - inn.runs)
            balls_remaining = max(0, inn.max_overs * BALLS_PER_OVER - inn.legal_balls)
            if balls_remaining > 0:
                req_rr = round((runs_needed * 6.0) / balls_remaining, 2)

        return {
            "match_id": self.match_id,
            "completed": self.completed,
            "innings_number": inn.innings_number,
            "is_super_over": inn.is_super_over,
            "super_over_round": self.super_over_round,
            "batting_team_id": inn.batting_team_id,
            "batting_team_name": inn.batting_team_name,
            "bowling_team_id": inn.bowling_team_id,
            "bowling_team_name": inn.bowling_team_name,
            "runs": inn.runs,
            "wickets": inn.wickets,
            "overs": inn.overs_display,
            "legal_balls": inn.legal_balls,
            "target": inn.target,
            "runs_needed": runs_needed,
            "balls_remaining": balls_remaining,
            "required_run_rate": req_rr,
            "striker": striker_card.to_public_dict() if striker_card else None,
            "non_striker": non_striker_card.to_public_dict() if non_striker_card else None,
            "bowler": bowler_card.to_public_dict() if bowler_card else None,
            "last_six_balls": recent_balls,
            "last_commentary": recent_balls[-1]["commentary"] if recent_balls else "Match ready to begin.",
            "innings_summary": [i.to_public_dict() for i in self.regular_innings],
            "super_over_summary": [i.to_public_dict() for i in self.super_over_innings],
            "result": self.result.to_public_dict() if self.result else None,
        }


def simulate_full_match(
    match_id: str,
    team_a_id: str,
    team_a_name: str,
    team_a_xi: list[Player],
    team_b_id: str,
    team_b_name: str,
    team_b_xi: list[Player],
    seed: int = 42,
) -> MatchResult:
    sim = T20MatchSimulator(
        match_id=match_id,
        team_a_id=team_a_id,
        team_a_name=team_a_name,
        team_a_xi=team_a_xi,
        team_b_id=team_b_id,
        team_b_name=team_b_name,
        team_b_xi=team_b_xi,
        seed=seed,
    )
    return sim.run_to_completion()
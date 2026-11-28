"""Deterministic, interactive T20 match engine independent of UI/WebSockets.

The match is a state machine. Humans (or the computer, via ``step_ball``)
drive it through these phases:

toss_call -> toss_decision -> select_openers -> select_bowler -> bowl_ball
-> bat_ball -> (select_next_batter) -> select_bowler ... -> completed

Every random outcome comes from ``random.Random(seed)`` so the same seed and
the same sequence of choices always produce the same match. Individual player
ratings are used internally only and never appear in a snapshot.
"""

import random
from typing import Any

from ipl_auction.match.commentary import build_commentary
from ipl_auction.match.constants import (
    AI_SHOT_BASE_WEIGHTS,
    AI_TOSS_BOWL_FIRST_CHANCE,
    AI_WIDE_DELIVERY_WEIGHT,
    BALLS_PER_OVER,
    BAT_ROLE_PRIORITY,
    BOWLER_OR_AR_BAT_PRIORITY,
    COMMENTARY_FEED_SIZE,
    DEATH_SPIN_RUN_BONUS,
    DEATH_SPIN_WICKET_MOD,
    DEFAULT_PLAYER_RATING,
    DEFAULT_TOURNAMENT_OVERS,
    DISMISSAL_TYPES,
    DISMISSAL_WEIGHTS_PACE,
    DISMISSAL_WEIGHTS_SPIN,
    FAILED_BOUNDARY_SINGLE_CHANCE,
    FALLBACK_BAT_PRIORITY,
    FINISHER_MAX_WICKETS_LOST,
    FINISHER_PROMOTION_BONUS,
    LATE_INNINGS_FRACTION,
    MATCH_SPEED_DELAYS,
    MAX_SAME_DELIVERY_PER_OVER,
    MAX_SUPER_OVERS_SAFETY_CAP,
    MAX_WICKETS_REGULAR,
    MOMENTUM_GAIN_ROTATE,
    MOMENTUM_LOSS_FOUR,
    MOMENTUM_LOSS_SIX,
    MOMENTUM_LOSS_WICKET,
    MOMENTUM_MAX,
    MOMENTUM_SUCCESS_BONUS,
    MOMENTUM_WICKET_RELIEF,
    OPENER_PENALTY_RUN,
    OPENER_PENALTY_WICKET,
    OUT_OF_POSITION_OPENER_ROLES,
    OVERS_RULES,
    PHASE_BAT_BALL,
    PHASE_BOWL_BALL,
    PHASE_COMPLETED,
    PHASE_SELECT_BOWLER,
    PHASE_SELECT_NEXT_BATTER,
    PHASE_SELECT_OPENERS,
    PHASE_TOSS_CALL,
    PHASE_TOSS_DECISION,
    RHYTHM_RUN_CAP,
    RHYTHM_RUN_PER_WICKET,
    RHYTHM_WICKET_CAP,
    RHYTHM_WICKET_PER_WICKET,
    SHOT_BASE_SUCCESS,
    SHOT_BASE_WICKET,
    SHOT_CODES,
    SHOT_LABELS,
    SIX_JUST_SHORT_FOUR_CHANCE,
    SKILL_DIFF_DIVISOR,
    SUCCESS_PROB_MAX,
    SUCCESS_PROB_MIN,
    SUPER_OVER_MAX_WICKETS,
    SUPER_OVER_OVERS,
    SUPER_OVER_RULES,
    WICKET_PROB_MAX,
    WICKET_PROB_MIN,
)
from ipl_auction.match.deliveries import (
    STYLE_LABELS as BOWLING_STYLE_LABELS,
    Delivery,
    bowler_style,
    deliveries_for,
    get_delivery,
    is_spin_bowler,
)
from ipl_auction.match.models import (
    BallEvent,
    BatterScorecard,
    BowlerScorecard,
    InningsState,
    MatchResult,
)
from ipl_auction.players.models import Player
from ipl_auction.teams.lineup import style_label


def _player_rating(player: Player) -> float:
    return player.rating if player.rating is not None else DEFAULT_PLAYER_RATING


def _role_lower(player: Player) -> str:
    return player.role.strip().casefold()


def _bat_role(player: Player) -> str:
    return (player.bat_role or "").strip().casefold()


def _bat_priority(player: Player) -> int:
    if _bat_role(player) in BAT_ROLE_PRIORITY:
        return BAT_ROLE_PRIORITY[_bat_role(player)]
    if _role_lower(player) in {"bowler", "all-rounder"}:
        return BOWLER_OR_AR_BAT_PRIORITY
    return FALLBACK_BAT_PRIORITY


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    """Top 3 batters/finishers/all-rounders, used by the computer for a Super Over."""
    ranked = sorted(
        enumerate(ordered_xi),
        key=lambda pair: (
            0 if _bat_role(pair[1]) in BAT_ROLE_PRIORITY else 1,
            -_player_rating(pair[1]),
            pair[0],
        ),
    )
    return [player for _, player in ranked[:3]]


def overs_rules(total_overs: int) -> dict[str, int]:
    if total_overs not in OVERS_RULES:
        raise ValueError("Match overs must be 5, 10 or 20.")
    return OVERS_RULES[total_overs]


# ---------------------------------------------------------------------------
# Pure probability helpers (unit-tested directly)
# ---------------------------------------------------------------------------
def tactical_modifiers(
    *,
    striker: Player,
    bowler: Player,
    shot: int,
    over_number: int,
    total_overs: int,
    powerplay_end: int,
    death_start: int,
    wickets: int,
    is_opener: bool,
    bowler_wickets: int,
) -> tuple[float, float]:
    """Return (tactical_wicket_mod, tactical_run_mod) for one delivery.

    over_number is 1-based. run_mod is added to the shot-success chance.
    """
    wicket_mod = 0.0
    run_mod = 0.0
    boundary = shot in (4, 6)

    # Out-of-position opener against the new ball.
    if (
        is_opener
        and _bat_role(striker) in OUT_OF_POSITION_OPENER_ROLES
        and over_number <= powerplay_end
    ):
        wicket_mod += OPENER_PENALTY_WICKET
        run_mod += OPENER_PENALTY_RUN

    # Finisher promoted into a strong platform late in the innings.
    late = over_number >= death_start or over_number > total_overs * (
        1.0 - LATE_INNINGS_FRACTION
    )
    if (
        boundary
        and _bat_role(striker) == "finisher"
        and late
        and wickets <= FINISHER_MAX_WICKETS_LOST
    ):
        run_mod += FINISHER_PROMOTION_BONUS

    # Spin in the death overs is easier to hit.
    if over_number >= death_start and is_spin_bowler(bowler):
        wicket_mod += DEATH_SPIN_WICKET_MOD
        if boundary:
            run_mod += DEATH_SPIN_RUN_BONUS

    # Bowler in rhythm after taking wickets.
    if bowler_wickets >= 1:
        wicket_mod += min(RHYTHM_WICKET_CAP, RHYTHM_WICKET_PER_WICKET * bowler_wickets)
        run_mod -= min(RHYTHM_RUN_CAP, RHYTHM_RUN_PER_WICKET * bowler_wickets)

    return wicket_mod, run_mod


def shot_probabilities(
    *,
    shot: int,
    delivery: Delivery,
    striker: Player,
    bowler: Player,
    momentum: float,
    tactical_wicket_mod: float,
    tactical_run_mod: float,
) -> tuple[float, float]:
    """Return (wicket_probability, success_probability) for a shot."""
    skill_diff = (_player_rating(striker) - _player_rating(bowler)) / SKILL_DIFF_DIVISOR
    p_wicket = _clamp(
        SHOT_BASE_WICKET[shot]
        + delivery.wicket_mod
        + tactical_wicket_mod
        - skill_diff
        - momentum * MOMENTUM_WICKET_RELIEF,
        WICKET_PROB_MIN,
        WICKET_PROB_MAX,
    )
    p_success = _clamp(
        SHOT_BASE_SUCCESS[shot]
        + skill_diff
        + delivery.run_mod
        + tactical_run_mod
        + momentum * MOMENTUM_SUCCESS_BONUS,
        SUCCESS_PROB_MIN,
        SUCCESS_PROB_MAX,
    )
    return p_wicket, p_success


def _player_public(player: Player) -> dict[str, Any]:
    """Public, rating-free view of a player for pickers."""
    return {
        "player_id": player.player_id,
        "name": player.name,
        "role": player.role,
        "style_label": style_label(player),
        "bat_role": player.bat_role,
        "bowl_type": player.bowl_type,
        "overseas": player.is_overseas,
        "elite": player.elite,
    }


class T20MatchSimulator:
    """Interactive state-machine T20 match with an automatic fallback."""

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
        total_overs: int = DEFAULT_TOURNAMENT_OVERS,
    ) -> None:
        if len(team_a_xi) < 2 or len(team_b_xi) < 2:
            raise ValueError("Both teams must have at least 2 players in their Playing XI.")
        self.rules = dict(overs_rules(total_overs))
        self.total_overs = total_overs
        self.match_id = match_id
        self.seed = seed
        self.rng = random.Random(seed)  # match outcomes
        self.ai_rng = random.Random(seed + 7919)  # computer-controlled choices
        self.comm_rng = random.Random(seed + 104729)  # commentary wording only

        self.teams = {
            team_a_id: self._team_entry(team_a_id, team_a_name, team_a_xi),
            team_b_id: self._team_entry(team_b_id, team_b_name, team_b_xi),
        }
        self.team_a_id = team_a_id
        self.team_b_id = team_b_id

        # Toss: the away side (Team B) calls.
        self.toss_caller_id = team_b_id
        self.toss_call: str | None = None
        self.toss_coin: str | None = None
        self.toss_winner_id: str | None = None
        self.toss_decision: str | None = None
        self.bat_first_id: str | None = None
        self.bowl_first_id: str | None = None

        self.phase = PHASE_TOSS_CALL
        self.revision = 0
        self.sequence = 0
        self.pending_delivery: Delivery | None = None
        self.regular_innings: list[InningsState] = []
        self.super_over_innings: list[InningsState] = []
        self.super_over_round = 0
        self.completed = False
        self.result: MatchResult | None = None
        self.current_innings: InningsState | None = None
        self.commentary_log: list[dict[str, Any]] = []
        self._last_commentary: str | None = None
        self._log("info", f"{self.teams[team_b_id]['name']} will call the toss.")

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _team_entry(team_id: str, name: str, xi: list[Player]) -> dict[str, Any]:
        return {
            "team_id": team_id,
            "name": name,
            "xi": list(xi),
            "by_id": {p.player_id: p for p in xi},
            "bat_order": build_batting_order(xi),
            "bowlers": eligible_bowlers(xi),
        }

    def _log(self, kind: str, text: str) -> None:
        self.commentary_log.append(
            {"kind": kind, "text": text, "sequence": self.sequence, "revision": self.revision}
        )
        if len(self.commentary_log) > 200:
            del self.commentary_log[:-200]

    def _bump(self) -> None:
        self.revision += 1

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
        bat_players = bat_team["bat_order"]
        rules = SUPER_OVER_RULES if is_super_over else self.rules

        batting_card = {
            p.player_id: BatterScorecard(player_id=p.player_id, player_name=p.name)
            for p in bat_players
        }
        bowling_card = {
            p.player_id: BowlerScorecard(player_id=p.player_id, player_name=p.name)
            for p in bowl_team["xi"]
        }
        return InningsState(
            innings_number=innings_number,
            batting_team_id=batting_team_id,
            batting_team_name=bat_team["name"],
            bowling_team_id=bowling_team_id,
            bowling_team_name=bowl_team["name"],
            max_overs=max_overs,
            max_wickets=min(max_wickets, len(bat_players) - 1),
            target=target,
            is_super_over=is_super_over,
            powerplay_end=rules["powerplay_end"],
            death_start=rules["death_start"],
            max_bowler_overs=rules["max_bowler_overs"],
            batting_order=[p.player_id for p in bat_players],
            batting_card=batting_card,
            bowling_card=bowling_card,
        )

    def _begin_innings(self, inn: InningsState) -> None:
        self.current_innings = inn
        self.pending_delivery = None
        self.phase = PHASE_SELECT_OPENERS
        label = "Super Over" if inn.is_super_over else f"Innings {inn.innings_number}"
        target_text = f" Target: {inn.target}." if inn.target is not None else ""
        self._log(
            "info",
            f"{label}: {inn.batting_team_name} to bat.{target_text} Pick the openers.",
        )

    # ----------------------------------------------------------- actor lookup
    def actor_team_id(self) -> str | None:
        """Which franchise has to act in the current phase."""
        inn = self.current_innings
        if self.completed or self.phase == PHASE_COMPLETED:
            return None
        if self.phase == PHASE_TOSS_CALL:
            return self.toss_caller_id
        if self.phase == PHASE_TOSS_DECISION:
            return self.toss_winner_id
        if inn is None:
            return None
        if self.phase in (PHASE_SELECT_OPENERS, PHASE_SELECT_NEXT_BATTER, PHASE_BAT_BALL):
            return inn.batting_team_id
        return inn.bowling_team_id  # select_bowler, bowl_ball

    def _require_phase(self, *phases: str) -> None:
        if self.completed:
            raise ValueError("This match has finished.")
        if self.phase not in phases:
            raise ValueError(f"That action is not available now (current step: {self.phase}).")

    # -------------------------------------------------------------------- toss
    def call_toss(self, call: str) -> dict[str, Any]:
        self._require_phase(PHASE_TOSS_CALL)
        call = str(call or "").strip().casefold()
        if call not in {"heads", "tails"}:
            raise ValueError("Call heads or tails.")
        coin = "heads" if self.rng.random() < 0.5 else "tails"
        winner = self.toss_caller_id if call == coin else self._other(self.toss_caller_id)
        self.toss_call, self.toss_coin, self.toss_winner_id = call, coin, winner
        self.phase = PHASE_TOSS_DECISION
        self._bump()
        self._log(
            "info",
            f"{self.teams[self.toss_caller_id]['name']} called {call}. It lands {coin}. "
            f"{self.teams[winner]['name']} won the toss.",
        )
        return {"toss_winner_id": winner}

    def decide_toss(self, decision: str) -> dict[str, Any]:
        self._require_phase(PHASE_TOSS_DECISION)
        decision = str(decision or "").strip().casefold()
        if decision not in {"bat", "bowl"}:
            raise ValueError("Choose to bat or bowl.")
        assert self.toss_winner_id is not None
        self.toss_decision = decision
        if decision == "bat":
            self.bat_first_id = self.toss_winner_id
        else:
            self.bat_first_id = self._other(self.toss_winner_id)
        self.bowl_first_id = self._other(self.bat_first_id)
        self._log(
            "info",
            f"{self.teams[self.toss_winner_id]['name']} chose to {decision} first.",
        )
        first = self._create_innings(
            innings_number=1,
            batting_team_id=self.bat_first_id,
            bowling_team_id=self.bowl_first_id,
            max_overs=self.total_overs,
            max_wickets=MAX_WICKETS_REGULAR,
            target=None,
            is_super_over=False,
        )
        self.regular_innings.append(first)
        self._begin_innings(first)
        self._bump()
        return {"bat_first_id": self.bat_first_id}

    def _other(self, team_id: str) -> str:
        return self.team_b_id if team_id == self.team_a_id else self.team_a_id

    # ----------------------------------------------------------- batting picks
    def _place_batter(self, inn: InningsState, player_id: str) -> None:
        inn.batting_order.remove(player_id)
        inn.batting_order.insert(inn.next_batter_index, player_id)
        inn.next_batter_index += 1
        inn.batting_card[player_id].batted = True

    def available_batters(self, inn: InningsState | None = None) -> list[Player]:
        inn = inn or self.current_innings
        if inn is None:
            return []
        team = self.teams[inn.batting_team_id]
        return [
            team["by_id"][pid]
            for pid in inn.batting_order
            if not inn.batting_card[pid].batted
        ]

    def select_openers(self, striker_id: str, non_striker_id: str) -> dict[str, Any]:
        self._require_phase(PHASE_SELECT_OPENERS)
        inn = self.current_innings
        assert inn is not None
        if striker_id == non_striker_id:
            raise ValueError("Pick two different batters as openers.")
        available = {p.player_id for p in self.available_batters(inn)}
        for pid in (striker_id, non_striker_id):
            if pid not in available:
                raise ValueError("Openers must come from your locked Playing XI.")
        self._place_batter(inn, striker_id)
        self._place_batter(inn, non_striker_id)
        inn.striker_id, inn.non_striker_id = striker_id, non_striker_id
        inn.opener_ids = [striker_id, non_striker_id]
        names = self.teams[inn.batting_team_id]["by_id"]
        self._log(
            "info",
            f"{names[striker_id].name} and {names[non_striker_id].name} open the batting.",
        )
        self.phase = PHASE_SELECT_BOWLER
        self._bump()
        return {}

    def select_next_batter(self, batter_id: str) -> dict[str, Any]:
        self._require_phase(PHASE_SELECT_NEXT_BATTER)
        inn = self.current_innings
        assert inn is not None
        if batter_id not in {p.player_id for p in self.available_batters(inn)}:
            raise ValueError("That batter is not available.")
        self._place_batter(inn, batter_id)
        inn.striker_id = batter_id
        name = self.teams[inn.batting_team_id]["by_id"][batter_id].name
        self._log("info", f"{name} walks in to bat.")
        if inn.pending_over_end:
            inn.pending_over_end = False
            if inn.non_striker_id:
                inn.striker_id, inn.non_striker_id = inn.non_striker_id, inn.striker_id
            self.phase = PHASE_SELECT_BOWLER
        else:
            self.phase = PHASE_BOWL_BALL
        self._bump()
        return {}

    # ----------------------------------------------------------- bowling picks
    def _bowling_pool(self, inn: InningsState) -> list[Player]:
        team = self.teams[inn.bowling_team_id]
        pool = list(team["bowlers"])
        if inn.is_super_over:
            return pool
        cap = inn.max_bowler_overs
        if len(pool) * cap < inn.max_overs:
            extras = sorted(
                (p for p in team["xi"] if p not in pool),
                key=lambda p: -_player_rating(p),
            )
            for player in extras:
                pool.append(player)
                if len(pool) * cap >= inn.max_overs:
                    break
        return pool

    def eligible_bowlers_now(self, inn: InningsState | None = None) -> list[Player]:
        """Bowlers allowed to start the next over."""
        inn = inn or self.current_innings
        if inn is None:
            return []
        pool = self._bowling_pool(inn)
        under = [
            p for p in pool if inn.bowler_overs.get(p.player_id, 0) < inn.max_bowler_overs
        ]
        non_consecutive = [p for p in under if p.player_id != inn.last_bowler_id]
        if non_consecutive:
            return non_consecutive
        if under:
            return under  # only the previous bowler is left
        return [p for p in pool if p.player_id != inn.last_bowler_id] or pool

    def select_bowler(self, bowler_id: str) -> dict[str, Any]:
        self._require_phase(PHASE_SELECT_BOWLER)
        inn = self.current_innings
        assert inn is not None
        pool = {p.player_id: p for p in self._bowling_pool(inn)}
        if bowler_id not in pool:
            raise ValueError("That player is not in your bowling attack.")
        allowed = {p.player_id for p in self.eligible_bowlers_now(inn)}
        if bowler_id not in allowed:
            if inn.bowler_overs.get(bowler_id, 0) >= inn.max_bowler_overs:
                raise ValueError(
                    f"{pool[bowler_id].name} has already bowled the maximum "
                    f"{inn.max_bowler_overs} over{'s' if inn.max_bowler_overs != 1 else ''}."
                )
            raise ValueError(f"{pool[bowler_id].name} bowled the previous over.")
        inn.current_bowler_id = bowler_id
        inn.bowler_overs[bowler_id] = inn.bowler_overs.get(bowler_id, 0) + 1
        inn.over_delivery_counts = {}
        inn.over_boost_used = False
        inn.current_over_runs = 0
        inn.current_over_legal_balls = 0
        over_no = inn.legal_balls // BALLS_PER_OVER + 1
        self._log("info", f"Over {over_no}: {pool[bowler_id].name} to bowl.")
        self.phase = PHASE_BOWL_BALL
        self._bump()
        return {}

    def delivery_options(self) -> list[dict[str, Any]]:
        """Deliveries for the active bowler with uses left this over."""
        inn = self.current_innings
        if inn is None or inn.current_bowler_id is None:
            return []
        bowler = self.teams[inn.bowling_team_id]["by_id"][inn.current_bowler_id]
        options = []
        for delivery in deliveries_for(bowler):
            used = inn.over_delivery_counts.get(delivery.code, 0)
            remaining = max(0, MAX_SAME_DELIVERY_PER_OVER - used)
            options.append(
                {
                    "code": delivery.code,
                    "label": delivery.label,
                    "used": used,
                    "remaining": remaining,
                    "max": MAX_SAME_DELIVERY_PER_OVER,
                    "disabled": remaining <= 0,
                    "wide_risk_pct": round(delivery.wide_risk * 100),
                }
            )
        return options

    def select_delivery(self, delivery_code: str) -> tuple[BallEvent | None, dict[str, Any]]:
        """Bowler picks a delivery. Returns (wide_event_or_None, transition)."""
        self._require_phase(PHASE_BOWL_BALL)
        inn = self.current_innings
        assert inn is not None and inn.current_bowler_id is not None
        bowler = self.teams[inn.bowling_team_id]["by_id"][inn.current_bowler_id]
        delivery = get_delivery(bowler, str(delivery_code or ""))
        if delivery is None:
            raise ValueError(f"{bowler.name} cannot bowl that delivery.")
        if all(opt["disabled"] for opt in self.delivery_options()):
            inn.over_delivery_counts.clear()  # safety valve after an extreme run of wides
        used = inn.over_delivery_counts.get(delivery.code, 0)
        if used >= MAX_SAME_DELIVERY_PER_OVER:
            raise ValueError(
                f"You can bowl the same delivery at most {MAX_SAME_DELIVERY_PER_OVER} "
                "times in an over."
            )
        inn.over_delivery_counts[delivery.code] = used + 1

        if self.rng.random() < delivery.wide_risk:
            event = self._record_wide(inn, bowler, delivery)
            transition = self._check_innings_completion(inn)
            self._bump()
            return event, transition

        self.pending_delivery = delivery
        self.phase = PHASE_BAT_BALL
        self._bump()
        return None, {"innings_completed": False, "match_completed": False}

    def _record_wide(self, inn: InningsState, bowler: Player, delivery: Delivery) -> BallEvent:
        striker = self.teams[inn.batting_team_id]["by_id"][inn.striker_id]
        card = inn.bowling_card[bowler.player_id]
        over_index = inn.legal_balls // BALLS_PER_OVER
        ball_no = (inn.legal_balls % BALLS_PER_OVER) + 1
        inn.runs += 1
        inn.extras_total += 1
        inn.wides += 1
        inn.current_over_runs += 1
        card.runs_conceded += 1
        card.wides += 1
        self.sequence += 1
        text = self._commentary("wide", inn, striker, bowler, delivery)
        event = BallEvent(
            match_id=self.match_id,
            innings_number=inn.innings_number,
            over_number=over_index,
            ball_number=ball_no,
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
            commentary=text,
            is_super_over=inn.is_super_over,
            delivery_code=delivery.code,
            delivery_label=delivery.label,
        )
        inn.deliveries.append(event)
        self._log("ball", text)
        return event

    # ------------------------------------------------------------------- shots
    def play_shot(self, shot_code: str) -> tuple[BallEvent, dict[str, Any]]:
        self._require_phase(PHASE_BAT_BALL)
        shot_code = str(shot_code if shot_code is not None else "").strip()
        if shot_code not in SHOT_CODES:
            raise ValueError("Pick a shot: 0, 1, 2, 4 or 6.")
        shot = int(shot_code)
        inn = self.current_innings
        assert inn is not None and self.pending_delivery is not None
        delivery = self.pending_delivery
        bat_team = self.teams[inn.batting_team_id]
        bowl_team = self.teams[inn.bowling_team_id]
        striker: Player = bat_team["by_id"][inn.striker_id]
        bowler: Player = bowl_team["by_id"][inn.current_bowler_id]
        bowler_card = inn.bowling_card[bowler.player_id]
        batter_card = inn.batting_card[striker.player_id]

        over_index = inn.legal_balls // BALLS_PER_OVER
        ball_no = (inn.legal_balls % BALLS_PER_OVER) + 1
        over_number = over_index + 1

        tactical_wicket, tactical_run = tactical_modifiers(
            striker=striker,
            bowler=bowler,
            shot=shot,
            over_number=over_number,
            total_overs=inn.max_overs,
            powerplay_end=inn.powerplay_end,
            death_start=inn.death_start,
            wickets=inn.wickets,
            is_opener=striker.player_id in inn.opener_ids,
            bowler_wickets=bowler_card.wickets,
        )
        p_wicket, p_success = shot_probabilities(
            shot=shot,
            delivery=delivery,
            striker=striker,
            bowler=bowler,
            momentum=inn.momentum,
            tactical_wicket_mod=tactical_wicket,
            tactical_run_mod=tactical_run,
        )

        runs_off_bat = 0
        is_wicket = self.rng.random() < p_wicket
        dismissal_type: str | None = None
        if is_wicket:
            weights = (
                DISMISSAL_WEIGHTS_SPIN if is_spin_bowler(bowler) else DISMISSAL_WEIGHTS_PACE
            )
            dismissal_type = self.rng.choices(DISMISSAL_TYPES, weights=weights, k=1)[0]
        else:
            if self.rng.random() < p_success:
                runs_off_bat = shot
            elif shot == 6 and self.rng.random() < SIX_JUST_SHORT_FOUR_CHANCE:
                runs_off_bat = 4
            elif shot >= 4 and self.rng.random() < FAILED_BOUNDARY_SINGLE_CHANCE:
                runs_off_bat = 1

        self.sequence += 1

        # Scorecards
        inn.runs += runs_off_bat
        inn.current_over_runs += runs_off_bat
        inn.legal_balls += 1
        inn.current_over_legal_balls += 1
        bowler_card.legal_balls += 1
        bowler_card.runs_conceded += runs_off_bat
        batter_card.balls += 1
        if runs_off_bat == 0:
            bowler_card.dots += 1
        if runs_off_bat > 0:
            batter_card.runs += runs_off_bat
            if runs_off_bat == 4:
                batter_card.fours += 1
            elif runs_off_bat == 6:
                batter_card.sixes += 1

        # Momentum
        if is_wicket:
            inn.momentum = max(0.0, inn.momentum - MOMENTUM_LOSS_WICKET)
        elif runs_off_bat in (1, 2):
            inn.momentum = min(MOMENTUM_MAX, inn.momentum + MOMENTUM_GAIN_ROTATE)
        elif runs_off_bat == 4:
            inn.momentum = max(0.0, inn.momentum - MOMENTUM_LOSS_FOUR)
        elif runs_off_bat == 6:
            inn.momentum = max(0.0, inn.momentum - MOMENTUM_LOSS_SIX)

        dismissed: Player | None = None
        if is_wicket:
            dismissed = striker
            inn.wickets += 1
            if dismissal_type == "run out":
                batter_card.dismissal = "run out"
            else:
                bowler_card.wickets += 1
                batter_card.dismissal = self._dismissal_text(dismissal_type, bowler.name)
            inn.fall_of_wickets.append(
                {
                    "wicket": inn.wickets,
                    "runs": inn.runs,
                    "overs": inn.overs_display,
                    "player_id": striker.player_id,
                    "player_name": striker.name,
                }
            )
            inn.striker_id = None
        elif runs_off_bat == 1 and inn.striker_id and inn.non_striker_id:
            inn.striker_id, inn.non_striker_id = inn.non_striker_id, inn.striker_id

        kind = "wicket" if is_wicket else str(runs_off_bat)
        text = self._commentary(kind, inn, striker, bowler, delivery, dismissal_type)
        event = BallEvent(
            match_id=self.match_id,
            innings_number=inn.innings_number,
            over_number=over_index,
            ball_number=ball_no,
            batter_id=striker.player_id,
            batter_name=striker.name,
            bowler_id=bowler.player_id,
            bowler_name=bowler.name,
            runs_off_bat=runs_off_bat,
            extras=0,
            extra_type=None,
            total_runs=runs_off_bat,
            is_wicket=is_wicket,
            dismissed_player_id=dismissed.player_id if dismissed else None,
            dismissed_player_name=dismissed.name if dismissed else None,
            dismissal_type=dismissal_type,
            is_legal=True,
            sequence=self.sequence,
            score_after=inn.runs,
            wickets_after=inn.wickets,
            overs_after=inn.overs_display,
            commentary=text,
            is_super_over=inn.is_super_over,
            delivery_code=delivery.code,
            delivery_label=delivery.label,
            shot_code=shot_code,
        )
        inn.deliveries.append(event)
        self._log("ball", text)
        self.pending_delivery = None

        over_finished = inn.current_over_legal_balls == BALLS_PER_OVER
        if over_finished:
            if inn.current_over_runs == 0:
                bowler_card.maidens += 1
            inn.last_bowler_id = bowler.player_id
            inn.current_bowler_id = None

        transition = self._check_innings_completion(inn)
        if not inn.completed:
            if is_wicket:
                inn.pending_over_end = over_finished
                self.phase = PHASE_SELECT_NEXT_BATTER
            elif over_finished:
                if inn.striker_id and inn.non_striker_id:
                    inn.striker_id, inn.non_striker_id = inn.non_striker_id, inn.striker_id
                self.phase = PHASE_SELECT_BOWLER
            else:
                self.phase = PHASE_BOWL_BALL
        self._bump()
        return event, transition

    @staticmethod
    def _dismissal_text(dismissal_type: str | None, bowler_name: str) -> str:
        return {
            "bowled": f"b {bowler_name}",
            "lbw": f"lbw b {bowler_name}",
            "caught behind": f"c wk b {bowler_name}",
            "stumped": f"st wk b {bowler_name}",
        }.get(dismissal_type or "", f"c b {bowler_name}")

    def _commentary(
        self,
        kind: str,
        inn: InningsState,
        striker: Player,
        bowler: Player,
        delivery: Delivery,
        dismissal: str | None = None,
    ) -> str:
        over_number = inn.legal_balls // BALLS_PER_OVER + 1
        runs_needed = balls_left = None
        if inn.target is not None:
            runs_needed = max(0, inn.target - inn.runs)
            balls_left = max(0, inn.max_overs * BALLS_PER_OVER - inn.legal_balls)
        text = build_commentary(
            self.comm_rng,
            kind,
            {
                "bowler": bowler.name,
                "striker": striker.name,
                "delivery": delivery.label,
                "is_death": over_number >= inn.death_start and not inn.is_super_over,
                "is_powerplay": over_number <= inn.powerplay_end,
                "runs_needed": runs_needed,
                "balls_remaining": balls_left,
            },
            dismissal=dismissal,
            last_text=self._last_commentary,
        )
        self._last_commentary = text
        return text

    # ------------------------------------------------------ computer decisions
    def _ai_toss_call(self) -> str:
        return self.ai_rng.choice(["heads", "tails"])

    def _ai_toss_decision(self) -> str:
        return "bowl" if self.ai_rng.random() < AI_TOSS_BOWL_FIRST_CHANCE else "bat"

    def _ai_openers(self, inn: InningsState) -> tuple[str, str]:
        team = self.teams[inn.batting_team_id]
        if inn.is_super_over:
            picks = _select_super_over_batters(team["bat_order"])
            return picks[0].player_id, picks[1].player_id
        order = [p.player_id for p in self.available_batters(inn)]
        return order[0], order[1]

    def _ai_next_batter(self, inn: InningsState) -> str:
        options = self.available_batters(inn)
        if inn.is_super_over:
            return _select_super_over_batters(options)[0].player_id
        over_number = inn.legal_balls // BALLS_PER_OVER + 1
        if over_number >= inn.death_start:
            finishers = [p for p in options if _bat_role(p) == "finisher"]
            if finishers and inn.wickets <= FINISHER_MAX_WICKETS_LOST + 2:
                return finishers[0].player_id
        return options[0].player_id

    def _ai_bowler(self, inn: InningsState) -> str:
        candidates = self.eligible_bowlers_now(inn)
        over_number = inn.legal_balls // BALLS_PER_OVER + 1
        power = 3.0 if over_number >= inn.death_start else 1.0
        weights = [max(10.0, _player_rating(p)) ** power for p in candidates]
        return self.ai_rng.choices(candidates, weights=weights, k=1)[0].player_id

    def _ai_delivery(self, inn: InningsState) -> str:
        options = [opt for opt in self.delivery_options() if not opt["disabled"]]
        if not options:
            options = self.delivery_options()
        weights = [
            AI_WIDE_DELIVERY_WEIGHT if opt["wide_risk_pct"] >= 15 else 1.0
            for opt in options
        ]
        return self.ai_rng.choices(options, weights=weights, k=1)[0]["code"]

    def _ai_shot(self, inn: InningsState) -> str:
        weights = dict(AI_SHOT_BASE_WEIGHTS)
        over_number = inn.legal_balls // BALLS_PER_OVER + 1
        aggression = 1.0
        if over_number <= inn.powerplay_end:
            aggression *= 1.25
        if over_number >= inn.death_start:
            aggression *= 1.6
        if inn.target is not None:
            runs_needed = max(0, inn.target - inn.runs)
            balls_left = max(1, inn.max_overs * BALLS_PER_OVER - inn.legal_balls)
            required = runs_needed * 6.0 / balls_left
            if required > 11:
                aggression *= 1.8
            elif required > 8.5:
                aggression *= 1.3
            elif required < 5.5:
                aggression *= 0.8
        wickets_in_hand = inn.max_wickets - inn.wickets
        if wickets_in_hand <= 2 and inn.target is None:
            aggression *= 0.75
        weights["4"] *= aggression
        weights["6"] *= aggression
        weights["0"] /= max(0.6, aggression**0.5)
        codes = list(weights)
        return self.ai_rng.choices(codes, weights=[weights[c] for c in codes], k=1)[0]

    def auto_decide(self) -> str:
        """Make the computer's choice for the current decision phase."""
        inn = self.current_innings
        phase = self.phase
        if phase == PHASE_TOSS_CALL:
            self.call_toss(self._ai_toss_call())
        elif phase == PHASE_TOSS_DECISION:
            self.decide_toss(self._ai_toss_decision())
        elif phase == PHASE_SELECT_OPENERS:
            assert inn is not None
            self.select_openers(*self._ai_openers(inn))
        elif phase == PHASE_SELECT_BOWLER:
            assert inn is not None
            self.select_bowler(self._ai_bowler(inn))
        elif phase == PHASE_SELECT_NEXT_BATTER:
            assert inn is not None
            self.select_next_batter(self._ai_next_batter(inn))
        else:
            raise ValueError("There is no decision to make right now.")
        return phase

    def step_ball(self) -> tuple[BallEvent | None, dict[str, Any]]:
        """Computer plays on until one delivery (including a wide) is bowled."""
        if self.completed:
            return None, {"match_completed": True}
        while not self.completed:
            inn = self.current_innings
            if self.phase == PHASE_BOWL_BALL:
                assert inn is not None
                event, transition = self.select_delivery(self._ai_delivery(inn))
                if event is not None:
                    return event, transition
            elif self.phase == PHASE_BAT_BALL:
                assert inn is not None
                return self.play_shot(self._ai_shot(inn))
            else:
                self.auto_decide()
        return None, {"match_completed": True}

    # -------------------------------------------------------- innings & result
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
                max_overs=self.total_overs,
                max_wickets=MAX_WICKETS_REGULAR,
                target=first.runs + 1,
                is_super_over=False,
            )
            self.regular_innings.append(second)
            self._log(
                "info",
                f"Innings break. {first.batting_team_name} scored {first.runs}/{first.wickets}. "
                f"{second.batting_team_name} need {first.runs + 1} to win.",
            )
            self._begin_innings(second)
            return {
                "innings_completed": True,
                "match_completed": False,
                "super_over_started": False,
            }

        # 2. Regular 2nd innings finished -> result or Super Over
        if (
            len(self.regular_innings) == 2
            and self.regular_innings[1].completed
            and not self.super_over_innings
        ):
            first, second = self.regular_innings
            if first.runs != second.runs:
                self._finalize_match(tied_regular=False)
                return {
                    "innings_completed": True,
                    "match_completed": True,
                    "super_over_started": False,
                }
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
            self.super_over_innings.append(so_first)
            self._log("info", "Scores are level! It goes to a Super Over.")
            self._begin_innings(so_first)
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
                self.super_over_innings.append(so_second)
                self._begin_innings(so_second)
                return {
                    "innings_completed": True,
                    "match_completed": False,
                    "super_over_started": False,
                }

            so_a = self.super_over_innings[-2]
            so_b = self.super_over_innings[-1]
            if so_a.runs != so_b.runs or self.super_over_round >= MAX_SUPER_OVERS_SAFETY_CAP:
                self._finalize_match(tied_regular=True)
                return {
                    "innings_completed": True,
                    "match_completed": True,
                    "super_over_started": False,
                }

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
            self.super_over_innings.append(next_so)
            self._log("info", "The Super Over is tied too. Another Super Over!")
            self._begin_innings(next_so)
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
                margin = (
                    f"{second.batting_team_name} won by {wickets_left} "
                    f"wicket{'s' if wickets_left != 1 else ''}"
                )
        else:
            so_a, so_b = self.super_over_innings[-2], self.super_over_innings[-1]
            if so_a.runs > so_b.runs:
                winner_id, loser_id = so_a.batting_team_id, so_b.batting_team_id
            elif so_b.runs > so_a.runs:
                winner_id, loser_id = so_b.batting_team_id, so_a.batting_team_id
            else:
                # Safety cap fallback: more boundaries in the regular innings
                a_boundaries = sum(c.fours + c.sixes for c in first.batting_card.values())
                b_boundaries = sum(c.fours + c.sixes for c in second.batting_card.values())
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
        self.phase = PHASE_COMPLETED
        self.pending_delivery = None
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
        self._log("info", f"Match over. {margin}.")

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
                entry["points"] += (
                    bcard.runs * 1.2 + bcard.sixes * 2.0 + bcard.fours * 1.0
                ) * bat_bonus

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

    # ---------------------------------------------------------------- snapshot
    def live_summary(self) -> dict[str, Any]:
        """Small summary for fixture cards."""
        inn = self.current_innings
        if inn is None:
            return {
                "batting_team_id": None,
                "batting_team_name": None,
                "runs": 0,
                "wickets": 0,
                "overs": "0.0",
                "target": None,
                "phase": self.phase,
            }
        return {
            "batting_team_id": inn.batting_team_id,
            "batting_team_name": inn.batting_team_name,
            "runs": inn.runs,
            "wickets": inn.wickets,
            "overs": inn.overs_display,
            "target": inn.target,
            "phase": self.phase,
        }

    def _awaiting_text(self) -> str:
        actor = self.actor_team_id()
        name = self.teams[actor]["name"] if actor else ""
        return {
            PHASE_TOSS_CALL: f"{name} to call the toss",
            PHASE_TOSS_DECISION: f"{name} to choose bat or bowl",
            PHASE_SELECT_OPENERS: f"{name} to pick the openers",
            PHASE_SELECT_BOWLER: f"{name} to pick the bowler",
            PHASE_BOWL_BALL: f"{name} to pick a delivery",
            PHASE_BAT_BALL: f"{name} to pick a shot",
            PHASE_SELECT_NEXT_BATTER: f"{name} to send in the next batter",
            PHASE_COMPLETED: "Match completed",
        }.get(self.phase, "")

    def snapshot(self) -> dict[str, Any]:
        inn = self.current_innings
        phase = self.phase
        recent_balls: list[dict[str, Any]] = []
        this_over: list[dict[str, Any]] = []
        striker_card = non_striker_card = bowler_card = None
        striker = bowler = None
        if inn is not None:
            recent_balls = [ev.to_public_dict() for ev in inn.deliveries[-6:]]
            over_index = inn.legal_balls // BALLS_PER_OVER
            current = [ev for ev in inn.deliveries if ev.over_number == over_index]
            if phase in (PHASE_SELECT_BOWLER,) and inn.current_over_legal_balls == 0:
                current = []
            this_over = [ev.to_public_dict() for ev in current]
            if inn.striker_id:
                striker_card = inn.batting_card.get(inn.striker_id)
                striker = self.teams[inn.batting_team_id]["by_id"].get(inn.striker_id)
            if inn.non_striker_id:
                non_striker_card = inn.batting_card.get(inn.non_striker_id)
            if inn.current_bowler_id:
                bowler_card = inn.bowling_card.get(inn.current_bowler_id)
                bowler = self.teams[inn.bowling_team_id]["by_id"].get(inn.current_bowler_id)

        req_rr = runs_needed = balls_remaining = None
        if inn is not None and inn.target is not None and not inn.completed:
            runs_needed = max(0, inn.target - inn.runs)
            balls_remaining = max(0, inn.max_overs * BALLS_PER_OVER - inn.legal_balls)
            if balls_remaining > 0:
                req_rr = round((runs_needed * 6.0) / balls_remaining, 2)

        available_batters: list[dict[str, Any]] = []
        available_bowlers: list[dict[str, Any]] = []
        if inn is not None and phase in (PHASE_SELECT_OPENERS, PHASE_SELECT_NEXT_BATTER):
            available_batters = [_player_public(p) for p in self.available_batters(inn)]
        if inn is not None and phase == PHASE_SELECT_BOWLER:
            allowed = {p.player_id for p in self.eligible_bowlers_now(inn)}
            for p in self._bowling_pool(inn):
                overs_used = inn.bowler_overs.get(p.player_id, 0)
                reason = None
                if p.player_id not in allowed:
                    reason = (
                        "Max overs bowled"
                        if overs_used >= inn.max_bowler_overs
                        else "Bowled the last over"
                    )
                available_bowlers.append(
                    {
                        **_player_public(p),
                        "bowling_style": BOWLING_STYLE_LABELS[bowler_style(p)],
                        "overs_bowled": overs_used,
                        "overs_max": inn.max_bowler_overs,
                        "eligible": p.player_id in allowed,
                        "reason": reason,
                    }
                )

        feed = [
            {"kind": e["kind"], "text": e["text"], "sequence": e["sequence"]}
            for e in self.commentary_log[-COMMENTARY_FEED_SIZE:]
        ]
        feed.reverse()

        toss = {
            "caller_id": self.toss_caller_id,
            "caller_name": self.teams[self.toss_caller_id]["name"],
            "call": self.toss_call,
            "coin": self.toss_coin,
            "winner_id": self.toss_winner_id,
            "winner_name": self.teams[self.toss_winner_id]["name"] if self.toss_winner_id else None,
            "decision": self.toss_decision,
        }
        last_commentary = (
            self.commentary_log[-1]["text"] if self.commentary_log else "Match ready to begin."
        )
        return {
            "match_id": self.match_id,
            "revision": self.revision,
            "completed": self.completed,
            "phase": phase,
            "awaiting": self._awaiting_text(),
            "actor_team_id": self.actor_team_id(),
            "team_a_id": self.team_a_id,
            "team_b_id": self.team_b_id,
            "team_a_name": self.teams[self.team_a_id]["name"],
            "team_b_name": self.teams[self.team_b_id]["name"],
            "total_overs": self.total_overs,
            "powerplay_end": self.rules["powerplay_end"],
            "death_start": self.rules["death_start"],
            "max_bowler_overs": self.rules["max_bowler_overs"],
            "toss": toss,
            "innings_number": inn.innings_number if inn else 0,
            "is_super_over": bool(inn and inn.is_super_over),
            "super_over_round": self.super_over_round,
            "batting_team_id": inn.batting_team_id if inn else None,
            "batting_team_name": inn.batting_team_name if inn else None,
            "bowling_team_id": inn.bowling_team_id if inn else None,
            "bowling_team_name": inn.bowling_team_name if inn else None,
            "runs": inn.runs if inn else 0,
            "wickets": inn.wickets if inn else 0,
            "overs": inn.overs_display if inn else "0.0",
            "legal_balls": inn.legal_balls if inn else 0,
            "current_over_runs": inn.current_over_runs if inn else 0,
            "target": inn.target if inn else None,
            "runs_needed": runs_needed,
            "balls_remaining": balls_remaining,
            "required_run_rate": req_rr,
            "momentum": round(inn.momentum, 2) if inn else 0.0,
            "striker": striker_card.to_public_dict() if striker_card else None,
            "non_striker": non_striker_card.to_public_dict() if non_striker_card else None,
            "bowler": bowler_card.to_public_dict() if bowler_card else None,
            "bowler_style": BOWLING_STYLE_LABELS[bowler_style(bowler)] if bowler else None,
            "striker_is_elite": bool(striker and striker.elite),
            "bowler_is_elite": bool(bowler and bowler.elite),
            "over_boost_used": bool(inn and inn.over_boost_used),
            "pending_delivery": (
                {"code": self.pending_delivery.code, "label": self.pending_delivery.label}
                if self.pending_delivery
                else None
            ),
            "available_batters": available_batters,
            "available_bowlers": available_bowlers,
            "available_deliveries": (
                self.delivery_options() if phase == PHASE_BOWL_BALL else []
            ),
            "shot_options": [{"code": c, "label": SHOT_LABELS[c]} for c in SHOT_CODES],
            "last_six_balls": recent_balls,
            "this_over": this_over,
            "commentary_feed": feed,
            "last_commentary": last_commentary,
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
    total_overs: int = DEFAULT_TOURNAMENT_OVERS,
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
        total_overs=total_overs,
    )
    return sim.run_to_completion()
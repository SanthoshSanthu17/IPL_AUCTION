"""Data structures for ball-by-ball T20 match simulation and scorecards."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BallEvent:
    match_id: str
    innings_number: int
    over_number: int
    ball_number: int
    batter_id: str
    batter_name: str
    bowler_id: str
    bowler_name: str
    runs_off_bat: int
    extras: int
    extra_type: str | None
    total_runs: int
    is_wicket: bool
    dismissed_player_id: str | None
    dismissed_player_name: str | None
    dismissal_type: str | None
    is_legal: bool
    sequence: int
    score_after: int
    wickets_after: int
    overs_after: str
    commentary: str
    is_super_over: bool = False
    delivery_code: str | None = None
    delivery_label: str | None = None
    shot_code: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "innings_number": self.innings_number,
            "over_number": self.over_number,
            "ball_number": self.ball_number,
            "batter_id": self.batter_id,
            "batter_name": self.batter_name,
            "bowler_id": self.bowler_id,
            "bowler_name": self.bowler_name,
            "runs_off_bat": self.runs_off_bat,
            "extras": self.extras,
            "extra_type": self.extra_type,
            "total_runs": self.total_runs,
            "is_wicket": self.is_wicket,
            "dismissed_player_id": self.dismissed_player_id,
            "dismissed_player_name": self.dismissed_player_name,
            "dismissal_type": self.dismissal_type,
            "is_legal": self.is_legal,
            "sequence": self.sequence,
            "score_after": self.score_after,
            "wickets_after": self.wickets_after,
            "overs_after": self.overs_after,
            "commentary": self.commentary,
            "is_super_over": self.is_super_over,
            "delivery_code": self.delivery_code,
            "delivery_label": self.delivery_label,
            "shot_code": self.shot_code,
        }


@dataclass
class BatterScorecard:
    player_id: str
    player_name: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    dismissal: str = "not out"
    batted: bool = False

    @property
    def strike_rate(self) -> float:
        if self.balls <= 0:
            return 0.0
        return round((self.runs / self.balls) * 100.0, 2)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "runs": self.runs,
            "balls": self.balls,
            "fours": self.fours,
            "sixes": self.sixes,
            "strike_rate": self.strike_rate,
            "dismissal": self.dismissal if self.batted else "did not bat",
            "batted": self.batted,
        }


@dataclass
class BowlerScorecard:
    player_id: str
    player_name: str
    legal_balls: int = 0
    maidens: int = 0
    runs_conceded: int = 0
    wickets: int = 0
    wides: int = 0
    dots: int = 0

    @property
    def overs_display(self) -> str:
        completed = self.legal_balls // 6
        rem = self.legal_balls % 6
        return f"{completed}.{rem}"

    @property
    def economy(self) -> float:
        if self.legal_balls <= 0:
            return 0.0
        overs_float = self.legal_balls / 6.0
        return round(self.runs_conceded / overs_float, 2)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "overs": self.overs_display,
            "legal_balls": self.legal_balls,
            "maidens": self.maidens,
            "runs_conceded": self.runs_conceded,
            "wickets": self.wickets,
            "wides": self.wides,
            "dots": self.dots,
            "economy": self.economy,
        }


@dataclass
class InningsState:
    innings_number: int
    batting_team_id: str
    batting_team_name: str
    bowling_team_id: str
    bowling_team_name: str
    max_overs: int
    max_wickets: int
    target: int | None = None
    is_super_over: bool = False
    runs: int = 0
    wickets: int = 0
    legal_balls: int = 0
    extras_total: int = 0
    wides: int = 0
    noballs: int = 0
    striker_id: str | None = None
    non_striker_id: str | None = None
    next_batter_index: int = 0
    current_bowler_id: str | None = None
    last_bowler_id: str | None = None
    current_over_runs: int = 0
    current_over_legal_balls: int = 0
    completed: bool = False
    all_out: bool = False
    powerplay_end: int = 0
    death_start: int = 1
    max_bowler_overs: int = 4
    momentum: float = 0.0
    opener_ids: list[str] = field(default_factory=list)
    pending_over_end: bool = False
    bowler_overs: dict[str, int] = field(default_factory=dict)
    over_delivery_counts: dict[str, int] = field(default_factory=dict)
    over_boost_used: bool = False
    batting_order: list[str] = field(default_factory=list)
    batting_card: dict[str, BatterScorecard] = field(default_factory=dict)
    bowling_card: dict[str, BowlerScorecard] = field(default_factory=dict)
    fall_of_wickets: list[dict[str, Any]] = field(default_factory=list)
    deliveries: list[BallEvent] = field(default_factory=list)

    @property
    def overs_display(self) -> str:
        return f"{self.legal_balls // 6}.{self.legal_balls % 6}"

    @property
    def nrr_overs(self) -> float:
        if self.all_out and not self.is_super_over:
            return float(self.max_overs)
        return self.legal_balls / 6.0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "innings_number": self.innings_number,
            "batting_team_id": self.batting_team_id,
            "batting_team_name": self.batting_team_name,
            "bowling_team_id": self.bowling_team_id,
            "bowling_team_name": self.bowling_team_name,
            "is_super_over": self.is_super_over,
            "runs": self.runs,
            "wickets": self.wickets,
            "overs": self.overs_display,
            "legal_balls": self.legal_balls,
            "target": self.target,
            "completed": self.completed,
            "all_out": self.all_out,
            "extras": {
                "total": self.extras_total,
                "wides": self.wides,
                "noballs": self.noballs,
            },
            "batting": [
                self.batting_card[pid].to_public_dict()
                for pid in self.batting_order
                if pid in self.batting_card
                and (self.batting_card[pid].batted or not self.is_super_over)
            ],
            "bowling": [
                card.to_public_dict()
                for card in self.bowling_card.values()
                if card.legal_balls > 0 or card.runs_conceded > 0
            ],
            "fall_of_wickets": list(self.fall_of_wickets),
        }


@dataclass
class MatchResult:
    match_id: str
    winner_team_id: str
    winner_team_name: str
    loser_team_id: str
    loser_team_name: str
    margin: str
    tied_regular: bool
    super_overs_played: int
    player_of_the_match: dict[str, Any]
    innings: list[InningsState]
    super_over_innings: list[InningsState] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "winner_team_id": self.winner_team_id,
            "winner_team_name": self.winner_team_name,
            "loser_team_id": self.loser_team_id,
            "loser_team_name": self.loser_team_name,
            "margin": self.margin,
            "tied_regular": self.tied_regular,
            "super_overs_played": self.super_overs_played,
            "player_of_the_match": dict(self.player_of_the_match),
            "innings": [inn.to_public_dict() for inn in self.innings],
            "super_over_innings": [inn.to_public_dict() for inn in self.super_over_innings],
        }
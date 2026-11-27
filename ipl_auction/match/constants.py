"""Tunable constants for the T20 match simulation engine."""

MAX_OVERS_REGULAR = 20
BALLS_PER_OVER = 6
MAX_WICKETS_REGULAR = 10

MAX_OVERS_PER_BOWLER = 4
MIN_BOWLING_OPTIONS_FALLBACK = 5

SUPER_OVER_OVERS = 1
SUPER_OVER_MAX_WICKETS = 2
MAX_SUPER_OVERS_SAFETY_CAP = 12

# Batting order priority based on bat_role
BAT_ROLE_PRIORITY: dict[str, int] = {
    "opener": 1,
    "anchor": 2,
    "middle": 3,
    "finisher": 4,
}
BOWLER_OR_AR_BAT_PRIORITY = 5
FALLBACK_BAT_PRIORITY = 6

# Default hidden rating when missing
DEFAULT_PLAYER_RATING = 50.0

# Base probability weights for legal deliveries [dot, 1, 2, 3, 4, 6, wicket]
BASE_OUTCOME_WEIGHTS: dict[str, float] = {
    "dot": 34.0,
    "1": 36.0,
    "2": 7.5,
    "3": 0.8,
    "4": 11.2,
    "6": 5.5,
    "wicket": 5.0,
}

# Extra delivery probabilities before legal ball
WIDE_PROBABILITY_BASE = 0.035
NO_BALL_PROBABILITY_BASE = 0.012

# Dismissal types for regular deliveries (excluding run-out)
BOWLER_DISMISSAL_TYPES: tuple[str, ...] = (
    "caught",
    "bowled",
    "lbw",
    "caught & bowled",
    "stumped",
)
RUN_OUT_SHARE_OF_WICKETS = 0.12

# Match speed delays (seconds per ball)
MATCH_SPEED_DELAYS: dict[str, float] = {
    "normal": 1.2,
    "fast": 0.25,
    "skip": 0.0,
}
DEFAULT_MATCH_SPEED = "normal"
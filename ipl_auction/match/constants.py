"""Tunable constants for the interactive T20 match engine."""

# ---- Match length ---------------------------------------------------------
TOURNAMENT_OVERS_OPTIONS: tuple[int, ...] = (5, 10, 20)
DEFAULT_TOURNAMENT_OVERS = 20

MAX_OVERS_REGULAR = 20  # fallback when no overs value is given
BALLS_PER_OVER = 6
MAX_WICKETS_REGULAR = 10

# Over numbers below are 1-based (over 1 is the first over of the innings).
# powerplay_end: last Powerplay over. death_start: first death over.
OVERS_RULES: dict[int, dict[str, int]] = {
    5: {"powerplay_end": 2, "death_start": 4, "max_bowler_overs": 1},
    10: {"powerplay_end": 3, "death_start": 8, "max_bowler_overs": 2},
    20: {"powerplay_end": 6, "death_start": 16, "max_bowler_overs": 4},
}
SUPER_OVER_RULES: dict[str, int] = {
    "powerplay_end": 0,
    "death_start": 1,
    "max_bowler_overs": 1,
}
# Share of the innings (from the end) that counts as "late" for finishers.
LATE_INNINGS_FRACTION = 0.25

SUPER_OVER_OVERS = 1
SUPER_OVER_MAX_WICKETS = 2
MAX_SUPER_OVERS_SAFETY_CAP = 12

# ---- Batting order priority based on bat_role ----------------------------
BAT_ROLE_PRIORITY: dict[str, int] = {
    "opener": 1,
    "anchor": 2,
    "middle": 3,
    "finisher": 4,
}
BOWLER_OR_AR_BAT_PRIORITY = 5
FALLBACK_BAT_PRIORITY = 6

# Default hidden rating when missing (ratings are never sent to browsers)
DEFAULT_PLAYER_RATING = 50.0

# ---- Phases of the match state machine -----------------------------------
PHASE_TOSS_CALL = "toss_call"
PHASE_TOSS_DECISION = "toss_decision"
PHASE_SELECT_OPENERS = "select_openers"
PHASE_SELECT_BOWLER = "select_bowler"
PHASE_BOWL_BALL = "bowl_ball"
PHASE_BAT_BALL = "bat_ball"
PHASE_SELECT_NEXT_BATTER = "select_next_batter"
PHASE_COMPLETED = "completed"

# ---- Deliveries -----------------------------------------------------------
MAX_SAME_DELIVERY_PER_OVER = 2

# ---- Shots ----------------------------------------------------------------
SHOT_CODES: tuple[str, ...] = ("0", "1", "2", "4", "6")
SHOT_LABELS: dict[str, str] = {
    "0": "Defend",
    "1": "Single",
    "2": "Two",
    "4": "Four",
    "6": "Six",
}
SHOT_BASE_WICKET: dict[int, float] = {0: 0.025, 1: 0.032, 2: 0.055, 4: 0.110, 6: 0.170}
SHOT_BASE_SUCCESS: dict[int, float] = {0: 0.85, 1: 0.78, 2: 0.65, 4: 0.50, 6: 0.38}

SKILL_DIFF_DIVISOR = 250.0
WICKET_PROB_MIN = 0.015
WICKET_PROB_MAX = 0.45
SUCCESS_PROB_MIN = 0.15
SUCCESS_PROB_MAX = 0.92

SIX_JUST_SHORT_FOUR_CHANCE = 0.22  # failed six that still reaches the rope
FAILED_BOUNDARY_SINGLE_CHANCE = 0.45  # failed attack that becomes a mistimed single

# ---- Momentum -------------------------------------------------------------
MOMENTUM_MAX = 1.0
MOMENTUM_GAIN_ROTATE = 0.18  # per 1 or 2
MOMENTUM_LOSS_FOUR = 0.10
MOMENTUM_LOSS_SIX = 0.15
MOMENTUM_LOSS_WICKET = 0.35
MOMENTUM_SUCCESS_BONUS = 0.08
MOMENTUM_WICKET_RELIEF = 0.03

# ---- Tactical modifiers ---------------------------------------------------
OUT_OF_POSITION_OPENER_ROLES: frozenset[str] = frozenset({"middle", "finisher"})
OPENER_PENALTY_WICKET = 0.045
OPENER_PENALTY_RUN = -0.08

FINISHER_PROMOTION_BONUS = 0.07
FINISHER_MAX_WICKETS_LOST = 2

DEATH_SPIN_RUN_BONUS = 0.09
DEATH_SPIN_WICKET_MOD = -0.02

RHYTHM_WICKET_PER_WICKET = 0.015
RHYTHM_WICKET_CAP = 0.045
RHYTHM_RUN_PER_WICKET = 0.03
RHYTHM_RUN_CAP = 0.09

# ---- Dismissals -----------------------------------------------------------
DISMISSAL_TYPES: tuple[str, ...] = (
    "caught",
    "bowled",
    "lbw",
    "caught behind",
    "stumped",
    "run out",
)
DISMISSAL_WEIGHTS_PACE: tuple[float, ...] = (0.48, 0.22, 0.14, 0.12, 0.01, 0.03)
DISMISSAL_WEIGHTS_SPIN: tuple[float, ...] = (0.42, 0.18, 0.16, 0.06, 0.14, 0.04)

# ---- Computer-controlled choices (AI fallback) ---------------------------
AI_SHOT_BASE_WEIGHTS: dict[str, float] = {
    "0": 0.4,
    "1": 2.0,
    "2": 2.8,
    "4": 1.5,
    "6": 0.6,
}
AI_WIDE_DELIVERY_WEIGHT = 0.35  # high-risk wide variations are chosen less often
AI_TOSS_BOWL_FIRST_CHANCE = 0.6

# ---- Match speed (auto-play) delays in seconds per ball -------------------
MATCH_SPEED_DELAYS: dict[str, float] = {
    "manual": 0.0,
    "normal": 1.2,
    "fast": 0.25,
    "skip": 0.0,
}
DEFAULT_MATCH_SPEED = "manual"
AUTOPLAY_SPEEDS: frozenset[str] = frozenset({"normal", "fast"})

# Commentary feed length kept for snapshots
COMMENTARY_FEED_SIZE = 14
"""Configuration constants for the post-auction T20 tournament."""

MIN_TOURNAMENT_TEAMS = 2
MAX_TOURNAMENT_TEAMS = 10

# League & Playoff rules
LEAGUE_MIN_TEAMS = 4
DIRECT_PLAYOFF_TOP_SEEDS = 2  # Rank 1 and Rank 2 skip league stage when >= 4 teams
LEAGUE_MATCHES_PER_TEAM = 2
LEAGUE_ROUNDS = 2

POINTS_WIN = 2
POINTS_LOSS = 0

# Stage identifiers
STAGE_LEAGUE_R1 = "League Round 1"
STAGE_LEAGUE_R2 = "League Round 2"
STAGE_ELIMINATOR_PLAYIN = "League Play-In"
STAGE_SEMI_FINAL = "Semi-Final"
STAGE_QUALIFIER_1 = "Qualifier 1"
STAGE_ELIMINATOR = "Eliminator"
STAGE_QUALIFIER_2 = "Qualifier 2"
STAGE_FINAL = "Final"

# Match statuses
STATUS_UPCOMING = "upcoming"
STATUS_LIVE = "live"
STATUS_COMPLETED = "completed"

# Past winners integration
DEFAULT_TOURNAMENT_SEASON = 2026
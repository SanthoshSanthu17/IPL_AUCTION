from dataclasses import dataclass, field


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    role: str
    nationality: str
    base_price_crore: float
    matches: int | None = None
    runs: int | None = None
    wickets: int | None = None
    batting_average: float | None = None
    strike_rate: float | None = None
    economy: float | None = None
    image_url: str | None = None
    elite: bool = False
    # Batting slot style: opener, anchor, middle or finisher (public).
    bat_role: str | None = None
    # Bowling style: pace or spin (public).
    bowl_type: str | None = None
    # Hidden rating (0-100). Never send this to browsers before the XI lock,
    # and never send individual player ratings at all.
    rating: float | None = field(default=None, repr=False, compare=False)

    @property
    def is_overseas(self) -> bool:
        return self.nationality.strip().casefold() != "india"

    @property
    def has_ipl_stats(self) -> bool:
        return any(
            value is not None
            for value in (
                self.matches,
                self.runs,
                self.wickets,
                self.batting_average,
                self.strike_rate,
                self.economy,
            )
        )
from dataclasses import dataclass


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
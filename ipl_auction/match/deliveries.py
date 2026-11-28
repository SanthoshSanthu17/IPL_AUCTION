"""Delivery variations per bowling style for the interactive match engine.

Each delivery has a wide risk, a wicket modifier (added to the batter's
dismissal chance) and a run modifier (added to the batter's shot-success
chance, so negative values suppress scoring). Pace bowlers only ever see pace
deliveries; spinners see the set that matches their sub-style.
"""

from dataclasses import dataclass

from ipl_auction.players.models import Player

STYLE_PACE = "pace"
STYLE_LEG_SPIN = "leg_spin"
STYLE_LEFT_ARM_ORTHODOX = "left_arm_orthodox"
STYLE_OFF_SPIN = "off_spin"

STYLE_LABELS = {
    STYLE_PACE: "Pace",
    STYLE_LEG_SPIN: "Leg-spin",
    STYLE_LEFT_ARM_ORTHODOX: "Left-arm orthodox",
    STYLE_OFF_SPIN: "Off-spin",
}


@dataclass(frozen=True)
class Delivery:
    code: str
    label: str
    wide_risk: float
    wicket_mod: float
    run_mod: float


def _d(code: str, label: str, wide: float, wicket: float, run: float) -> Delivery:
    return Delivery(code, label, wide, wicket, run)


PACE_DELIVERIES: tuple[Delivery, ...] = (
    _d("yorker", "Yorker", 0.03, 0.035, -0.22),
    _d("wide_yorker", "Wide Yorker", 0.21, 0.015, -0.28),
    _d("bouncer", "Bouncer", 0.07, 0.030, -0.05),
    _d("inswinger", "Inswinger", 0.04, 0.028, -0.08),
    _d("outswinger", "Outswinger", 0.04, 0.026, -0.08),
    _d("late_swing", "Late Swing", 0.05, 0.032, -0.10),
    _d("knuckle_ball", "Knuckle Ball", 0.04, 0.024, -0.14),
    _d("off_cutter", "Off-Cutter", 0.04, 0.020, -0.12),
)

LEG_SPIN_DELIVERIES: tuple[Delivery, ...] = (
    _d("leg_break", "Stock Leg-Break", 0.03, 0.022, -0.04),
    _d("googly", "Googly", 0.05, 0.034, -0.10),
    _d("flipper", "Flipper", 0.04, 0.026, -0.14),
    _d("slider", "Slider", 0.03, 0.020, -0.12),
    _d("wide_googly", "Wide Googly", 0.21, 0.022, -0.26),
    _d("top_spinner", "Top-Spinner", 0.03, 0.024, -0.08),
)

LEFT_ARM_ORTHODOX_DELIVERIES: tuple[Delivery, ...] = (
    _d("orthodox_stock", "Orthodox Stock", 0.02, 0.020, -0.08),
    _d("la_arm_ball", "Arm Ball", 0.03, 0.026, -0.12),
    _d("undercutter", "Undercutter", 0.03, 0.022, -0.14),
    _d("flighted_loop", "Flighted Loop", 0.04, 0.025, -0.05),
    _d("wide_flatter", "Wide Flatter", 0.20, 0.018, -0.25),
    _d("la_quicker_dart", "Quicker Dart", 0.03, 0.016, -0.15),
)

OFF_SPIN_DELIVERIES: tuple[Delivery, ...] = (
    _d("off_break", "Stock Off-Break", 0.02, 0.018, -0.06),
    _d("doosra", "Doosra", 0.05, 0.030, -0.10),
    _d("carrom_ball", "Carrom Ball", 0.05, 0.028, -0.12),
    _d("arm_ball", "Arm Ball", 0.03, 0.022, -0.10),
    _d("wide_flighted", "Wide Flighted", 0.20, 0.020, -0.25),
    _d("quicker_dart", "Quicker Dart", 0.03, 0.015, -0.14),
)

DELIVERIES_BY_STYLE: dict[str, tuple[Delivery, ...]] = {
    STYLE_PACE: PACE_DELIVERIES,
    STYLE_LEG_SPIN: LEG_SPIN_DELIVERIES,
    STYLE_LEFT_ARM_ORTHODOX: LEFT_ARM_ORTHODOX_DELIVERIES,
    STYLE_OFF_SPIN: OFF_SPIN_DELIVERIES,
}

KNOWN_LEG_SPINNERS: frozenset[str] = frozenset(
    {
        "rashid khan", "yuzvendra chahal", "kuldeep yadav", "wanindu hasaranga",
        "ravi bishnoi", "adam zampa", "rahul chahar", "piyush chawla",
        "noor ahmad", "varun chakravarthy", "suyash sharma", "karn sharma",
        "amit mishra", "mayank markande", "murugan ashwin", "tabraiz shamsi",
        "adil rashid", "ish sodhi", "maheesh theekshana",
    }
)

KNOWN_LEFT_ARM_ORTHODOX: frozenset[str] = frozenset(
    {
        "ravindra jadeja", "axar patel", "krunal pandya", "mitchell santner",
        "keshav maharaj", "shakib al hasan", "r sai kishore", "sai kishore",
        "shahbaz ahmed", "harpreet brar", "left arm", "manimaran siddharth",
        "kumar kartikeya", "abhishek sharma", "ankul roy", "swapnil singh",
    }
)

# Every delivery code that exists, for validation.
ALL_DELIVERY_CODES: frozenset[str] = frozenset(
    d.code for group in DELIVERIES_BY_STYLE.values() for d in group
)


def is_spin_bowler(player: Player) -> bool:
    return (player.bowl_type or "").strip().casefold() == "spin"


def _name_matches(name: str, known: frozenset[str]) -> bool:
    if name in known:
        return True
    return any(" " in key and key in name for key in known)


def classify_spin_style(player: Player) -> str:
    """Return leg_spin, left_arm_orthodox or off_spin (the default) for a spinner."""
    name = " ".join(player.name.casefold().split())
    if _name_matches(name, KNOWN_LEG_SPINNERS):
        return STYLE_LEG_SPIN
    if _name_matches(name, KNOWN_LEFT_ARM_ORTHODOX):
        return STYLE_LEFT_ARM_ORTHODOX
    return STYLE_OFF_SPIN


def bowler_style(player: Player) -> str:
    """Pace for anyone who is not a spinner, otherwise the spin sub-style."""
    if is_spin_bowler(player):
        return classify_spin_style(player)
    return STYLE_PACE


def deliveries_for(player: Player) -> tuple[Delivery, ...]:
    return DELIVERIES_BY_STYLE[bowler_style(player)]


def get_delivery(player: Player, code: str) -> Delivery | None:
    for delivery in deliveries_for(player):
        if delivery.code == code:
            return delivery
    return None
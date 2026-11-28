"""Varied ball-by-ball commentary pools so consecutive balls never repeat."""

import random
from typing import Any

WIDE = [
    "{bowler} sprays the {delivery} way down the leg side. Wide.",
    "Too much movement! {bowler}'s {delivery} drifts outside the tramlines. Wide called.",
    "{bowler} tries the {delivery} and loses the line completely. Wide.",
    "Umpire stretches both arms. The {delivery} from {bowler} was never in play. Wide.",
    "{striker} leaves it alone and the umpire signals wide. The {delivery} was a touch ambitious.",
    "Risk taken, risk punished: {bowler}'s {delivery} is too wide. Extra run.",
]

SIX = [
    "{striker} swings hard at the {delivery} and sends it deep into the stands. SIX!",
    "SIX! {striker} clears the rope with ease off {bowler}.",
    "Absolutely smashed! {striker} launches {bowler} over long-on for a maximum.",
    "{striker} stays leg side and slog-sweeps the {delivery} out of the ground. Huge SIX!",
    "That is gone! {striker} picks the {delivery} early and deposits it over midwicket.",
    "{bowler} gets the length wrong and {striker} makes him pay. Into the crowd for SIX.",
]

FOUR = [
    "FOUR! {striker} times the {delivery} perfectly through the covers.",
    "{striker} pierces the gap and the ball races to the fence. FOUR.",
    "Cracked away! {bowler} drops short and {striker} pulls it for FOUR.",
    "{striker} opens the face and the {delivery} runs away past third man. FOUR.",
    "No need to run. {striker} drives {bowler} straight back down the ground. FOUR!",
    "Beautiful timing from {striker}. The {delivery} is dispatched to the boundary.",
]

TWO = [
    "{striker} places the {delivery} into the gap and comes back for the second. Two runs.",
    "Good running! {striker} pushes {bowler} into the deep and they get two.",
    "{striker} finds the gap at midwicket. They turn sharply and complete two.",
    "Two more. {striker} works the {delivery} away and keeps the scoreboard moving.",
]

ONE = [
    "{striker} nudges the {delivery} into the leg side for a quick single.",
    "Single taken. {striker} tucks {bowler} off the pads.",
    "{striker} drops it at his feet and scampers through for one.",
    "Mistimed but safe. {striker} squeezes the {delivery} out for a single.",
    "{striker} pushes to cover and the batters rotate the strike.",
    "Worked away for one by {striker}. The pair keep the scoreboard ticking.",
]

DOT = [
    "{bowler} nails the {delivery}. {striker} can only defend. Dot ball.",
    "Dot ball. {striker} pushes forward and finds the fielder.",
    "{striker} swings and misses the {delivery}! Beaten all ends up.",
    "Tight line from {bowler}. {striker} has no room and it is a dot.",
    "{striker} looks for the big hit but the {delivery} is too good. No run.",
    "Straight to the man in the ring. Dot ball, and the pressure builds on {striker}.",
    "{striker} blocks the {delivery} out. Nothing doing for the batters.",
]

WICKET: dict[str, list[str]] = {
    "caught": [
        "OUT! {striker} goes after the {delivery} and finds the fielder in the deep. {bowler} strikes!",
        "Caught! {striker} mistimes the {delivery} and the simple catch is taken.",
        "WICKET! {bowler} lures {striker} into the big shot and the fielder holds on.",
        "{striker} skies it and it lands safely in the hands. {bowler} gets his man.",
    ],
    "bowled": [
        "BOWLED! The {delivery} from {bowler} sneaks through and the stumps are shattered. {striker} has to go.",
        "Timber! {striker} is clean bowled by a beauty of a {delivery}.",
        "WICKET! {striker} plays the wrong line and {bowler} hits the top of off.",
        "Through the gate! {bowler}'s {delivery} rattles the stumps.",
    ],
    "lbw": [
        "LBW! {striker} is trapped in front by the {delivery} and the finger goes up.",
        "Plumb in front! {bowler} appeals and {striker} is given out leg before.",
        "{striker} misses the sweep, the ball hits the pad. Out LBW to {bowler}.",
    ],
    "caught behind": [
        "Edged and taken! {striker} feathers the {delivery} through to the keeper.",
        "CAUGHT BEHIND! The faintest of nicks off {bowler}'s {delivery} and the keeper gloves it.",
        "{striker} gets a thin edge and the wicketkeeper pouches it. {bowler} celebrates.",
    ],
    "stumped": [
        "STUMPED! {striker} charges at the {delivery} and the keeper whips off the bails.",
        "Beaten in flight! {striker} is out of the crease and the stumping is completed.",
        "Lightning hands behind the stumps. {striker} is stumped off {bowler}.",
    ],
    "run out": [
        "RUN OUT! Mix-up in the middle and {striker} is short of the crease.",
        "Direct hit! {striker} is run out going for a risky run.",
        "Terrible communication. {striker} is caught well short. Run out!",
    ],
}

CONTEXT_DEATH = [
    "Death overs, every ball counts.",
    "The pressure is huge at this stage.",
    "Crucial moments in the final overs.",
]
CONTEXT_POWERPLAY = [
    "Fielding restrictions are on.",
    "The new ball is doing a bit in the Powerplay.",
    "Powerplay overs are precious.",
]


def _pool_for(kind: str, dismissal: str | None) -> list[str]:
    if kind == "wicket":
        return WICKET.get(dismissal or "caught", WICKET["caught"])
    return {
        "wide": WIDE,
        "6": SIX,
        "4": FOUR,
        "2": TWO,
        "1": ONE,
        "0": DOT,
    }[kind]


def build_commentary(
    rng: random.Random,
    kind: str,
    context: dict[str, Any],
    dismissal: str | None = None,
    last_text: str | None = None,
) -> str:
    """Pick a template for the outcome, avoiding an immediate repeat."""
    pool = _pool_for(kind, dismissal)
    values = {
        "bowler": context.get("bowler", "The bowler"),
        "striker": context.get("striker", "The batter"),
        "delivery": str(context.get("delivery", "delivery")).lower(),
    }
    candidates = [t.format(**values) for t in pool]
    options = [c for c in candidates if not (last_text and last_text.startswith(c))]
    text = rng.choice(options or candidates)
    note = None
    if context.get("is_death") and rng.random() < 0.35:
        note = rng.choice(CONTEXT_DEATH)
    elif context.get("is_powerplay") and rng.random() < 0.3:
        note = rng.choice(CONTEXT_POWERPLAY)
    needed = context.get("runs_needed")
    balls = context.get("balls_remaining")
    if needed is not None and balls and rng.random() < 0.4:
        note = f"{needed} needed from {balls} ball{'s' if balls != 1 else ''}."
    return f"{text} {note}" if note else text
# IPL Auction

A real-time, browser-based auction room for friends to represent IPL franchises
and bid together. Python runs the auction server; WebSockets synchronize bids,
the timer, team purses, and sale results across connected browsers.

## Run locally

From PowerShell in this project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open `http://localhost:8000`. The auction host creates a room and shares its
six-character code with participants. Each participant joins with a display
name and an available franchise. The host also selects a franchise and bids
for it while retaining admin controls. The host chooses a 5- or 10-second
timer and a bid increment, then starts the auction. After each lot settles,
the next player opens automatically after the hammer announcement. Only the
host can pause/resume or change the timer between lots; pausing a live lot
preserves its remaining time. When the clock ends, the server records the sale
or unsold result and the browser shows a compact hammer announcement in the
space below the player card; it does not cover or disable the auction.
The host can place the opening bid while playing solo; once their franchise is
leading, another franchise must bid before they can raise again.

## Project structure

- `web/`: responsive auction overview, player/result tabs, franchise directory, and live room.
- `data/auction_info.json`: season, squad size, and live-session details.
- `data/teams.json`: franchise catalogue and official team logo references.
- `data/auction_settings.json`: default live-game purse in crore.
- `data/players.csv`: the player pool, base prices, IPL stats, and verified portrait URLs.
- `data/player_profiles.csv`: batting style and bowling type per player (draft).
- `data/player_ratings.csv`: hidden player ratings used for franchise ratings (draft).
- `data/ipl_stats_aliases.csv`: verified name mappings for the IPL stats source.
- `ipl_auction/players/`: player model, CSV loading, validation, and the Cricsheet stats importer.
- `ipl_auction/teams/`: team data and purse state.
- `ipl_auction/auction/`: bid validation and sale rules.
- `ipl_auction/services/`: application-level auction operations.
- `ipl_auction/server/`: FastAPI endpoints, WebSocket events, and room state.
- `tests/`: auction and data validation tests.

## Player CSV

The columns are:

```csv
player_id,name,role,nationality,base_price_crore,matches,runs,wickets,batting_average,strike_rate,economy,image_url,portrait_profile_url
```

Prices are in crore. IPL career statistics are calculated from Cricsheet's
ball-by-ball IPL data through December 31, 2025. Refresh them with
`python -m ipl_auction.players.update_stats`; the importer uses no API key and
accepts an optional `--cutoff YYYY-MM-DD`. Name aliases are kept separately in
`data/ipl_stats_aliases.csv`. A blank stat means the importer could not verify a matching IPL appearance;
the player may have no prior appearance or still need a verified name alias.
Values are not guessed. Cricsheet match data is
licensed under the Open Data Commons Attribution License
<https://opendatacommons.org/licenses/by/1-0/> and is available at
<https://cricsheet.org/downloads/>.

The auction pool includes only players with at least one verified IPL
appearance by the cutoff and an official portrait URL that returns a player
image. Players missing either are excluded from `data/players.csv`; rate
statistics may remain blank when a player did not bat or bowl.

Player portraits use explicit URLs from the matching official IPL player
profile; auction IDs are not treated as portrait IDs. Verified profile images
are requested at 1080px from the official image optimizer; source portraits are
1024px. Players without a verified profile image use initials rather than a
potentially mismatched photo. The source profile link is retained in
`portrait_profile_url` for auditing. Images require an internet connection and
no API key. The player list is a live-room auction pool: sold and unsold status
is created only as players are called in that room, and each result records its
winning price and franchise. No historical auction outcome is preloaded. The
live-game purse in `data/auction_settings.json` defaults to ₹125 crore per
franchise.

Every franchise builds a squad of 15 players in any mix of roles (all openers,
all bowlers, anything goes). There are no role slots during the auction, but
three rules keep it realistic: a squad can hold at most 6 overseas players, a
bid is blocked if it would leave too little money to fill the remaining spots
at the ₹0.30 crore minimum base price, and a franchise with 15 players cannot
bid. The auction ends when every franchise that joined has 15 players, the pool
is exhausted, or nobody who still needs players can buy anyone else.

After the auction each franchise picks its Playing XI (exactly 11 players, at
most 4 overseas) and clicks Lock; the other players are the bench. The host can
auto-pick and lock for franchises that have not locked. Only those two rules are
enforced; an unbalanced XI (for example three openers or no wicketkeeper) is
allowed but lowers the franchise rating.

Each player has a hidden rating in `data/player_ratings.csv`, which is never
shown during the auction. The franchise rating is the average rating of the XI,
plus 10% of the bench average, minus balance penalties (constants at the top of
`ipl_auction/teams/lineup.py`). A franchise sees its own rating and breakdown
right after locking; the ranking of all franchises appears once every franchise
has locked. `data/player_profiles.csv` tags each player with a batting style
(opener, anchor, middle, finisher) and bowling type (pace, spin) for those
checks. Both files are drafts for the owner to review: regenerate the ratings
with `python -m ipl_auction.players.build_ratings`, or edit the CSV by hand.

## Hosting for friends in different locations

`python app.py` starts the server on your computer for local development. For
friends outside your network, deploy this app to a public Python hosting
service and share its HTTPS URL. WebSocket connections use secure WSS
automatically when the site uses HTTPS.

This first multiplayer version keeps rooms in server memory. Keep one server
process running for the whole auction; rooms are lost when it restarts, and
multiple server instances are not yet coordinated. Persistent rooms, host
accounts, and production deployment configuration can be added before a
larger public event.
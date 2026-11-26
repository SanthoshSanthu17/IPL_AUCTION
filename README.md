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
- `data/auction_info.json`: season, lineup targets, and live-session details.
- `data/teams.json`: franchise catalogue and official team logo references.
- `data/auction_settings.json`: default live-game purse in crore.
- `data/players.csv`: the player pool, base prices, IPL stats, and verified portrait URLs.
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

Each franchise tracks an 11-slot lineup: one wicketkeeper, two openers, one
anchor, one middle-order batter, one finisher, two all-rounders, one fast
bowler, one spinner, and one other bowler. When a franchise bids, the game
automatically assigns the player to its first available compatible role in
lineup priority order. The lineup score is the percentage of these slots
filled; it measures roster coverage, not player performance.

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

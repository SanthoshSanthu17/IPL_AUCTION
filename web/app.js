const landingScreen = document.querySelector("#landing-screen");
const auctionScreen = document.querySelector("#auction-screen");
const createForm = document.querySelector("#create-form");
const joinForm = document.querySelector("#join-form");
const teamChoice = document.querySelector("#team-choice");
const adminTeamChoice = document.querySelector("#admin-team-choice");
const entryError = document.querySelector("#entry-error");
const connectionIndicator = document.querySelector("#connection-indicator");
const roomChip = document.querySelector("#room-chip");
const toast = document.querySelector("#toast");
const playerSearch = document.querySelector("#player-search");
const roleFilter = document.querySelector("#role-filter");

let socket;
let currentState;
let currentSession;
let toastTimeout;
let announcementTimeout;
let setBannerTimeout;
let activeTab = "overview";
let activeLiveTab = "all";
let activeRoomTab = "auction";
let autoSwitchedToTournament = false;
let activeMatchId = null;
let activeMatchRole = null;
let activeMatchState = null;
let xiSelection = new Set();
let catalogData = { player_records: [], teams: [], auction: {} };

const money = (value) => value == null ? "—" : `₹${Number(value).toFixed(2)} Cr`;
const auctionMoney = (value) => value == null
  ? "—"
  : `₹${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })} Cr`;
const displayNumber = (value, digits = 1) => value == null ? "—" : Number(value).toFixed(digits);

function showEntryError(message) {
  entryError.textContent = message;
  entryError.classList.remove("hidden");
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.add("hidden"), 4000);
}

function setConnected(connected) {
  connectionIndicator.classList.toggle("online", connected);
  connectionIndicator.querySelector("span").textContent = connected ? "CONNECTED" : "RECONNECTING";
}

function updateLiveClock() {
  const now = new Date();
  document.querySelector("#auction-date").textContent = new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(now);
  document.querySelector("#auction-time").textContent = new Intl.DateTimeFormat("en-IN", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    timeZoneName: "short",
  }).format(now);
}

document.querySelectorAll(".brand-mark").forEach((logo) => {
  logo.addEventListener("error", () => {
    const fallback = document.createElement("span");
    fallback.className = "brand-mark-fallback";
    fallback.textContent = "IPL";
    fallback.setAttribute("aria-label", "Indian Premier League");
    logo.replaceWith(fallback);
  }, { once: true });
});

function makeTeamLogo(team, className = "") {
  if (!team) {
    const fallback = document.createElement("span");
    fallback.className = `${className} team-logo-fallback`;
    fallback.textContent = "TBD";
    return fallback;
  }
  if (team.logo_url) {
    const image = document.createElement("img");
    image.className = [className, team.team_id === "rr" ? "team-logo-rr" : ""]
      .filter(Boolean)
      .join(" ");
    image.src = team.logo_url;
    image.alt = team.name;
    image.loading = "lazy";
    image.addEventListener("error", () => {
      const fallback = document.createElement("span");
      fallback.className = `${className} team-logo-fallback`;
      fallback.textContent = team.short_name || team.team_id.toUpperCase();
      fallback.setAttribute("aria-label", team.name);
      image.replaceWith(fallback);
    }, { once: true });
    return image;
  }

  const fallback = document.createElement("span");
  fallback.className = `${className} team-logo-fallback`;
  fallback.textContent = team.short_name || team.team_id.toUpperCase();
  fallback.setAttribute("aria-label", team.name);
  return fallback;
}

function makePlayerPhoto(player, sizeClass = "player-photo-small") {
  const photo = document.createElement("span");
  photo.className = `player-photo ${sizeClass}`;
  const fallback = document.createElement("span");
  fallback.className = "player-photo-fallback";
  fallback.textContent = player?.name
    ? player.name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase()
    : "IPL";
  fallback.setAttribute("aria-hidden", "true");

  if (!player?.image_url) {
    photo.classList.add("photo-missing");
    photo.append(fallback);
    return photo;
  }

  const image = document.createElement("img");
  image.src = player.image_url;
  image.alt = "";
  image.loading = "lazy";
  image.addEventListener("error", () => {
    photo.classList.add("photo-missing");
    photo.replaceChildren(fallback);
  }, { once: true });
  photo.append(image);
  return photo;
}

function makeEliteBadge() {
  const badge = document.createElement("span");
  badge.className = "elite-badge";
  badge.textContent = "ELITE";
  badge.title = "Elite player";
  return badge;
}

function makeSetChip(number) {
  const chip = document.createElement("span");
  chip.className = "set-chip";
  chip.textContent = `SET ${number}`;
  return chip;
}

function makePlayerCell(player) {
  const cell = document.createElement("td");
  cell.className = "player-cell";
  const identity = document.createElement("div");
  identity.className = "player-cell-identity";
  identity.append(makePlayerPhoto(player));
  const name = document.createElement("span");
  name.className = "player-cell-name";
  name.textContent = player.name;
  identity.append(name);
  if (player.elite) identity.append(makeEliteBadge());
  if (player.set_number) identity.append(makeSetChip(player.set_number));
  cell.append(identity);
  return cell;
}

function makeCareerStatsCell(player) {
  const cell = document.createElement("td");
  cell.className = "career-stats-cell";
  if (player.matches == null) {
    cell.textContent = "No matched IPL career record";
    cell.classList.add("stats-unavailable");
    return cell;
  }

  const stats = [
    ["M", player.matches],
    ["R", Number(player.runs || 0).toLocaleString("en-IN")],
    ["W", player.wickets ?? 0],
    ["AVG", player.batting_average == null ? "—" : displayNumber(player.batting_average, 2)],
    ["SR", player.strike_rate == null ? "—" : displayNumber(player.strike_rate, 2)],
    ["ECON", player.economy == null ? "—" : displayNumber(player.economy, 2)],
  ];
  const grid = document.createElement("div");
  grid.className = "career-stats-grid";
  stats.forEach(([label, value]) => {
    const item = document.createElement("span");
    item.textContent = `${label} ${value}`;
    grid.append(item);
  });
  cell.append(grid);
  return cell;
}

function renderFranchises(teams) {
  const preview = document.querySelector("#team-preview-list");
  const grid = document.querySelector("#franchise-grid");
  preview.replaceChildren();
  grid.replaceChildren();

  teams.forEach((team) => {
    const mini = document.createElement("button");
    mini.type = "button";
    mini.className = "franchise-mini";
    mini.setAttribute("aria-label", `Select ${team.name} as your franchise`);
    mini.addEventListener("click", () => selectFranchise(team));
    mini.append(makeTeamLogo(team));
    const miniName = document.createElement("strong");
    miniName.textContent = team.short_name;
    mini.append(miniName);
    preview.append(mini);

    const card = document.createElement("button");
    card.type = "button";
    card.className = "franchise-card";
    card.setAttribute("aria-label", `Select ${team.name} as your franchise`);
    card.addEventListener("click", () => selectFranchise(team));
    card.append(makeTeamLogo(team));
    const name = document.createElement("strong");
    name.textContent = team.name;
    const shortName = document.createElement("span");
    shortName.textContent = team.short_name;
    const action = document.createElement("small");
    action.className = "franchise-card-action";
    action.textContent = "SELECT FRANCHISE";
    card.append(name, shortName, action);
    grid.append(card);
  });
}

function selectFranchise(team) {
  teamChoice.value = team.team_id;
  setActiveTab("overview");
  const roomCode = document.querySelector("#join-room-code");
  const playerName = document.querySelector("#player-name");
  const focusTarget = !roomCode.value ? roomCode : !playerName.value ? playerName : teamChoice;
  requestAnimationFrame(() => {
    document.querySelector("#entry-screen").scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
    focusTarget.focus({ preventScroll: true });
  });
  showToast(`${team.name} selected. Enter your room code and name to join.`);
}

function renderPastWinners(winners) {
  const body = document.querySelector("#winners-table-body");
  document.querySelector("#winners-count").textContent =
    `${winners.length} ${winners.length === 1 ? "SEASON" : "SEASONS"}`;

  if (!winners.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty-row";
    cell.textContent = "No tournament has been completed yet.";
    row.append(cell);
    body.replaceChildren(row);
    return;
  }

  const teamCell = (team) => {
    const cell = document.createElement("td");
    const wrapper = document.createElement("span");
    wrapper.className = "result-team";
    const name = document.createElement("span");
    name.textContent = team.name;
    wrapper.append(makeTeamLogo(team), name);
    cell.append(wrapper);
    return cell;
  };

  body.replaceChildren(...winners.map((entry) => {
    const row = document.createElement("tr");
    const season = document.createElement("td");
    season.textContent = `Season ${entry.season}`;
    const date = document.createElement("td");
    date.textContent = entry.date || "—";
    const room = document.createElement("td");
    room.textContent = entry.room_code || "—";
    row.append(season, teamCell(entry.winner), teamCell(entry.runner_up), date, room);
    return row;
  }));
}

async function loadPastWinners() {
  try {
    const response = await fetch("/api/past-winners");
    if (!response.ok) throw new Error("Could not load past winners.");
    const result = await response.json();
    renderPastWinners(result.winners || []);
  } catch (error) {
    const body = document.querySelector("#winners-table-body");
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty-row";
    cell.textContent = error.message;
    row.append(cell);
    body.replaceChildren(row);
  }
}

function setActiveTab(tab) {
  activeTab = tab;
  const panelId = tab === "overview"
    ? "overview-panel"
    : tab === "teams"
      ? "teams-panel"
      : tab === "past-winners" ? "winners-panel" : "players-panel";

  document.querySelectorAll(".auction-tab[data-tab]").forEach((button) => {
    const selected = button.dataset.tab === tab;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== panelId);
  });

  if (tab === "past-winners") {
    loadPastWinners();
  } else if (tab !== "overview" && tab !== "teams") {
    document.querySelector("#players-panel").setAttribute(
      "aria-labelledby",
      `tab-${tab === "all-players" ? "all-players" : tab}`,
    );
    renderPlayerTable(tab);
  }
}

function setRoomTab(tab) {
  activeRoomTab = tab;
  document.querySelectorAll("[data-room-tab]").forEach((btn) => {
    const active = btn.dataset.roomTab === tab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", String(active));
  });
  document.querySelector("#room-auction-view").classList.toggle("hidden", tab !== "auction");
  document.querySelector("#room-tournament-view").classList.toggle("hidden", tab !== "tournament");
}

function renderPlayerTable(tab) {
  const headings = document.querySelector("#players-table-head");
  const body = document.querySelector("#players-table-body");
  const heading = document.querySelector("#players-heading");
  const description = document.querySelector("#players-description");
  const kicker = document.querySelector("#players-kicker");
  const count = document.querySelector("#players-count");
  const labels = ["PLAYER", "IPL CAREER", "ROLE", "NATIONALITY", "BASE PRICE", "LIVE ROOM STATUS"];
  const titles = {
    "all-players": ["All Players", "Browse the player pool for your live room and plan your targets.", "LIVE PLAYER POOL"],
    sold: ["Sold Players", "Players move here only after they are sold in a live room.", "LIVE RESULTS"],
    unsold: ["Unsold Players", "Players move here only after the live timer expires without a bid.", "LIVE RESULTS"],
  };
  [heading.textContent, description.textContent, kicker.textContent] = titles[tab] || titles["all-players"];
  headings.replaceChildren(...labels.map((label) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    return cell;
  }));

  const records = catalogData.player_records || [];
  const search = playerSearch.value.trim().toLocaleLowerCase();
  const role = roleFilter.value;
  const filtered = records.filter((player) => {
    if (tab === "sold" || tab === "unsold") return false;
    if (role && player.role !== role) return false;
    if (!search) return true;
    return `${player.name} ${player.nationality}`.toLocaleLowerCase().includes(search);
  });
  count.textContent = tab === "sold"
    ? "0 SOLD"
    : tab === "unsold"
      ? "0 UNSOLD"
      : `${filtered.length.toLocaleString("en-IN")} PLAYERS`;
  body.replaceChildren();

  if (!filtered.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "empty-row";
    cell.colSpan = labels.length;
    cell.textContent = tab === "sold" || tab === "unsold"
      ? "Create or join a room to see live auction results here."
      : "No players match your search.";
    row.append(cell);
    body.append(row);
    return;
  }

  filtered.forEach((player) => {
    const row = document.createElement("tr");
    row.append(makePlayerCell(player), makeCareerStatsCell(player));

    [player.role, player.nationality].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value || "—";
      row.append(cell);
    });

    const basePrice = document.createElement("td");
    basePrice.className = "price-cell";
    basePrice.textContent = auctionMoney(player.base_price_crore);
    row.append(basePrice);

    const liveStatus = document.createElement("td");
    const status = document.createElement("span");
    status.className = "status-chip status-upcoming";
    status.textContent = "Awaiting live auction";
    liveStatus.append(status);
    row.append(liveStatus);

    body.append(row);
  });
}

async function loadCatalog() {
  try {
    const response = await fetch("/api/catalog");
    if (!response.ok) throw new Error("Could not load the auction data.");
    catalogData = await response.json();

    teamChoice.replaceChildren(new Option("Select your franchise", ""));
    adminTeamChoice.replaceChildren(new Option("Select your franchise", ""));
    catalogData.teams.forEach((team) => {
      teamChoice.add(new Option(team.name, team.team_id));
      adminTeamChoice.add(new Option(team.name, team.team_id));
    });

    const auction = catalogData.auction;
    document.querySelector("#auction-venue").textContent = auction.venue;
    document.querySelector("#pool-count").textContent = auction.registered_players;
    document.querySelector("#lineup-count").textContent = auction.target_squad_size;
    document.querySelector("#franchise-count").textContent = catalogData.teams.length;
    document.querySelector("#purse-card").textContent = auctionMoney(auction.total_purse_per_team_crore);
    document.querySelector("#purse-value").textContent = auctionMoney(auction.total_purse_per_team_crore);
    document.querySelector("#catalog-note").textContent =
      `${catalogData.players} players are available for this live auction pool. Sold and unsold lists update inside each room as players are called.`;
    if (catalogData.sets) {
      const { count, size, taking_part: takingPart } = catalogData.sets;
      document.querySelector("#sets-note").textContent =
        `Players are auctioned in ${count} sets by rating: Set 1 (all ELITE players) first, then Set 2 to Set ${count}. Each set has ${size} players and ${takingPart} of them take part in every auction.`;
    }

    renderFranchises(catalogData.teams);
    renderPlayerTable(["overview", "teams", "past-winners"].includes(activeTab) ? "all-players" : activeTab);
  } catch (error) {
    document.querySelector("#catalog-note").textContent = error.message;
  }

  updateLiveClock();
  setInterval(updateLiveClock, 1000);
}

async function createRoom(event) {
  event.preventDefault();
  entryError.classList.add("hidden");
  const name = document.querySelector("#admin-name").value.trim();
  const teamId = adminTeamChoice.value;
  if (!name) return showEntryError("Enter your name to host the room.");
  if (!teamId) return showEntryError("Choose your franchise to host and bid.");

  const submit = createForm.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const response = await fetch("/api/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        timer_seconds: Number(document.querySelector("#timer-setting").value),
        bid_increment_crore: Number(document.querySelector("#increment-setting").value),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not create the room.");
    document.querySelector("#created-room-code").textContent = result.room_code;
    document.querySelector("#created-room").classList.remove("hidden");
    document.querySelector("#join-room-code").value = result.room_code;
    currentSession = {
      roomCode: result.room_code,
      adminToken: result.admin_token,
      name,
      teamId,
    };
    sessionStorage.setItem("auction-session", JSON.stringify(currentSession));
    connect(currentSession);
  } catch (error) {
    showEntryError(error.message);
  } finally {
    submit.disabled = false;
  }
}

function joinRoom(event) {
  event.preventDefault();
  entryError.classList.add("hidden");
  const session = {
    roomCode: document.querySelector("#join-room-code").value.trim().toUpperCase(),
    name: document.querySelector("#player-name").value.trim(),
    teamId: teamChoice.value,
    adminToken: null,
  };
  if (!session.roomCode || !session.name || !session.teamId) {
    return showEntryError("Enter the room code, your name, and a franchise.");
  }
  currentSession = session;
  sessionStorage.setItem("auction-session", JSON.stringify(session));
  connect(session);
}

function connect(session) {
  if (socket) socket.close();
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${scheme}//${location.host}/ws/${encodeURIComponent(session.roomCode)}`);

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({
      type: "join",
      name: session.name,
      team_id: session.teamId,
      admin_token: session.adminToken,
    }));
    setConnected(true);
    if (activeMatchId && activeMatchRole) {
      socket.send(JSON.stringify({
        type: activeMatchRole === "participant" ? "join_match" : "join_spectator",
        match_id: activeMatchId,
      }));
    }
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "error") {
      showEntryError(message.message);
      showToast(message.message);
      if (!currentState) {
        currentSession = null;
        sessionStorage.removeItem("auction-session");
        socket.close();
      }
      return;
    }
    if (message.type === "state") {
      renderState(message.state);
      return;
    }
    if (message.type === "match_started" || message.type === "match_snapshot") {
      activeMatchId = message.match.match_id;
      activeMatchRole = message.role || activeMatchRole || "spectator";
      activeMatchState = message.match;
      setRoomTab("tournament");
      renderLiveMatch(activeMatchState, activeMatchRole);
      return;
    }
    if (message.type === "match_update" || message.type === "ball_update" || message.type === "score_update" || message.type === "innings_completed" || message.type === "super_over_started") {
      if (message.match && message.match.match_id === activeMatchId) {
        if (message.role) activeMatchRole = message.role;
        if (activeMatchState && message.match.revision != null && activeMatchState.revision != null
          && message.match.revision < activeMatchState.revision) return;
        activeMatchState = message.match;
        renderLiveMatch(activeMatchState, activeMatchRole);
      }
      return;
    }
    if (message.type === "match_completed") {
      if (message.match && message.match.match_id === activeMatchId) {
        if (message.role) activeMatchRole = message.role;
        activeMatchState = message.match;
        renderLiveMatch(activeMatchState, activeMatchRole);
      }
      if (message.result) {
        showToast(`${message.result.winner_team_name} won! (${message.result.margin})`);
        renderScorecardModal(message.result);
      }
      return;
    }
    if (message.type === "match_scorecard" && message.result) {
      renderScorecardModal(message.result);
      return;
    }
    if (message.type === "tournament_standings_updated" && currentState?.tournament) {
      currentState.tournament.standings = message.standings || [];
      renderTournament(currentState);
      return;
    }
    if (message.type === "playoff_bracket_updated" && message.tournament && currentState) {
      currentState.tournament = message.tournament;
      renderTournament(currentState);
    }
  });

  socket.addEventListener("close", () => {
    setConnected(false);
    if (currentSession && !document.hidden) {
      setTimeout(() => {
        if (currentSession) connect(currentSession);
      }, 2000);
    }
  });
  socket.addEventListener("error", () => setConnected(false));
}

function sendAction(type, payload = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    showToast("Not connected to the auction room.");
    return;
  }
  socket.send(JSON.stringify({ ...payload, type }));
}

function renderState(state) {
  const previousResultCount = currentState?.results.length;
  currentState = state;
  const isAdmin = Boolean(currentSession?.adminToken);
  landingScreen.classList.add("hidden");
  auctionScreen.classList.remove("hidden");
  roomChip.classList.remove("hidden");
  document.querySelector("#room-code-label").textContent = state.room_code;

  const hasTournament = Boolean(state.tournament && state.tournament.active);
  const roomModeNav = document.querySelector("#room-mode-nav");
  roomModeNav.classList.toggle("hidden", !hasTournament);

  if (hasTournament && !autoSwitchedToTournament) {
    autoSwitchedToTournament = true;
    setRoomTab("tournament");
  } else if (!hasTournament) {
    setRoomTab("auction");
  }

  document.querySelector("#auction-mode-label").textContent = hasTournament
    ? "T20 TOURNAMENT"
    : state.status === "live"
      ? "LIVE AUCTION"
      : state.status === "paused" ? "AUCTION PAUSED"
        : state.status === "lobby" ? isAdmin ? "HOST & PARTICIPANT" : "AUCTION LOBBY"
          : state.status === "complete" ? "PLAYING XI" : "AUCTION ROOM";
  document.querySelector("#auction-heading").textContent = hasTournament
    ? (state.tournament.champion ? `Champion: ${state.tournament.champion.name}` : state.tournament.current_stage)
    : state.status === "complete"
      ? "Auction complete"
      : state.status === "paused" ? "The auction is paused"
        : state.status === "lobby" ? isAdmin ? "Ready to call the first player" : "Waiting for the host"
          : "The bidding floor";

  const adminNote = document.querySelector("#admin-note");
  adminNote.classList.toggle("hidden", !isAdmin);
  const ownTeam = state.teams.find((team) => team.team_id === currentSession?.teamId);
  if (isAdmin) {
    adminNote.textContent = `You are the host and auctioneer, bidding for ${ownTeam?.name || "your franchise"}. Start the auction once; each next player opens automatically after the hammer announcement.`;
  }
  const startButton = document.querySelector("#start-button");
  startButton.classList.toggle("hidden", !isAdmin || state.status !== "lobby");
  startButton.disabled = !state.teams.some((team) => team.captain);
  startButton.title = startButton.disabled
    ? "Choose a franchise before starting the auction."
    : "Call the first player and open bidding.";
  renderAdminControls(state, isAdmin);
  renderOversSetting(state, isAdmin);

  renderPlayer(state);
  renderSetInfo(state);
  updateSetBanner(state);
  renderTeams(state);
  renderSales(state);
  renderLiveBoard(state);
  renderXi(state);
  if (hasTournament) {
    renderTournament(state);
  }
  updateTimer(state);
  if (previousResultCount != null && state.results.length > previousResultCount) {
    showHammerAnnouncement(state.results[state.results.length - 1]);
  }
}

function renderTournament(state) {
  const t = state.tournament;
  if (!t) return;

  document.querySelector("#tournament-stage-heading").textContent = t.current_stage || "T20 Tournament";
  document.querySelector("#tournament-counts-pill").textContent =
    `${t.counts.completed} / ${t.counts.total} COMPLETED`;
  document.querySelector("#t-teams-count").textContent = t.total_teams;
  document.querySelector("#t-upcoming-count").textContent = t.counts.upcoming;
  document.querySelector("#t-live-count").textContent = t.counts.live;
  document.querySelector("#t-completed-count").textContent = t.counts.completed;

  // Champion banner
  const champBanner = document.querySelector("#tournament-champion-banner");
  champBanner.classList.toggle("hidden", !t.champion);
  if (t.champion) {
    document.querySelector("#champion-team-name").textContent = t.champion.name;
    document.querySelector("#champion-margin").textContent = t.final_result?.margin || "Tournament Winners";
    const potm = t.final_result?.player_of_the_match;
    document.querySelector("#champion-potm").textContent = potm
      ? `Player of the Match: ${potm.player_name} (${potm.summary})`
      : "";
    const logoSlot = document.querySelector("#champion-team-logo");
    logoSlot.replaceChildren(makeTeamLogo(t.champion));
    document.querySelector("#champion-scorecard-btn").onclick = () => {
      if (t.final_result) renderScorecardModal(t.final_result);
    };
  }

  // Fixtures Grid
  const ownTeamId = currentSession?.teamId;
  const grid = document.querySelector("#tournament-fixtures-grid");
  grid.replaceChildren();
  t.fixtures.forEach((fx) => {
    const isMine = ownTeamId && (fx.team_a?.team_id === ownTeamId || fx.team_b?.team_id === ownTeamId);
    const card = document.createElement("article");
    card.className = `fixture-card${isMine ? " mine" : ""}${fx.status === "live" ? " live" : ""}`;

    const top = document.createElement("div");
    top.className = "fixture-top";
    const badge = document.createElement("span");
    badge.className = "panel-kicker";
    badge.textContent = `MATCH ${fx.fixture_number} · ${fx.stage.toUpperCase()}`;
    const pill = document.createElement("span");
    pill.className = `status-chip status-${fx.status === "completed" ? "sold" : fx.status === "live" ? "live" : "upcoming"}`;
    pill.textContent = fx.status === "live" ? "LIVE" : fx.status === "completed" ? "COMPLETED" : "UPCOMING";
    top.append(badge, pill);

    const teamsRow = document.createElement("div");
    teamsRow.className = "fixture-teams";

    const makeSide = (team, tbdLabel) => {
      const box = document.createElement("div");
      box.className = "fixture-team-side";
      if (team) {
        box.append(makeTeamLogo(team));
        const nm = document.createElement("strong");
        nm.textContent = team.short_name || team.name;
        box.append(nm);
      } else {
        const nm = document.createElement("span");
        nm.className = "muted-tbd";
        nm.textContent = tbdLabel || "TBD";
        box.append(nm);
      }
      return box;
    };

    const vs = document.createElement("span");
    vs.className = "fixture-vs";
    vs.textContent = "VS";
    teamsRow.append(makeSide(fx.team_a, fx.tbd_a_label), vs, makeSide(fx.team_b, fx.tbd_b_label));

    const meta = document.createElement("p");
    meta.className = "fixture-meta";
    if (fx.status === "live" && fx.live_score) {
      meta.textContent = fx.live_score.batting_team_name
        ? `${fx.live_score.batting_team_name}: ${fx.live_score.runs}/${fx.live_score.wickets} (${fx.live_score.overs} ov)`
        : "Toss in progress";
    } else if (fx.status === "completed" && fx.result_summary) {
      meta.textContent = fx.result_summary;
    } else if (!fx.is_ready) {
      meta.textContent = "Awaiting qualifying teams";
    } else {
      meta.textContent = isMine ? "Your scheduled fixture" : "Ready to start";
    }

    const actions = document.createElement("div");
    actions.className = "fixture-actions";

    if (fx.status === "upcoming" && fx.is_ready && isMine) {
      const startBtn = document.createElement("button");
      startBtn.type = "button";
      startBtn.className = "button button-primary";
      startBtn.textContent = "Start Match";
      startBtn.addEventListener("click", () => sendAction("start_match", { match_id: fx.match_id }));
      actions.append(startBtn);
    } else if (fx.status === "live") {
      if (isMine) {
        const joinBtn = document.createElement("button");
        joinBtn.type = "button";
        joinBtn.className = "button button-primary";
        joinBtn.textContent = "Open Live Match";
        joinBtn.addEventListener("click", () => sendAction("join_match", { match_id: fx.match_id }));
        actions.append(joinBtn);
      } else {
        const watchBtn = document.createElement("button");
        watchBtn.type = "button";
        watchBtn.className = "button button-secondary";
        watchBtn.textContent = "Watch Live";
        watchBtn.addEventListener("click", () => sendAction("join_spectator", { match_id: fx.match_id }));
        actions.append(watchBtn);
      }
    } else if (fx.status === "completed") {
      const scBtn = document.createElement("button");
      scBtn.type = "button";
      scBtn.className = "button button-secondary";
      scBtn.textContent = "View Scorecard";
      scBtn.addEventListener("click", () => sendAction("get_match_scorecard", { match_id: fx.match_id }));
      actions.append(scBtn);
    }

    card.append(top, teamsRow, meta, actions);
    grid.append(card);
  });

  // Points table
  const standingsSection = document.querySelector("#tournament-standings-section");
  standingsSection.classList.toggle("hidden", !t.standings || t.standings.length === 0);
  if (t.standings && t.standings.length > 0) {
    const body = document.querySelector("#tournament-standings-body");
    body.replaceChildren(...t.standings.map((row) => {
      const tr = document.createElement("tr");
      if (row.team_id === ownTeamId) tr.classList.add("live-current");
      const pos = document.createElement("td");
      pos.textContent = row.position;
      const teamTd = document.createElement("td");
      const wrap = document.createElement("span");
      wrap.className = "result-team";
      wrap.append(makeTeamLogo(row), document.createTextNode(row.name));
      teamTd.append(wrap);
      const p = document.createElement("td");
      p.textContent = row.played;
      const w = document.createElement("td");
      w.textContent = row.won;
      const l = document.createElement("td");
      l.textContent = row.lost;
      const pts = document.createElement("td");
      pts.className = "price-cell";
      pts.textContent = row.points;
      const nrr = document.createElement("td");
      nrr.textContent = row.nrr >= 0 ? `+${row.nrr.toFixed(3)}` : row.nrr.toFixed(3);
      tr.append(pos, teamTd, p, w, l, pts, nrr);
      return tr;
    }));
  }

  // Playoff bracket
  const bracketGrid = document.querySelector("#tournament-bracket-grid");
  bracketGrid.replaceChildren();
  const playoffFixtures = t.fixtures.filter((fx) => fx.is_playoff);
  playoffFixtures.forEach((fx) => {
    const slot = document.createElement("div");
    slot.className = "bracket-card";
    const title = document.createElement("span");
    title.className = "panel-kicker";
    title.textContent = fx.stage.toUpperCase();
    const lineA = document.createElement("div");
    lineA.className = `bracket-team${fx.winner_team_id && fx.winner_team_id === fx.team_a?.team_id ? " winner" : ""}`;
    lineA.textContent = fx.team_a ? fx.team_a.name : fx.tbd_a_label;
    const lineB = document.createElement("div");
    lineB.className = `bracket-team${fx.winner_team_id && fx.winner_team_id === fx.team_b?.team_id ? " winner" : ""}`;
    lineB.textContent = fx.team_b ? fx.team_b.name : fx.tbd_b_label;
    const note = document.createElement("small");
    note.textContent = fx.result_summary || (fx.status === "live" ? "Live now" : "Pending");
    slot.append(title, lineA, lineB, note);
    bracketGrid.append(slot);
  });
}

let openerPick = [];
let openerPickKey = "";

function matchAction(type, payload = {}) {
  if (!activeMatchId) return;
  sendAction(type, { match_id: activeMatchId, ...payload });
}

function clearChildren(...selectors) {
  selectors.forEach((selector) => document.querySelector(selector).replaceChildren());
}

function pickerButton(label, detail, options = {}) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `button button-secondary picker-btn${options.selected ? " selected" : ""}`;
  btn.disabled = Boolean(options.disabled);
  const name = document.createElement("strong");
  name.textContent = label;
  const small = document.createElement("small");
  small.textContent = detail || "";
  btn.append(name, small);
  if (options.title) btn.title = options.title;
  if (options.onClick) btn.addEventListener("click", options.onClick);
  return btn;
}

function renderLiveMatch(match, role) {
  const panel = document.querySelector("#live-match-panel");
  if (!match) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");

  const isParticipant = role === "participant";
  const myTeamId = currentSession?.teamId;
  const manual = !match.speed || match.speed === "manual";
  const myTurn = isParticipant && !match.completed && manual && match.actor_team_id === myTeamId;
  const pill = document.querySelector("#live-match-role-pill");
  pill.innerHTML = `<i></i> ${isParticipant ? "YOUR MATCH · LIVE" : "SPECTATOR MODE · READ ONLY"}`;

  const speedControls = document.querySelector("#match-speed-controls");
  speedControls.classList.toggle("hidden", !(isParticipant || currentSession?.adminToken) || match.completed);
  speedControls.querySelectorAll(".speed-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.speed === (match.speed || "manual"));
    btn.classList.toggle("hidden", !isParticipant);
  });
  const autoBall = document.querySelector("#simulate-ball-btn");
  autoBall.disabled = !manual;

  const hasInnings = Boolean(match.batting_team_name);
  document.querySelector("#live-match-title").textContent = hasInnings
    ? `${match.batting_team_name} vs ${match.bowling_team_name}`
    : `${match.team_a_name} vs ${match.team_b_name}`;
  document.querySelector("#live-match-innings-label").textContent = !hasInnings
    ? "Toss"
    : match.is_super_over
      ? `Super Over #${match.super_over_round} · Innings ${match.innings_number}`
      : `Innings ${match.innings_number} of 2`;

  document.querySelector("#sb-batting-team").textContent = match.batting_team_name || "Toss";
  document.querySelector("#sb-score").textContent = `${match.runs}/${match.wickets}`;
  document.querySelector("#sb-overs").textContent = `(${match.overs} / ${match.total_overs} ov)`;

  const chaseBox = document.querySelector("#sb-chase-info");
  if (match.completed && match.result) {
    chaseBox.textContent = match.result.margin;
  } else if (match.target != null) {
    chaseBox.textContent = `Target: ${match.target} · Need ${match.runs_needed} off ${match.balls_remaining} balls (RRR: ${match.required_run_rate ?? "—"})`;
  } else if (hasInnings) {
    chaseBox.textContent = "1st Innings · Setting the target";
  } else {
    chaseBox.textContent = "Toss to be decided";
  }

  document.querySelector("#sb-striker").textContent = match.striker ? `${match.striker.player_name}*` : "—";
  document.querySelector("#sb-striker-stats").textContent = match.striker
    ? `${match.striker.runs} (${match.striker.balls}) · 4s: ${match.striker.fours} · 6s: ${match.striker.sixes}`
    : "—";
  document.querySelector("#sb-non-striker").textContent = match.non_striker ? match.non_striker.player_name : "—";
  document.querySelector("#sb-non-striker-stats").textContent = match.non_striker
    ? `${match.non_striker.runs} (${match.non_striker.balls})`
    : "—";
  document.querySelector("#sb-bowler").textContent = match.bowler ? match.bowler.player_name : "—";
  document.querySelector("#sb-bowler-stats").textContent = match.bowler
    ? `${match.bowler.overs}-${match.bowler.maidens}-${match.bowler.runs_conceded}-${match.bowler.wickets} (Econ ${match.bowler.economy})`
    : "—";

  const chipSource = match.this_over && match.this_over.length ? match.this_over : match.last_six_balls || [];
  document.querySelector("#sb-recent-balls").replaceChildren(...chipSource.map((ev) => {
    const chip = document.createElement("span");
    chip.className = "ball-chip";
    if (ev.is_wicket) {
      chip.classList.add("wicket");
      chip.textContent = "W";
    } else if (ev.extra_type === "wide") {
      chip.textContent = "Wd";
    } else {
      if (ev.runs_off_bat === 4 || ev.runs_off_bat === 6) chip.classList.add("boundary");
      chip.textContent = String(ev.runs_off_bat);
    }
    return chip;
  }));
  document.querySelector("#sb-commentary").textContent = match.last_commentary || "";

  const feed = document.querySelector("#match-commentary-feed");
  feed.replaceChildren(...(match.commentary_feed || []).map((entry) => {
    const li = document.createElement("li");
    li.className = entry.kind === "info" ? "feed-info" : "feed-ball";
    li.textContent = entry.text;
    return li;
  }));

  document.querySelector("#match-format-note").textContent =
    `${match.total_overs} overs · Powerplay 1–${match.powerplay_end} · Death ${match.death_start}–${match.total_overs} · Max ${match.max_bowler_overs} per bowler`;
  document.querySelector("#match-momentum-note").textContent = hasInnings
    ? `Momentum ${Math.round((match.momentum || 0) * 100)}%`
    : "";
  document.querySelector("#match-bowler-style-note").textContent = match.bowler_style
    ? `Bowler: ${match.bowler_style}${match.bowler_is_elite ? " · ELITE" : ""}${match.striker_is_elite ? " · Striker ELITE" : ""}`
    : "";

  renderMatchControls(match, myTurn, isParticipant, manual);
}

function renderMatchControls(match, myTurn, isParticipant, manual) {
  const phase = match.phase;
  const title = document.querySelector("#match-phase-title");
  const detail = document.querySelector("#match-phase-detail");
  const toss = match.toss || {};
  if (match.completed) {
    title.textContent = "Match completed";
    detail.textContent = match.result ? match.result.margin : "";
  } else if (!manual) {
    title.textContent = "Auto-play running";
    detail.textContent = "The computer is making both sides' choices. Switch to Manual to take control.";
  } else if (myTurn) {
    title.textContent = "Your turn";
    detail.textContent = match.awaiting || "";
  } else {
    title.textContent = isParticipant ? "Waiting for the other side" : "Watching live";
    detail.textContent = match.awaiting || "";
  }
  if (toss.winner_name && !match.completed) {
    detail.textContent += ` · Toss: ${toss.winner_name}${toss.decision ? ` chose to ${toss.decision}` : ""}`;
  }

  const show = (selector, visible) => document.querySelector(selector).classList.toggle("hidden", !visible);
  const tossControls = document.querySelector("#match-toss-controls");
  const showToss = myTurn && (phase === "toss_call" || phase === "toss_decision");
  show("#match-toss-controls", showToss);
  show("#match-toss-call-row", showToss && phase === "toss_call");
  show("#match-toss-decision-row", showToss && phase === "toss_decision");
  tossControls.dataset.phase = phase;

  const showPicker = myTurn && ["select_openers", "select_bowler", "select_next_batter"].includes(phase);
  show("#match-picker", showPicker);
  clearChildren("#match-picker-grid");
  show("#match-picker-confirm", showPicker && phase === "select_openers");
  if (showPicker) renderMatchPicker(match, phase);

  show("#match-delivery-controls", myTurn && phase === "bowl_ball");
  clearChildren("#match-delivery-grid");
  if (myTurn && phase === "bowl_ball") {
    const grid = document.querySelector("#match-delivery-grid");
    (match.available_deliveries || []).forEach((opt) => {
      const risk = opt.wide_risk_pct >= 15 ? " · high wide risk" : "";
      grid.append(pickerButton(opt.label, `${opt.remaining} of ${opt.max} left${risk}`, {
        disabled: opt.disabled,
        onClick: () => matchAction("select_delivery", { delivery_code: opt.code }),
      }));
    });
  }

  show("#match-shot-controls", myTurn && phase === "bat_ball");
  clearChildren("#match-shot-grid");
  if (myTurn && phase === "bat_ball") {
    document.querySelector("#match-shot-title").textContent = match.pending_delivery
      ? `CHOOSE YOUR SHOT · FACING ${match.pending_delivery.label.toUpperCase()}`
      : "CHOOSE YOUR SHOT";
    const grid = document.querySelector("#match-shot-grid");
    (match.shot_options || []).forEach((opt) => {
      grid.append(pickerButton(opt.code, opt.label, {
        onClick: () => matchAction("play_shot", { shot_code: opt.code }),
      }));
    });
  }
}

function renderMatchPicker(match, phase) {
  const grid = document.querySelector("#match-picker-grid");
  const titleEl = document.querySelector("#match-picker-title");
  const confirmBtn = document.querySelector("#match-picker-confirm");
  grid.replaceChildren();
  if (phase === "select_bowler") {
    titleEl.textContent = `PICK THE BOWLER · MAX ${match.max_bowler_overs} OVER${match.max_bowler_overs === 1 ? "" : "S"} EACH`;
    (match.available_bowlers || []).forEach((b) => {
      const detail = `${b.bowling_style} · ${b.overs_bowled}/${b.overs_max} ov${b.reason ? ` · ${b.reason}` : ""}`;
      grid.append(pickerButton(b.name, detail, {
        disabled: !b.eligible,
        onClick: () => matchAction("select_bowler", { bowler_id: b.player_id }),
      }));
    });
    return;
  }
  const batters = match.available_batters || [];
  if (phase === "select_next_batter") {
    titleEl.textContent = "SEND IN THE NEXT BATTER (ANY AVAILABLE PLAYER)";
    batters.forEach((p) => {
      grid.append(pickerButton(p.name, [p.role, p.style_label].filter(Boolean).join(" · "), {
        onClick: () => matchAction("select_next_batter", { batter_id: p.player_id }),
      }));
    });
    return;
  }
  // select_openers: two-step pick
  const key = `${match.match_id}:${match.innings_number}:${match.revision}`;
  if (key !== openerPickKey) {
    openerPickKey = key;
    openerPick = [];
  }
  titleEl.textContent = "PICK TWO OPENERS · FIRST PICK FACES THE FIRST BALL";
  batters.forEach((p) => {
    const index = openerPick.indexOf(p.player_id);
    grid.append(pickerButton(p.name, [p.role, p.style_label].filter(Boolean).join(" · "), {
      selected: index >= 0,
      title: index === 0 ? "On strike" : index === 1 ? "Non-striker" : "",
      onClick: () => {
        if (index >= 0) openerPick.splice(index, 1);
        else if (openerPick.length < 2) openerPick.push(p.player_id);
        renderMatchPicker(match, phase);
      },
    }));
  });
  confirmBtn.disabled = openerPick.length !== 2;
}

function renderScorecardModal(result) {
  const panel = document.querySelector("#match-scorecard-panel");
  panel.classList.remove("hidden");
  document.querySelector("#sc-match-title").textContent =
    `${result.winner_team_name} vs ${result.loser_team_name}`;
  document.querySelector("#sc-match-margin").textContent =
    `${result.margin} · Player of the Match: ${result.player_of_the_match?.player_name || "—"}`;

  const body = document.querySelector("#sc-body");
  body.replaceChildren();

  const allInnings = [...(result.innings || []), ...(result.super_over_innings || [])];
  allInnings.forEach((inn) => {
    const block = document.createElement("div");
    block.className = "innings-scorecard-block";
    const heading = document.createElement("h4");
    heading.textContent = `${inn.is_super_over ? "[Super Over] " : ""}${inn.batting_team_name} — ${inn.runs}/${inn.wickets} (${inn.overs} ov)`;
    block.append(heading);

    const batTable = document.createElement("table");
    batTable.className = "player-table";
    batTable.innerHTML = `<thead><tr><th>BATTER</th><th>DISMISSAL</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead>`;
    const batBody = document.createElement("tbody");
    (inn.batting || []).forEach((b) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${b.player_name}</td><td>${b.dismissal}</td><td class="price-cell">${b.runs}</td><td>${b.balls}</td><td>${b.fours}</td><td>${b.sixes}</td><td>${b.strike_rate.toFixed(1)}</td>`;
      batBody.append(tr);
    });
    batTable.append(batBody);
    block.append(batTable);

    const bowlTable = document.createElement("table");
    bowlTable.className = "player-table";
    bowlTable.innerHTML = `<thead><tr><th>BOWLER</th><th>O</th><th>M</th><th>R</th><th>W</th><th>ECON</th></tr></thead>`;
    const bowlBody = document.createElement("tbody");
    (inn.bowling || []).forEach((w) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${w.player_name}</td><td>${w.overs}</td><td>${w.maidens}</td><td>${w.runs_conceded}</td><td class="price-cell">${w.wickets}</td><td>${w.economy.toFixed(2)}</td>`;
      bowlBody.append(tr);
    });
    bowlTable.append(bowlBody);
    block.append(bowlTable);

    body.append(block);
  });
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderOversSetting(state, isAdmin) {
  const locked = Boolean(state.tournament_overs_locked);
  const overs = String(state.tournament_overs ?? 20);
  const auctionSelect = document.querySelector("#room-overs-setting");
  auctionSelect.value = overs;
  auctionSelect.disabled = locked;
  const field = document.querySelector("#tournament-overs-field");
  field.classList.toggle("hidden", !isAdmin);
  const tournamentSelect = document.querySelector("#tournament-overs-setting");
  tournamentSelect.value = overs;
  tournamentSelect.disabled = locked;
  tournamentSelect.title = locked ? "Match overs are locked once a tournament match has started." : "";
}

function renderAdminControls(state, isAdmin) {
  const controls = document.querySelector("#auction-admin-settings");
  const timerSelect = document.querySelector("#room-timer-setting");
  const applyTimerButton = document.querySelector("#apply-timer-button");
  const pauseButton = document.querySelector("#pause-button");
  controls.classList.toggle("hidden", !isAdmin || state.status === "complete");

  const canChangeTimer = state.status === "lobby" || state.status === "between";
  timerSelect.value = String(state.timer_seconds);
  timerSelect.disabled = !canChangeTimer;
  applyTimerButton.disabled = !canChangeTimer;
  applyTimerButton.title = canChangeTimer
    ? "Apply this timer to the next lot."
    : "Change the timer between lots.";

  const canPause = state.status === "live" || state.status === "between";
  pauseButton.classList.toggle("hidden", !isAdmin || (!canPause && state.status !== "paused"));
  pauseButton.textContent = state.status === "paused" ? "Resume auction" : "Pause auction";
  pauseButton.classList.toggle("button-resume", state.status === "paused");
}

function showHammerAnnouncement(result) {
  const announcement = document.querySelector("#hammer-announcement");
  const sold = result.status === "sold";
  document.querySelector("#hammer-overline").textContent = sold ? "SOLD!" : "UNSOLD";
  document.querySelector("#hammer-player").textContent = result.player_name;
  document.querySelector("#hammer-award").classList.toggle("hidden", !sold);
  document.querySelector("#hammer-unsold-note").classList.toggle("hidden", sold);
  if (sold) {
    const team = currentState.teams.find((item) => item.team_id === result.team_id);
    const logo = document.querySelector("#hammer-team-logo");
    logo.replaceChildren();
    if (team) logo.append(makeTeamLogo(team));
    document.querySelector("#hammer-team").textContent = result.team_name || team?.name || "Franchise";
    document.querySelector("#hammer-price").textContent = auctionMoney(result.price_crore);
    document.querySelector("#hammer-role").textContent = result.squad_count
      ? `SQUAD ${result.squad_count}/${result.squad_capacity || 15}`
      : "";
  }
  announcement.classList.remove("hammer-visible", "hammer-run");
  announcement.setAttribute("aria-hidden", "true");
  void announcement.offsetWidth;
  announcement.classList.add("hammer-visible", "hammer-run");
  announcement.setAttribute("aria-hidden", "false");
  clearTimeout(announcementTimeout);
  announcementTimeout = setTimeout(() => {
    announcement.classList.remove("hammer-visible", "hammer-run");
    announcement.setAttribute("aria-hidden", "true");
  }, 3200);
}

function renderPlayer(state) {
  const player = state.status === "complete" ? null : state.current_player;
  const isAdmin = Boolean(currentSession?.adminToken);
  const ownTeam = state.teams.find((team) => team.team_id === currentSession?.teamId);
  const livePill = document.querySelector("#auction-status");
  livePill.classList.toggle("live", state.status === "live");
  livePill.classList.toggle("complete", state.status === "complete");
  livePill.classList.toggle("paused", state.status === "paused");
  const statusLabels = {
    lobby: isAdmin ? "READY TO START" : "WAITING FOR HOST",
    live: "BIDDING LIVE",
    between: "HAMMER DOWN",
    paused: "AUCTION PAUSED",
    complete: "AUCTION COMPLETE",
  };
  livePill.innerHTML = `<i></i>${statusLabels[state.status] || "AUCTION"}`;
  document.querySelector("#player-counter").textContent = player
    ? `PLAYER ${String(state.current_index + 1).padStart(2, "0")} / ${String(state.player_count).padStart(2, "0")}`
    : state.status === "complete"
      ? `PLAYER ${String(state.player_count).padStart(2, "0")} / ${String(state.player_count).padStart(2, "0")}`
      : state.status === "lobby" ? `${state.player_count} PLAYERS READY` : "PLAYER -- / --";

  const lobbyContent = isAdmin
    ? {
      role: "HOST & AUCTIONEER",
      title: "Auction ready to start",
      description: `Click Start auction to call the first player. You will bid for ${ownTeam?.name || "your franchise"}.`,
    }
    : {
      role: "AUCTION LOBBY",
      title: "Waiting for the host",
      description: "The host will start the first lot. Then you can bid for your selected franchise.",
    };
  document.querySelector("#player-role").textContent = player
    ? `${player.role || "Player"}${player.nationality ? `  /  ${player.nationality}` : ""}`
    : state.status === "complete" ? "AUCTION CLOSED" : lobbyContent.role;
  const completeTitles = {
    squads_full: "Every franchise has a full squad",
    no_buyers: "No franchise can sign anyone else",
  };
  document.querySelector("#current-player-name").textContent = player
    ? player.name
    : state.status === "complete"
      ? completeTitles[state.complete_reason] || "Every lot has been called"
      : lobbyContent.title;
  if (player?.elite) document.querySelector("#current-player-name").append(makeEliteBadge());
  document.querySelector("#player-country").textContent = player
    ? `${player.nationality || "Nationality not set"} • ${player.role || "Role not set"}`
    : state.status === "complete"
      ? "Pick and lock your Playing XI below, then review the sold and unsold players."
      : lobbyContent.description;
  const portrait = document.querySelector("#current-player-portrait");
  portrait.replaceChildren();
  portrait.classList.toggle("hidden", !player);
  if (player) portrait.append(makePlayerPhoto(player, "player-photo-large"));
  document.querySelector("#base-price").textContent = player ? money(player.base_price_crore) : "—";
  document.querySelector("#current-price").textContent = money(state.current_bid_crore);

  const stats = [
    ["MATCHES", player?.matches],
    ["RUNS", player?.runs],
    ["WICKETS", player?.wickets],
    ["AVG", player?.batting_average == null ? null : displayNumber(player.batting_average, 2)],
    ["STRIKE RATE", player?.strike_rate == null ? null : displayNumber(player.strike_rate, 2)],
    ["ECONOMY", player?.economy == null ? null : displayNumber(player.economy, 2)],
  ];
  const statGrid = document.querySelector("#stat-grid");
  statGrid.replaceChildren();
  stats.forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "stat-item";
    const title = document.createElement("span");
    title.textContent = label;
    const number = document.createElement("strong");
    number.textContent = value == null ? "—" : value;
    item.append(title, number);
    statGrid.append(item);
  });

  const highest = document.querySelector("#highest-bid");
  const blockReason = ownTeam?.bid_block_reason || null;
  const squadLine = ownTeam
    ? `Squad ${ownTeam.squad_size}/${state.squad_size} · Purse left ${money(ownTeam.purse_remaining_crore)}`
    : "";
  if (state.highest_team_name) {
    highest.replaceChildren();
    highest.append(document.createTextNode(state.highest_team_name));
    const sub = document.createElement("span");
    sub.textContent = ownTeam && state.highest_team_id === ownTeam.team_id
      ? `You are leading · ${squadLine}`
      : "Current highest bidder";
    highest.append(sub);
  } else {
    highest.replaceChildren(document.createTextNode("No bids yet"));
    const sub = document.createElement("span");
    sub.textContent = state.status === "paused"
      ? "Waiting for the host to resume."
      : state.status === "live" && !currentSession?.teamId
        ? "Join a franchise to place bids."
        : state.status === "live" && ownTeam && blockReason
          ? blockReason
          : state.status === "live"
            ? `Be first to bid. ${squadLine}`
            : state.status === "between"
              ? "The next player starts automatically after the hammer announcement."
              : state.status === "complete"
                ? "The auction is complete."
                : "Bidding has not opened.";
    highest.append(sub);
  }

  const bidButton = document.querySelector("#bid-button");
  const alreadyLeading = Boolean(
    currentSession?.teamId && state.highest_team_id === currentSession.teamId
  );
  const canBid = Boolean(
    player &&
    state.status === "live" &&
    currentSession?.teamId &&
    !alreadyLeading &&
    !blockReason
  );
  bidButton.disabled = !canBid;
  bidButton.innerHTML = currentSession?.teamId && state.highest_team_id === currentSession.teamId
    ? "You are leading"
    : "Raise bid <span>↗</span>";
  bidButton.title = canBid
    ? "Bid the base price or raise the current bid."
    : alreadyLeading
      ? "Your franchise is already leading this lot; another franchise must bid before you can raise."
      : state.status === "between"
        ? "Bidding has ended. The next player starts automatically."
        : state.status === "paused"
          ? "Bidding is paused by the host."
          : state.status === "live" && ownTeam && blockReason
            ? blockReason
            : state.status === "live" && !currentSession?.teamId
              ? "Join the room with a franchise to bid."
              : "Bidding is not open.";
}

function renderSetInfo(state) {
  const sets = state.sets;
  const player = state.status === "complete" ? null : state.current_player;
  const chip = document.querySelector("#current-set-chip");
  chip.textContent = player?.set_number ? `SET ${player.set_number}` : "";
  chip.classList.toggle("hidden", !player?.set_number);

  const progress = document.querySelector("#set-progress");
  progress.replaceChildren();
  progress.classList.toggle("hidden", !sets?.enabled);
  if (!sets?.enabled) return;
  progress.title = sets.used_fallback
    ? "The role minimums could not be met, so the closest sit-out draw was used."
    : "";
  sets.rows.forEach((row) => {
    const step = document.createElement("span");
    const done = row.called >= row.total;
    step.className = `set-step${row.number === sets.current ? " current" : ""}${done ? " done" : ""}`;
    step.textContent = `${row.label} · ${row.called}/${row.total}`;
    progress.append(step);
  });
}

function updateSetBanner(state) {
  const banner = document.querySelector("#set-banner");
  const info = state.set_break;
  clearTimeout(setBannerTimeout);
  let show = false;
  if (info && (state.status === "between" || state.status === "paused")) {
    const remaining = state.status === "paused"
      ? state.paused_remaining_seconds
      : Math.max(0, (state.advance_deadline_at * 1000 - Date.now()) / 1000);
    if (remaining != null && remaining <= info.seconds) {
      show = true;
    } else if (remaining != null && state.status === "between") {
      setBannerTimeout = setTimeout(() => {
        if (currentState === state) updateSetBanner(state);
      }, (remaining - info.seconds) * 1000 + 30);
    }
  }
  if (show) {
    document.querySelector("#set-banner-title").textContent = `${info.label} BEGINS`;
    document.querySelector("#set-banner-note").textContent = `${info.players} players go under the hammer`;
  }
  banner.classList.toggle("visible", show);
  banner.setAttribute("aria-hidden", String(!show));
}

function renderTeams(state) {
  const list = document.querySelector("#team-list");
  list.replaceChildren();
  state.teams.forEach((team) => {
    const row = document.createElement("div");
    row.className = `team-row${team.team_id === state.highest_team_id ? " highest" : ""}`;
    row.append(makeTeamLogo(team));
    const name = document.createElement("span");
    name.className = "team-name";
    name.textContent = team.name;
    const owner = document.createElement("small");
    owner.className = "team-owner";
    owner.textContent = `${team.captain || "Available"} · ${team.players_bought}/${team.squad_capacity || 15} players`;
    name.append(owner);
    const purse = document.createElement("strong");
    purse.className = "team-purse";
    purse.textContent = money(team.purse_remaining_crore);
    row.append(name, purse);
    list.append(row);
  });
}

function setLiveBoardTab(tab) {
  activeLiveTab = tab;
  document.querySelectorAll(".live-board-tab").forEach((button) => {
    const active = button.dataset.liveTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelector("#live-player-panel").classList.toggle("hidden", tab === "teams");
  document.querySelector("#live-team-panel").classList.toggle("hidden", tab !== "teams");
  if (currentState) renderLiveBoard(currentState);
}

function renderLiveBoard(state) {
  const resultById = new Map(state.results.map((result) => [result.player_id, result]));
  const called = state.results.length;
  const sold = state.results.filter((result) => result.status === "sold").length;
  const unsold = state.results.length - sold;
  const tabCounts = { all: state.player_count, sold, unsold, teams: state.teams.length };
  document.querySelector("#live-board-summary").textContent =
    `${called} / ${state.player_count} PLAYERS CALLED`;
  document.querySelectorAll(".live-board-tab").forEach((button) => {
    const labels = { all: "All Players", sold: "Sold", unsold: "Unsold", teams: "Teams" };
    button.textContent = `${labels[button.dataset.liveTab]} (${tabCounts[button.dataset.liveTab]})`;
  });

  if (activeLiveTab === "teams") {
    renderLiveTeams(state);
    return;
  }

  const labels = ["PLAYER", "ROLE", "BASE PRICE", "STATUS", "SOLD FOR", "FRANCHISE"];
  const head = document.querySelector("#live-player-head");
  head.replaceChildren(...labels.map((label) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    return cell;
  }));
  const body = document.querySelector("#live-player-body");
  const rows = state.players.filter((player) => {
    if (activeLiveTab === "sold") return player.live_status === "sold";
    if (activeLiveTab === "unsold") return player.live_status === "unsold";
    return true;
  });
  body.replaceChildren();
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "empty-row";
    cell.colSpan = labels.length;
    cell.textContent = activeLiveTab === "all"
      ? "The player lineup will appear when the auction starts."
      : `No players have been ${activeLiveTab} in this room yet.`;
    row.append(cell);
    body.append(row);
    return;
  }

  rows.forEach((player) => {
    const row = document.createElement("tr");
    if (player.live_status === "live") row.classList.add("live-current");
    row.append(makePlayerCell(player));

    [player.role, auctionMoney(player.base_price_crore)].forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === 1) cell.className = "price-cell";
      cell.textContent = value || "—";
      row.append(cell);
    });

    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    status.className = `status-chip status-${player.live_status}`;
    status.textContent = player.live_status === "upcoming"
      ? "Up next"
      : player.live_status === "live"
        ? "On the block"
        : player.live_status;
    statusCell.append(status);
    row.append(statusCell);

    const result = resultById.get(player.player_id);
    const price = document.createElement("td");
    price.className = "price-cell";
    price.textContent = result?.status === "sold" ? auctionMoney(result.price_crore) : "—";
    row.append(price);

    const franchise = document.createElement("td");
    const team = state.teams.find((item) => item.team_id === result?.team_id);
    if (team) {
      franchise.className = "result-team";
      franchise.append(makeTeamLogo(team));
      franchise.append(document.createTextNode(team.short_name));
    } else {
      franchise.textContent = "—";
    }
    row.append(franchise);
    body.append(row);
  });
}

function renderLiveTeams(state) {
  const grid = document.querySelector("#live-team-grid");
  grid.replaceChildren();
  state.teams.forEach((team) => {
    const card = document.createElement("details");
    card.className = "live-team-card";
    const summary = document.createElement("summary");
    const heading = document.createElement("div");
    heading.className = "live-team-title";
    heading.append(makeTeamLogo(team));
    const identity = document.createElement("span");
    identity.className = "live-team-identity";
    const name = document.createElement("strong");
    name.textContent = team.name;
    const shortName = document.createElement("small");
    shortName.textContent = `${team.players_bought}/${team.squad_capacity || 15} players · ${auctionMoney(team.purse_remaining_crore)} left`;
    identity.append(name, shortName);
    heading.append(identity);
    const score = document.createElement("span");
    score.className = "score-badge";
    score.textContent = `${team.players_bought}/${team.squad_capacity || 15}`;
    heading.append(score);
    summary.append(heading);

    const chips = document.createElement("div");
    chips.className = "role-chips";
    const squadChips = [
      [`Squad ${team.players_bought}/${team.squad_capacity || 15}`, team.players_bought >= (team.squad_capacity || 15)],
      [`Overseas ${team.overseas_count}/${state.max_overseas_squad || 6}`, false],
    ];
    if (team.xi_locked) squadChips.push(["Playing XI locked", true]);
    squadChips.forEach(([text, done]) => {
      const chip = document.createElement("span");
      chip.className = `role-chip${done ? " complete" : ""}`;
      chip.textContent = text;
      chips.append(chip);
    });
    summary.append(chips);
    const hint = document.createElement("span");
    hint.className = "team-expand-hint";
    hint.textContent = "Select to view this franchise’s players";
    summary.append(hint);

    const roster = document.createElement("div");
    roster.className = "roster-list";
    if (!team.roster.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No players bought yet.";
      roster.append(empty);
    } else {
      team.roster.forEach((player) => {
        const row = document.createElement("div");
        row.className = "roster-row";
        row.append(makePlayerPhoto(player, "player-photo-roster"));
        const details = document.createElement("span");
        details.className = "roster-player";
        const playerName = document.createElement("strong");
        playerName.textContent = player.name;
        if (player.elite) playerName.append(makeEliteBadge());
        const role = document.createElement("small");
        role.textContent = [player.role, player.style_label, player.overseas ? "Overseas" : ""]
          .filter(Boolean)
          .join(" · ");
        details.append(playerName, role);
        const price = document.createElement("strong");
        price.className = "price-cell";
        price.textContent = auctionMoney(player.price_crore);
        row.append(details, price);
        roster.append(row);
      });
    }
    card.append(summary, roster);
    grid.append(card);
  });
}

function renderSales(state) {
  const list = document.querySelector("#sales-list");
  document.querySelector("#sale-count").textContent = `${state.results.length} ${state.results.length === 1 ? "SALE" : "SALES"}`;
  list.replaceChildren();
  if (!state.results.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "The first hammer is waiting.";
    list.append(empty);
    return;
  }
  state.results.slice().reverse().slice(0, 8).forEach((sale) => {
    const row = document.createElement("div");
    row.className = "sale-row";
    const playerRecord = state.players.find((player) => player.player_id === sale.player_id);
    if (playerRecord) row.append(makePlayerPhoto(playerRecord, "player-photo-sale"));
    const details = document.createElement("div");
    const player = document.createElement("div");
    player.className = `sale-player${sale.status === "unsold" ? " sale-unsold" : ""}`;
    player.textContent = sale.player_name;
    const team = document.createElement("span");
    team.className = "sale-team";
    team.textContent = sale.team_name || "UNSOLD";
    details.append(player, team);
    const price = document.createElement("strong");
    price.className = "sale-price";
    price.textContent = sale.price_crore == null ? "—" : money(sale.price_crore);
    row.append(details, price);
    list.append(row);
  });
}

function xiRequired(state, ownTeam) {
  return ownTeam ? state.xi.required[ownTeam.team_id] ?? state.xi_size : 0;
}

function xiOverseasCount(ownTeam) {
  return ownTeam.roster.filter((player) => xiSelection.has(player.player_id) && player.overseas).length;
}

function toggleXiPlayer(state, ownTeam, player) {
  if (xiSelection.has(player.player_id)) {
    xiSelection.delete(player.player_id);
  } else {
    if (xiSelection.size >= xiRequired(state, ownTeam)) {
      return showToast(`Your Playing XI already has ${xiSelection.size} players. Remove one first.`);
    }
    if (player.overseas && xiOverseasCount(ownTeam) >= state.max_overseas_xi) {
      return showToast(`A Playing XI can have at most ${state.max_overseas_xi} overseas players.`);
    }
    xiSelection.add(player.player_id);
  }
  renderXi(state);
}

function makeXiCard(state, ownTeam, player, selected, interactive) {
  const card = document.createElement(interactive ? "button" : "div");
  card.className = `xi-card${selected ? " selected" : ""}`;
  if (interactive) {
    card.type = "button";
    card.setAttribute("aria-pressed", String(selected));
    card.addEventListener("click", () => toggleXiPlayer(state, ownTeam, player));
  }
  card.append(makePlayerPhoto(player, "player-photo-roster"));
  const details = document.createElement("span");
  details.className = "xi-card-details";
  const name = document.createElement("strong");
  name.textContent = player.name;
  if (player.elite) name.append(makeEliteBadge());
  const meta = document.createElement("small");
  meta.textContent = [player.role, player.style_label].filter(Boolean).join(" · ");
  const origin = document.createElement("small");
  origin.textContent = `${player.nationality || ""}${player.overseas ? " · Overseas" : ""}`;
  details.append(name, meta, origin);
  card.append(details);
  return card;
}

function renderXi(state) {
  const panel = document.querySelector("#xi-panel");
  const open = state.status === "complete" && Boolean(state.xi && state.xi.open);
  panel.classList.toggle("hidden", !open);
  if (!open) return;

  const isAdmin = Boolean(currentSession?.adminToken);
  const ownTeam = state.teams.find((team) => team.team_id === currentSession?.teamId);
  const mine = state.my_xi || null;
  const xi = state.xi;
  const required = xiRequired(state, ownTeam);
  const pickArea = document.querySelector("#xi-pick");
  const resultArea = document.querySelector("#xi-result");
  const counter = document.querySelector("#xi-counter");

  if (ownTeam) {
    const rosterIds = new Set(ownTeam.roster.map((player) => player.player_id));
    xiSelection = new Set([...xiSelection].filter((id) => rosterIds.has(id)));
  }

  pickArea.classList.toggle("hidden", !ownTeam || Boolean(mine));
  resultArea.classList.toggle("hidden", !mine);

  document.querySelector("#xi-title").textContent = mine
    ? "Your Playing XI is locked"
    : ownTeam ? "Select your Playing XI" : "Franchises are picking their Playing XI";
  document.querySelector("#xi-subtitle").textContent = mine
    ? "Your lineup cannot be changed now."
    : ownTeam
      ? `Choose exactly ${required} of your ${ownTeam.roster.length} players. At most ${state.max_overseas_xi} can be overseas. Once you lock, it cannot be changed.`
      : "Each franchise owner is choosing and locking a Playing XI.";

  if (ownTeam && !mine) {
    counter.textContent = `${xiSelection.size} / ${required} SELECTED · OVERSEAS ${xiOverseasCount(ownTeam)}/${state.max_overseas_xi}`;
    const grid = document.querySelector("#xi-grid");
    grid.replaceChildren();
    if (!ownTeam.roster.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Your franchise did not buy any players.";
      grid.append(empty);
    }
    ownTeam.roster.forEach((player) => {
      grid.append(makeXiCard(state, ownTeam, player, xiSelection.has(player.player_id), true));
    });
    const lockButton = document.querySelector("#xi-lock-button");
    lockButton.disabled = xiSelection.size !== required;
    document.querySelector("#xi-status").textContent = xiSelection.size === required
      ? "Ready to lock."
      : `Pick ${required - xiSelection.size} more.`;
  } else if (mine) {
    counter.textContent = "LOCKED";
    document.querySelector("#xi-rating-value").textContent = Number(mine.rating).toFixed(1);
    document.querySelector("#xi-rank").textContent = mine.rank
      ? `Rank ${mine.rank} of ${xi.rankings.length}`
      : "Rank shown when every franchise has locked";
    const lines = document.querySelector("#xi-breakdown");
    lines.replaceChildren();
    mine.breakdown.forEach((line) => {
      const row = document.createElement("div");
      row.className = `xi-breakdown-row${line.points < 0 ? " negative" : ""}`;
      const label = document.createElement("span");
      label.textContent = line.label;
      const points = document.createElement("strong");
      points.textContent = line.points > 0 && line.label !== "Playing XI strength"
        ? `+${line.points.toFixed(1)}`
        : line.points.toFixed(1);
      row.append(label, points);
      lines.append(row);
    });
    const lockedList = document.querySelector("#xi-locked-list");
    lockedList.replaceChildren();
    const chosen = new Set(mine.player_ids);
    ownTeam.roster.filter((player) => chosen.has(player.player_id)).forEach((player) => {
      lockedList.append(makeXiCard(state, ownTeam, player, true, false));
    });
  } else {
    counter.textContent = `${xi.locked_team_ids.length} / ${xi.locked_team_ids.length + xi.waiting_team_ids.length} LOCKED`;
  }

  const waiting = document.querySelector("#xi-waiting");
  const nameOf = (teamId) => state.teams.find((team) => team.team_id === teamId)?.name || teamId;
  waiting.classList.toggle("all-locked", xi.all_locked);
  waiting.textContent = xi.all_locked
    ? "All Playing XIs are locked. Tournament fixtures are ready in the Tournament tab."
    : `Waiting for ${xi.waiting_team_ids.length} franchise${xi.waiting_team_ids.length === 1 ? "" : "s"} to lock: ${xi.waiting_team_ids.map(nameOf).join(", ")}`;

  const ranking = document.querySelector("#xi-ranking");
  ranking.classList.toggle("hidden", !xi.all_locked);
  if (xi.all_locked) {
    const body = document.querySelector("#xi-ranking-body");
    body.replaceChildren();
    xi.rankings.forEach((entry) => {
      const row = document.createElement("div");
      row.className = `xi-ranking-row${entry.team_id === currentSession?.teamId ? " own" : ""}`;
      const rank = document.createElement("span");
      rank.className = "xi-rank-number";
      rank.textContent = entry.rank;
      const team = state.teams.find((item) => item.team_id === entry.team_id);
      const logo = document.createElement("span");
      if (team) logo.append(makeTeamLogo(team));
      const name = document.createElement("strong");
      name.textContent = entry.name;
      const rating = document.createElement("span");
      rating.className = "xi-rank-rating";
      rating.textContent = Number(entry.rating).toFixed(1);
      row.append(rank, logo, name, rating);
      body.append(row);
    });
  }

  document.querySelector("#xi-host").classList.toggle(
    "hidden",
    !isAdmin || xi.all_locked || !xi.waiting_team_ids.length,
  );
}

function updateTimer(state) {
  const bar = document.querySelector("#timer-bar");
  const value = document.querySelector("#timer-value");
  const caption = document.querySelector(".timer-label span");
  if (state.status === "paused") {
    const remaining = state.paused_remaining_seconds;
    caption.textContent = state.paused_from_status === "between"
      ? "NEXT PLAYER"
      : "HAMMER CLOCK";
    bar.style.width = remaining == null
      ? "0%"
      : `${Math.max(0, remaining / state.timer_seconds * 100)}%`;
    value.textContent = remaining == null
      ? "AUCTION PAUSED"
      : state.paused_from_status === "between"
        ? `PAUSED · NEXT PLAYER IN ${remaining.toFixed(1)}s`
        : `PAUSED · ${remaining.toFixed(1)}s`;
    return;
  }
  if (state.status === "between" && state.advance_deadline_at) {
    caption.textContent = "AUTO ADVANCE";
    const remaining = Math.max(0, (state.advance_deadline_at * 1000 - Date.now()) / 1000);
    const transitionSeconds = state.advance_delay_seconds || 3.2;
    value.textContent = `NEXT PLAYER IN ${remaining.toFixed(1)}s`;
    bar.style.width = `${Math.max(0, remaining / transitionSeconds * 100)}%`;
    if (remaining > 0) requestAnimationFrame(() => {
      if (currentState === state) updateTimer(state);
    });
    return;
  }
  caption.textContent = "HAMMER CLOCK";
  if (state.status !== "live" || !state.deadline_at) {
    bar.style.width = "0%";
    value.textContent = state.status === "between" ? "NEXT PLAYER SOON" : "—";
    return;
  }

  const remaining = Math.max(0, (state.deadline_at * 1000 - Date.now()) / 1000);
  value.textContent = `${remaining.toFixed(1)}s`;
  bar.style.width = `${Math.max(0, remaining / state.timer_seconds * 100)}%`;
  if (remaining > 0) requestAnimationFrame(() => {
    if (currentState === state) updateTimer(state);
  });
}

document.querySelectorAll(".auction-tab[data-tab]").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});
document.querySelectorAll("[data-room-tab]").forEach((button) => {
  button.addEventListener("click", () => setRoomTab(button.dataset.roomTab));
});
document.querySelectorAll("[data-tab-link]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.tabLink;
    document.querySelector(`[data-tab="${target}"]`).focus();
    setActiveTab(target);
  });
});
document.querySelectorAll(".live-board-tab").forEach((button) => {
  button.addEventListener("click", () => setLiveBoardTab(button.dataset.liveTab));
});
playerSearch.addEventListener("input", () => renderPlayerTable(activeTab));
roleFilter.addEventListener("change", () => renderPlayerTable(activeTab));
createForm.addEventListener("submit", createRoom);
joinForm.addEventListener("submit", joinRoom);
document.querySelector("#start-button").addEventListener("click", () => sendAction("start"));
document.querySelector("#apply-timer-button").addEventListener("click", () => sendAction(
  "set_timer",
  { timer_seconds: Number(document.querySelector("#room-timer-setting").value) },
));
document.querySelector("#pause-button").addEventListener("click", () => {
  if (!currentState) return;
  sendAction(currentState.status === "paused" ? "resume" : "pause");
});
document.querySelector("#bid-button").addEventListener("click", () => sendAction("bid"));
document.querySelector("#xi-lock-button").addEventListener("click", () => {
  sendAction("lock_xi", { player_ids: [...xiSelection] });
});
document.querySelector("#xi-autolock-button").addEventListener("click", () => sendAction("auto_lock"));
document.querySelector("#join-room-code").addEventListener("input", (event) => {
  event.target.value = event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
});
document.querySelectorAll(".speed-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (!activeMatchId) return;
    sendAction("set_match_speed", { match_id: activeMatchId, speed: btn.dataset.speed });
  });
});
["#room-overs-setting", "#tournament-overs-setting"].forEach((selector) => {
  document.querySelector(selector).addEventListener("change", (event) => {
    sendAction("set_tournament_overs", { total_overs: Number(event.target.value) });
  });
});
document.querySelector("#simulate-ball-btn").addEventListener("click", () => matchAction("simulate_ball"));
document.querySelectorAll("[data-toss-call]").forEach((btn) => {
  btn.addEventListener("click", () => matchAction("call_toss", { call: btn.dataset.tossCall }));
});
document.querySelectorAll("[data-toss-decision]").forEach((btn) => {
  btn.addEventListener("click", () => matchAction("decide_toss", { decision: btn.dataset.tossDecision }));
});
document.querySelector("#match-picker-confirm").addEventListener("click", () => {
  if (openerPick.length === 2) {
    matchAction("select_openers", { striker_id: openerPick[0], non_striker_id: openerPick[1] });
  }
});
document.querySelector("#leave-match-btn").addEventListener("click", () => {
  if (activeMatchId) {
    sendAction("leave_match", { match_id: activeMatchId });
  }
  activeMatchId = null;
  activeMatchRole = null;
  activeMatchState = null;
  document.querySelector("#live-match-panel").classList.add("hidden");
});
document.querySelector("#close-scorecard-btn").addEventListener("click", () => {
  document.querySelector("#match-scorecard-panel").classList.add("hidden");
});

loadCatalog();
const savedSession = sessionStorage.getItem("auction-session");
if (savedSession) {
  try {
    currentSession = JSON.parse(savedSession);
    if (currentSession.roomCode && currentSession.name) connect(currentSession);
  } catch {
    sessionStorage.removeItem("auction-session");
  }
}
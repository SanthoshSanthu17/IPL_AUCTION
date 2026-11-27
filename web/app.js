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

function setActiveTab(tab) {
  activeTab = tab;
  const panelId = tab === "overview"
    ? "overview-panel"
    : tab === "teams" ? "teams-panel" : "players-panel";

  document.querySelectorAll(".auction-tab").forEach((button) => {
    const selected = button.dataset.tab === tab;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== panelId);
  });

  if (tab !== "overview" && tab !== "teams") {
    document.querySelector("#players-panel").setAttribute(
      "aria-labelledby",
      `tab-${tab === "all-players" ? "all-players" : tab}`,
    );
    renderPlayerTable(tab);
  }
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
    renderPlayerTable(activeTab === "overview" || activeTab === "teams" ? "all-players" : activeTab);
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
    if (message.type === "state") renderState(message.state);
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
  document.querySelector("#auction-mode-label").textContent = state.status === "live"
    ? "LIVE AUCTION"
    : state.status === "paused" ? "AUCTION PAUSED"
      : state.status === "lobby" ? isAdmin ? "HOST & PARTICIPANT" : "AUCTION LOBBY"
        : state.status === "complete" ? "PLAYING XI" : "AUCTION ROOM";
  document.querySelector("#auction-heading").textContent = state.status === "complete"
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

  renderPlayer(state);
  renderSetInfo(state);
  updateSetBanner(state);
  renderTeams(state);
  renderSales(state);
  renderLiveBoard(state);
  renderXi(state);
  updateTimer(state);
  if (previousResultCount != null && state.results.length > previousResultCount) {
    showHammerAnnouncement(state.results[state.results.length - 1]);
  }
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
      // Wait for the hammer announcement to finish, then show the banner.
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
    ? "All Playing XIs are locked. Ready for the toss of Match 1."
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

document.querySelectorAll(".auction-tab").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
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
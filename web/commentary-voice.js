(() => {
  "use strict";

  // Spoken commentary for the live auction and for match play.
  // Uses the browser's built-in speech engine, so there is nothing to install or host.
  // It listens to the same WebSocket messages app.js receives and never touches app.js.
  const synth = window.speechSynthesis;
  if (!synth || typeof window.SpeechSynthesisUtterance !== "function") return;

  const VOICE_KEY = "iplAuctionVoice";
  const MUTE_KEY = "iplAuctionMuted";
  const SPEECH_RATE = 1.05;
  const SPEECH_VOLUME = 1;
  const LAST_FEW_SECONDS = 3.5;
  const MAX_PENDING_MATCH_LINES = 2;
  const SAY_LAKHS = true; // true: "2 crore 10 lakh", false: "2.1 crores"

  let enabled = true;
  try { enabled = localStorage.getItem(VOICE_KEY) !== "0"; } catch (error) { /* storage unavailable */ }

  const lineCounters = { bid: 0, raise: 0, sold: 0, unsold: 0, lot: 0 };

  // ---------- speech queue ----------
  let queue = [];
  let speaking = null;
  let watchdog = null;
  let chosenVoice = null;

  function soundIsMuted() {
    try { return localStorage.getItem(MUTE_KEY) === "1"; } catch (error) { return false; }
  }

  function canSpeak() {
    return enabled && !soundIsMuted();
  }

  function pickVoice() {
    const voices = synth.getVoices ? synth.getVoices() : [];
    if (!voices.length) return null;
    const english = voices.filter((voice) => /^en[-_]/i.test(voice.lang));
    const rank = (voice) => {
      let score = 0;
      if (/^en[-_]IN/i.test(voice.lang)) score += 4;
      if (/natural|online/i.test(voice.name)) score += 3;
      if (/^en[-_](GB|AU)/i.test(voice.lang)) score += 1;
      return score;
    };
    return english.sort((a, b) => rank(b) - rank(a))[0] || voices[0];
  }

  function finishCurrent() {
    clearTimeout(watchdog);
    speaking = null;
    playNext();
  }

  function playNext() {
    if (speaking) return;
    while (queue.length) {
      const item = queue.shift();
      if (item.expiresAt && Date.now() > item.expiresAt) continue;
      if (!canSpeak()) { queue = []; return; }
      const utterance = new SpeechSynthesisUtterance(item.text);
      if (!chosenVoice) chosenVoice = pickVoice();
      if (chosenVoice) { utterance.voice = chosenVoice; utterance.lang = chosenVoice.lang; }
      else utterance.lang = "en-IN";
      utterance.rate = item.rate || SPEECH_RATE;
      utterance.volume = SPEECH_VOLUME;
      speaking = item;
      utterance.onend = finishCurrent;
      utterance.onerror = finishCurrent;
      // Some browsers never fire onend; do not let one lost event silence the commentary.
      clearTimeout(watchdog);
      watchdog = setTimeout(finishCurrent, 2500 + item.text.length * 95);
      try { synth.speak(utterance); } catch (error) { finishCurrent(); }
      return;
    }
  }

  function interruptSpeech() {
    queue = [];
    clearTimeout(watchdog);
    speaking = null;
    try { synth.cancel(); } catch (error) { /* ignore */ }
  }

  function say(text, options = {}) {
    if (!canSpeak() || !text) return;
    if (options.interrupt) interruptSpeech();
    queue.push({ text, expiresAt: options.expiresAt || 0, rate: options.rate });
    if (options.maxPending && queue.length > options.maxPending) {
      queue = queue.slice(-options.maxPending);
    }
    playNext();
  }

  // ---------- wording helpers ----------
  function spokenPrice(crore) {
    const lakhTotal = Math.round(Number(crore) * 100);
    if (!Number.isFinite(lakhTotal)) return "";
    if (!SAY_LAKHS) {
      const value = Number((lakhTotal / 100).toFixed(2));
      return `${value} ${value === 1 ? "crore" : "crores"}`;
    }
    const wholeCrore = Math.floor(lakhTotal / 100);
    const lakh = lakhTotal % 100;
    if (wholeCrore === 0) return `${lakh} lakh`;
    if (lakh === 0) return `${wholeCrore} crore`;
    return `${wholeCrore} crore ${lakh} lakh`;
  }

  function spokenTeam(state, teamId, fallbackName) {
    const team = (state.teams || []).find((item) => item.team_id === teamId);
    const short = team && team.short_name ? String(team.short_name).trim() : "";
    if (/^[A-Z]{2,5}$/.test(short)) return short.split("").join(" ");
    return (team && team.name) || fallbackName || "a franchise";
  }

  function nextLine(kind, lines) {
    const line = lines[lineCounters[kind] % lines.length];
    lineCounters[kind] += 1;
    return line;
  }

  function withArticle(word) {
    return /^[aeiou]/i.test(word) ? `an ${word}` : `a ${word}`;
  }

  // ---------- auction commentary ----------
  const auction = {
    state: null,
    lotKey: null,
    bidKey: null,
    highestTeamId: null,
    resultCount: null,
    status: null,
    warnedDeadline: null,
  };

  function announceLot(state, player, first) {
    const role = player.role ? String(player.role).toLowerCase() : "";
    const nationality = player.nationality ? String(player.nationality) : "";
    let who = player.name;
    if (role && nationality) who += `, ${withArticle(role)} from ${nationality}`;
    else if (role) who += `, ${withArticle(role)}`;
    const opener = first
      ? "Ladies and gentlemen, the auction begins. First up,"
      : nextLine("lot", ["Next player,", "Up next,", "Coming under the hammer,"]);
    const elite = player.elite ? " An elite player!" : "";
    say(
      `${opener} ${who}.${elite} Starting bid, base price ${spokenPrice(player.base_price_crore)}. Is anyone interested?`,
    );
  }

  function announceBid(state, isFirstBid, previousTeamId) {
    const team = spokenTeam(state, state.highest_team_id, state.highest_team_name);
    const price = spokenPrice(state.current_bid_crore);
    let line;
    if (isFirstBid) {
      line = nextLine("bid", [
        `${team} bids ${price}!`,
        `${team} opens the bidding at ${price}!`,
        `We have a bid. ${team}, ${price}!`,
      ]);
    } else {
      const previous = previousTeamId && previousTeamId !== state.highest_team_id
        ? spokenTeam(state, previousTeamId)
        : "";
      line = nextLine("raise", [
        `${team} raises to ${price}!`,
        `We have ${team} with ${price}. Any advance?`,
        previous ? `${team} takes it to ${price}, over ${previous}!` : `${team} goes to ${price}!`,
        `${price} from ${team}. Who will go higher?`,
      ]);
    }
    say(line, { interrupt: true });
  }

  function announceLastFewSeconds(state) {
    const expiresAt = state.deadline_at * 1000;
    if (state.highest_team_id) {
      const team = spokenTeam(state, state.highest_team_id, state.highest_team_name);
      say(
        `Last few seconds! ${team} at ${spokenPrice(state.current_bid_crore)}. Going once. Going twice.`,
        { expiresAt },
      );
    } else {
      say("Last few seconds! No bids yet. Is anyone interested?", { expiresAt });
    }
  }

  function announceResult(state, result) {
    if (result.status === "sold") {
      const team = spokenTeam(state, result.team_id, result.team_name);
      const price = spokenPrice(result.price_crore);
      say(
        nextLine("sold", [
          `Sold! ${result.player_name} goes to ${team} for ${price}!`,
          `Sold to ${team}, for ${price}! ${result.player_name} is on board.`,
          `Hammer down! ${team} get ${result.player_name} for ${price}.`,
        ]),
        { interrupt: true },
      );
    } else {
      say(
        nextLine("unsold", [
          `Unsold. No takers for ${result.player_name}.`,
          `${result.player_name} goes unsold.`,
        ]),
        { interrupt: true },
      );
    }
  }

  function onAuctionState(state) {
    const first = auction.state === null;
    const previousStatus = auction.status;
    auction.state = state;
    auction.status = state.status;

    const results = state.results || [];
    if (!first && auction.resultCount !== null && results.length > auction.resultCount) {
      announceResult(state, results[results.length - 1]);
    }
    auction.resultCount = results.length;

    const player = state.status === "complete" ? null : state.current_player;
    if (state.status === "live" && player) {
      const lotKey = String(player.player_id || player.name);
      if (lotKey !== auction.lotKey) {
        auction.lotKey = lotKey;
        auction.bidKey = null;
        auction.highestTeamId = null;
        auction.warnedDeadline = null;
        if (!first) announceLot(state, player, results.length === 0);
      }
      const bidKey = state.highest_team_id
        ? `${state.highest_team_id}|${state.current_bid_crore}`
        : null;
      if (bidKey && bidKey !== auction.bidKey && !first) {
        announceBid(state, auction.bidKey === null, auction.highestTeamId);
      }
      auction.bidKey = bidKey;
      auction.highestTeamId = state.highest_team_id || null;
    }

    if (state.status === "complete" && previousStatus && previousStatus !== "complete") {
      say("And that brings the auction to a close. Time to lock your playing eleven.");
    }
  }

  function watchHammerClock() {
    setInterval(() => {
      const state = auction.state;
      if (!state || state.status !== "live" || !state.deadline_at || !canSpeak()) return;
      const remaining = state.deadline_at - Date.now() / 1000;
      if (remaining <= 0 || remaining > LAST_FEW_SECONDS) return;
      if (auction.warnedDeadline === state.deadline_at) return;
      auction.warnedDeadline = state.deadline_at;
      announceLastFewSeconds(state);
    }, 200);
  }

  // ---------- match commentary ----------
  const matches = new Map();
  const HIGH_PRIORITY = /\b(six|four|wicket|out|bowled|caught|stumped|lbw|run out|timber)\b/i;

  function onMatchMessage(message) {
    const match = message.match;
    if (!match || !match.match_id || !Array.isArray(match.commentary_feed)) return;
    let seen = matches.get(match.match_id);
    const firstSight = !seen;
    if (firstSight) {
      seen = new Set();
      matches.set(match.match_id, seen);
    }
    const unseen = [];
    for (let index = match.commentary_feed.length - 1; index >= 0; index -= 1) {
      const entry = match.commentary_feed[index];
      const key = `${entry.sequence}|${entry.text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unseen.push(entry);
    }
    if (!unseen.length) return;
    if (firstSight && message.type !== "match_started") return; // rejoin: stay quiet about history
    const lines = unseen.slice(-3);
    if (lines.some((entry) => HIGH_PRIORITY.test(entry.text))) interruptSpeech();
    lines.forEach((entry) => say(entry.text, { maxPending: MAX_PENDING_MATCH_LINES }));
  }

  // ---------- socket tap ----------
  function handleMessage(message) {
    if (!message || typeof message !== "object") return;
    if (message.type === "state" && message.state) {
      onAuctionState(message.state);
    } else if (message.match) {
      onMatchMessage(message);
    }
  }

  const NativeWebSocket = window.WebSocket;
  function TappedWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new NativeWebSocket(url)
      : new NativeWebSocket(url, protocols);
    socket.addEventListener("message", (event) => {
      try { handleMessage(JSON.parse(event.data)); } catch (error) { /* never disturb the game */ }
    });
    return socket;
  }
  TappedWebSocket.prototype = NativeWebSocket.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach((name) => { TappedWebSocket[name] = NativeWebSocket[name]; });
  window.WebSocket = TappedWebSocket;

  // ---------- on/off button ----------
  let voiceButton = null;
  function renderVoiceButton() {
    if (!voiceButton) return;
    voiceButton.style.opacity = enabled ? "1" : "0.45";
    voiceButton.title = enabled ? "Turn spoken commentary off" : "Turn spoken commentary on";
    voiceButton.setAttribute("aria-label", voiceButton.title);
    voiceButton.setAttribute("aria-pressed", enabled ? "true" : "false");
  }

  function setEnabled(value) {
    enabled = value;
    try { localStorage.setItem(VOICE_KEY, enabled ? "1" : "0"); } catch (error) { /* ignore */ }
    if (!enabled) interruptSpeech();
    renderVoiceButton();
    if (enabled) say("Commentary on.", { interrupt: true });
  }

  function createVoiceButton() {
    if (voiceButton) return;
    voiceButton = document.createElement("button");
    voiceButton.type = "button";
    voiceButton.id = "voice-toggle";
    voiceButton.textContent = "🎙️";
    voiceButton.style.cssText = [
      "position:fixed", "right:16px", "bottom:68px", "z-index:1000",
      "width:44px", "height:44px", "border-radius:50%", "border:1px solid rgba(255,255,255,.35)",
      "background:rgba(10,20,40,.8)", "color:#fff", "font-size:20px", "cursor:pointer",
    ].join(";");
    voiceButton.addEventListener("click", () => setEnabled(!enabled));
    document.body.append(voiceButton);
    renderVoiceButton();
  }

  // Browsers only allow speech after the page has had a click or key press.
  function unlockOnFirstGesture() {
    const unlock = () => {
      try {
        const primer = new SpeechSynthesisUtterance(" ");
        primer.volume = 0;
        synth.speak(primer);
      } catch (error) { /* ignore */ }
      ["pointerdown", "keydown", "touchstart"].forEach((name) => document.removeEventListener(name, unlock));
    };
    ["pointerdown", "keydown", "touchstart"].forEach((name) => document.addEventListener(name, unlock));
  }

  window.AuctionVoice = {
    isEnabled: () => canSpeak(),
    spokenPrice,
  };

  if (synth.addEventListener) synth.addEventListener("voiceschanged", () => { chosenVoice = pickVoice(); });
  document.addEventListener("DOMContentLoaded", createVoiceButton);
  if (document.readyState !== "loading") createVoiceButton();
  watchHammerClock();
  unlockOnFirstGesture();
})();
(() => {
  const AUDIO_BASE = "/assets/audio/";
  const WELCOME_FILE = "welcome.mp3";
  const WELCOME_VOLUME = 0.5;
  const TEAM_VOLUME = 0.9;
  const TEAM_VOLUME_WITH_VOICE = 0.35; // keeps the spoken "SOLD!" line audible over the franchise music
  const TEAM_MAX_MS = 8000;
  const FADE_MS = 800;
  const MUTE_KEY = "iplAuctionMuted";

  let muted = false;
  try { muted = localStorage.getItem(MUTE_KEY) === "1"; } catch (error) { /* storage unavailable */ }

  let welcome = null;
  let welcomeStopped = false;
  let teamAudio = null;
  let teamStopTimer = null;
  let teamIdByName = {};

  function makeAudio(file, loop, volume) {
    const audio = new Audio(`${AUDIO_BASE}${file}`);
    audio.loop = loop;
    audio.volume = volume;
    audio.preload = "auto";
    return audio;
  }

  function fadeOut(audio, done) {
    if (!audio) return;
    const startVolume = audio.volume;
    const steps = 16;
    let step = 0;
    const timer = setInterval(() => {
      step += 1;
      audio.volume = Math.max(0, startVolume * (1 - step / steps));
      if (step >= steps) {
        clearInterval(timer);
        audio.pause();
        audio.currentTime = 0;
        if (done) done();
      }
    }, FADE_MS / steps);
  }

  function startWelcome() {
    if (welcomeStopped || muted) return;
    if (!welcome) welcome = makeAudio(WELCOME_FILE, true, WELCOME_VOLUME);
    if (!welcome.paused) return;
    welcome.play().catch(() => { /* blocked until the first click or key press */ });
  }

  function stopWelcome() {
    welcomeStopped = true;
    if (welcome && !welcome.paused) fadeOut(welcome);
  }

  function playTeamMusic(teamId) {
    if (muted || !teamId) return;
    clearTimeout(teamStopTimer);
    if (teamAudio) teamAudio.pause();
    teamAudio = makeAudio(`${teamId}.mp3`, false, window.AuctionVoice && window.AuctionVoice.isEnabled() ? TEAM_VOLUME_WITH_VOICE : TEAM_VOLUME);
    teamAudio.play().catch(() => { /* file missing or blocked */ });
    const current = teamAudio;
    teamStopTimer = setTimeout(() => fadeOut(current), TEAM_MAX_MS);
  }

  function setMuted(value) {
    muted = value;
    try { localStorage.setItem(MUTE_KEY, muted ? "1" : "0"); } catch (error) { /* ignore */ }
    if (muted) {
      if (welcome) welcome.pause();
      if (teamAudio) teamAudio.pause();
    } else {
      startWelcome();
    }
    renderMuteButton();
  }

  let muteButton = null;
  function renderMuteButton() {
    if (!muteButton) return;
    muteButton.textContent = muted ? "🔇" : "🔊";
    muteButton.title = muted ? "Turn sound on" : "Turn sound off";
    muteButton.setAttribute("aria-label", muteButton.title);
  }

  function createMuteButton() {
    muteButton = document.createElement("button");
    muteButton.type = "button";
    muteButton.id = "sound-toggle";
    muteButton.style.cssText = [
      "position:fixed", "right:16px", "bottom:16px", "z-index:1000",
      "width:44px", "height:44px", "border-radius:50%", "border:1px solid rgba(255,255,255,.35)",
      "background:rgba(10,20,40,.8)", "color:#fff", "font-size:20px", "cursor:pointer",
    ].join(";");
    muteButton.addEventListener("click", () => setMuted(!muted));
    document.body.append(muteButton);
    renderMuteButton();
  }

  function loadTeamNames() {
    fetch("/api/catalog")
      .then((response) => response.json())
      .then((catalog) => {
        (catalog.teams || []).forEach((team) => {
          teamIdByName[team.name.trim().toLowerCase()] = team.team_id;
          teamIdByName[team.short_name.trim().toLowerCase()] = team.team_id;
        });
      })
      .catch(() => { /* team music simply stays silent */ });
  }

  function watchSaleAnnouncement() {
    const announcement = document.querySelector("#hammer-announcement");
    if (!announcement) return;
    let wasVisible = false;
    new MutationObserver(() => {
      const visible = announcement.classList.contains("hammer-visible");
      if (visible && !wasVisible) {
        const sold = document.querySelector("#hammer-overline")?.textContent.trim() === "SOLD!";
        if (sold) {
          const teamName = document.querySelector("#hammer-team")?.textContent.trim().toLowerCase();
          playTeamMusic(teamIdByName[teamName]);
        } else {
          playTeamMusic("unsold");
        }
      }
      wasVisible = visible;
    }).observe(announcement, { attributes: true, attributeFilter: ["class"] });
  }

  function watchLandingScreen() {
    const landing = document.querySelector("#landing-screen");
    if (!landing) return;
    new MutationObserver(() => {
      if (landing.classList.contains("hidden")) stopWelcome();
    }).observe(landing, { attributes: true, attributeFilter: ["class"] });
  }

  function unlockOnFirstGesture() {
    const unlock = () => {
      startWelcome();
      ["pointerdown", "keydown", "touchstart"].forEach((name) => document.removeEventListener(name, unlock));
    };
    ["pointerdown", "keydown", "touchstart"].forEach((name) => document.addEventListener(name, unlock));
  }

  createMuteButton();
  loadTeamNames();
  watchSaleAnnouncement();
  watchLandingScreen();
  startWelcome();
  unlockOnFirstGesture();
})();
(() => {
  const track = document.querySelector("#franchise-marquee-track");
  if (!track) return;

  // Franchise colours: gradient start, gradient end, text colour.
  const FRANCHISE_COLORS = {
    csk: { from: "#F9CD05", to: "#F28F00", text: "#0B2A5B" },
    dc: { from: "#17479E", to: "#EF1B23", text: "#FFFFFF" },
    gt: { from: "#1B2133", to: "#A0864A", text: "#FFFFFF" },
    kkr: { from: "#3A225D", to: "#6B46A8", text: "#F2D675" },
    lsg: { from: "#0A5DB0", to: "#E26A1F", text: "#FFFFFF" },
    mi: { from: "#0A2A66", to: "#004BA0", text: "#E8C766" },
    pbks: { from: "#D71920", to: "#8F0F14", text: "#FFFFFF" },
    rr: { from: "#254AA5", to: "#EA1A85", text: "#FFFFFF" },
    rcb: { from: "#111111", to: "#C8102E", text: "#E8C766" },
    srh: { from: "#FF822A", to: "#D94A0A", text: "#FFFFFF" },
  };
  const DEFAULT_COLORS = { from: "#17479E", to: "#0A2A66", text: "#FFFFFF" };

  function makeLogo(team) {
    const logo = typeof makeTeamLogo === "function"
      ? makeTeamLogo(team)
      : Object.assign(document.createElement("img"), { src: team.logo_url, alt: team.name });
    if (logo.tagName === "IMG") logo.loading = "eager";
    return logo;
  }

  function makeCard(team) {
    const colors = FRANCHISE_COLORS[team.team_id] || DEFAULT_COLORS;
    const card = document.createElement("article");
    card.className = "franchise-marquee-card";
    card.style.setProperty("--fc-from", colors.from);
    card.style.setProperty("--fc-to", colors.to);
    card.style.setProperty("--fc-text", colors.text);

    const name = document.createElement("strong");
    name.className = "franchise-marquee-name";
    name.textContent = team.name;
    const code = document.createElement("span");
    code.className = "franchise-marquee-code";
    code.textContent = team.short_name;

    card.append(makeLogo(team), name, code);
    return card;
  }

  fetch("/api/catalog")
    .then((response) => response.json())
    .then((catalog) => {
      const teams = catalog.teams || [];
      if (!teams.length) return;
      const cards = teams.map(makeCard);
      track.append(...cards);
      // Second copy makes the scroll loop seamless.
      cards.forEach((card) => {
        const copy = card.cloneNode(true);
        copy.setAttribute("aria-hidden", "true");
        track.append(copy);
      });
    })
    .catch(() => { /* strip simply stays empty */ });
})();
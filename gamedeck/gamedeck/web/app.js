/* GameDeck front end. Plain DOM, no build step. */

const TOKEN = document.documentElement.dataset.token;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  games: [],
  query: "",
  filter: "all",
  sort: "name",
  selected: null,
  cards: new Map(),
};

/* ---------------- api ---------------- */

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: {
      "X-GameDeck-Token": TOKEN,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = { error: `Server returned ${response.status}` };
  }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 6000 : 3200);
}

/* ---------------- formatting ---------------- */

function playtime(seconds) {
  if (!seconds) return "None yet";
  if (seconds < 60) return `${Math.round(seconds)} seconds`;
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)} minutes`;
  return `${(minutes / 60).toFixed(1)} hours`;
}

function clock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const hh = String(Math.floor(total / 3600)).padStart(2, "0");
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function whenLast(timestamp) {
  if (!timestamp) return "Never";
  const days = (Date.now() / 1000 - timestamp) / 86400;
  if (days < 1 / 24) return "Just now";
  if (days < 1) return `${Math.round(days * 24)} hours ago`;
  if (days < 30) return `${Math.round(days)} days ago`;
  return new Date(timestamp * 1000).toLocaleDateString();
}

function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0].toUpperCase())
    .join("");
}

function tint(name) {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return `linear-gradient(155deg, hsl(${hash} 42% 26%), hsl(${(hash + 45) % 360} 38% 13%))`;
}

/* ---------------- selection & filtering ---------------- */

function visibleGames() {
  const query = state.query.trim().toLowerCase();
  const cutoff = Date.now() / 1000 - 14 * 86400;

  let games = state.games.filter((game) => {
    if (query && !game.name.toLowerCase().includes(query)) return false;
    if (state.filter === "favorite") return game.favorite;
    if (state.filter === "running") return game.running;
    if (state.filter === "missing") return !game.exists;
    if (state.filter === "recent") return game.last_played && game.last_played > cutoff;
    return true;
  });

  const sorters = {
    name: (a, b) => a.name.localeCompare(b.name),
    recent: (a, b) => (b.last_played || 0) - (a.last_played || 0),
    playtime: (a, b) => (b.play_seconds || 0) - (a.play_seconds || 0),
    added: (a, b) => (b.added || 0) - (a.added || 0),
  };
  return games.sort(sorters[state.sort] || sorters.name);
}

function counts() {
  const cutoff = Date.now() / 1000 - 14 * 86400;
  return {
    all: state.games.length,
    favorite: state.games.filter((g) => g.favorite).length,
    running: state.games.filter((g) => g.running).length,
    missing: state.games.filter((g) => !g.exists).length,
    recent: state.games.filter((g) => g.last_played && g.last_played > cutoff).length,
  };
}

/* ---------------- cards ---------------- */

function buildCard(game) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.id = game.id;
  card.innerHTML = `
    <div class="card-art"></div>
    <div class="card-fallback"><span class="initials"></span></div>
    <span class="card-badge" hidden></span>
    <span class="card-fav" hidden>★</span>
    <button class="btn btn-play card-play">Play</button>
    <div class="card-title"></div>`;

  card.addEventListener("click", (event) => {
    if (event.target.closest(".card-play")) return;
    openDetail(game.id);
  });
  card.querySelector(".card-play").addEventListener("click", (event) => {
    event.stopPropagation();
    togglePlay(game.id);
  });

  card.addEventListener("dragover", (event) => {
    event.preventDefault();
    card.classList.add("drop-target");
  });
  card.addEventListener("dragleave", () => card.classList.remove("drop-target"));
  card.addEventListener("drop", (event) => {
    event.preventDefault();
    card.classList.remove("drop-target");
    const file = event.dataTransfer.files[0];
    if (file) uploadCover(game.id, file);
  });

  return card;
}

function paintCard(card, game) {
  const art = card.querySelector(".card-art");
  const fallback = card.querySelector(".card-fallback");
  if (game.cover_url) {
    art.style.backgroundImage = `url("${game.cover_url}")`;
    art.hidden = false;
    fallback.hidden = true;
  } else {
    art.hidden = true;
    fallback.hidden = false;
    fallback.style.background = tint(game.name);
    fallback.querySelector(".initials").textContent = initials(game.name);
  }

  card.querySelector(".card-title").textContent = game.name;
  card.querySelector(".card-fav").hidden = !game.favorite;
  card.querySelector(".card-play").textContent = game.running ? "Stop" : "Play";
  card.classList.toggle("is-running", !!game.running);
  card.classList.toggle("is-missing", !game.exists);

  const badge = card.querySelector(".card-badge");
  if (game.running) {
    badge.hidden = false;
    badge.className = "card-badge";
    badge.textContent = clock(game.session_seconds || 0);
  } else if (!game.exists) {
    badge.hidden = false;
    badge.className = "card-badge warn";
    badge.textContent = "File missing";
  } else {
    badge.hidden = true;
  }
}

function renderGrid() {
  const grid = $("#grid");
  const games = visibleGames();
  const seen = new Set();

  games.forEach((game, index) => {
    let card = state.cards.get(game.id);
    if (!card) {
      card = buildCard(game);
      state.cards.set(game.id, card);
    }
    paintCard(card, game);
    if (grid.children[index] !== card) grid.insertBefore(card, grid.children[index] || null);
    seen.add(game.id);
  });

  for (const [id, card] of state.cards) {
    if (!seen.has(id)) {
      card.remove();
      state.cards.delete(id);
    }
  }

  $("#empty").hidden = state.games.length !== 0;
  grid.hidden = state.games.length === 0;
  if (state.games.length && !games.length) {
    grid.hidden = false;
    grid.innerHTML = "";
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "Nothing matches that filter.";
    grid.append(note);
    state.cards.clear();
  }
}

function renderRail() {
  const tallies = counts();
  $$("[data-count]").forEach((node) => {
    node.textContent = tallies[node.dataset.count] ?? 0;
  });

  const list = $("#rail-list");
  list.innerHTML = "";
  visibleGames().forEach((game) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.className = "rail-item" + (state.selected === game.id ? " is-selected" : "");
    if (game.running) {
      const dot = document.createElement("span");
      dot.className = "dot";
      button.append(dot);
    }
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = game.name;
    button.append(label);
    button.addEventListener("click", () => openDetail(game.id));
    item.append(button);
    list.append(item);
  });
}

function render() {
  renderGrid();
  renderRail();
  if (state.selected) paintDetail();
}

/* ---------------- detail ---------------- */

function currentGame() {
  return state.games.find((game) => game.id === state.selected) || null;
}

function openDetail(id) {
  state.selected = id;
  const game = currentGame();
  if (!game) return;

  $("#field-name").value = game.name;
  $("#field-exe").value = game.exe;
  $("#field-args").value = game.args || "";
  $("#field-cwd").value = game.cwd || "";
  $("#field-notes").value = game.notes || "";
  $("#detail-saved").textContent = "";

  $("#detail").hidden = false;
  paintDetail();
  renderRail();
}

function closeDetail() {
  $("#detail").hidden = true;
  state.selected = null;
  renderRail();
}

function paintDetail() {
  const game = currentGame();
  if (!game) return closeDetail();

  $("#detail-name").textContent = game.name;
  $("#detail-path").textContent = game.exe;

  const art = $("#detail-art");
  art.style.background = game.cover_url
    ? `center / cover url("${game.cover_url}")`
    : tint(game.name);

  const play = $("#detail-play");
  play.textContent = game.running ? "Stop game" : "Play";
  play.classList.toggle("is-running", !!game.running);
  play.disabled = !game.exists && !game.running;

  $("#detail-fav").textContent = game.favorite ? "★ Favorite" : "☆ Favorite";
  $("#stat-playtime").textContent = playtime(game.play_seconds);
  $("#stat-last").textContent = whenLast(game.last_played);
  $("#stat-count").textContent = game.play_count || 0;
  $("#stat-status").textContent = game.running
    ? `Running · ${clock(game.session_seconds || 0)}`
    : game.exists
      ? "Ready"
      : "File missing";
}

/* ---------------- actions ---------------- */

async function togglePlay(id) {
  const game = state.games.find((entry) => entry.id === id);
  if (!game) return;
  try {
    const updated = await api("POST", `/api/games/${id}/${game.running ? "stop" : "launch"}`);
    mergeGame(updated);
    toast(game.running ? `Stopped ${game.name}` : `Launching ${game.name}…`, "good");
    render();
  } catch (error) {
    toast(error.message, "error");
  }
  refresh();
}

function mergeGame(updated) {
  const index = state.games.findIndex((game) => game.id === updated.id);
  if (index >= 0) state.games[index] = updated;
  else state.games.push(updated);
}

async function saveDetail() {
  const game = currentGame();
  if (!game) return;
  try {
    const updated = await api("PATCH", `/api/games/${game.id}`, {
      name: $("#field-name").value,
      exe: $("#field-exe").value,
      args: $("#field-args").value,
      cwd: $("#field-cwd").value,
      notes: $("#field-notes").value,
    });
    mergeGame(updated);
    $("#detail-saved").textContent = "Saved";
    setTimeout(() => ($("#detail-saved").textContent = ""), 1800);
    render();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function uploadCover(id, file) {
  if (!file.type.startsWith("image/")) return toast("That is not an image.", "error");
  if (file.size > 10 * 1024 * 1024) return toast("Image is larger than 10 MB.", "error");

  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Could not read that file."));
    reader.readAsDataURL(file);
  });

  try {
    mergeGame(await api("POST", `/api/games/${id}/cover`, { data: dataUrl }));
    toast("Cover updated", "good");
    render();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function chooseCover() {
  const game = currentGame();
  if (!game) return;
  try {
    const { path } = await api("POST", "/api/browse", { kind: "image" });
    if (!path) return;
    mergeGame(await api("POST", `/api/games/${game.id}/cover`, { path }));
    toast("Cover updated", "good");
    render();
  } catch (error) {
    // No Tk on this machine: fall back to the browser's own file input.
    $("#cover-file").click();
  }
}

/* ---------------- add games ---------------- */

function openAdd() {
  $("#add").hidden = false;
  $("#scan-path").focus();
}

function closeAdd() {
  $("#add").hidden = true;
}

function renderCandidates(candidates) {
  const box = $("#scan-results");
  box.innerHTML = "";
  const actions = $(".results-actions");
  actions.hidden = candidates.length === 0;

  candidates.forEach((candidate, index) => {
    const row = document.createElement("div");
    row.className = "result" + (candidate.already_added ? " added" : "");

    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = !candidate.already_added;
    check.disabled = candidate.already_added;
    check.dataset.index = String(index);

    const meta = document.createElement("div");
    meta.className = "meta";

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = candidate.name + (candidate.already_added ? " — already added" : "");

    const options = [candidate.exe, ...candidate.alternates];
    const picker = document.createElement("select");
    options.forEach((option) => {
      const item = document.createElement("option");
      item.value = option;
      item.textContent = option;
      picker.append(item);
    });
    picker.disabled = candidate.already_added;

    meta.append(name, picker);
    row.append(check, meta);
    box.append(row);
  });
}

async function runScan() {
  const path = $("#scan-path").value.trim();
  const status = $("#scan-status");
  status.className = "status";
  status.textContent = "Scanning…";
  try {
    const { candidates } = await api("POST", "/api/scan", { path });
    renderCandidates(candidates);
    const fresh = candidates.filter((entry) => !entry.already_added).length;
    status.textContent = candidates.length
      ? `Found ${candidates.length} game${candidates.length === 1 ? "" : "s"}` +
        (fresh === candidates.length ? "." : ` (${fresh} new).`)
      : "No games found in that folder.";
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
    renderCandidates([]);
  }
}

async function addSelected() {
  const rows = $$("#scan-results .result");
  let added = 0;
  let failed = 0;

  for (const row of rows) {
    const check = row.querySelector('input[type="checkbox"]');
    if (!check.checked || check.disabled) continue;
    const name = row.querySelector(".name").textContent;
    const exe = row.querySelector("select").value;
    try {
      mergeGame(await api("POST", "/api/games", { name, exe }));
      added += 1;
      row.classList.add("added");
      check.checked = false;
      check.disabled = true;
    } catch (error) {
      failed += 1;
    }
  }

  render();
  if (added) toast(`Added ${added} game${added === 1 ? "" : "s"}`, "good");
  if (failed) toast(`${failed} could not be added.`, "error");
  if (added && !failed) closeAdd();
}

async function addSingle() {
  const status = $("#single-status");
  status.className = "status";
  try {
    mergeGame(
      await api("POST", "/api/games", {
        exe: $("#single-exe").value.trim(),
        name: $("#single-name").value.trim(),
      })
    );
    render();
    toast("Game added", "good");
    $("#single-exe").value = "";
    $("#single-name").value = "";
    closeAdd();
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
  }
}

async function browseInto(input, kind) {
  try {
    const { path } = await api("POST", "/api/browse", { kind });
    if (path) input.value = path;
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ---------------- polling ---------------- */

async function refresh() {
  try {
    const { games } = await api("GET", "/api/state");
    state.games = games;
    render();
  } catch (error) {
    /* the server may be restarting; the next tick will pick it up */
  }
}

function tickTimers() {
  state.games.forEach((game) => {
    if (game.running && game.session_seconds !== null) game.session_seconds += 1;
  });
  state.games.filter((game) => game.running).forEach((game) => {
    const card = state.cards.get(game.id);
    if (card) card.querySelector(".card-badge").textContent = clock(game.session_seconds);
  });
  if (state.selected && currentGame()?.running) paintDetail();
}

/* ---------------- wiring ---------------- */

function wire() {
  $("#search").addEventListener("input", (event) => {
    state.query = event.target.value;
    render();
  });

  $("#sort").addEventListener("change", (event) => {
    state.sort = event.target.value;
    render();
  });

  $$(".filter").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".filter").forEach((other) => other.classList.remove("is-active"));
      button.classList.add("is-active");
      state.filter = button.dataset.filter;
      render();
    });
  });

  $("#add-games").addEventListener("click", openAdd);
  $$("[data-open-add]").forEach((node) => node.addEventListener("click", openAdd));
  $$("[data-close-add]").forEach((node) => node.addEventListener("click", closeAdd));
  $$("[data-close-detail]").forEach((node) => node.addEventListener("click", closeDetail));

  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((other) => other.classList.remove("is-active"));
      tab.classList.add("is-active");
      $$(".tab-panel").forEach((panel) => {
        panel.hidden = panel.dataset.panel !== tab.dataset.tab;
      });
    });
  });

  $("#scan-run").addEventListener("click", runScan);
  $("#scan-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runScan();
  });
  $("#scan-browse").addEventListener("click", () => browseInto($("#scan-path"), "folder"));
  $("#scan-add").addEventListener("click", addSelected);
  $("#scan-all").addEventListener("change", (event) => {
    $$('#scan-results input[type="checkbox"]').forEach((check) => {
      if (!check.disabled) check.checked = event.target.checked;
    });
  });

  $("#single-browse").addEventListener("click", () => browseInto($("#single-exe"), "file"));
  $("#single-add").addEventListener("click", addSingle);

  $("#detail-play").addEventListener("click", () => state.selected && togglePlay(state.selected));
  $("#detail-save").addEventListener("click", saveDetail);
  $("#detail-cover").addEventListener("click", chooseCover);

  $("#cover-file").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file && state.selected) uploadCover(state.selected, file);
    event.target.value = "";
  });

  $("#detail-fav").addEventListener("click", async () => {
    const game = currentGame();
    if (!game) return;
    try {
      mergeGame(await api("PATCH", `/api/games/${game.id}`, { favorite: !game.favorite }));
      render();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#detail-folder").addEventListener("click", async () => {
    try {
      await api("POST", "/api/reveal", { id: state.selected });
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#detail-remove").addEventListener("click", async () => {
    const game = currentGame();
    if (!game) return;
    if (!confirm(`Remove ${game.name} from your library?\n\nThe game's files are left alone.`)) return;
    try {
      await api("DELETE", `/api/games/${game.id}`);
      state.games = state.games.filter((entry) => entry.id !== game.id);
      closeDetail();
      render();
      toast(`Removed ${game.name}`);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#detail-art").addEventListener("dragover", (event) => event.preventDefault());
  $("#detail-art").addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    if (file && state.selected) uploadCover(state.selected, file);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!$("#detail").hidden) closeDetail();
      else if (!$("#add").hidden) closeAdd();
    }
    if (event.key === "/" && document.activeElement.tagName !== "INPUT") {
      event.preventDefault();
      $("#search").focus();
    }
  });
}

wire();
refresh();
setInterval(refresh, 4000);
setInterval(tickTimers, 1000);

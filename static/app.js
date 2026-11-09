"use strict";
// AI Card Table front-end (vanilla JS, no build step).

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KINDS = { human: "You", claude: "Claude Code", codex: "Codex", openrouter: "OpenRouter", bot: "Local bot" };
const BADGE = { human: "YOU", claude: "CLAUDE", codex: "CODEX", openrouter: "OPENROUTER", bot: "BOT" };
const NAME_POOL = {
  claude: ["Claude", "Claudette", "Sonny", "Opie", "Haiku", "Fable"],
  codex: ["Codex", "Cody", "Dex", "Gippy", "Turbo", "Omni"],
  openrouter: ["Router", "Gemma", "Llama", "Mistral", "Qwen", "Deepy"],
  bot: ["Bot", "Robo", "Chip", "Dealer Dan", "Basic Bill", "Tilt"],
};
const PERSONALITIES = [
  "Loose and aggressive. Loves to bluff and needle opponents.",
  "Tight and patient. Only plays strong hands, speaks rarely.",
  "Math nerd. Talks about pot odds and expected value.",
  "Old-school card shark with a folksy drawl.",
  "Overconfident trash-talker who hates folding.",
  "Calm, polite, quietly ruthless.",
  "Chaotic. Makes unpredictable plays to stay unreadable.",
];

const BRIDGE_COMPASS = ["South", "West", "North", "East"];
const SUIT_GLYPH = { s: "♠", h: "♥", d: "♦", c: "♣" };

const api = {
  async get(path) { const r = await fetch(path); return r.json(); },
  async post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  },
};

let settings = {};
let clis = {};
let catalog = {};
let personas = [];
let seats = [];
let state = null;
let polling = false;
let lastRequestKey = "";
let thinkingClock = {};
let seatsBeforeBridge = null;

// ======================================================================
// Lobby
// ======================================================================
function defaultSeat(kind, i) {
  const pool = NAME_POOL[kind] || ["Player"];
  const sameKind = seats.filter((s) => s.kind === kind).length;
  return {
    kind,
    name: kind === "human" ? "You" : pool[(sameKind + i) % pool.length] + (sameKind >= pool.length ? ` ${sameKind + 1}` : ""),
    model: kind === "claude" ? settings.claude_default_model || "sonnet" : kind === "codex" ? settings.codex_default_model || "" : kind === "openrouter" ? settings.openrouter_default_model || "" : "",
    effort: kind === "claude" || kind === "codex" ? "low" : "",
    personality: kind === "human" || kind === "bot" ? "" : PERSONALITIES[i % PERSONALITIES.length],
    persona: "random",
  };
}

function preset(name) {
  const plans = {
    mixed: ["human", "claude", "claude", "claude", "codex", "codex"],
    full: ["human", "claude", "claude", "codex", "codex", "openrouter", "bot"],
    claude: ["human", "claude", "claude", "claude", "claude"],
    bots: ["human", "bot", "bot", "bot", "bot", "bot"],
    spectate: ["claude", "claude", "codex", "codex", "openrouter"],
    // Bridge seats run South, West, North, East: seats 1 and 3 are partners.
    bridge_claude: ["human", "codex", "claude", "codex"],
    bridge_codex: ["human", "claude", "codex", "claude"],
    bridge_bots: ["human", "bot", "bot", "bot"],
    bridge_watch: ["claude", "codex", "claude", "codex"],
  };
  seats = [];
  plans[name].forEach((kind, i) => seats.push(defaultSeat(kind, i)));
  // Give the same-kind seats distinct names.
  const used = {};
  seats.forEach((s) => {
    if (s.kind === "human") return;
    const pool = NAME_POOL[s.kind];
    used[s.kind] = (used[s.kind] || 0) + 1;
    s.name = pool[(used[s.kind] - 1) % pool.length];
  });
  renderSeats();
}

const CUSTOM_MODEL = "__custom__";

function modelField(s) {
  if (s.kind === "openrouter") {
    return `<input data-k="model" value="${esc(s.model)}" placeholder="provider/model" list="or-models" title="Model">`;
  }
  if (s.kind !== "claude" && s.kind !== "codex") return '<select data-k="model" disabled><option>—</option></select>';
  const ids = (catalog[s.kind]?.models || []).map((m) => m.id);
  if (s.model && !ids.includes(s.model)) ids.push(s.model);
  const option = (id, label) => `<option value="${esc(id)}" ${id === s.model ? "selected" : ""}>${esc(label)}</option>`;
  return `<select data-k="model" title="Model">${option("", "model: default")}${ids.map((id) => option(id, id)).join("")}${option(CUSTOM_MODEL, "Other…")}</select>`;
}

function effortChoices(s) {
  const cat = catalog[s.kind];
  if (!cat) return [""];
  return ["", ...(cat.models.find((m) => m.id === s.model)?.efforts || cat.efforts)];
}

function personalityField(s) {
  if (selectedGame() !== "holdem") {
    const off = s.kind === "human" || s.kind === "bot";
    return `<input data-k="personality" value="${esc(s.personality)}" placeholder="${off ? "" : "Personality / play style"}" ${off ? "disabled" : ""}>`;
  }
  if (s.kind === "human") return '<select data-k="persona" disabled><option>—</option></select>';
  const chosen = s.persona || "random";
  const option = (id, label) => `<option value="${esc(id)}" ${id === chosen ? "selected" : ""}>${esc(label)}</option>`;
  return `<select data-k="persona" title="Personality">${option("random", "Random")}${personas.map((p) => option(p.id, p.label)).join("")}</select>`;
}

function renderSeats() {
  const box = $("#seats");
  box.innerHTML = "";
  $("#seat-count").textContent = seats.length;
  const bridge = selectedGame() === "bridge";
  seats.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = `seat-row kind-${s.kind}`;
    const efforts = effortChoices(s);
    if (!efforts.includes(s.effort)) s.effort = "";
    row.innerHTML = `
      <div class="num" title="${bridge ? "South & North are partners, West & East are partners" : ""}">${bridge ? BRIDGE_COMPASS[i][0] : i + 1}</div>
      <input data-k="name" value="${esc(s.name)}" maxlength="24" placeholder="Name">
      <select data-k="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === s.kind ? "selected" : ""}>${v}</option>`).join("")}</select>
      ${modelField(s)}
      <select data-k="effort" ${s.kind === "human" || s.kind === "bot" ? "disabled" : ""} title="Reasoning effort (lower = faster, cheaper)">
        ${efforts.map((e) => `<option value="${e}" ${e === s.effort ? "selected" : ""}>${e || "effort: default"}</option>`).join("")}
      </select>
      ${personalityField(s)}
      <div class="seat-actions">
        <button class="btn small" data-act="test" ${s.kind === "human" ? "disabled" : ""}>Test</button>
        <button class="btn small ghost ${bridge ? "hidden" : ""}" data-act="remove" title="Remove seat">&times;</button>
      </div>
      <div class="test-result"></div>`;
    row.querySelectorAll("[data-k]").forEach((el) => {
      el.addEventListener("change", () => {
        const k = el.dataset.k;
        if (k === "kind") {
          if (el.value === "human" && seats.some((x, j) => j !== i && x.kind === "human")) {
            alert("Only one human seat.");
            el.value = s.kind;
            return;
          }
          seats[i] = { ...defaultSeat(el.value, i), name: el.value === "human" ? "You" : defaultSeat(el.value, i).name };
          renderSeats();
        } else if (k === "model" && el.value === CUSTOM_MODEL) {
          const id = (prompt("Model ID", s.model) || "").trim();
          if (id) s.model = id;
          renderSeats();
        } else {
          s[k] = el.value;
          if (k === "model" && s.kind !== "openrouter") renderSeats();  // effort levels depend on the model
        }
        saveLobby();
      });
    });
    row.querySelector('[data-act="remove"]').onclick = () => {
      if (seats.length <= 2) return;
      seats.splice(i, 1);
      renderSeats();
    };
    row.querySelector('[data-act="test"]').onclick = async (ev) => {
      const out = row.querySelector(".test-result");
      ev.target.disabled = true;
      out.className = "test-result";
      out.textContent = `Asking ${KINDS[s.kind]}… (CLI start-up can take 5-20 s)`;
      try {
        const r = await api.post("/api/test-seat", s);
        out.className = `test-result ${r.ok ? "ok" : "bad"}`;
        out.textContent = r.ok ? `OK in ${r.seconds}s: "${r.message}"` : `Failed: ${r.message}`;
      } catch (e) {
        out.className = "test-result bad";
        out.textContent = `Failed: ${e.message}`;
      }
      ev.target.disabled = false;
    };
    box.appendChild(row);
  });
  saveLobby();
}

function selectedGame() { return document.querySelector('input[name="game"]:checked').value; }

function gameOptions() {
  if (selectedGame() === "bridge") {
    return { boards: +$("#opt-boards").value, hand_pause_seconds: +$("#opt-bridge-pause").value };
  }
  if (selectedGame() === "holdem") {
    return {
      starting_chips: +$("#opt-chips").value, small_blind: +$("#opt-sb").value, big_blind: +$("#opt-bb").value,
      blind_increase_every: +$("#opt-blind-every").value, hand_pause_seconds: +$("#opt-pause").value,
    };
  }
  return {
    starting_chips: +$("#opt-bj-chips").value, min_bet: +$("#opt-min-bet").value, max_bet: +$("#opt-max-bet").value,
    decks: +$("#opt-decks").value, dealer_hits_soft17: $("#opt-h17").checked, hand_pause_seconds: +$("#opt-bj-pause").value,
  };
}

function saveLobby() {
  localStorage.setItem("aitable.lobby", JSON.stringify({ seats, game: selectedGame() }));
}

function loadLobby() {
  try {
    const saved = JSON.parse(localStorage.getItem("aitable.lobby") || "null");
    if (saved && Array.isArray(saved.seats) && saved.seats.length >= 2) {
      seats = saved.seats;
      const radio = document.querySelector(`input[name="game"][value="${saved.game}"]`);
      if (radio) radio.checked = true;
      return true;
    }
  } catch (_) { /* ignore */ }
  return false;
}

function syncOptionsVisibility() {
  const g = selectedGame();
  $("#options-holdem").classList.toggle("hidden", g !== "holdem");
  $("#options-blackjack").classList.toggle("hidden", g !== "blackjack");
  $("#options-bridge").classList.toggle("hidden", g !== "bridge");
  $("#presets-bridge").classList.toggle("hidden", g !== "bridge");
  $("#presets-standard").classList.toggle("hidden", g === "bridge");
  $("#seat-count-box").classList.toggle("hidden", g === "bridge");
  if (g === "bridge" && seats.length !== 4) {
    seatsBeforeBridge = seats;
    preset("bridge_claude");
  } else if (g !== "bridge" && seatsBeforeBridge) {
    seats = seatsBeforeBridge;
    seatsBeforeBridge = null;
  }
  renderSeats();
}

function renderBackendStatus() {
  const c = clis.claude || {}, x = clis.codex || {}, o = clis.openrouter || {};
  $("#backend-status").innerHTML = `
    <span class="pill ${c.path ? "ok" : "bad"}" title="${esc(c.path || "not on PATH")}">Claude Code ${c.path ? esc((c.version || "").split(" ")[0]) : "not found"}</span>
    <span class="pill ${x.path ? "ok" : "bad"}" title="${esc(x.path || "not on PATH")}">Codex ${x.path ? esc((x.version || "").replace("codex-cli ", "")) : "not found"}</span>
    <span class="pill ${o.key_set ? "ok" : "bad"}">OpenRouter ${o.key_set ? "key set" : "no key"}</span>`;
}

async function startGame() {
  $("#start-error").textContent = "";
  const problems = [];
  if (seats.some((s) => s.kind === "claude") && !clis.claude?.path) problems.push("Claude Code CLI not found (install it or set its path in Settings)");
  if (seats.some((s) => s.kind === "codex") && !clis.codex?.path) problems.push("Codex CLI not found (npm i -g @openai/codex, or set its path in Settings)");
  if (seats.some((s) => s.kind === "openrouter") && !clis.openrouter?.key_set) problems.push("No OpenRouter API key (add it in Settings)");
  if (problems.length && !confirm(problems.join("\n") + "\n\nThose seats will fall back to the local bot. Start anyway?")) return;
  try {
    await api.post("/api/game/start", { game: selectedGame(), seats, options: gameOptions() });
    showTable();
  } catch (e) {
    $("#start-error").textContent = e.message;
  }
}

// ======================================================================
// Settings
// ======================================================================
async function loadSettings() {
  const r = await api.get("/api/settings");
  settings = r.settings;
  clis = r.clis;
  catalog = r.catalog;
  personas = r.personas || [];
  renderBackendStatus();
}

function openSettings() {
  const s = settings;
  $("#set-or-key").value = "";
  $("#set-or-key").placeholder = s.openrouter_api_key ? `saved: ${s.openrouter_api_key}` : s.openrouter_key_from_env ? "using OPENROUTER_API_KEY env var" : "sk-or-v1-…";
  $("#set-or-model").value = s.openrouter_default_model || "";
  $("#set-claude-path").value = s.claude_path || "";
  $("#set-claude-model").value = s.claude_default_model || "";
  $("#set-codex-path").value = s.codex_path || "";
  $("#set-codex-model").value = s.codex_default_model || "";
  $("#set-claude-sub").checked = !!s.claude_force_subscription;
  $("#set-codex-sub").checked = !!s.codex_force_subscription;
  $("#set-par-claude").value = s.max_parallel_claude;
  $("#set-par-codex").value = s.max_parallel_codex;
  $("#set-par-or").value = s.max_parallel_openrouter;
  $("#set-par-bj").checked = !!s.parallel_blackjack;
  $("#set-timeout").value = s.agent_timeout;
  $("#set-pace").value = s.ai_pace_seconds;
  $("#set-reveal").checked = !!s.reveal_ai_cards;
  $("#settings-dialog").showModal();
  loadOpenRouterModels();
}

async function saveSettings(extra = {}) {
  const body = {
    openrouter_default_model: $("#set-or-model").value,
    claude_path: $("#set-claude-path").value,
    claude_default_model: $("#set-claude-model").value,
    codex_path: $("#set-codex-path").value,
    codex_default_model: $("#set-codex-model").value,
    claude_force_subscription: $("#set-claude-sub").checked,
    codex_force_subscription: $("#set-codex-sub").checked,
    max_parallel_claude: +$("#set-par-claude").value || 4,
    max_parallel_codex: +$("#set-par-codex").value || 4,
    max_parallel_openrouter: +$("#set-par-or").value || 6,
    parallel_blackjack: $("#set-par-bj").checked,
    agent_timeout: +$("#set-timeout").value || 150,
    ai_pace_seconds: +$("#set-pace").value,
    reveal_ai_cards: $("#set-reveal").checked,
    ...extra,
  };
  const key = $("#set-or-key").value.trim();
  if (key) body.openrouter_api_key = key;
  const r = await api.post("/api/settings", body);
  settings = r.settings;
  clis = r.clis;
  renderBackendStatus();
}

let orModelsLoaded = false;
async function loadOpenRouterModels() {
  if (orModelsLoaded) return;
  try {
    const r = await api.get("/api/openrouter/models");
    if (r.models?.length) {
      $("#or-models").innerHTML = r.models.map((m) => `<option value="${esc(m)}">`).join("");
      orModelsLoaded = true;
    }
  } catch (_) { /* offline is fine */ }
}

// ======================================================================
// Table rendering
// ======================================================================
// Update `target` to match `html`, touching only what differs. Elements that stay
// keep their running animations and transitions; only new ones animate in.
const DEALT = ".card:not(.ghost)";
let dealt = [];  // cards the last render put on the table, in the order they were dealt

function morph(target, html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  morphChildren(target, tpl.content);
}

function morphChildren(parent, next) {
  const pool = [...parent.childNodes];
  const take = (want) => {
    const key = want.dataset?.key;
    const i = pool.findIndex((n) => n.nodeName === want.nodeName && n.dataset?.key === key);
    return i < 0 ? null : pool.splice(i, 1)[0];
  };
  const pairs = [...next.childNodes].map((want) => [want, take(want)]);
  // Drop leftovers first so that kept nodes rarely need moving (a move restarts animations).
  pool.forEach((n) => n.remove());
  let cursor = parent.firstChild;
  for (const [want, old] of pairs) {
    const node = old || want;
    if (node === cursor) cursor = cursor.nextSibling;
    else parent.insertBefore(node, cursor);
    if (old) morphNode(old, want);
    else if (want.nodeType === Node.ELEMENT_NODE) {
      if (want.matches(DEALT)) dealt.push(want);
      dealt.push(...want.querySelectorAll(DEALT));
    }
  }
}

function morphNode(old, want) {
  if (old.nodeType !== Node.ELEMENT_NODE) {
    if (old.nodeValue !== want.nodeValue) old.nodeValue = want.nodeValue;
    return;
  }
  for (const { name } of [...old.attributes]) if (!want.hasAttribute(name)) old.removeAttribute(name);
  for (const { name, value } of want.attributes) if (old.getAttribute(name) !== value) old.setAttribute(name, value);
  morphChildren(old, want);
}

function cardHTML(c) {
  if (!c) return '<div class="card ghost"></div>';
  if (c === "??") return '<div class="card back"></div>';
  const rank = c[0] === "T" ? "10" : c[0];
  return `<div class="card ${c[1] === "h" || c[1] === "d" ? "red" : ""} suit-${c[1]}" data-key="${c}"><span class="r">${rank}</span><span class="p"></span><span class="s"></span></div>`;
}

// ---------------------------------------------------------------------- chips
const CHIP_VALUES = [100000, 25000, 5000, 1000, 500, 100, 25, 5, 1];

// Split an amount into chips, biggest first. That gives the fewest chips; `spread` then
// breaks big chips into change while the total stays within that many, so a player's
// stack looks like a stack instead of one lonely chip.
function chipCounts(amount, spread = 0) {
  amount = Math.max(0, Math.round(amount));
  const counts = CHIP_VALUES.map((v) => {
    const n = Math.floor(amount / v);
    amount -= n * v;
    return n;
  });
  let total = counts.reduce((a, b) => a + b, 0);
  for (let broke = true; broke;) {
    broke = false;
    for (let i = 0; i < CHIP_VALUES.length - 1; i++) {
      const extra = CHIP_VALUES[i] / CHIP_VALUES[i + 1] - 1;
      if (counts[i] && total + extra <= spread) {
        counts[i]--;
        counts[i + 1] += extra + 1;
        total += extra;
        broke = true;
      }
    }
  }
  return counts;
}

// Stacks of chips for an amount, one column per denomination. The drawing is capped at
// `cols` columns of 8 chips: the number beside it is exact, the chips are the picture.
function chipsHTML(amount, { spread = 0, cols = 4, cls = "" } = {}) {
  const stacks = chipCounts(amount, spread).map((n, i) => [CHIP_VALUES[i], n]).filter(([, n]) => n);
  const html = stacks.slice(0, cols).map(([v, n]) =>
    `<span class="chip-col" data-key="${v}">${`<i class="chip c${v}"></i>`.repeat(Math.min(n, 8))}</span>`).join("");
  return `<span class="chips-viz ${cls}" title="${stacks.map(([v, n]) => `${n} × ${v}`).join(" · ")}">${html}</span>`;
}

// A single small chip in the colour of the biggest denomination that fits the amount.
function chipIcon(amount) {
  return `<i class="chip mini c${CHIP_VALUES.find((v) => v <= amount) || 1}"></i>`;
}

// Where a seat's bet sits on the felt (percent of the table), part-way to the centre.
const betPos = ({ x, y }) => ({ x: 50 + (x - 50) * 0.62, y: 50 + (y - 50) * 0.55 });

function thinkingHTML(p) {
  if (!p.thinking) {
    delete thinkingClock[p.id];
    return "";
  }
  thinkingClock[p.id] = thinkingClock[p.id] || Date.now() - p.thinking_for * 1000;
  const secs = Math.round((Date.now() - thinkingClock[p.id]) / 1000);
  return `<span class="thinking" data-key="thinking"><span class="spinner"></span><span class="think-secs" data-pid="${p.id}">${secs}s</span></span>`;
}

function modelLabel(p) {
  return esc([p.model || (p.kind === "human" ? "" : "default model"), p.effort].filter(Boolean).join(" · "));
}

function seatPositions(n, humanIndex) {
  // Human sits bottom-centre; everyone else spreads clockwise around the ellipse.
  const pos = [];
  const start = humanIndex >= 0 ? humanIndex : 0;
  for (let k = 0; k < n; k++) {
    const i = (start + k) % n;
    const angle = Math.PI / 2 + (2 * Math.PI * k) / n; // 90deg = bottom
    pos[i] = { x: 50 + 41 * Math.cos(angle), y: 50 + 40 * Math.sin(angle), angle };
  }
  return pos;
}

function render() {
  if (!state || !state.game) return;
  if (state.game === "Contract Bridge") {
    renderBridge();
    renderLog();
    renderStats();
    return;
  }
  const isPoker = state.game === "Texas Hold'em";
  $("#table-title").textContent = state.game;
  const info = [];
  if (state.hand_no) info.push(`${isPoker ? "Hand" : "Round"} #${state.hand_no}`);
  if (isPoker && state.blinds) info.push(`Blinds ${state.blinds[0]}/${state.blinds[1]}`);
  if (!isPoker && state.limits) info.push(`Bets ${state.limits[0]}–${state.limits[1]}`, `Shoe ${state.shoe_left}/${state.shoe_total}`);
  $("#table-info").textContent = info.join(" · ");
  $("#status-line").textContent = state.status || "";
  $("#stop-game").disabled = state.finished;

  renderCenter(isPoker);
  renderSeatsLayer(isPoker);
  renderLog();
  renderStats();
  renderActionPanel(isPoker);
}

function renderCenter(isPoker) {
  const c = $("#center");
  if (isPoker) {
    const board = [0, 1, 2, 3, 4].map((i) => cardHTML(state.board[i])).join("");
    // The pile is what has been pushed in; this round's bets still sit in front of the seats.
    const inFront = Object.values(state.seats).reduce((sum, seat) => sum + seat.bet, 0);
    const pots = (state.pots || []).map((p) => `${esc(p.name)} ${p.amount} → ${p.winners.map((id) => esc(state.players[id].name)).join(" & ")}`).join(" · ");
    morph(c, `
      <div class="label">${esc(state.street || "")}</div>
      <div class="board">${board}</div>
      <div class="pot-pile">${chipsHTML(state.pot - inFront, { cols: 6 })}</div>
      <div class="pot">Pot ${state.pot}${pots ? ` <small>${pots}</small>` : ""}</div>
      <div class="label">${state.human_hand ? `You have: ${esc(state.human_hand)}` : ""}</div>`);
  } else {
    const dealer = state.dealer.length ? state.dealer.map(cardHTML).join("") : cardHTML(null) + cardHTML(null);
    morph(c, `
      <div class="dealer-area">
        <div class="label">Dealer ${state.dealer_value ? "· " + esc(state.dealer_value) : ""}</div>
        <div class="board">${dealer}</div>
        <div class="label">${esc(state.phase || "")}</div>
      </div>`);
  }
}

const SEAT_HALF = 67;  // px: half the height of a seat holding one two-card hand

function renderSeatsLayer(isPoker) {
  const layer = $("#seats-layer");
  const players = state.players;
  const humanIndex = players.findIndex((p) => p.kind === "human");
  const pos = seatPositions(players.length, humanIndex);
  const winners = new Set((state.pots || []).flatMap((p) => p.winners));
  let html = "";
  let betChips = "";  // after all the seats, so a chip appearing never moves a seat in the DOM
  players.forEach((p, i) => {
    const seat = state.seats[p.id] || {};
    const { x, y } = pos[i];
    const classes = ["seat", "pinned"];
    if (isPoker) {
      if (state.to_act === p.id) classes.push("turn");
      if (seat.folded) classes.push("folded");
      if (!seat.in_hand) classes.push("out");
      if (winners.has(p.id) && seat.won) classes.push("winner");
    } else {
      if (p.thinking || (state.human_request && p.kind === "human")) classes.push("turn");
      if (!seat.in_round) classes.push("out");
      if (seat.hands?.some((h) => h.payout > 0)) classes.push("winner");
    }

    let tags = "";
    if (isPoker) {
      if (state.dealer_id === p.id) tags += '<span class="dealer-btn" title="Dealer">D</span>';
      if (state.sb_id === p.id) tags += '<span class="blind-tag">SB</span>';
      if (state.bb_id === p.id) tags += '<span class="blind-tag">BB</span>';
    }
    // The seat's own chips sit beside its cards; the exact count is the number above them.
    const stack = chipsHTML(p.chips, { spread: 10, cols: 3, cls: "stack" });
    let body = "";
    if (isPoker) {
      const mucked = seat.folded && p.kind !== "human";
      const cards = seat.cards?.length && !mucked ? seat.cards.map(cardHTML).join("") : "";
      body = `<div class="cards">${cards}${stack}</div>
        <div class="meta"><span class="action">${esc(seat.last_action || "")}</span>
        <span class="hand-name">${esc(seat.hand_name || "")}${seat.won ? ` +${seat.won}` : ""}</span></div>`;
    } else {
      const hands = (seat.hands || []).map((h, hi) => `
        <div class="bj-hand ${seat.active_hand === hi && (seat.hands.length > 1 || p.kind === "human") ? "active" : ""}">
          <div class="cards">${h.cards.map(cardHTML).join("")}</div>
          <div class="info"><span>${esc(h.value)}</span><span class="bet">${chipIcon(h.bet)}${h.bet}${h.doubled ? " ×2" : ""}</span>
          ${h.result ? `<span class="res ${h.result}">${h.result}${h.payout ? ` ${h.payout > 0 ? "+" : ""}${h.payout}` : ""}</span>` : ""}</div>
        </div>`).join("");
      body = `<div class="bj-hands">${hands}${stack}</div>`;
    }
    // Pinned by its outer edge, so a seat that gets taller (a split, a long hand) grows
    // toward the middle of the table and not off the screen.
    const edge = y < 50 ? `top:calc(${y}% - ${SEAT_HALF}px)` : `bottom:calc(${100 - y}% - ${SEAT_HALF}px)`;
    html += `
      <div class="${classes.join(" ")}" style="left:${x}%;${edge}" data-key="seat-${p.id}">
        <div class="head"><span class="badge ${p.kind}">${BADGE[p.kind]}</span><span class="name">${esc(p.name)}</span>${tags}</div>
        <div class="row-between"><span class="model">${modelLabel(p)}</span>${thinkingHTML(p)}<span class="chips">${p.chips}</span></div>
        ${body}
      </div>`;
    // Poker: this round's bet. Blackjack: everything the seat still has riding on the dealer.
    const staked = isPoker ? seat.bet : (seat.hands || []).reduce((sum, h) => sum + (h.result ? 0 : h.bet), 0);
    if (staked > 0) {
      const at = betPos(pos[i]);
      betChips += `<div class="bet-spot" style="left:${at.x}%;top:${at.y}%" data-key="bet-${p.id}">${chipsHTML(staked, { cols: 3 })}<span class="amt">${staked}</span></div>`;
    }
  });
  morph(layer, html + betChips);
}

function renderLog() {
  const log = $("#log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  const items = state.log.map((l) => ({ t: l.t, html: `<div class="${l.kind}" data-key="${l.t}">${esc(l.text)}</div>` }))
    .concat(state.chat.map((c) => ({ t: c.t, html: `<div class="chat" data-key="${c.t}">${esc(c.name)}: “${esc(c.text)}”</div>` })))
    .sort((a, b) => a.t - b.t);
  morph(log, items.map((i) => i.html).join(""));
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function renderStats() {
  const rows = state.players.filter((p) => p.kind !== "human").map((p) => `
    <tr><td><span class="badge ${p.kind}">${BADGE[p.kind]}</span> ${esc(p.name)}<br><small>${esc(p.model || "default")}</small></td>
    <td>${p.stats.calls}</td><td>${p.stats.avg_seconds}s</td><td>${p.stats.fallbacks}</td>
    <td>${p.kind === "openrouter" ? "$" + p.stats.cost_usd.toFixed(4) : p.kind === "bot" ? "free" : "subscription"}</td></tr>
    ${p.stats.last_error ? `<tr><td colspan="5" class="err">${esc(p.stats.last_error)}</td></tr>` : ""}`).join("");
  morph($("#stats"), `<table><tr><th>Agent</th><th>Calls</th><th>Avg</th><th>Fallbacks</th><th>Cost</th></tr>${rows}</table>
    <p class="hint">“Fallbacks” = times the agent errored or timed out and the local bot made the move instead.</p>`);
}

// ---------------------------------------------------------------------- bridge
function callHTML(text, call) {
  const strain = /^[1-7]/.test(call) ? call.slice(1).toLowerCase() : "";
  return `<span class="call ${strain === "h" || strain === "d" ? strain : ""}">${esc(text)}</span>`;
}

function bridgeHandRows(cards) {
  return `<div class="hand-rows">${"shdc".split("").map((suit) => {
    const ranks = cards.filter((c) => c[1] === suit).map((c) => (c[0] === "T" ? "10" : c[0]));
    return `<div class="hrow"><span class="sym ${suit}">${SUIT_GLYPH[suit]}</span>${ranks.join(" ") || "–"}</div>`;
  }).join("")}</div>`;
}

function renderBridge() {
  const vul = state.vulnerable || "";
  const info = [];
  if (state.hand_no) info.push(`Board ${state.hand_no} of ${state.boards}`, `Vul: ${vul || "None"}`);
  info.push(`NS ${state.scores.NS}`, `EW ${state.scores.EW}`);
  $("#table-title").textContent = state.game;
  $("#table-info").textContent = info.join(" · ");
  $("#status-line").textContent = state.status || "";
  $("#stop-game").disabled = state.finished;
  const humanIndex = state.players.findIndex((p) => p.kind === "human");
  renderBridgeCenter(humanIndex, vul);
  renderBridgeSeats(humanIndex);
  renderBridgePanel(humanIndex);
}

function renderBridgeCenter(humanIndex, vul) {
  const center = $("#center");
  const isVul = (pid) => vul === "Both" || vul === (pid % 2 === 0 ? "NS" : "EW");
  if (state.phase === "auction" || (!state.contract && state.auction.length)) {
    const columns = [1, 2, 3, 0];   // West, North, East, South
    const offset = columns.indexOf(state.dealer_id);
    const cells = Array(offset).fill("").concat(state.auction.map((a) => callHTML(a.text, a.call)));
    if (state.phase === "auction") cells.push("?");
    let rows = "";
    for (let i = 0; i < cells.length; i += 4) {
      rows += `<tr>${[0, 1, 2, 3].map((j) => `<td>${cells[i + j] ?? ""}</td>`).join("")}</tr>`;
    }
    morph(center, `
      <div class="label">Auction</div>
      <table class="auction"><tr>${columns.map((p) => `<th class="${isVul(p) ? "vul" : ""}">${BRIDGE_COMPASS[p]}</th>`).join("")}</tr>${rows}</table>
      ${state.result ? `<div class="result-line">${esc(state.result)}</div>` : ""}`);
    return;
  }
  if (!state.contract) {
    morph(center, '<div class="label">Shuffling…</div>');
    return;
  }
  const base = humanIndex >= 0 ? humanIndex : 0;
  const winnerPid = state.last_winner;
  const slots = state.trick.map((t) => `
    <div class="slot rel-${(t.pid - base + 4) % 4} ${t.pid === winnerPid ? "winning" : ""}">${cardHTML(t.card)}</div>`).join("");
  const c = state.contract;
  morph(center, `
    <div class="contract-line">${esc(c.text)} by ${BRIDGE_COMPASS[c.declarer]}</div>
    <div class="tricks-line">NS ${state.tricks.NS} · EW ${state.tricks.EW}</div>
    <div class="trick-box">${slots}</div>
    ${state.result ? `<div class="result-line">${esc(state.result)}</div>` : ""}`);
}

function renderBridgeSeats(humanIndex) {
  const pos = seatPositions(4, humanIndex);
  let html = "";
  state.players.forEach((p, i) => {
    const seat = state.seats[p.id];
    const { x, y } = pos[i];
    const classes = ["seat", "bridge"];
    if (state.to_act === p.id) classes.push("turn");
    const roles = (seat.is_declarer ? '<span class="role-tag">Declarer</span>' : "") + (seat.is_dummy ? '<span class="role-tag">Dummy</span>' : "");
    const dealer = state.dealer_id === p.id && state.phase === "auction" ? '<span class="dealer-btn" title="Dealer">D</span>' : "";
    const hand = seat.cards ? bridgeHandRows(seat.cards) : `<div class="hidden-count">${seat.count} cards</div>`;
    html += `
      <div class="${classes.join(" ")}" style="left:${x}%;top:${y}%" data-key="seat-${p.id}">
        <div class="head"><span class="compass">${seat.compass}</span><span class="badge ${p.kind}">${BADGE[p.kind]}</span><span class="name">${esc(p.name)}</span>${dealer}${roles}</div>
        <div class="row-between"><span class="model">${modelLabel(p)}</span>${thinkingHTML(p)}<span class="chips">${seat.team} ${p.chips}</span></div>
        ${hand}
      </div>`;
  });
  morph($("#seats-layer"), html);
}

function renderBridgePanel(humanIndex) {
  const panel = $("#action-panel");
  panel.classList.add("bridge");
  panel.classList.toggle("auction", state.phase === "auction" && humanIndex >= 0);  // room for the bid grid
  const req = state.human_request;
  const owner = req?.type === "bridge_play" ? req.hand_owner : humanIndex;
  const cards = owner >= 0 ? state.seats[owner]?.cards || [] : [];
  const key = JSON.stringify([req, cards, state.finished]);
  if (key === lastRequestKey) return;
  lastRequestKey = key;
  if (state.finished) {
    panel.classList.remove("bridge");
    panel.innerHTML = `<span class="prompt">${esc(state.status)}</span><button class="btn primary" id="again">Back to lobby</button>`;
    $("#again").onclick = showLobby;
    return;
  }
  const playable = req?.type === "bridge_play" ? new Set(req.legal) : null;
  const label = !req ? (humanIndex >= 0 ? "Your hand" : "")
    : req.type === "bridge_bid" ? "Your hand: make a call"
    : req.from_dummy ? "Play a card from DUMMY" : "Your turn: play a card";
  const handHTML = cards.length ? `<div class="bridge-hand"><div class="label">${label}</div>${cards.map((c) =>
    `<button class="card-btn ${playable && !playable.has(c) ? "dim" : ""}" data-card="${c}" ${playable?.has(c) ? "" : "disabled"}>${cardHTML(c)}</button>`).join("")}</div>` : "";
  let bidHTML = "";
  if (req?.type === "bridge_bid") {
    const legal = new Set(req.legal);
    const glyph = { NT: "NT", S: "♠", H: "♥", D: "♦", C: "♣" };
    let grid = "";
    for (const strain of ["NT", "S", "H", "D", "C"]) {
      for (let level = 1; level <= 7; level++) {
        const call = `${level}${strain}`;
        grid += `<button class="bid-btn ${strain.toLowerCase()}" data-call="${call}" ${legal.has(call) ? "" : "disabled"}>${level}${glyph[strain]}</button>`;
      }
    }
    bidHTML = `<div class="bid-box"><div class="bid-grid">${grid}</div><div class="bid-side">
      <button class="btn" data-call="Pass">Pass</button>
      <button class="btn danger" data-call="X" ${legal.has("X") ? "" : "disabled"}>Double</button>
      <button class="btn danger" data-call="XX" ${legal.has("XX") ? "" : "disabled"}>Redouble</button>
    </div></div>`;
  }
  const waiting = !req && !cards.length ? '<span class="prompt">Watching the AIs play…</span>' : "";
  const err = req?.error ? `<div class="err">${esc(req.error)}</div>` : "";
  panel.innerHTML = handHTML + bidHTML + waiting + err;
  panel.querySelectorAll("[data-card]").forEach((b) => { if (!b.disabled) b.onclick = () => act({ card: b.dataset.card }); });
  panel.querySelectorAll("[data-call]").forEach((b) => { if (!b.disabled) b.onclick = () => act({ call: b.dataset.call }); });
}

// ---------------------------------------------------------------------- sound & motion
// Deal the cards the last render added, one after another, each with its own sound.
function dealCards(cards, audible) {
  const gap = cards.length > 6 ? 65 : 90;  // ms; a full deal goes round faster than a single hit
  cards.forEach((el, i) => el.animate(
    [{ transform: "translateY(-14px) rotate(-4deg)", opacity: 0 }, { transform: "none", opacity: 1 }],
    { duration: 250, delay: i * gap, easing: "ease-out", fill: "backwards" }));
  if (audible && cards.length) Sound.cards(Math.min(cards.length, 16), gap / 1000);
}

// The middle of an element on screen, or null if it isn't showing.
function pointOf(selector) {
  const r = document.querySelector(selector)?.getBoundingClientRect();
  if (!r || !r.height) return null;
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}
const stackPoint = (pid) => pointOf(`[data-key="seat-${pid}"] .stack`);

// Slide a few chips across the table. `from` and `to` are screen points, `delay` is in seconds.
function flyChips(from, to, amount, delay = 0) {
  if (!from || !to || document.hidden) return;
  const counts = chipCounts(amount);
  const chips = CHIP_VALUES.flatMap((v, i) => Array(Math.min(counts[i], 3)).fill(v)).slice(0, 6);
  chips.forEach((v, k) => {
    const el = document.createElement("i");
    el.className = `chip c${v} fly`;
    el.style.left = `${from.x}px`;
    el.style.top = `${from.y}px`;
    document.body.appendChild(el);
    const dx = to.x - from.x + (Math.random() - 0.5) * 14, dy = to.y - from.y + (Math.random() - 0.5) * 8;
    el.animate([
      { transform: "translate(-50%, -50%)", opacity: 0 },
      { opacity: 1, offset: 0.15 },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`, opacity: 1 },
    ], { duration: 420, delay: delay * 1000 + k * 50, easing: "cubic-bezier(.25,.8,.35,1)", fill: "both" })
      .onfinish = () => el.remove();
  });
}

// Compare two snapshots of the table and play what happened in between: chips bet, won
// and lost, checks and folds. (Cards make their own sound as they are dealt.)
function tableEvents(prev, cur) {
  if (!prev || prev.game !== cur.game) return;
  if (cur.game === "Contract Bridge") {
    if (cur.hand_no !== prev.hand_no) Sound.cards(13, 0.05);  // a new board is dealt
    return;
  }
  const isPoker = cur.game === "Texas Hold'em";
  const sameHand = prev.hand_no === cur.hand_no;
  const centre = pointOf(isPoker ? "#center .pot-pile" : "#center .dealer-area .label");
  const layer = $("#seats-layer").getBoundingClientRect();
  const humanIndex = cur.players.findIndex((p) => p.kind === "human");
  const pos = seatPositions(cur.players.length, humanIndex);
  const betPoint = (i) => {
    const at = betPos(pos[i]);
    return { x: layer.left + (layer.width * at.x) / 100, y: layer.top + (layer.height * at.y) / 100 };
  };
  const size = (amount) => Math.min(4, Math.floor(Math.log10(Math.max(1, amount))));

  // Your own chips are heard one by one at full volume. Everyone else's are quieter, and
  // however many seats move chips at once, the table makes each kind of sound only once.
  let wait = 0;
  const others = {};
  const cue = (kind, amount, mine) => {
    if (!mine) {
      others[kind] = Math.max(others[kind] || 0, amount);
      return;
    }
    if (kind === "lose") Sound.chipsLose(1, wait);
    else Sound[kind === "win" ? "chipsWin" : "chipsBet"](size(amount), 1, wait);
    wait += 0.15;
  };

  if (isPoker && sameHand) {
    // End of a betting round: the bets in front of the seats are pushed into the pot.
    let pushed = false;
    prev.players.forEach((p, i) => {
      const bet = prev.seats[p.id]?.bet;
      if (!bet || cur.seats[p.id]?.bet) return;
      flyChips(betPoint(i), centre, bet);
      pushed = true;
    });
    if (pushed) {
      Sound.chipsSlide(0.8);
      wait = 0.35;
    }
  }

  cur.players.forEach((p, i) => {
    const was = prev.players[i];
    if (!was) return;
    const mine = p.kind === "human";
    const delta = p.chips - was.chips;
    const seat = cur.seats[p.id] || {};
    const before = (sameHand && prev.seats[p.id]) || {};
    const stack = stackPoint(p.id);
    if (delta < 0) {
      cue("bet", -delta, mine);
      flyChips(stack, betPoint(i), -delta);
    }
    if (isPoker) {
      if (delta > 0) {
        cue("win", delta, mine);
        flyChips(centre, stack, delta, wait);
      }
      if (seat.folded && !before.folded) Sound.fold(mine ? 1 : 0.6);
      if (seat.last_action === "Check" && before.last_action !== "Check") Sound.knock(mine ? 1 : 0.6);
      return;
    }
    // Blackjack: hands that have just been settled against the dealer.
    const settled = (seat.hands || []).filter((h, k) => h.result && !before.hands?.[k]?.result);
    const staked = settled.reduce((sum, h) => sum + h.bet, 0);
    const net = settled.reduce((sum, h) => sum + h.payout, 0);
    if (net > 0) {
      cue("win", net, mine);
      flyChips(betPoint(i), stack, staked);
      flyChips(centre, stack, net, 0.15);
    } else if (net < 0) {
      cue("lose", -net, mine);
      flyChips(betPoint(i), centre, -net);
    } else if (settled.length) {  // a push: the bet comes back
      cue("bet", staked, mine);
      flyChips(betPoint(i), stack, staked);
    } else if (delta > 0) {  // a rebuy
      cue("win", delta, mine);
      flyChips(centre, stack, delta);
    }
  });

  for (const kind of ["bet", "lose", "win"]) {
    if (!others[kind]) continue;
    if (kind === "lose") Sound.chipsLose(0.55, wait);
    else Sound[kind === "win" ? "chipsWin" : "chipsBet"](size(others[kind]), 0.55, wait);
    wait += 0.15;
  }
}

function renderSoundToggle() {
  $("#sound-toggle").textContent = Sound.muted ? "Sound off" : "Sound on";
}

// ---------------------------------------------------------------------- actions
// The chips for the amount being typed or dragged in the bet box.
function showBetChips(amount) {
  morph($("#bet-chips"), chipsHTML(amount, { cols: 4 }));
}

function renderActionPanel(isPoker) {
  const panel = $("#action-panel");
  panel.classList.remove("bridge", "auction");
  const req = state.human_request;
  const key = JSON.stringify(req) + state.finished;
  if (key === lastRequestKey) return;
  lastRequestKey = key;
  if (state.finished) {
    panel.innerHTML = `<span class="prompt">${esc(state.status)}</span><button class="btn primary" id="again">Back to lobby</button>`;
    $("#again").onclick = showLobby;
    return;
  }
  if (!req) {
    const thinking = state.players.filter((p) => p.thinking).map((p) => esc(p.name));
    panel.innerHTML = `<span class="prompt">${thinking.length ? `Waiting for ${thinking.join(", ")}…` : "Waiting…"}</span>`;
    return;
  }
  const err = req.error ? `<div class="err">${esc(req.error)}</div>` : "";
  if (req.type === "poker") {
    const L = req.legal;
    const mid = Math.min(req.max_to, Math.max(req.min_to, Math.round(req.current_bet + (req.pot + req.to_call) * 0.66)));
    panel.innerHTML = `
      <span class="prompt">Your move</span>
      ${L.includes("fold") ? '<button class="btn danger" data-a="fold">Fold<span class="kbd">F</span></button>' : ""}
      ${L.includes("check") ? '<button class="btn" data-a="check">Check<span class="kbd">C</span></button>' : ""}
      ${L.includes("call") ? `<button class="btn" data-a="call">Call ${req.to_call}<span class="kbd">C</span></button>` : ""}
      ${L.includes("raise") ? `
        <div class="raise-box">
          <input type="range" id="raise-range" min="${req.min_to}" max="${req.max_to}" step="1" value="${mid}">
          <input type="number" id="raise-amt" min="${req.min_to}" max="${req.max_to}" value="${mid}">
          <span class="bet-chips" id="bet-chips"></span>
          <button class="btn small" data-quick="min">Min</button>
          <button class="btn small" data-quick="half">½ pot</button>
          <button class="btn small" data-quick="pot">Pot</button>
          <button class="btn small" data-quick="all">All-in</button>
          <button class="btn primary" data-a="raise">${req.label === "bet" ? "Bet" : "Raise to"}<span class="kbd">R</span></button>
        </div>` : ""}
      ${err}`;
    const range = $("#raise-range"), amt = $("#raise-amt");
    if (range) {
      range.oninput = () => showBetChips((amt.value = range.value));
      amt.oninput = () => showBetChips((range.value = amt.value));
      showBetChips(mid);
      panel.querySelectorAll("[data-quick]").forEach((b) => (b.onclick = () => {
        const q = b.dataset.quick;
        const potAfterCall = req.pot + req.to_call;
        let v = q === "min" ? req.min_to : q === "all" ? req.max_to
          : q === "pot" ? req.current_bet + potAfterCall : Math.round(req.current_bet + potAfterCall / 2);
        v = Math.max(req.min_to, Math.min(req.max_to, v));
        amt.value = range.value = v;
        showBetChips(v);
        Sound.chip();
      }));
    }
    panel.querySelectorAll("[data-a]").forEach((b) => (b.onclick = () => act({ action: b.dataset.a, amount: amt ? +amt.value : 0 })));
  } else if (req.type === "bj_bet") {
    const last = Math.min(req.max, Math.max(req.min, +(localStorage.getItem("aitable.lastbet") || req.min)));
    panel.innerHTML = `
      <span class="prompt">Your bet (stack ${req.chips})</span>
      <div class="raise-box">
        <input type="range" id="bet-range" min="${req.min}" max="${req.max}" value="${last}">
        <input type="number" id="bet-amt" min="${req.min}" max="${req.max}" value="${last}">
        <span class="bet-chips" id="bet-chips"></span>
        ${[req.min, 25, 50, 100, req.max].filter((v, i, a) => v >= req.min && v <= req.max && a.indexOf(v) === i).map((v) => `<button class="btn small" data-chip="${v}">${v === req.max ? "Max" : v}</button>`).join("")}
      </div>
      <button class="btn primary" id="place-bet">Deal<span class="kbd">Enter</span></button>${err}`;
    const range = $("#bet-range"), amt = $("#bet-amt");
    range.oninput = () => showBetChips((amt.value = range.value));
    amt.oninput = () => showBetChips((range.value = amt.value));
    showBetChips(last);
    panel.querySelectorAll("[data-chip]").forEach((b) => (b.onclick = () => {
      showBetChips((amt.value = range.value = b.dataset.chip));
      Sound.chip();
    }));
    $("#place-bet").onclick = () => { localStorage.setItem("aitable.lastbet", amt.value); act({ amount: +amt.value }); };
  } else if (req.type === "bj_play") {
    const keys = { hit: "H", stand: "S", double: "D", split: "P" };
    panel.innerHTML = `<span class="prompt">Hand ${req.hand_index + 1}: ${esc(req.value)}</span>` +
      req.legal.map((a) => `<button class="btn ${a === "stand" ? "" : a === "hit" ? "primary" : ""}" data-a="${a}">${a[0].toUpperCase() + a.slice(1)}<span class="kbd">${keys[a]}</span></button>`).join("") + err;
    panel.querySelectorAll("[data-a]").forEach((b) => (b.onclick = () => act({ action: b.dataset.a })));
  } else if (req.type === "busted") {
    panel.innerHTML = `<span class="prompt">${esc(req.message)}</span>
      <button class="btn primary" data-a="rebuy">Rebuy</button>
      <button class="btn" data-a="watch">Watch the AIs play</button>`;
    panel.querySelectorAll("[data-a]").forEach((b) => (b.onclick = () => act({ action: b.dataset.a })));
  }
}

async function act(body) {
  $("#action-panel").querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    await api.post("/api/action", body);
  } catch (e) {
    lastRequestKey = "";
    $("#action-panel").insertAdjacentHTML("beforeend", `<div class="err">${esc(e.message)}</div>`);
  }
}

document.addEventListener("keydown", (e) => {
  if (!state?.human_request || e.target.id === "chat-input") return;
  if (e.target.matches("input:not([type=range]), textarea") && e.key !== "Enter") return;
  const req = state.human_request;
  const click = (sel) => { const b = $("#action-panel " + sel); if (b && !b.disabled) { b.click(); e.preventDefault(); } };
  const k = e.key.toLowerCase();
  if (req.type === "poker") {
    if (k === "f") click('[data-a="fold"]');
    if (k === "c") { click('[data-a="check"]'); click('[data-a="call"]'); }
    if (k === "r") click('[data-a="raise"]');
  } else if (req.type === "bj_play") {
    const map = { h: "hit", s: "stand", d: "double", p: "split" };
    if (map[k]) click(`[data-a="${map[k]}"]`);
  } else if (req.type === "bj_bet" && e.key === "Enter") {
    click("#place-bet");
  }
});

// ======================================================================
// Polling + navigation
// ======================================================================
async function poll() {
  if (polling) return;
  polling = true;
  let v = -1;
  while (!$("#table-screen").classList.contains("hidden")) {
    try {
      const s = await api.get(`/api/state?v=${v}`);
      if (!s.game) { showLobby(); break; }
      const prev = state;
      state = s;
      v = s.version;
      render();
      dealCards(dealt.splice(0), !!prev);
      tableEvents(prev, s);
    } catch (_) {
      $("#status-line").textContent = "Lost connection to the server — is server.py still running?";
      await new Promise((r) => setTimeout(r, 1500));
    }
  }
  polling = false;
}

setInterval(() => {
  document.querySelectorAll(".think-secs").forEach((el) => {
    const start = thinkingClock[el.dataset.pid];
    if (start) el.textContent = `${Math.round((Date.now() - start) / 1000)}s`;
  });
}, 500);

function showTable() {
  $("#lobby").classList.add("hidden");
  $("#table-screen").classList.remove("hidden");
  lastRequestKey = "";
  state = null;
  poll();
}

function showLobby() {
  $("#table-screen").classList.add("hidden");
  $("#lobby").classList.remove("hidden");
  loadSettings();
}

// ======================================================================
// Wire-up
// ======================================================================
async function init() {
  await loadSettings();
  loadOpenRouterModels();
  if (!loadLobby()) preset("mixed");
  renderSeats();
  syncOptionsVisibility();
  document.querySelectorAll('input[name="game"]').forEach((r) => r.addEventListener("change", syncOptionsVisibility));
  document.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => preset(b.dataset.preset)));
  $("#seat-plus").onclick = () => { if (seats.length < 7) { seats.push(defaultSeat(seats.length % 2 ? "claude" : "codex", seats.length)); renderSeats(); } };
  $("#seat-minus").onclick = () => { if (seats.length > 2) { seats.pop(); renderSeats(); } };
  $("#start-game").onclick = startGame;
  $("#open-settings").onclick = openSettings;
  $("#open-settings-2").onclick = openSettings;
  $("#settings-cancel").onclick = () => $("#settings-dialog").close();
  $("#settings-save").onclick = async () => { await saveSettings(); $("#settings-dialog").close(); };
  $("#clear-or-key").onclick = async () => {
    if (!confirm("Remove the saved OpenRouter key?")) return;
    await saveSettings({ openrouter_api_key: "" });
    $("#settings-dialog").close();
  };
  $("#back-lobby").onclick = showLobby;
  renderSoundToggle();
  $("#sound-toggle").onclick = () => {
    Sound.setMuted(!Sound.muted);
    renderSoundToggle();
    Sound.chip();
  };
  $("#stop-game").onclick = async () => { if (confirm("Stop this game?")) await api.post("/api/game/stop"); };
  $("#chat-form").onsubmit = async (e) => {
    e.preventDefault();
    const text = $("#chat-input").value.trim();
    if (!text) return;
    $("#chat-input").value = "";
    await api.post("/api/chat", { text });
  };
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $("#log").classList.toggle("hidden", t.dataset.tab !== "log");
    $("#stats").classList.toggle("hidden", t.dataset.tab !== "stats");
  }));
  // Resume a game already running on the server (e.g. after a page refresh).
  const s = await api.get("/api/state").catch(() => null);
  if (s && s.game && !s.finished) showTable();
}

init();

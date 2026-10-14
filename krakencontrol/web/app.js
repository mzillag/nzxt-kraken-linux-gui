/* Kraken Control UI.  Vanilla JS, talks to the local daemon over HTTP + Server-Sent Events. */
"use strict";

// ----------------------------------------------------------------------------- state
const S = {
  data: null,          // last state from the daemon
  page: "monitoring",
  metrics: [],
  library: [],
  openRow: null,       // cooling row expanded: "pump" | "fan"
  lcdOpen: true,
  dragging: null,
  previewId: -1,
  processes: [],
  specs: null,
  autostart: null,
};

const MODE_LABELS = {
  image: "Image/GIF",
  single: "Single Infographic",
  single_gif: "Single Infographic + GIF",
  dual: "Dual Infographic",
  dual_gif: "Dual Infographic + GIF",
  triple: "Triple Infographic",
  triple_gif: "Triple Infographic + GIF",
  clock: "Clockface",
  carousel: "Carousel",
  liquid: "Liquid Temperature (firmware)",
};
const MODE_ORDER = ["image", "single", "single_gif", "dual", "dual_gif", "triple", "triple_gif", "clock", "carousel", "liquid"];
const UNAVAILABLE_MODES = ["Audio Visualizer", "Web Integration"];
const CURVE_TEMPS = [20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100];
const CHANNEL_MIN = { pump: 20, fan: 0 };
const SOURCE_LABELS = { liquid_temp: "Liquid", cpu_temp: "CPU", gpu_temp: "GPU" };
const BUILTIN_PROFILES = ["Default", "Silent", "Performance", "Fixed"];

// ----------------------------------------------------------------------------- icons
const ICONS = {
  monitoring: '<svg viewBox="0 0 24 24"><path d="M3 20V4M3 20h18"/><path d="M6 16l4-6 3 3 5-7"/></svg>',
  specs: '<svg viewBox="0 0 24 24"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  lighting: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></svg>',
  cooling: '<svg viewBox="0 0 24 24"><circle cx="12" cy="11" r="8"/><path d="M8 11c1.5 2 6.5 2 8 0"/><path d="M9 19c0 2 1 3 3 3s3-1 3-3"/></svg>',
  settings: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  pump: '<svg viewBox="0 0 34 34"><circle cx="17" cy="17" r="15" fill="none" stroke="#fff" stroke-width="2"/><text x="17" y="21" text-anchor="middle" font-size="9" font-weight="800" fill="#fff" font-family="Inter, sans-serif">NZXT</text></svg>',
  fan: '<svg viewBox="0 0 34 34" fill="none" stroke="#fff" stroke-width="1.5"><rect x="2" y="2" width="30" height="30" rx="3"/><circle cx="17" cy="17" r="12"/><circle cx="17" cy="17" r="3"/><path d="M17 5c3 4 3 8 0 9M29 17c-4 3-8 3-9 0M17 29c-3-4-3-8 0-9M5 17c4-3 8-3 9 0"/></svg>',
  lcd: '<svg viewBox="0 0 34 34"><circle cx="17" cy="17" r="15" fill="none" stroke="#fff" stroke-width="2"/><text x="17" y="21" text-anchor="middle" font-size="9" font-weight="800" fill="#fff" font-family="Inter, sans-serif">NZXT</text></svg>',
  thermo: '<svg viewBox="0 0 24 24"><path d="M10 14.5V5a2 2 0 1 1 4 0v9.5a4 4 0 1 1-4 0z"/></svg>',
  rotate: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
  sunLow: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/></svg>',
  sunHigh: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></svg>',
  chevron: '<svg viewBox="0 0 12 8"><path d="M1 1l5 5 5-5"/></svg>',
};

// ----------------------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "style") node.style.cssText = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
};
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const cfg = () => (S.data ? S.data.config : null);
const unit = () => (cfg() ? cfg().general.temperature_unit : "C");
const fmtTemp = (v) => (v === null || v === undefined ? "--" : `${Math.round(unit() === "F" ? v * 9 / 5 + 32 : v)}°`);
const metricName = (id) => (S.metrics.find((m) => m.id === id) || { name: id }).name;

async function api(path, method = "GET", body = null, raw = false) {
  const opts = { method, headers: {} };
  if (body !== null && !raw) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  if (body !== null && raw) opts.body = body;
  const resp = await fetch(path, opts);
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(json.error || resp.statusText);
  return json;
}

let toastTimer = null;
function toast(message, error = false) {
  let node = $("#toast");
  if (!node) { node = el("div", { id: "toast", class: "toast" }); document.body.append(node); }
  node.textContent = message;
  node.className = "toast" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), 3500);
}

function deepMerge(base, patch) {
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object" && !Array.isArray(base[k])) deepMerge(base[k], v);
    else base[k] = v;
  }
  return base;
}

async function patchConfig(patch, rerender = false) {
  if (S.data) deepMerge(S.data.config, JSON.parse(JSON.stringify(patch)));
  if (rerender) render();
  try { await api("/api/config", "POST", patch); }
  catch (e) { toast(`Could not save: ${e.message}`, true); }
}

const debounced = {};
function patchConfigDebounced(key, patch, delay = 250) {
  if (S.data) deepMerge(S.data.config, JSON.parse(JSON.stringify(patch)));
  clearTimeout(debounced[key]);
  debounced[key] = setTimeout(() => api("/api/config", "POST", patch).catch((e) => toast(e.message, true)), delay);
}

// ----------------------------------------------------------------------------- generic controls
function select(options, value, onChange, cls = "select") {
  const node = el("select", { class: cls, onchange: (e) => onChange(e.target.value) });
  for (const opt of options) {
    const o = el("option", { value: opt.value }, opt.label);
    if (opt.disabled) o.disabled = true;
    if (String(opt.value) === String(value)) o.selected = true;
    node.append(o);
  }
  return node;
}

function swatch(color, onChange) {
  const input = el("input", { type: "color", value: color || "#ffffff" });
  const chip = el("span", { class: "chip", style: `background:${color || "#fff"}` });
  const wrap = el("span", { class: "swatch-wrap" },
    el("span", { class: "swatch", onclick: () => input.click() }, chip, el("span", { html: ICONS.chevron })),
    input);
  input.addEventListener("input", () => { chip.style.background = input.value; });
  input.addEventListener("change", () => onChange(input.value.toUpperCase()));
  return wrap;
}

function checkbox(checked, onChange, label = "") {
  const input = el("input", { type: "checkbox" });
  input.checked = !!checked;
  input.addEventListener("change", () => onChange(input.checked));
  return el("label", { class: "checkbox" }, input, label ? el("span", {}, label) : null);
}

function toggle(on, onChange) {
  const node = el("div", { class: "toggle" + (on ? " on" : "") });
  node.addEventListener("click", () => { node.classList.toggle("on"); onChange(node.classList.contains("on")); });
  return node;
}

function brightnessSlider(value, onInput, wide = false) {
  const input = el("input", { type: "range", class: "slider" + (wide ? " wide" : ""), min: 0, max: 100, value });
  input.addEventListener("input", () => onInput(parseInt(input.value, 10)));
  return el("div", { class: "brightness" }, el("span", { html: ICONS.sunLow }), input, el("span", { html: ICONS.sunHigh }));
}

function pageHeader(title, extras = []) {
  const profiles = profileOptions();
  return el("div", { class: "page-header" },
    el("h1", {}, title),
    select(profiles, cfg().general.active_profile, applyProfile),
    el("span", { class: "gear", html: ICONS.settings, title: "Manage profiles", onclick: openProfileManager }),
    el("div", { class: "spacer" }),
    ...extras);
}

// ----------------------------------------------------------------------------- profiles
function profileOptions() {
  const custom = Object.keys((cfg() && cfg().profiles) || {});
  return [{ value: "__add", label: "Add Profile" }, ...BUILTIN_PROFILES.map((p) => ({ value: p, label: p })),
    ...custom.map((p) => ({ value: p, label: p }))];
}

function applyProfile(name) {
  if (name === "__add") { openAddProfile(); render(); return; }
  const c = cfg();
  if (name === "Default") {
    api("/api/config/reset", "POST", { section: "cooling" }).then(() => patchConfig({ general: { active_profile: "Default" } }, true)).catch((e) => toast(e.message, true));
    return;
  }
  if (name === "Silent" || name === "Performance" || name === "Fixed") {
    const mode = name.toLowerCase();
    patchConfig({ cooling: { fan: { mode }, pump: { mode } }, general: { active_profile: name } }, true);
    return;
  }
  const profile = c.profiles[name];
  if (!profile) return;
  api("/api/config", "POST", { cooling: profile.cooling, lcd: profile.lcd || {}, general: { active_profile: name } })
    .then(() => toast(`Profile "${name}" applied`)).catch((e) => toast(e.message, true));
}

function openAddProfile() {
  const input = el("input", { class: "text", placeholder: "Profile name" });
  openModal("Add Profile", [el("p", { class: "hint", style: "margin:0 0 12px;color:var(--text-dim)" }, "Saves the current cooling and LCD settings under a new name."), input], [
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: () => {
      const name = input.value.trim();
      if (!name || BUILTIN_PROFILES.includes(name) || name === "__add") { toast("Choose another name", true); return; }
      const c = cfg();
      const profiles = Object.assign({}, c.profiles, { [name]: { cooling: c.cooling, lcd: c.lcd } });
      api("/api/config/profiles", "PUT", profiles).then(() => patchConfig({ general: { active_profile: name } }, true)).then(closeModal).catch((e) => toast(e.message, true));
    } }, "Save"),
  ]);
  setTimeout(() => input.focus(), 50);
}

function openProfileManager() {
  const names = Object.keys(cfg().profiles || {});
  const list = names.length ? names.map((n) => el("div", { class: "settings-row" }, el("div", {}, n),
    el("button", { class: "btn", onclick: () => {
      const profiles = Object.assign({}, cfg().profiles); delete profiles[n];
      api("/api/config/profiles", "PUT", profiles).then(() => { cfg().profiles = profiles; closeModal(); render(); });
    } }, "Delete"))) : [el("p", { style: "color:var(--text-dim)" }, "No custom profiles yet. Choose \"Add Profile\" in the profile dropdown.")];
  openModal("Profiles", list, [el("button", { class: "btn primary", onclick: closeModal }, "Close")]);
}

// ----------------------------------------------------------------------------- modal
function openModal(title, body, footer) {
  closeModal();
  const root = $("#modal-root");
  root.append(el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target.classList.contains("modal-backdrop")) closeModal(); } },
    el("div", { class: "modal" },
      el("h2", {}, title, el("span", { class: "close", onclick: closeModal }, "✕")),
      el("hr"),
      ...body,
      footer ? el("div", { class: "foot" }, ...footer) : null)));
}
function closeModal() { $("#modal-root").innerHTML = ""; }

// ----------------------------------------------------------------------------- navigation
const PAGES = [
  { id: "monitoring", label: "PC Monitoring", icon: "monitoring" },
  { id: "specs", label: "System Specs", icon: "specs" },
  { id: "lighting", label: "Lighting", icon: "lighting" },
  { id: "cooling", label: "Cooling", icon: "cooling" },
];

function renderNav() {
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const p of PAGES) {
    nav.append(el("div", { class: "item" + (S.page === p.id ? " active" : ""), onclick: () => navigate(p.id) },
      el("span", { html: ICONS[p.icon] }), p.label));
  }
  const bottom = $("#nav-bottom");
  bottom.innerHTML = "";
  bottom.append(el("div", { class: "item" + (S.page === "settings" ? " active" : ""), onclick: () => navigate("settings") },
    el("span", { html: ICONS.settings }), "Settings"));
}

function navigate(page) {
  S.page = page;
  try { localStorage.setItem("kc.page", page); } catch (e) { /* ignore */ }
  render();
}

// ----------------------------------------------------------------------------- render
function render() {
  if (!S.data) return;
  document.documentElement.dataset.theme = cfg().general.dark_mode === false ? "light" : "dark";
  renderNav();
  renderTitlebar();
  const content = $("#content");
  const scroll = content.scrollTop;
  content.innerHTML = "";
  content.append(renderBanner());
  const pages = { monitoring: renderMonitoring, specs: renderSpecs, lighting: renderLighting, cooling: renderCooling, settings: renderSettings };
  content.append((pages[S.page] || renderMonitoring)());
  content.scrollTop = scroll;
}

function renderTitlebar() {
  const sel = $("#profile-select");
  const options = profileOptions();
  if (sel.options.length !== options.length || sel.value !== cfg().general.active_profile) {
    sel.innerHTML = "";
    for (const o of options) sel.append(el("option", { value: o.value }, o.label));
    sel.value = cfg().general.active_profile;
    sel.onchange = (e) => applyProfile(e.target.value);
  }
  const d = S.data.device;
  $("#conn-dot").className = "dot" + (d.connected ? " ok" : "");
  $("#conn-text").textContent = d.connected ? (d.info.mock ? "mock device" : `${d.info.name} · fw ${d.info.firmware}`) : "Kraken not connected";
}

function renderBanner() {
  const d = S.data.device;
  if (d.connected) return el("span");
  if (d.error_kind === "permission") {
    return el("div", { class: "banner" }, el("div", { class: "text" },
      el("div", {}, el("b", {}, "No permission to access the Kraken over USB."), " Install the udev rule once, then the device connects automatically:"),
      el("div", { style: "margin-top:6px" }, el("code", {}, "cd ~/Projects/kraken-control && sudo sh packaging/install-udev.sh"))),
      el("button", { class: "btn", onclick: reconnect }, "Retry"));
  }
  return el("div", { class: "banner warn" }, el("div", { class: "text" }, el("b", {}, "Kraken not connected. "), d.error || ""),
    el("button", { class: "btn", onclick: reconnect }, "Retry"));
}

async function reconnect() {
  try { const r = await api("/api/device/reconnect", "POST", {}); toast(r.connected ? "Connected" : (r.error || "Not connected"), !r.connected); }
  catch (e) { toast(e.message, true); }
}

// ----------------------------------------------------------------------------- PC Monitoring
function gauge(value, label) {
  const r = 52, c = 2 * Math.PI * r, frac = clamp((value || 0) / 100, 0, 1);
  const arc = c * 0.75;
  return el("div", { class: "gauge" },
    el("div", { html: `<svg viewBox="0 0 130 130"><circle class="track" cx="65" cy="65" r="${r}" stroke-dasharray="${arc} ${c}" transform="rotate(135 65 65)"/><circle class="fill" cx="65" cy="65" r="${r}" stroke-dasharray="${arc * frac} ${c}" transform="rotate(135 65 65)"/></svg>` }),
    el("div", { class: "num" }, value === null || value === undefined ? "--" : `${Math.round(value)}%`, el("small", {}, label)));
}
function bar(label, value, text, max = 100) {
  return el("div", { class: "bar" },
    el("div", { class: "track" }, el("div", { class: "fill", style: `width:${clamp((value || 0) / max * 100, 0, 100)}%` })),
    el("div", { class: "line" }, el("span", {}, label), el("b", {}, text)));
}
function loadWord(v) { return v === null || v === undefined ? "No data" : v < 25 ? "Minimal Load" : v < 60 ? "Moderate Load" : "Heavy Load"; }

function renderMonitoring() {
  const s = S.data.sensors;
  const cooler = s.cooler;
  const root = el("div", {});
  root.append(el("div", { class: "mon-grid" },
    el("div", { class: "mon-card" }, el("h2", {}, "CPU"),
      el("div", { class: "body" }, gauge(s.cpu.load, "Load"),
        el("div", { class: "bars" },
          bar("Temperature", s.cpu.temperature, fmtTemp(s.cpu.temperature)),
          bar("Clock", s.cpu.frequency, s.cpu.frequency ? `${Math.round(s.cpu.frequency)} MHz` : "n/a", 6000),
          bar("Fan", cooler.fan_rpm, cooler.fan_rpm ? `${cooler.fan_rpm} RPM` : "n/a", 2500))),
      el("div", { class: "foot" }, loadWord(s.cpu.load))),
    el("div", { class: "mon-card" }, el("h2", {}, "GPU"),
      el("div", { class: "body" }, gauge(s.gpu.available ? s.gpu.load : null, "Load"),
        el("div", { class: "bars" },
          bar("Temperature", s.gpu.temperature, fmtTemp(s.gpu.temperature)),
          bar("Clock", s.gpu.frequency, s.gpu.frequency ? `${Math.round(s.gpu.frequency)} MHz` : "n/a", 3000),
          bar("Memory", s.gpu.memory_total ? s.gpu.memory_used / s.gpu.memory_total * 100 : 0, s.gpu.memory_used ? `${Math.round(s.gpu.memory_used)} MB` : "n/a"))),
      el("div", { class: "foot" }, loadWord(s.gpu.available ? s.gpu.load : null)))));
  root.append(el("div", { class: "mon-grid three", style: "margin-top:16px" },
    el("div", { class: "mon-card" }, el("h2", {}, "RAM"), el("div", { class: "body" }, gauge(s.memory.load, "Load"),
      el("div", { class: "bars" }, bar("Used", s.memory.load, s.memory.used ? `${s.memory.used.toFixed(1)} GB` : "n/a"), bar("Total", 100, s.memory.total ? `${s.memory.total.toFixed(1)} GB` : "n/a")))),
    el("div", { class: "mon-card" }, el("h2", {}, "Liquid"), el("div", { class: "body" }, gauge(cooler.liquid_temperature, "Temp"),
      el("div", { class: "bars" }, bar("Liquid", cooler.liquid_temperature, fmtTemp(cooler.liquid_temperature)), bar("Pump", cooler.pump_rpm, cooler.pump_rpm ? `${cooler.pump_rpm} RPM` : "n/a", 3200), bar("Fan", cooler.fan_rpm, cooler.fan_rpm ? `${cooler.fan_rpm} RPM` : "n/a", 2500)))),
    el("div", { class: "mon-card" }, el("h2", {}, "Kraken"), el("div", { class: "body" }, gauge(cooler.pump_duty, "Pump"),
      el("div", { class: "bars" }, bar("Pump duty", cooler.pump_duty, cooler.pump_duty !== null ? `${cooler.pump_duty}%` : "n/a"), bar("Fan duty", cooler.fan_duty, cooler.fan_duty !== null ? `${cooler.fan_duty}%` : "n/a"), bar("LCD", S.data.lcd.fps * 10, S.data.lcd.transport ? `${S.data.lcd.transport} · ${S.data.lcd.fps} fps` : "idle", 300))))));
  const procs = el("div", { class: "procs" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Top Processes"), el("th", {}, "CPU"), el("th", {}, "RAM %"), el("th", {}, "RAM"))),
    el("tbody", { id: "procs-body" }, ...S.processes.map((p) => el("tr", {}, el("td", {}, p.name), el("td", {}, `${p.cpu.toFixed(0)}%`), el("td", {}, `${p.mem.toFixed(1)}%`), el("td", {}, `${p.rss_mb}MB`))))));
  root.append(procs);
  return root;
}

// ----------------------------------------------------------------------------- System Specs
function renderSpecs() {
  const sp = S.specs;
  const root = el("div", {});
  root.append(el("div", { class: "page-header" }, el("h1", {}, "System Specs")));
  if (!sp) { root.append(el("p", { style: "color:var(--text-dim)" }, "Loading…")); api("/api/system/specs").then((r) => { S.specs = r; if (S.page === "specs") render(); }); return root; }
  const rows = [
    ["Operating system", sp.os], ["Kernel", sp.kernel], ["Hostname", sp.hostname],
    ["Motherboard", [sp.board.vendor, sp.board.name].filter(Boolean).join(" ") || "n/a"],
    ["CPU", `${sp.cpu.name} (${sp.cpu.threads} threads)`],
    ["GPU", sp.gpu.name ? `${sp.gpu.name}${sp.gpu.memory_total ? ` · ${Math.round(sp.gpu.memory_total / 1024)} GB` : ""}` : "n/a"],
    ["Memory", sp.memory_total ? `${sp.memory_total.toFixed(1)} GB` : "n/a"],
    ["Cooler", sp.kraken.name ? `${sp.kraken.name} · firmware ${sp.kraken.firmware} · serial ${sp.kraken.serial}` : "not connected"],
    ["Kernel driver", sp.kraken.kernel_driver ? "nzxt_kraken3 (hwmon)" : "not loaded"],
  ];
  root.append(el("table", { class: "spec-table" }, ...rows.map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v)))));
  return root;
}

// ----------------------------------------------------------------------------- Cooling
// CAM 4.76 preset tables (same values as krakencontrol/config.py), kept here so the
// curve follows a preset or source change immediately instead of waiting for the
// daemon's next state event.
const PRESETS = {
  fan: {
    silent: [[20, 30], [25, 30], [30, 30], [35, 30], [40, 35], [45, 42], [50, 50], [55, 57], [60, 63], [65, 69], [70, 74], [75, 78], [80, 84], [85, 90], [90, 100], [95, 100], [100, 100]],
    performance: [[20, 50], [25, 50], [30, 50], [35, 50], [40, 60], [45, 70], [50, 80], [55, 90], [60, 100], [65, 100], [70, 100], [75, 100], [80, 100], [85, 100], [90, 100], [95, 100], [100, 100]],
  },
  pump: {
    silent: [[20, 60], [25, 60], [30, 60], [35, 60], [40, 70], [45, 80], [50, 90], [55, 100], [60, 100], [65, 100], [70, 100], [75, 100], [80, 100], [85, 100], [90, 100], [95, 100], [100, 100]],
    performance: [[20, 70], [25, 70], [30, 70], [35, 70], [40, 80], [45, 85], [50, 90], [55, 95], [60, 100], [65, 100], [70, 100], [75, 100], [80, 100], [85, 100], [90, 100], [95, 100], [100, 100]],
  },
};
function channelPoints(channel) {
  const c = cfg().cooling[channel];
  if (c.mode === "fixed") return CURVE_TEMPS.map((t) => [t, c.fixed]);
  let pts;
  if (c.mode === "custom") pts = c.custom && c.custom.length ? c.custom : PRESETS[channel].performance;
  else if (PRESETS[channel][c.mode]) pts = PRESETS[channel][c.mode];
  else pts = (S.data.cooling[channel].points || []).length ? S.data.cooling[channel].points : PRESETS[channel].silent;
  // Expand preset/custom points onto the 17 CAM nodes
  const expanded = pts.map(([t, d]) => [Number(t), Number(d)]);
  return CURVE_TEMPS.map((t) => [t, interp(expanded, t)]);
}
function interp(points, t) {
  if (!points.length) return 50;
  if (t <= points[0][0]) return points[0][1];
  if (t >= points[points.length - 1][0]) return points[points.length - 1][1];
  for (let i = 0; i < points.length - 1; i++) {
    const [t0, d0] = points[i], [t1, d1] = points[i + 1];
    if (t >= t0 && t <= t1) return t1 === t0 ? d0 : d0 + (d1 - d0) * (t - t0) / (t1 - t0);
  }
  return points[points.length - 1][1];
}

function renderCooling() {
  const s = S.data.sensors;
  const root = el("div", {});
  root.append(pageHeader("Cooling"));
  root.append(el("div", { class: "tiles" },
    tile("Liquid Temperature", fmtTemp(s.cooler.liquid_temperature), S.data.device.info ? S.data.device.info.name : "", "tile-liquid"),
    tile("CPU Temperature", fmtTemp(s.cpu.temperature), "", "tile-cpu"),
    tile("GPU Temperature", fmtTemp(s.gpu.temperature), s.gpu.name || "", "tile-gpu")));
  const card = el("div", { class: "card" }, el("div", { class: "card-title" }, S.data.device.info ? S.data.device.info.name : "Kraken Elite"));
  for (const ch of ["pump", "fan"]) {
    card.append(coolingRow(ch));
    if (S.openRow === ch) card.append(curvePanel(ch));
  }
  root.append(card);
  return root;
}
function tile(label, value, sub, id) {
  return el("div", { class: "tile" }, el("div", { class: "label" }, label), el("div", { class: "value", id: id + "-value" }, value), el("div", { class: "sub" }, sub));
}

function coolingRow(channel) {
  const c = cfg().cooling[channel];
  const rpm = channel === "pump" ? S.data.sensors.cooler.pump_rpm : S.data.sensors.cooler.fan_rpm;
  const sensor = c.sensor;
  const modeSel = select([
    { value: "fixed", label: "Fixed" }, { value: "silent", label: "Silent" }, { value: "performance", label: "Performance" }, { value: "custom", label: "Custom" },
  ], c.mode, (v) => {
    const patch = { cooling: { [channel]: { mode: v } } };
    if (v === "custom" && (!c.custom || !c.custom.length)) patch.cooling[channel].custom = channelPoints(channel);
    patchConfig(patch, true);
  });
  modeSel.addEventListener("click", (e) => e.stopPropagation());
  return el("div", { class: "device-row" + (S.openRow === channel ? " open" : ""), onclick: () => { S.openRow = S.openRow === channel ? null : channel; render(); } },
    el("div", { class: "icon", html: ICONS[channel] }),
    el("div", { class: "name" }, channel === "pump" ? "Pump" : "Fan"),
    el("div", { class: "right" },
      el("div", { class: "rpm", id: `${channel}-rpm` }, rpm === null || rpm === undefined ? "-- RPM" : `${rpm} RPM`),
      el("span", { class: "pill" }, el("span", { html: ICONS.thermo }), SOURCE_LABELS[sensor] || sensor),
      modeSel));
}

function curvePanel(channel) {
  const c = cfg().cooling[channel];
  const sensor = c.sensor;
  const panel = el("div", { class: "curve-panel" });
  panel.append(el("div", { class: "pwm" }, "PWM"));
  panel.append(el("div", { class: "source" }, "Temperatures source:",
    select([{ value: "cpu_temp", label: "CPU" }, { value: "gpu_temp", label: "GPU" }, { value: "liquid_temp", label: "Liquid" }], sensor,
      (v) => patchConfig({ cooling: { [channel]: { sensor: v } } }, true), "select small")));
  if (c.mode === "fixed") {
    const num = el("input", { type: "number", class: "num", min: CHANNEL_MIN[channel], max: 100, value: c.fixed });
    num.addEventListener("change", () => patchConfig({ cooling: { [channel]: { fixed: clamp(parseInt(num.value, 10) || 0, CHANNEL_MIN[channel], 100) } } }, true));
    panel.append(el("div", { class: "fixed-row" }, "Fixed duty:", num, "%"));
  }
  panel.append(curveSvg(channel));
  if (c.mode === "custom") {
    panel.append(el("div", { class: "actions" },
      el("span", { class: "apply-link", onclick: () => {
        const other = channel === "pump" ? "fan" : "pump";
        const pts = channelPoints(channel).map(([t, d]) => [t, clamp(d, CHANNEL_MIN[other], 100)]);
        patchConfig({ cooling: { [other]: { mode: "custom", custom: pts } } }, true);
        toast(`Curve applied to ${other}`);
      } }, "Apply this curve to other channels"),
      el("button", { class: "btn primary", onclick: () => patchConfig({ cooling: { [channel]: { custom: channelPoints(channel) } } }).then(() => toast("Curve applied")) }, "Apply")));
  }
  return panel;
}

const CV = { w: 760, h: 270, left: 56, right: 24, top: 34, bottom: 36 };
function cx(t) { return CV.left + (t - 10) / 90 * (CV.w - CV.left - CV.right); }
function cy(d) { return CV.top + (100 - d) / 100 * (CV.h - CV.top - CV.bottom); }

function curveSvg(channel) {
  const c = cfg().cooling[channel];
  const editable = c.mode === "custom" || c.mode === "fixed";
  let points = channelPoints(channel);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${CV.w} ${CV.h}`);
  svg.setAttribute("class", "curve");
  svg.dataset.channel = channel;
  const ns = (tag, attrs) => { const n = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
  for (const d of [0, 25, 50, 75, 100]) {
    svg.append(ns("line", { class: "grid", x1: CV.left, x2: CV.w - CV.right, y1: cy(d), y2: cy(d) }));
    const t = ns("text", { x: CV.left - 10, y: cy(d) + 4, "text-anchor": "end" }); t.textContent = `${d}%`; svg.append(t);
  }
  for (let temp = 10; temp <= 100; temp += 10) {
    const t = ns("text", { x: cx(temp), y: CV.h - 10, "text-anchor": "middle" }); t.textContent = `${temp}°`; svg.append(t);
  }
  // current sensor marker
  const sensor = cfg().cooling[channel].sensor;
  const sensorValue = S.data.sensors.cooler.liquid_temperature !== null ? valueOfSensor(sensor) : null;
  const marker = ns("line", { class: "marker", x1: 0, x2: 0, y1: CV.top - 6, y2: cy(0), id: `${channel}-marker` });
  const markerLabel = ns("text", { class: "marker-label", "text-anchor": "middle", y: CV.top - 12, id: `${channel}-marker-label` });
  svg.append(marker, markerLabel);
  positionMarker(marker, markerLabel, sensor, sensorValue);
  const line = ns("polyline", { class: "line" });
  svg.append(line);
  const dots = points.map(([t], i) => { const d = ns("circle", { class: "pt" + (editable ? "" : " locked"), r: 5, "data-i": i }); svg.append(d); return d; });
  const halo = ns("circle", { class: "halo", r: 11, visibility: "hidden" });
  const tip = ns("text", { class: "tip", "text-anchor": "middle", visibility: "hidden" });
  svg.append(halo, tip);
  const draw = () => {
    line.setAttribute("points", points.map(([t, d]) => `${cx(t)},${cy(d)}`).join(" "));
    points.forEach(([t, d], i) => { dots[i].setAttribute("cx", cx(t)); dots[i].setAttribute("cy", cy(d)); });
  };
  draw();
  if (!editable) return svg;

  let active = null;
  const toDuty = (evt) => {
    const rect = svg.getBoundingClientRect();
    const y = (evt.clientY - rect.top) / rect.height * CV.h;
    return clamp(Math.round(100 - (y - CV.top) / (CV.h - CV.top - CV.bottom) * 100), CHANNEL_MIN[channel], 100);
  };
  const move = (evt) => {
    if (active === null) return;
    const duty = toDuty(evt);
    if (c.mode === "fixed") {
      points = points.map(([t]) => [t, duty]);
    } else {
      points[active][1] = duty;
      // CAM rule: nodes left can't be higher, nodes right can't be lower
      for (let i = active - 1; i >= 0; i--) if (points[i][1] > duty) points[i][1] = duty;
      for (let i = active + 1; i < points.length; i++) if (points[i][1] < duty) points[i][1] = duty;
    }
    draw();
    halo.setAttribute("cx", cx(points[active][0])); halo.setAttribute("cy", cy(points[active][1]));
    tip.setAttribute("x", cx(points[active][0])); tip.setAttribute("y", cy(points[active][1]) - 18); tip.textContent = `${duty}%`;
  };
  const up = () => {
    if (active === null) return;
    active = null;
    halo.setAttribute("visibility", "hidden"); tip.setAttribute("visibility", "hidden");
    window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
    if (c.mode === "fixed") patchConfig({ cooling: { [channel]: { fixed: points[0][1] } } }, true);
    else patchConfig({ cooling: { [channel]: { custom: points.map(([t, d]) => [t, d]) } } });
  };
  dots.forEach((dot, i) => dot.addEventListener("mousedown", (evt) => {
    evt.preventDefault(); evt.stopPropagation();
    active = i;
    halo.setAttribute("visibility", "visible"); tip.setAttribute("visibility", "visible");
    move(evt);
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  }));
  return svg;
}
function valueOfSensor(sensor) {
  const s = S.data.sensors;
  return sensor === "cpu_temp" ? s.cpu.temperature : sensor === "gpu_temp" ? s.gpu.temperature : s.cooler.liquid_temperature;
}
function positionMarker(marker, label, sensor, value) {
  if (value === null || value === undefined) { marker.setAttribute("visibility", "hidden"); label.setAttribute("visibility", "hidden"); return; }
  const x = cx(clamp(value, 10, 100));
  marker.setAttribute("visibility", "visible"); label.setAttribute("visibility", "visible");
  marker.setAttribute("x1", x); marker.setAttribute("x2", x);
  label.setAttribute("x", x);
  label.textContent = `${SOURCE_LABELS[sensor] || sensor}: ${fmtTemp(value)}`;
}

// ----------------------------------------------------------------------------- Lighting (LCD)
function renderLighting() {
  const lcd = cfg().lcd;
  const root = el("div", {});
  root.append(pageHeader("Lighting", [brightnessSlider(lcd.brightness, (v) => patchConfigDebounced("brightness", { lcd: { brightness: v } }))]));
  const card = el("div", { class: "card" }, el("div", { class: "card-title" }, S.data.device.info ? S.data.device.info.name : "Kraken Elite", el("span", { class: "info", title: S.data.device.info ? `Firmware ${S.data.device.info.firmware} · serial ${S.data.device.info.serial}` : "" }, "i")));
  const modeOptions = MODE_ORDER.map((m) => ({ value: m, label: MODE_LABELS[m] })).concat(UNAVAILABLE_MODES.map((m) => ({ value: "x-" + m, label: `${m} (not available)`, disabled: true })));
  const modeSel = select(modeOptions, lcd.mode, (v) => patchConfig({ lcd: { mode: v } }, true));
  modeSel.addEventListener("click", (e) => e.stopPropagation());
  const slider = brightnessSlider(lcd.brightness, (v) => patchConfigDebounced("brightness", { lcd: { brightness: v } }));
  slider.addEventListener("click", (e) => e.stopPropagation());
  card.append(el("div", { class: "device-row" + (S.lcdOpen ? " open" : ""), onclick: () => { S.lcdOpen = !S.lcdOpen; render(); } },
    el("div", { class: "icon", html: ICONS.lcd }),
    el("div", { class: "name" }, "LCD Display", el("small", {}, "LCD Display")),
    el("div", { class: "right" }, slider, modeSel)));
  if (S.lcdOpen) card.append(lcdPanel());
  root.append(card);
  return root;
}

function lcdPanel() {
  const lcd = cfg().lcd;
  const mode = lcd.mode;
  const panel = el("div", { class: "lcd-panel" });
  const settings = el("div", { class: "settings" });
  const builders = { image: lcdImageSettings, single: lcdSingleSettings, single_gif: lcdGifInfographicSettings, dual: lcdDualSettings, dual_gif: lcdGifInfographicSettings, triple: lcdTripleSettings, triple_gif: lcdGifInfographicSettings, clock: lcdClockSettings, carousel: lcdCarouselSettings, liquid: lcdLiquidSettings };
  (builders[mode] || lcdSingleSettings)(settings, mode);
  panel.append(settings);
  const preview = el("div", { class: "preview-col" },
    el("span", { class: "rotate", onclick: openRotation }, el("span", { html: ICONS.rotate }), "Rotate Display"),
    el("div", { class: "lcd-preview" }, el("img", { id: "lcd-preview-img", src: `/api/lcd/preview.jpg?v=${S.data.lcd.preview_id}` })),
    el("div", { class: "stream-info", id: "stream-info" }, streamInfoText()));
  panel.append(preview);
  return panel;
}
function streamInfoText() {
  const l = S.data.lcd;
  if (l.error) return `Error: ${l.error}`;
  if (!l.transport) return "";
  if (l.transport === "firmware") return "Rendered by the cooler firmware";
  if (l.transport === "device gif") return "GIF played by the cooler firmware";
  return `Streaming ${l.transport} · ${l.fps} fps · ${l.frames_sent} frames`;
}

function screenPatch(mode, patch) { return { lcd: { screens: { [mode]: patch } } }; }
function field(label, control) { return el("div", { class: "field" }, el("label", {}, label), control); }
function readingSelect(value, onChange) { return select(S.metrics.map((m) => ({ value: m.id, label: m.name })), value, onChange, "select wide"); }
function colorPair(labels, colors, onChange) {
  return el("div", { class: "row" }, ...labels.flatMap((l, i) => [el("span", { class: "lbl" }, l), swatch(colors[i], (v) => onChange(i, v))]));
}

function lcdLiquidSettings(root) {
  root.append(el("div", { class: "hint" }, "The cooler shows its built-in liquid temperature screen. This is what the Kraken displays when no software is running."));
}

function lcdSingleSettings(root, mode) {
  const sc = cfg().lcd.screens.single;
  root.append(field("Color 1", swatch(sc.color1, (v) => patchConfig(screenPatch("single", { color1: v })))));
  root.append(field("Color 2", swatch(sc.color2, (v) => patchConfig(screenPatch("single", { color2: v })))));
  root.append(field("Reading", swatch(sc.text_color, (v) => patchConfig(screenPatch("single", { text_color: v })))));
  root.append(el("div", { class: "field stack" }, el("label", {}, "Primary Reading"), readingSelect(sc.reading, (v) => patchConfig(screenPatch("single", { reading: v })))));
  root.append(field("Background", swatch(sc.background, (v) => patchConfig(screenPatch("single", { background: v })))));
  root.append(field("Logo", el("div", { class: "row" }, checkbox(sc.logo, (v) => patchConfig(screenPatch("single", { logo: v }))), swatch(sc.logo_color, (v) => patchConfig(screenPatch("single", { logo_color: v }))))));
}

function lcdDualSettings(root) {
  const sc = cfg().lcd.screens.dual;
  for (const i of [0, 1]) {
    root.append(el("div", { class: "field stack" }, el("label", {}, `Reading ${i + 1}`), readingSelect(sc.readings[i], (v) => { const r = sc.readings.slice(); r[i] = v; patchConfig(screenPatch("dual", { readings: r })); })));
    root.append(el("div", { class: "field" }, el("label", {}, ""), colorPair(["Number", "Text"], [sc.number_colors[i], sc.text_colors[i]], (j, v) => {
      const key = j === 0 ? "number_colors" : "text_colors"; const arr = sc[key].slice(); arr[i] = v; patchConfig(screenPatch("dual", { [key]: arr }));
    })));
    root.append(field("Visualization", swatch(sc.viz_colors[i], (v) => { const arr = sc.viz_colors.slice(); arr[i] = v; patchConfig(screenPatch("dual", { viz_colors: arr })); })));
  }
  root.append(field("Background", swatch(sc.background, (v) => patchConfig(screenPatch("dual", { background: v })))));
  root.append(field("Logo", el("div", { class: "row" }, checkbox(sc.logo, (v) => patchConfig(screenPatch("dual", { logo: v }))), swatch(sc.logo_color, (v) => patchConfig(screenPatch("dual", { logo_color: v }))))));
}

function lcdTripleSettings(root) {
  const sc = cfg().lcd.screens.triple;
  root.append(field("Background", swatch(sc.background, (v) => patchConfig(screenPatch("triple", { background: v })))));
  root.append(field("Logo", el("div", { class: "row" }, checkbox(sc.logo, (v) => patchConfig(screenPatch("triple", { logo: v }))), swatch(sc.logo_color, (v) => patchConfig(screenPatch("triple", { logo_color: v }))))));
  root.append(field("Visualization", colorPair(["", ""], sc.viz, (i, v) => { const arr = sc.viz.slice(); arr[i] = v; patchConfig(screenPatch("triple", { viz: arr })); })));
  root.append(el("div", { class: "field stack" }, el("label", {}, "Primary Reading"), readingSelect(sc.primary, (v) => patchConfig(screenPatch("triple", { primary: v })))));
  root.append(el("div", { class: "field" }, el("label", {}, ""), colorPair(["Number", "Text"], [sc.number_color, sc.text_color], (j, v) => patchConfig(screenPatch("triple", j === 0 ? { number_color: v } : { text_color: v })))));
  root.append(el("div", { class: "field stack" }, el("label", {}, "Secondary Readings")));
  for (const i of [0, 1]) {
    root.append(el("div", { class: "field stack" }, readingSelect(sc.secondary[i], (v) => { const arr = sc.secondary.slice(); arr[i] = v; patchConfig(screenPatch("triple", { secondary: arr })); })));
    root.append(el("div", { class: "field" }, el("label", {}, ""), colorPair(["Number", "Text"], [sc.secondary_number_colors[i], sc.secondary_text_colors[i]], (j, v) => {
      const key = j === 0 ? "secondary_number_colors" : "secondary_text_colors"; const arr = sc[key].slice(); arr[i] = v; patchConfig(screenPatch("triple", { [key]: arr }));
    })));
  }
}

function lcdClockSettings(root) {
  const sc = cfg().lcd.screens.clock;
  root.append(field("Dial", swatch(sc.dial_color, (v) => patchConfig(screenPatch("clock", { dial_color: v })))));
  root.append(field("Seconds", swatch(sc.seconds_color, (v) => patchConfig(screenPatch("clock", { seconds_color: v })))));
  const faces = el("div", { class: "clockfaces" });
  for (const face of ["analog", "digital"]) {
    const spec = Object.assign({}, sc, { mode: "clock", face });
    faces.append(el("div", { class: "clockface" + (sc.face === face ? " active" : ""), onclick: () => patchConfig(screenPatch("clock", { face }), true) },
      el("img", { src: `/api/lcd/render?size=70&spec=${encodeURIComponent(JSON.stringify(spec))}` })));
  }
  root.append(el("div", { class: "field stack" }, el("label", {}, "Clockfaces"), faces));
  root.append(field("24-hour", checkbox(sc.hour24, (v) => patchConfig(screenPatch("clock", { hour24: v })))));
  root.append(field("Background", swatch(sc.background, (v) => patchConfig(screenPatch("clock", { background: v })))));
}

function uploadControls(root, mode) {
  const sc = cfg().lcd.screens[mode];
  const fileInput = el("input", { type: "file", accept: ".gif,.png,.jpg,.jpeg,.webp,.bmp", style: "display:none" });
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    toast(`Uploading ${file.name}…`);
    try {
      const resp = await fetch("/api/lcd/upload", { method: "POST", headers: { "X-File-Name": encodeURIComponent(file.name) }, body: file });
      const json = await resp.json();
      if (!resp.ok) throw new Error(json.error || resp.statusText);
      S.library = json.library;
      await patchConfig(screenPatch(mode, { path: json.path }), true);
      toast("Image set");
    } catch (e) { toast(`Upload failed: ${e.message}`, true); }
  });
  root.append(el("div", { class: "row" },
    el("button", { class: "btn", onclick: () => fileInput.click() }, "Upload"),
    el("button", { class: "btn", disabled: true, title: "GIF search services are not available in this app" }, "Browse GIFs"), fileInput));
  root.append(el("div", { class: "hint" }, "Supports GIF, PNG, and JPG. Files are fitted to the 640×640 circular screen. Large GIFs are streamed frame by frame."));
  if (mode === "image") {
    root.append(checkbox(cfg().lcd.stream_gif, (v) => patchConfig({ lcd: { stream_gif: v } }), "Stream from app. Recommended for high frame rate GIFs. Additional system resources used."));
  }
  const thumbs = el("div", { class: "thumbs" });
  for (const item of S.library) {
    thumbs.append(el("div", { class: "thumb" + (sc.path === item.path ? " active" : ""), title: item.name, onclick: () => patchConfig(screenPatch(mode, { path: item.path }), true) },
      el("img", { src: `/api/lcd/thumb?path=${encodeURIComponent(item.path)}` }),
      el("div", { class: "del", title: "Delete", onclick: async (e) => { e.stopPropagation(); S.library = await api(`/api/lcd/library?path=${encodeURIComponent(item.path)}`, "DELETE"); render(); } }, "✕")));
  }
  root.append(el("div", { class: "field stack" }, el("label", {}, "Previous Images ", el("a", { onclick: refreshLibrary }, "Refresh")), thumbs));
}
async function refreshLibrary() { S.library = await api("/api/lcd/library"); render(); }

function lcdImageSettings(root, mode) { uploadControls(root, mode); }

function lcdGifInfographicSettings(root, mode) {
  uploadControls(root, mode);
  const sc = cfg().lcd.screens[mode];
  if (mode === "single_gif") {
    root.append(el("div", { class: "field stack" }, el("label", {}, "Reading"), readingSelect(sc.reading, (v) => patchConfig(screenPatch(mode, { reading: v })))));
  } else if (mode === "dual_gif") {
    for (const i of [0, 1]) root.append(el("div", { class: "field stack" }, el("label", {}, `Reading ${i + 1}`), readingSelect(sc.readings[i], (v) => { const r = sc.readings.slice(); r[i] = v; patchConfig(screenPatch(mode, { readings: r })); })));
  } else {
    root.append(el("div", { class: "field stack" }, el("label", {}, "Primary Reading"), readingSelect(sc.primary, (v) => patchConfig(screenPatch(mode, { primary: v })))));
    for (const i of [0, 1]) root.append(el("div", { class: "field stack" }, el("label", {}, `Secondary Reading ${i + 1}`), readingSelect(sc.secondary[i], (v) => { const r = sc.secondary.slice(); r[i] = v; patchConfig(screenPatch(mode, { secondary: r })); })));
  }
  if (mode === "dual_gif") {
    // CAM gives each reading of "Dual Infographic + GIF" its own Number / Text colours
    const numbers = sc.number_colors || [sc.number_color || "#FFFFFF", sc.number_color || "#FFFFFF"];
    const texts = sc.text_colors || [sc.text_color || "#FFFFFF", sc.text_color || "#FFFFFF"];
    for (const i of [0, 1]) {
      root.append(el("div", { class: "field" }, el("label", {}, `Reading ${i + 1}`), colorPair(["Number", "Text"], [numbers[i], texts[i]], (j, v) => {
        const key = j === 0 ? "number_colors" : "text_colors"; const arr = (j === 0 ? numbers : texts).slice(); arr[i] = v; patchConfig(screenPatch(mode, { [key]: arr }));
      })));
    }
  } else {
    root.append(el("div", { class: "field" }, el("label", {}, ""), colorPair(["Number", "Text"], [sc.number_color, sc.text_color], (j, v) => patchConfig(screenPatch(mode, j === 0 ? { number_color: v } : { text_color: v })))));
  }
}

function lcdCarouselSettings(root) {
  const car = cfg().lcd.carousel;
  root.append(el("div", { class: "row", style: "justify-content:flex-end" },
    select([5, 10, 15, 30, 60].map((s) => ({ value: s, label: `Duration: ${s}s` })), car.interval, (v) => patchConfig({ lcd: { carousel: { interval: parseInt(v, 10) } } }))));
  const tiles = el("div", { class: "carousel-tiles" });
  car.items.forEach((item, i) => {
    tiles.append(el("div", { class: "carousel-tile", title: MODE_LABELS[item.mode] || item.mode },
      el("img", { src: `/api/lcd/render?size=100&spec=${encodeURIComponent(JSON.stringify(item))}` }),
      el("div", { class: "del", onclick: () => { const items = car.items.filter((_, j) => j !== i); patchConfig({ lcd: { carousel: { items } } }, true); } }, "Remove")));
  });
  if (car.items.length < 5) {
    tiles.append(el("div", { class: "carousel-tile add", onclick: () => {
      const choices = ["single", "dual", "triple", "clock", "image", "single_gif", "dual_gif", "triple_gif"];
      openModal("Add to Carousel", [el("div", { class: "mode-chooser" }, ...choices.map((m) => el("button", { class: "btn", onclick: () => {
        const spec = Object.assign({ mode: m }, cfg().lcd.screens[m]);
        patchConfig({ lcd: { carousel: { items: car.items.concat([spec]) } } }, true); closeModal();
      } }, MODE_LABELS[m])))], [el("button", { class: "btn", onclick: closeModal }, "Cancel")]);
    } }, "+"));
  }
  root.append(tiles);
  root.append(el("div", { class: "hint", style: "text-align:center;margin-top:12px" }, "Each screen uses the settings of its display mode at the moment it is added."));
}

// ----------------------------------------------------------------------------- rotation modal
function openRotation() {
  let angle = cfg().lcd.orientation || 0;
  const dial = el("div", { class: "dial" });
  const caption = el("div", { class: "caption" }, "Match the tubes to your Kraken installation.");
  const draw = () => {
    const dots = [];
    for (let i = 0; i < 12; i++) {
      const a = (i * 30 - 90) * Math.PI / 180, r = 150, x = 165 + r * Math.cos(a), y = 165 + r * Math.sin(a);
      const sel = ((i * 30) % 360) === ((angle % 360) + 360) % 360;
      dots.push(`<circle cx="${x}" cy="${y}" r="${sel ? 8 : 4}" fill="${sel ? "#fff" : "#8d8d93"}" data-a="${i * 30}" style="cursor:pointer"/>`);
    }
    dial.innerHTML = `<svg viewBox="0 0 330 330"><circle cx="165" cy="165" r="150" fill="none" stroke="#5a5962" stroke-width="1"/>
      <g transform="rotate(${angle} 165 165)">
        <circle cx="165" cy="165" r="105" fill="#2a2a2e" stroke="#444" stroke-width="6"/>
        <circle cx="165" cy="165" r="85" fill="#000"/>
        <rect x="135" y="262" width="22" height="30" rx="6" fill="#333"/><rect x="173" y="262" width="22" height="30" rx="6" fill="#333"/>
      </g>${dots.join("")}</svg>`;
    dial.append(caption);
    dial.querySelectorAll("circle[data-a]").forEach((d) => d.addEventListener("click", () => { angle = parseInt(d.dataset.a, 10); draw(); }));
  };
  draw();
  openModal("Kraken Screen Rotation", [el("div", { class: "rotation" },
    el("div", { class: "arrow", onclick: () => { angle = (angle + 330) % 360; draw(); } }, "↺"), dial,
    el("div", { class: "arrow", onclick: () => { angle = (angle + 30) % 360; draw(); } }, "↻"))], [
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: () => { patchConfig({ lcd: { orientation: angle } }); closeModal(); } }, "Save"),
  ]);
}

// ----------------------------------------------------------------------------- Settings
function settingsRow(title, desc, control) {
  return el("div", { class: "settings-row" }, el("div", {}, el("div", {}, title), desc ? el("div", { class: "desc" }, desc) : null), control);
}
function renderSettings() {
  const g = cfg().general, lcd = cfg().lcd, d = S.data.device;
  const root = el("div", {});
  root.append(el("div", { class: "page-header" }, el("h1", {}, "Settings")));
  root.append(el("div", { class: "settings-section" }, el("h3", {}, "General"),
    settingsRow("Temperature Display", "Unit used across the app and on the LCD", select([{ value: "C", label: "Celsius (°C)" }, { value: "F", label: "Fahrenheit (°F)" }], g.temperature_unit, (v) => patchConfig({ general: { temperature_unit: v } }, true), "select small")),
    settingsRow("Enable Dark Mode", "", checkbox(g.dark_mode !== false, (v) => patchConfig({ general: { dark_mode: v } }, true))),
    settingsRow("Launch at login", "Runs the background service as a systemd user unit so curves and the LCD are restored after reboot", autostartToggle())));
  root.append(el("div", { class: "settings-section" }, el("h3", {}, "LCD streaming"),
    settingsRow("Frame rate", "Upper limit for streamed content (GIFs, clock). Infographics update once per second.", select([5, 10, 15, 20, 30].map((f) => ({ value: f, label: `${f} fps` })), lcd.fps, (v) => patchConfig({ lcd: { fps: parseInt(v, 10) } }), "select small")),
    settingsRow("Transport", "q565 is the compressed fast path (smallest USB traffic); BGR888 sends raw frames like CAM", select([{ value: "auto", label: "Auto (q565)" }, { value: "q565", label: "q565 compressed" }, { value: "bgr888", label: "Raw BGR888" }], lcd.transport, (v) => patchConfig({ lcd: { transport: v } }), "select small")),
    settingsRow("Native encoder", S.data.lcd.native_encoder ? "libkraken_q565.so loaded" : "Not found: using the slower pure Python fallback. Build it with: cd native/q565 && cargo build --release", el("span", {}, S.data.lcd.native_encoder ? "Active" : "Fallback"))));
  root.append(el("div", { class: "settings-section" }, el("h3", {}, "Device"),
    settingsRow("Cooler", d.connected ? `${d.info.name} · firmware ${d.info.firmware} · serial ${d.info.serial} · USB id 1e71:${d.info.product_id}` : (d.error || "not connected"), el("button", { class: "btn", onclick: reconnect }, "Reconnect")),
    settingsRow("Kernel driver", d.kernel_driver ? "nzxt_kraken3 is bound: sensor readings come from hwmon without USB traffic" : "nzxt_kraken3 not loaded: readings are requested over USB", el("span", {}, d.kernel_driver ? "Active" : "Absent")),
    settingsRow("USB access", "One-time udev rule for hidraw and bulk access without root", el("code", {}, "sudo sh packaging/install-udev.sh"))));
  root.append(el("div", { class: "settings-section" }, el("h3", {}, "Reset"),
    settingsRow("Cooling", "Silent presets for pump and fan, liquid temperature source", el("button", { class: "btn", onclick: () => api("/api/config/reset", "POST", { section: "cooling" }).then(() => toast("Cooling reset")) }, "Reset to default")),
    settingsRow("LCD", "Single infographic with liquid temperature, default colors", el("button", { class: "btn", onclick: () => api("/api/config/reset", "POST", { section: "lcd" }).then(() => toast("LCD reset")) }, "Reset to default"))));
  root.append(el("div", { class: "settings-section" }, el("h3", {}, "About"),
    settingsRow(`Kraken Control ${S.data.version}`, "Open-source control for the NZXT Kraken Elite on Linux. Protocol knowledge from liquidctl, the nzxt-kraken3 kernel driver, AIOLCDUnchained, kraken-elite-screen-manager and nzxt-ctl. Not affiliated with NZXT.", el("span"))));
  return root;
}
function autostartToggle() {
  const node = toggle(!!S.autostart, async (on) => {
    try { const r = await api("/api/autostart", "POST", { enabled: on }); S.autostart = r.enabled; if (r.error) toast(r.error, true); else toast(on ? "Service enabled for next login" : "Service disabled"); }
    catch (e) { toast(e.message, true); }
  });
  if (S.autostart === null) api("/api/autostart").then((r) => { S.autostart = r.enabled; node.classList.toggle("on", !!r.enabled); });
  return node;
}

// ----------------------------------------------------------------------------- live updates (no full re-render)
function updateLive() {
  if (!S.data) return;
  renderTitlebar();
  const s = S.data.sensors;
  const set = (id, text) => { const n = document.getElementById(id); if (n) n.textContent = text; };
  if (S.page === "cooling") {
    const sig = JSON.stringify(["pump", "fan"].map((ch) => [S.data.cooling[ch].mode, S.data.cooling[ch].sensor, S.data.cooling[ch].points]));
    if (S.coolingSig !== undefined && sig !== S.coolingSig && !S.dragging) { S.coolingSig = sig; render(); return; }
    S.coolingSig = sig;
    set("tile-liquid-value", fmtTemp(s.cooler.liquid_temperature)); set("tile-cpu-value", fmtTemp(s.cpu.temperature)); set("tile-gpu-value", fmtTemp(s.gpu.temperature));
    set("pump-rpm", s.cooler.pump_rpm ? `${s.cooler.pump_rpm} RPM` : "-- RPM"); set("fan-rpm", s.cooler.fan_rpm ? `${s.cooler.fan_rpm} RPM` : "-- RPM");
    for (const ch of ["pump", "fan"]) {
      const m = document.getElementById(`${ch}-marker`), l = document.getElementById(`${ch}-marker-label`);
      if (m && l) positionMarker(m, l, cfg().cooling[ch].sensor, valueOfSensor(cfg().cooling[ch].sensor));
    }
  } else if (S.page === "lighting") {
    const img = document.getElementById("lcd-preview-img");
    if (img && S.data.lcd.preview_id !== S.previewId) { S.previewId = S.data.lcd.preview_id; img.src = `/api/lcd/preview.jpg?v=${S.previewId}`; }
    set("stream-info", streamInfoText());
  } else if (S.page === "monitoring") {
    render();
  }
  const bannerNeeded = !S.data.device.connected;
  const hasBanner = !!$("#content .banner");
  if (bannerNeeded !== hasBanner) render();
}

// ----------------------------------------------------------------------------- SSE
function connectEvents() {
  const es = new EventSource("/api/events");
  es.addEventListener("state", (evt) => {
    const first = !S.data;
    S.data = JSON.parse(evt.data);
    if (first) render(); else updateLive();
  });
  es.onerror = () => { $("#conn-text").textContent = "service unreachable"; $("#conn-dot").className = "dot"; };
}

window.addEventListener("error", (e) => { const n = $("#conn-text"); if (n) n.textContent = `JS error: ${e.message} (${(e.filename || "").split("/").pop()}:${e.lineno})`; });
window.addEventListener("unhandledrejection", (e) => { const n = $("#conn-text"); if (n) n.textContent = `JS error: ${e.reason && e.reason.message ? e.reason.message : e.reason}`; });

async function init() {
  try { S.page = localStorage.getItem("kc.page") || "monitoring"; } catch (e) { /* ignore */ }
  // Deep link: #cooling, #cooling/pump, #lighting
  const hash = location.hash.replace(/^#/, "");
  if (hash) { const [page, sub] = hash.split("/"); S.page = page; if (page === "cooling" && sub) S.openRow = sub; }
  if (!["monitoring", "specs", "lighting", "cooling", "settings"].includes(S.page)) S.page = "monitoring";
  S.metrics = await api("/api/metrics").catch(() => []);
  S.library = await api("/api/lcd/library").catch(() => []);
  connectEvents();
  setInterval(async () => { if (S.page === "monitoring") { S.processes = await api("/api/system/processes").catch(() => S.processes); } }, 3000);
  S.processes = await api("/api/system/processes").catch(() => []);
}
init();

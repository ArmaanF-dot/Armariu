"use strict";
/* Armariu review app. Plain JS, no build step, no outside scripts. Everything shown comes from
   /api/*; all text from filings and agents is escaped before display, never inserted as raw HTML. */

const TOKEN = document.querySelector('meta[name="radar-token"]').content;
const main = document.getElementById("main");
const S = {
  view: null,
  version: null,
  decisions: null,
  decTab: "lenders",
  current: null,          // lender name being decided
  picks: {},              // name -> {category, parent}
  chargesQuery: { q: "", tier: "", check: "", from: "", to: "", status: "", category: "" },
  chartWeekly: false,
  lendersQuery: { q: "", category: "", status: "", sector: "" },
  sigF: { theme: "", type: "", sector: "", lender: "", period: "", pc: false, q: "", sort: "new" },
  hideProperty: false,
  reportsTab: "drafts",
  reportSel: null,
  editing: null,          // draft path being edited
  choices: [],
};

// ------------------------------------------------------------------ utilities
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
const fmtDate = (d) => {
  if (!d) return "";
  const dt = new Date(d.length === 10 ? d + "T12:00:00" : d);
  return isNaN(dt) ? esc(d) : dt.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
};
const fmtTime = (d) => {
  if (!d) return "never";
  const dt = new Date(d), mins = Math.round((Date.now() - dt) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return dt.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
};
// Small Markdown renderer for agent-written reports. It escapes everything first, then adds a
// fixed set of tags, so nothing an agent (or a filing) writes can inject HTML or scripts.
function inline(t) {
  return t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}
function md(text) {
  const src = esc(String(text || "").replace(/\r\n/g, "\n"))
    .replace(/&lt;!--([\s\S]*?)--&gt;/g, (_, c) => "\n:::note\n" + c.trim() + "\n:::\n");
  const lines = src.split("\n");
  const out = [];
  let i = 0;
  const para = [];
  const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para.length = 0; } };
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flush(); const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      out.push(`<pre><code>${buf.join("\n")}</code></pre>`); i++; continue;
    }
    if (line.trim() === ":::note") {
      flush(); const buf = []; i++;
      while (i < lines.length && lines[i].trim() !== ":::") buf.push(lines[i++]);
      out.push(`<div class="md-note">${md(buf.join("\n").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'"))}</div>`);
      i++; continue;
    }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flush(); const n = Math.min(m[1].length + 1, 4); out.push(`<h${n}>${inline(m[2])}</h${n}>`); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush(); const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r, k) => !(k === 1 && /^[\s|:-]+$/.test(r)));
      const [head, ...rest] = body;
      out.push(`<div class="table-wrap"><table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rest.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      flush(); const ordered = /^\s*\d+\./.test(line); const items = [];
      while (i < lines.length && (/^\s*([-*+]|\d+\.)\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (/^\s*([-*+]|\d+\.)\s+/.test(lines[i])) items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
        else items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    if (/^&gt;\s?/.test(line)) {
      flush(); const buf = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) buf.push(lines[i++].replace(/^&gt;\s?/, ""));
      out.push(`<blockquote>${inline(buf.join(" "))}</blockquote>`); continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }
    if (!line.trim()) { flush(); i++; continue; }
    para.push(line.trim()); i++;
  }
  flush();
  return out.join("\n");
}
async function get(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Radar-Token": TOKEN }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
let toastTimer;
function toast(msg, error = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), error ? 6000 : 2600);
}
const labelFor = (key) => (S.choices.find((c) => c.key === key) || {}).label || (CAT_LABELS[key] || (key === "unknown" ? "Can't tell" : key || ""));
const CAT_LABELS = { direct_lender: "Direct lender", bank: "Bank", security_agent: "Security agent", abl_specialist: "Asset-based lender",
  asset_finance: "Asset finance", property_lender: "Property lender", public_sector: "Public sector", individual: "Individual", other: "Other", unknown: "Can't tell" };
const TIER_LABELS = { A: "Direct lender", B: "Behind a security agent", C: "Specialist or property lender", D: "Needs review", E: "Bank or individual" };
const tierBadge = (t) => `<span class="tier ${esc(t)}" title="Tier ${esc(t)}: ${esc(TIER_LABELS[t] || "")}">${esc(t)}</span>`;
// Rules record their reason as "rule:<type>:<pattern>"; show it in words.
const reasonText = (r, source) => {
  const m = /^rule:(exact|contains|regex):(.*)$/.exec(r || "");
  if (!m) return r || "";
  const who = source === "human" ? "your rule" : "the starter rule";
  return m[1] === "exact" ? `Matched ${who} for this exact name.` : `Matched ${who} for names containing ${m[2]}.`;
};
const CATEGORY_KEYS = Object.keys(CAT_LABELS);
const catKey = (k) => (k && CATEGORY_KEYS.includes(k) ? k : "unknown");
const catChip = (k, label) => `<span class="cat" data-cat="${catKey(k)}">${esc(label || labelFor(k) || "Can't tell")}</span>`;
const sic = (codes) => (codes && codes.length ? codes.map(esc).join(", ") : "");
// A borrower whose profile hasn't been fetched yet: show its number, which still opens its panel.
const coName = (name, number) => name ? esc(name) : `<span class="pending-name" title="The name arrives with the next cloud run">Company ${esc(number)}</span>`;
const isoDate = (d) => (d ? esc(String(d).slice(0, 10)) : '<span class="muted">—</span>');
const STATUS_WORDS = { outstanding: "Outstanding", "fully-satisfied": "Repaid", "part-satisfied": "Part repaid", satisfied: "Repaid" };
const statusPill = (s) => `<span class="pill" data-cat="${s === "outstanding" ? "unknown" : "bank"}">${esc(STATUS_WORDS[s] || s || "—")}</span>`;
const shortDate = (d) => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------------ icons (one outline set)
const ICONS = {
  overview: '<path d="M4 13h6V4H4zM14 20h6v-9h-6zM14 4h6v4h-6zM4 20h6v-3H4z"/>',
  decisions: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>',
  charges: '<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  lenders: '<path d="M3 21h18M5 21V10M19 21V10M9 21v-7M15 21v-7M2 10l10-6 10 6"/>',
  lenderview: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  scorecard: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8"/>',
  watch: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  pack: '<path d="M4 7l8-4 8 4v10l-8 4-8-4z"/><path d="M4 7l8 4 8-4M12 11v10"/>',
  reports: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  status: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  cloud: '<path d="M7 18a4 4 0 0 1-.6-8A6 6 0 0 1 18 9a4.5 4.5 0 0 1-.5 9z"/>',
  agent: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16h6"/>',
  you: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  coins: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  inbox: '<path d="M4 13l2.5-8h11L20 13v6H4z"/><path d="M4 13h5l1 2h4l1-2h5"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
  check: '<path d="M5 12.5 10 17l9-10"/>',
  alert: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4M12 17h.01"/>',
  signals: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
  refi: '<path d="M4 9a8 8 0 0 1 14-3l2 2M20 15a8 8 0 0 1-14 3l-2-2"/><path d="M20 4v4h-4M4 20v-4h4"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  plus: '<circle cx="12" cy="12" r="8.5"/><path d="M12 8v8M8 12h8"/>',
  star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.9z"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  note: '<path d="M5 4h10l4 4v12H5z"/><path d="M9 12h6M9 16h4"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

// ------------------------------------------------------------------ navigation and routing
const NAV = [
  ["overview", "Overview", null], ["signals", "Signals", "count"], ["decisions", "Decisions", "count"], ["reports", "Reports and drafts", null],
  ["|Register"], ["charges", "Charges", null], ["lenders", "Lender directory", null],
  ["|Additions"], ["lenderview", "Lender view", null], ["scorecard", "Scorecard", null], ["watch", "Refinance watch", null], ["pack", "Weekly pack", null],
  ["|Radar"], ["status", "Status", null],
];
document.getElementById("nav").innerHTML = NAV.map(([k, label, extra]) => k.startsWith("|")
  ? `<li class="group">${k.slice(1)}</li>`
  : `<li><a href="#/${k}" data-view="${k}">${icon(k)}<span>${label}</span>${extra ? `<span class="count" id="nav-${k}"></span>` : ""}</a></li>`).join("");

const VIEWS = { overview: renderOverview, signals: renderSignals, decisions: renderDecisions, charges: renderCharges, lenders: renderLenders, reports: renderReports, status: renderStatus,
  lenderview: renderLenderView, scorecard: renderScorecard, watch: renderWatch, pack: renderPack };
const VIEW_TITLES = { overview: "Overview", signals: "Signals", decisions: "Decisions", charges: "Charges", lenders: "Lender directory", reports: "Reports and drafts", status: "Status",
  lenderview: "Lender view", scorecard: "Scorecard", watch: "Refinance watch", pack: "Weekly pack" };
let routing = Promise.resolve();
function route() {
  const name = (location.hash.replace(/^#\//, "").split("?")[0]) || "overview";
  const next = VIEWS[name] ? name : "overview";
  const changed = next !== S.view;
  S.view = next;
  document.querySelectorAll("[data-view]").forEach((a) => a.classList.toggle("active", a.dataset.view === S.view));
  document.getElementById("crumb-title").textContent = VIEW_TITLES[S.view];
  document.title = `${VIEW_TITLES[S.view]} · Armariu review`;
  setMenu(false);
  closeDrawer();
  routing = routing.then(async () => {
    if (changed && main.innerHTML && !reduced()) { main.classList.add("leave"); await new Promise((r) => setTimeout(r, 130)); }
    main.classList.remove("leave");
    await refresh(true);
    if (changed) animateIn(main);
    window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
  });
}
// Cards rise in one after another when a section opens; live refreshes don't animate.
function countUp(el) {
  const text = el.textContent.trim(), m = /^([\d,]+(?:\.\d+)?)(.*)$/.exec(text);
  if (!m || reduced()) return;
  const to = Number(m[1].replace(/,/g, "")), dec = (m[1].split(".")[1] || "").length, t0 = performance.now();
  const step = (t) => { const k = Math.min(1, (t - t0) / 1100), e = 1 - Math.pow(1 - k, 4);
    el.textContent = (to * e).toLocaleString("en-GB", { minimumFractionDigits: dec, maximumFractionDigits: dec }) + m[2]; if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
function animateIn(el) {
  el.querySelectorAll(".sv.num, .fig .v.num, .mini .v.num, .count-up").forEach(countUp);
  const target = el.querySelector(".anim-root") || el;
  target.classList.remove("enter-stagger"); void target.offsetWidth; target.classList.add("enter-stagger");
  setTimeout(() => target.classList.remove("enter-stagger"), 900);
}
function fadeIn(el) { el.classList.remove("fade"); void el.offsetWidth; el.classList.add("fade"); }
async function refresh(focus = false) {
  try {
    await VIEWS[S.view]();
    if (focus) main.focus({ preventScroll: true });
  } catch (e) {
    main.innerHTML = `<div class="card"><div class="notice bad">${icon("alert")}<div>Couldn't load this page: ${esc(e.message)}. Check that the window running the app is still open, then reload.</div></div></div>`;
  }
}
function setSub(text) { document.getElementById("crumb-sub").textContent = text || ""; }
function setMenu(open) {
  document.body.classList.toggle("menu-open", open);
  document.getElementById("scrim").classList.toggle("on", open || document.getElementById("drawer").classList.contains("open"));
  document.getElementById("menu-open").setAttribute("aria-expanded", String(open));
}
document.getElementById("menu-open").onclick = () => setMenu(true);
document.getElementById("menu-close").onclick = () => setMenu(false);
document.getElementById("scrim").onclick = () => { setMenu(false); closeDrawer(); };
document.getElementById("search").onsubmit = (e) => {
  e.preventDefault();
  S.chargesQuery = { q: document.getElementById("search-q").value.trim(), tier: "", check: "", from: "", to: "", status: "", category: "", sector: "" };
  if (S.view === "charges") refresh(); else location.hash = "#/charges";
};
document.getElementById("refresh").onclick = async (e) => {
  const b = e.currentTarget;
  b.classList.add("spin");
  try { await post("/api/sync", {}); toast("Checking for updates"); } catch (err) { toast(err.message, true); }
  setTimeout(() => b.classList.remove("spin"), 2500);
};
const goCharges = (patch) => {
  S.chargesQuery = { q: "", tier: "", check: "", from: "", to: "", status: "", category: "", sector: "", ...patch };
  if (S.view === "charges") refresh(); else location.hash = "#/charges";
};

// ------------------------------------------------------------------ colour theme and saved preferences
// Saved in this browser only (a per-viewer convenience); the app works the same without it.
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private window */ } },
};
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
function currentTheme() { return document.documentElement.dataset.theme || "system"; }
function isDark() { const t = currentTheme(); return t === "dark" || (t === "system" && darkQuery.matches); }
function setTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  store.set("radar-theme", t === "system" ? null : t);
  document.querySelectorAll("[data-theme-pick]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.themePick === t));
  const tg = document.getElementById("theme-toggle");
  tg.innerHTML = `<svg viewBox="0 0 24 24">${isDark() ? '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>' : '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>'}</svg>`;
  tg.title = isDark() ? "Switch to light mode" : "Switch to dark mode";
  tg.setAttribute("aria-label", tg.title);
}
document.querySelectorAll("[data-theme-pick]").forEach((b) => (b.onclick = () => setTheme(b.dataset.themePick)));
document.getElementById("theme-toggle").onclick = () => setTheme(isDark() ? "light" : "dark");
darkQuery.addEventListener("change", () => setTheme(currentTheme()));
setTheme(currentTheme());
function setHideProperty(on) { S.hideProperty = !!on; store.set("radar-hide-property", on ? "1" : null); }
S.hideProperty = store.get("radar-hide-property") === "1";

// ------------------------------------------------------------------ live updates
async function poll() {
  try {
    const v = await get("/api/version");
    S.dataKey = v.version;
    renderSync(v.sync, v.git);
    const key = v.version + "|" + (v.sync && v.sync.at) + "|" + (v.git && v.git.state);
    if (S.version && key !== S.version && !S.editing && !isTyping() && !document.getElementById("drawer").classList.contains("open")) await refresh();
    S.version = key;
  } catch {
    renderSync({ state: "error", message: "The app has stopped. Start it again to keep reviewing." });
  }
}
const isTyping = () => { const a = document.activeElement; return a && (a.tagName === "TEXTAREA" || (a.tagName === "INPUT" && a.type !== "checkbox")); };
function renderSync(sync, git) {
  const el = document.getElementById("sync");
  let cls = "", text = "Not synced yet";
  if (git && git.state === "error") { cls = "error"; text = git.message; }
  else if (git && git.state === "saving") { cls = "busy"; text = "Saving your decision to GitHub"; }
  else if (sync && sync.state === "syncing") { cls = "busy"; text = "Checking for new filings"; }
  else if (sync && sync.state === "error") { cls = "error"; text = sync.message; }
  else if (sync && sync.state === "ok") { cls = "ok"; text = `Up to date, checked ${fmtTime(sync.at)}` + (sync.mode === "cloud" ? " from the cloud" : ""); }
  el.className = "sync " + cls;
  el.querySelector(".sync-text").textContent = text;
}

// ------------------------------------------------------------------ tooltip
const tip = document.getElementById("tip");
function showTip(html, x, y) {
  tip.innerHTML = html; tip.classList.add("on");
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(window.innerWidth - r.width - 8, Math.max(8, x - r.width / 2)) + "px";
  tip.style.top = Math.max(8, y - r.height - 14) + "px";
}
const hideTip = () => tip.classList.remove("on");

// ------------------------------------------------------------------ charts
function niceMax(v) {
  if (v <= 4) return 4;
  const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}
// Monotone cubic curve (Fritsch-Carlson): smooth, but never overshoots the data or dips below zero.
function monotonePath(pts) {
  const n = pts.length;
  if (n < 2) return "";
  const dx = [], m = [], t = [];
  for (let i = 0; i < n - 1; i++) { dx.push(pts[i + 1][0] - pts[i][0]); m.push((pts[i + 1][1] - pts[i][1]) / dx[i]); }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], h = a * a + b * b;
    if (h > 9) { const k = 3 / Math.sqrt(h); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += ` C${pts[i][0] + h},${pts[i][1] + t[i] * h} ${pts[i + 1][0] - h},${pts[i + 1][1] - t[i + 1] * h} ${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}
const SERIES = [
  { key: "all", label: "All charges", color: "var(--brand)", get: (p) => p.A + p.B + p.C + p.D + p.E },
  { key: "pc", label: "Direct lenders and security agents", color: "var(--k-agent)", get: (p) => p.A + p.B },
];
function areaChart(el, points, { weekly, coverageStart }) {
  const W = Math.max(320, el.clientWidth), H = W < 400 ? 230 : 290, L = 34, R = 10, T = 12, B = 28;
  const max = niceMax(Math.max(1, ...points.map(SERIES[0].get)));
  const x = (i) => L + (i * (W - L - R)) / Math.max(1, points.length - 1);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const ticks = [0, .25, .5, .75, 1].map((f) => Math.round(f * max * 10) / 10);
  const every = Math.ceil(points.length / (W < 560 ? 4 : 7));
  const labels = points.map((p, i) => (i % every === 0 || i === points.length - 1) && (i === points.length - 1 || points.length - 1 - i >= Math.max(2, every * 0.8))
    ? `<text class="axis-t" x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}">${weekly ? "w/c " : ""}${shortDate(p.date)}</text>` : "").join("");
  let shade = "";
  const startIdx = coverageStart ? points.findIndex((p) => (weekly ? addDays(p.date, 6) : p.date) >= coverageStart) : 0;
  if (startIdx > 0) {
    const xe = x(startIdx) - (x(1) - x(0)) / 2;
    shade = `<rect class="nowatch" x="${L}" y="${T}" width="${Math.max(0, xe - L)}" height="${H - T - B}" rx="8"/>
      ${xe - L > 120 ? `<text class="nowatch-t" x="${(L + xe) / 2}" y="${T + 20}" text-anchor="middle">Partly covered</text>` : ""}`;
  }
  const uid = "g" + Math.random().toString(36).slice(2, 8);
  const series = SERIES.map((s, si) => {
    const pts = points.map((p, i) => [x(i), y(s.get(p))]);
    const line = monotonePath(pts);
    return `<defs><linearGradient id="${uid}${si}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity="${si ? .28 : .3}"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs>
      <path class="area" d="${line} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z" fill="url(#${uid}${si})"/>
      <path class="line" d="${line}" fill="none" stroke="${s.color}" stroke-width="${si ? 2.5 : 3}"/>`;
  }).join("");
  el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Charges delivered per ${weekly ? "week" : "day"}">
    ${shade}
    ${ticks.map((t) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text class="axis-t" x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join("")}
    ${series}${labels}
    <line class="hover-line" id="${uid}h" y1="${T}" y2="${H - B}" x1="-10" x2="-10" visibility="hidden"/>
    ${SERIES.map((s, si) => `<circle id="${uid}d${si}" r="5" cx="-10" cy="-10" visibility="hidden" fill="${s.color}" stroke="var(--card)" stroke-width="2.5"/>`).join("")}
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" style="cursor:pointer" id="${uid}o"/>
  </svg>`;
  el.querySelectorAll(".line").forEach((p) => p.style.setProperty("--len", Math.ceil(p.getTotalLength())));
  const over = el.querySelector(`#${uid}o`), hl = el.querySelector(`#${uid}h`);
  const idxAt = (ev) => { const r = over.getBoundingClientRect(); return Math.max(0, Math.min(points.length - 1, Math.round(((ev.clientX - r.left) / r.width) * (points.length - 1)))); };
  over.onmousemove = (ev) => {
    const i = idxAt(ev), p = points[i];
    hl.setAttribute("x1", x(i)); hl.setAttribute("x2", x(i)); hl.setAttribute("visibility", "visible");
    SERIES.forEach((s, si) => { const d = el.querySelector(`#${uid}d${si}`); d.setAttribute("cx", x(i)); d.setAttribute("cy", y(s.get(p))); d.setAttribute("visibility", "visible"); });
    const range = weekly ? `Week of ${fmtDate(p.date)}` : fmtDate(p.date);
    const pre = coverageStart && (weekly ? addDays(p.date, 6) : p.date) < coverageStart;
    showTip(`<b>${range}</b><br>${SERIES.map((s) => `${s.label}: <b>${s.get(p)}</b>`).join("<br>")}<br>Tier A ${p.A} · B ${p.B} · C ${p.C} · D ${p.D} · E ${p.E}${pre ? "<br><i>Before the radar started: only partly captured</i>" : ""}<br><span style="opacity:.7">Click to list them</span>`, ev.clientX, ev.clientY);
  };
  over.onmouseleave = () => { hideTip(); hl.setAttribute("visibility", "hidden"); SERIES.forEach((s, si) => el.querySelector(`#${uid}d${si}`).setAttribute("visibility", "hidden")); };
  over.onclick = (ev) => { const p = points[idxAt(ev)]; hideTip(); goCharges({ from: p.date, to: weekly ? addDays(p.date, 6) : p.date }); };
}
function addDays(iso, n) { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); }
function donut(mix, total) {
  const R = 80, C = 105, SW = 26, gap = mix.length > 1 ? 0.025 : 0;
  let a = -Math.PI / 2;
  const arcs = mix.map((m, i) => {
    const frac = m.count / total, a0 = a + gap / 2, a1 = a + frac * Math.PI * 2 - gap / 2;
    a += frac * Math.PI * 2;
    if (a1 <= a0) return "";
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (ang) => `${C + R * Math.cos(ang)},${C + R * Math.sin(ang)}`;
    const d = frac >= 0.9999 ? `M${C},${C - R} A${R},${R} 0 1 1 ${C - 0.01},${C - R}` : `M${p(a0)} A${R},${R} 0 ${large} 1 ${p(a1)}`;
    return `<path class="seg-in" style="animation-delay:${i * 60}ms" data-cat="${catKey(m.key)}" data-key="${m.key}" d="${d}" fill="none" stroke="var(--c)" stroke-width="${SW}" stroke-linecap="butt"><title>${esc(m.label)}: ${m.count}</title></path>`;
  }).join("");
  return `<svg class="donut" viewBox="0 0 210 210" role="img" aria-label="Charges by lender type">${arcs}
    <text class="centre-v" x="105" y="110" text-anchor="middle">${total.toLocaleString("en-GB")}</text>
    <text class="centre-l" x="105" y="130" text-anchor="middle">charges</text></svg>`;
}
const DECO = {
  waves: '<path d="M4 18c6-6 10 6 16 0s10 6 16 0 10 6 16 0 10 6 16 0M4 32c6-6 10 6 16 0s10 6 16 0 10 6 16 0 10 6 16 0M4 46c6-6 10 6 16 0s10 6 16 0 10 6 16 0 10 6 16 0"/>',
  rise: '<path d="M4 52 22 38l12 6 18-20 20-16" stroke-dasharray="4 5"/>',
  bars: '<path d="M10 54V30M22 54V12M34 54V36M46 54V20M58 54V42M70 54V26"/>',
  check: '<circle cx="40" cy="30" r="24"/><path d="m28 30 8 8 16-16"/>',
};

// ------------------------------------------------------------------ overview
let resizeTimer, lastOverview;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (S.view === "overview" && lastOverview) drawOverviewChart(lastOverview); }, 150); });
function drawOverviewChart(o) {
  const el = document.getElementById("ov-chart");
  if (el) areaChart(el, S.chartWeekly ? o.weekly : o.daily, { weekly: S.chartWeekly, coverageStart: o.coverage_start });
}
async function renderOverview() {
  const o = await get("/api/overview");
  lastOverview = o;
  const w = o.waiting, waitingTotal = w.review + w.confirm + w.messages + w.drafts;
  document.getElementById("nav-decisions").textContent = waitingTotal || "";
  const tw = o.this_week;
  setSub(`${plural(o.total, "charge")} on the register${o.coverage_start ? `, watching since ${fmtDate(o.coverage_start)}` : ""}`);
  const mixTotal = o.mix.reduce((a, m) => a + m.count, 0);
  const feedIcon = { cloud: "cloud", agent: "agent", you: "you" };
  main.innerHTML = `<div class="grid anim-root">
    <div class="span-12" id="ov-banner-slot"></div>
    <section class="card span-8 hero-card">
      <div class="card-head"><div><h2>Charges delivered</h2><p>${S.chartWeekly ? "Last 12 weeks" : "Last 30 days"}. Hover for the breakdown, click a point to list those charges.</p></div>
        <div class="seg" role="group" aria-label="Period"><button data-period="daily" aria-pressed="${!S.chartWeekly}">Daily</button><button data-period="weekly" aria-pressed="${!!S.chartWeekly}">Weekly</button></div></div>
      <div class="hero-split">
        <div class="hero-figs">
          <div class="fig"><div class="v num">${tw.total}</div><div class="l">delivered this week${tw.covered ? "" : ", partly covered"}</div></div>
          <div class="fig"><div class="v sm num">${tw.direct}</div><div class="l">to direct lenders this week</div></div>
          <div><button class="btn primary" id="ov-open-week">Open this week's charges</button></div>
        </div>
        <div>
          <div class="legend-row">${SERIES.map((s) => `<span><span class="sw" style="background:${s.color}"></span>${s.label}</span>`).join("")}</div>
          <div class="chart" id="ov-chart"></div>
        </div>
      </div>
      <div class="mini-stats">
        <button class="mini" data-go="#/charges"><span class="disc" data-cat="cloud">${icon("charges")}</span><span><span class="l">Charges collected</span><br><span class="v num">${o.total.toLocaleString("en-GB")}</span></span></button>
        <button class="mini" data-go="#/lenders"><span class="disc" data-cat="agent">${icon("lenders")}</span><span><span class="l">Lender names</span><br><span class="v num">${o.lenders}</span></span></button>
        <button class="mini" data-go="#/decisions"><span class="disc" data-cat="alert">${icon("inbox")}</span><span><span class="l">Waiting for you</span><br><span class="v num">${waitingTotal}</span></span></button>
        <button class="mini" data-go="#/status"><span class="disc" data-cat="bank">${icon("clock")}</span><span><span class="l">Last cloud run</span><br><span class="v">${o.last_pipeline_commit ? fmtTime(o.last_pipeline_commit) : "None yet"}</span></span></button>
      </div>
    </section>
    <section class="card span-4 donut-card">
      <div class="card-head"><div><h2>Who's lending</h2><p>Every charge by lender type. Click a type to list its charges.</p></div></div>
      ${mixTotal ? `<div class="donut-wrap">${donut(o.mix, mixTotal)}</div>
      <ul class="mix-list">${o.mix.map((m) => `<li><button data-cat="${catKey(m.key)}" data-mix="${esc(m.key)}"><span class="sw"></span><span>${esc(m.label)}</span><span class="pct num">${Math.round((m.count / mixTotal) * 100)}%</span><span class="n num">${m.count}</span></button></li>`).join("")}</ul>`
      : `<div class="empty"><p>No charges yet.</p></div>`}
    </section>
    <button class="stat g-direct span-3" data-tier="A"><span class="sl">Direct lender charges</span><span class="sv num">${o.tiers.A}</span><span class="ss">Tier A, private credit funds</span><svg class="deco" viewBox="0 0 76 60">${DECO.waves}</svg></button>
    <button class="stat g-agent span-3" data-tier="B"><span class="sl">Behind a security agent</span><span class="sv num">${o.tiers.B}</span><span class="ss">Tier B, lender hidden</span><svg class="deco" viewBox="0 0 76 60">${DECO.rise}</svg></button>
    <button class="stat g-review span-3" data-tier="D"><span class="sl">Need a lender type</span><span class="sv num">${o.tiers.D}</span><span class="ss">Tier D, for the agent or you</span><svg class="deco" viewBox="0 0 76 60">${DECO.bars}</svg></button>
    <button class="stat g-repaid span-3" data-status="repaid"><span class="sl">Repaid charges</span><span class="sv num">${o.repaid_total}</span><span class="ss">${o.repaid_30} repaid in the last 30 days</span><svg class="deco" viewBox="0 0 76 60">${DECO.check}</svg></button>
    <section class="card span-7">
      <div class="card-head"><div><h2>What it means</h2><p>What the latest filings suggest, private credit first. Click one for the company's full picture.</p></div><a class="link-btn" href="#/signals">All ${o.signals.total} signals</a></div>
      <div class="sig-counts">${Object.entries(o.signals.counts).filter(([, n]) => n).map(([k, n]) => `<button class="chip" data-cat="${sigCat(k)}" data-sigjump="${k}"><span class="sw"></span>${esc(o.signals.labels[k])} <b class="num">${n}</b></button>`).join("")}</div>
      ${o.signals.top.length ? `<ul class="sig-mini">${o.signals.top.map((s) => `<li><button data-company="${esc(s.company_number)}" data-cat="${sigCat(s.type)}"><span class="disc">${sigIcon(s.type)}</span>
        <span class="txt"><span class="hd"><b>${esc(s.label)}</b><span class="muted">${esc(s.company_name)}</span>${s.private_credit ? '<span class="pill" data-cat="direct_lender">Private credit</span>' : ""}</span><span class="mn">${esc(s.meaning)}</span></span></button></li>`).join("")}</ul>`
        : `<div class="empty"><span class="disc" data-cat="cloud">${icon("signals")}</span><p>No signals yet. They appear as the radar fetches each borrower's older charges.</p></div>`}
    </section>
    <section class="card span-5">
      <div class="card-head"><div><h2>Recent activity</h2><p>Cloud runs, agent runs and your decisions.</p></div><a class="link-btn" href="#/status">View status</a></div>
      ${o.activity.length ? `<ul class="feed">${o.activity.map((a) => `<li><span class="when">${fmtTime(a.at)}</span><span class="disc" data-cat="${a.kind}">${icon(feedIcon[a.kind] || "cloud")}</span>
        <span><span class="what">${esc(a.text.replace(/^You: /, ""))}</span><br><span class="who">${a.kind === "you" ? "You, in the review app" : a.kind === "agent" ? "Claude agent on GitHub" : "Scheduled cloud run"}</span></span></li>`).join("")}</ul>`
        : `<div class="empty"><span class="disc" data-cat="cloud">${icon("cloud")}</span><p>No activity yet. Runs and decisions appear here once this folder is linked to your GitHub repo.</p></div>`}
    </section>
    <section class="card span-12">
      <div class="card-head"><div><h2>Latest charges</h2><p>Newest filings first.</p></div><a class="link-btn" href="#/charges">View all ${o.total}</a></div>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Tier</th><th>Borrower</th><th>Lender</th><th class="num">Delivered</th><th></th></tr></thead><tbody>
      ${o.latest.map((r) => `<tr><td class="side-fact">${tierBadge(r.tier)}</td>
        <td class="lead"><button class="linkish strong" data-company="${esc(r.company_number)}">${coName(r.company_name, r.company_number)}</button><span class="sub id">${esc(r.company_number)}</span></td>
        <td class="meta">${r.chargees.slice(0, 1).map((c) => `<button class="linkish" data-lender="${esc(c.name)}">${esc(c.display)}</button>`).join("")}${r.chargees.length > 1 ? ` <span class="muted">+${r.chargees.length - 1}</span>` : ""}</td>
        <td class="num meta">${isoDate(r.delivered_on)}</td><td class="act"><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")
        || `<tr><td colspan="5" class="muted">No charges yet.</td></tr>`}
      </tbody></table></div>
    </section>
  </div>`;
  placeBanner(o, waitingTotal);
  drawOverviewChart(o);
  main.querySelectorAll("[data-period]").forEach((b) => (b.onclick = () => {
    S.chartWeekly = b.dataset.period === "weekly";
    main.querySelectorAll("[data-period]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    main.querySelector(".card-head p").textContent = `${S.chartWeekly ? "Last 12 weeks" : "Last 30 days"}. Hover for the breakdown, click a point to list those charges.`;
    drawOverviewChart(o); fadeIn(document.getElementById("ov-chart"));
  }));
  document.getElementById("ov-open-week").onclick = () => goCharges({ from: tw.start, to: tw.end });
  main.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => (location.hash = b.dataset.go)));
  main.querySelectorAll("[data-sigjump]").forEach((b) => (b.onclick = () => { S.sigF = { ...SIG_DEFAULT, type: b.dataset.sigjump }; location.hash = "#/signals"; }));
  document.getElementById("nav-signals").textContent = o.signals.total || "";
  main.querySelectorAll("[data-tier]").forEach((b) => (b.onclick = () => goCharges({ tier: b.dataset.tier })));
  main.querySelectorAll("[data-status]").forEach((b) => (b.onclick = () => goCharges({ status: b.dataset.status })));
  main.querySelectorAll("[data-mix], .donut path").forEach((b) => {
    b.onclick = () => goCharges({ category: b.dataset.mix || b.dataset.key });
    b.onmouseenter = () => main.querySelectorAll(".donut path").forEach((p) => (p.style.opacity = p.dataset.key === (b.dataset.mix || b.dataset.key) ? 1 : .35));
    b.onmouseleave = () => main.querySelectorAll(".donut path").forEach((p) => (p.style.opacity = 1));
  });
}

// One banner element, kept across live refreshes so its skyline keeps running instead of restarting.
let ovBanner = null;
function placeBanner(o, waiting) {
  const slot = document.getElementById("ov-banner-slot");
  if (!slot) return;
  const h = new Date().getHours(), greet = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  if (!ovBanner) {
    ovBanner = document.createElement("section");
    ovBanner.className = "ov-banner"; ovBanner.dataset.seed = "5";
    ovBanner.dataset.photo = "/static/img/hero.jpg";   // brought to life by skyline.js; settings in img/hero.json
    ovBanner.dataset.focus = "0.5,0.47";               // a short banner: keep the towers and the top of the river in view
    ovBanner.innerHTML = `<div class="ov-banner-inner"><div class="eyebrow-dot"><i></i><span id="ovb-date"></span></div><h1 id="ovb-greet"></h1><p id="ovb-line"></p>
      <div class="ov-banner-actions"><a class="btn primary" href="#/pack">${icon("pack")}Weekly pack</a><a class="btn glass" href="#/lenderview">${icon("lenderview")}Lender view</a><a class="btn glass" href="#/decisions">${icon("decisions")}Decisions</a></div></div>`;
    if (window.ArmariuSkyline) ArmariuSkyline.mount(ovBanner);
  }
  ovBanner.querySelector("#ovb-date").textContent = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  ovBanner.querySelector("#ovb-greet").textContent = `${greet}, Armaan.`;
  ovBanner.querySelector("#ovb-line").innerHTML = `This week so far: <b>${o.this_week.total.toLocaleString("en-GB")}</b> charges delivered, <b>${o.this_week.direct}</b> to direct lenders. ${waiting ? `<b>${waiting}</b> decision${waiting === 1 ? "" : "s"} waiting for you.` : "Nothing is waiting for you."}`;
  slot.replaceWith(ovBanner);
}

// ------------------------------------------------------------------ additions: shared bits
const CLASS_CAT = { bank: "bank", direct_lender: "direct_lender", abl_invoice: "abl_specialist", asset_finance: "asset_finance", trustee_agent: "security_agent",
  government_backed: "public_sector", property: "property_lender", individual: "individual", other: "other" };
let ADD = null, addWeek = 0, addHideProp = true;
async function loadAdditions() { ADD = await get("/api/additions"); return ADD; }
function classChip(snap, cls) { return `<span class="cat" data-cat="${CLASS_CAT[cls] || "other"}">${esc((snap.class_labels || {})[cls] || cls)}</span>`; }
function snapshotBar(a, what) {
  const s = a.snapshot, j = a.job;
  return `<div class="card span-12 snap-bar"><span class="disc" data-cat="cloud">${icon("clock")}</span><div><b>${s ? `${what} built ${fmtDate(s.built)} from the database` : "Not built yet"}</b><br><span class="muted">${j.running ? "Rebuilding now, about 30 seconds…" : j.error ? `Last rebuild failed: ${esc(j.error)}` : "Rebuild after the database updates. Rules only, no AI, the same data always gives the same numbers."}</span></div>
    <button class="btn soft" id="rebuild" ${j.running ? "disabled" : ""}>${icon("refi")}${j.running ? "Rebuilding" : "Rebuild"}</button></div>`;
}
function wireRebuild() {
  const b = document.getElementById("rebuild");
  if (!b) return;
  b.onclick = async () => { b.disabled = true; await post("/api/additions/rebuild", {}); toast("Rebuilding the lender view and scorecard");
    const wait = async () => { const a = await get("/api/additions"); if (a.job.running) return setTimeout(wait, 2500); ADD = a; if (a.job.error) toast(a.job.error, true); else toast("Rebuilt"); refresh(); }; setTimeout(wait, 2500); };
}
const pct1 = (x) => `${(Number(x) * 100).toFixed(1)}%`;

// ------------------------------------------------------------------ Module C: lender view
async function renderLenderView() {
  const a = await loadAdditions(), s = a.snapshot;
  if (!s) { main.innerHTML = `<div class="grid anim-root">${snapshotBar(a, "Lender view")}<div class="card span-12 empty"><span class="disc" data-cat="cloud">${icon("lenderview")}</span><p>Press Rebuild to build the lender view from the database.</p></div></div>`; wireRebuild(); return; }
  const weeks = s.weeks, w = weeks[weeks.length - 1 - addWeek] || weeks[weeks.length - 1];
  setSub(`${fmtDate(w.start)} to ${fmtDate(w.end)}${w.complete ? "" : ", partial window"}`);
  const league = w.league.filter((r) => r.class !== "individual" && (!addHideProp || r.class !== "property"));
  const max = Math.max(1, ...league.map((r) => r.this_week)), secMax = Math.max(1, ...w.sectors.map((r) => r.lender_charges));
  const cov = s.aliases.coverage, synMax = Math.max(1, ...w.syndication.map((r) => r.all_charges));
  main.innerHTML = `<div class="grid anim-root">
    ${snapshotBar(a, "Lender view")}
    ${w.complete ? "" : `<div class="card span-12"><div class="notice">${icon("alert")}<div><b>Partial window.</b> ${esc(s.sample_caveat)}</div></div></div>`}
    <section class="card span-8">
      <div class="card-head"><div><h2>League table</h2><p>New charges per lender this week, with trailing 4 and 13 week counts. ${w.raw_charges.toLocaleString("en-GB")} raw charges; a charge naming several lenders counts once for each.</p></div>
        <div class="row-gap"><select id="lv-week" class="select">${weeks.map((x, i) => `<option value="${weeks.length - 1 - i}">${fmtDate(x.start)}</option>`).reverse().join("")}</select>
        <label class="check"><input type="checkbox" id="lv-prop" ${addHideProp ? "checked" : ""}> Hide property</label></div></div>
      <div class="table-wrap"><table class="stack"><thead><tr><th>#</th><th>Lender</th><th>Type</th><th class="num">This week</th><th></th><th class="num">4 wks</th><th class="num">13 wks</th></tr></thead><tbody>
      ${league.slice(0, 30).map((r, i) => `<tr><td class="muted num">${i + 1}</td><td class="lead"><b>${esc(r.lender)}</b>${r.low_observations ? ' <span class="pill" data-cat="review" title="Fewer than 5 charges in 13 weeks">few</span>' : ""}</td><td>${classChip(s, r.class)}</td>
        <td class="num"><b>${r.this_week}</b></td><td style="width:22%"><div class="hbar" data-cat="${CLASS_CAT[r.class] || "other"}"><i style="width:${(r.this_week / max) * 100}%"></i></div></td><td class="num">${r.trailing_4w}</td><td class="num">${r.trailing_13w}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="fine">${esc(s.caveat)}</p>
    </section>
    <section class="card span-4">
      <div class="card-head"><div><h2>Name matching</h2><p>Module A. Share of the last 12 months' charges that map to one canonical lender or the trustee class.</p></div></div>
      <div class="gauge"><div class="gauge-v count-up">${(cov.share * 100).toFixed(1)}%</div><div class="gauge-l">${cov.mapped.toLocaleString("en-GB")} of ${cov.charges.toLocaleString("en-GB")} charges · target 90%</div>
        <div class="hbar big"><i style="width:${cov.share * 100}%"></i><span class="target" style="left:90%"></span></div></div>
      <h3 class="mini-h">Map these next</h3>
      <ul class="plain-list">${s.aliases.review.slice(0, 10).map((r) => `<li><span><b>${esc(r.example)}</b>${r.suggested_canonical ? `<br><span class="muted">close to ${esc(r.suggested_canonical)} (${r.similarity})</span>` : ""}</span><span class="num">${r.charges}</span></li>`).join("")}</ul>
      <p class="fine">Write the full list with <code>python -m radar.aliases review</code>, then add rows to <code>data/lender_aliases.csv</code>.</p>
    </section>
    <section class="card span-6"><div class="card-head"><div><h2>Refinancing flows</h2><p>Lender X to lender Y, 13 weeks to ${fmtDate(w.end)}. A pattern in the filings, not proof.</p></div></div>
      <ul class="plain-list">${w.flows.map((f) => `<li><span>${esc(f.from_lender)} <span class="arrow">→</span> <b>${esc(f.to_lender)}</b></span><span class="num">${f.signals_13w}</span></li>`).join("") || "<li>None yet.</li>"}</ul></section>
    <section class="card span-6"><div class="card-head"><div><h2>Sector cut</h2><p>Lender-charge credits by borrower sector this week.</p></div></div>
      ${w.sectors.slice(0, 10).map((r) => `<div class="sec-row"><span>${esc(r.sector)}</span><div class="hbar"><i style="width:${(r.lender_charges / secMax) * 100}%"></i></div><span class="num">${r.lender_charges}</span></div>`).join("")}</section>
    <section class="card span-6"><div class="card-head"><div><h2>New entrants</h2><p>Lenders with no charge in the prior 12 months among observed borrowers.</p></div></div>
      <ul class="plain-list">${w.new_entrants.slice(0, 10).map((e) => `<li><span>${classChip(s, e.class)} ${esc(e.lender)}</span><span class="num">${e.this_week}</span></li>`).join("") || "<li>None.</li>"}</ul></section>
    <section class="card span-6"><div class="card-head"><div><h2>Syndication proxy</h2><p>Charges naming a security trustee or agent, per week.</p></div></div>
      <div class="cols">${w.syndication.map((r) => `<div class="col" title="Week of ${esc(r.week_start)}: ${r.agent_charges} agent charges, ${r.multi_lender_charges} multi-lender, ${r.all_charges} in all"><i style="height:${(r.agent_charges / Math.max(1, ...w.syndication.map((x) => x.agent_charges))) * 100}%"></i><span>${shortDate(r.week_start)}</span></div>`).join("")}</div>
      <p class="fine">Reconciliation: ${w.reconciliation.raw_charges} raw charges, ${w.reconciliation.charges_with_a_lender} name a lender, ${w.reconciliation.lender_credits} lender credits. ${w.reconciliation.ok ? "Totals reconcile." : "Totals do not reconcile: check the data."}</p></section>
  </div>`;
  document.getElementById("lv-week").value = String(addWeek);
  document.getElementById("lv-week").onchange = (e) => { addWeek = Number(e.target.value); refresh(); };
  document.getElementById("lv-prop").onchange = (e) => { addHideProp = e.target.checked; refresh(); };
  wireRebuild();
}

// ------------------------------------------------------------------ Module D: scorecard
async function renderScorecard() {
  const a = await loadAdditions(), s = a.snapshot;
  if (!s) { main.innerHTML = `<div class="grid anim-root">${snapshotBar(a, "Scorecard")}</div>`; wireRebuild(); return; }
  setSub(`Backtest, rules ${s.rule_version}`);
  main.innerHTML = `<div class="grid anim-root">${snapshotBar(a, "Scorecard")}
    <div class="card span-12"><div class="notice">${icon("alert")}<div><b>Backtest, not a live record.</b> ${esc(s.bias_note)}</div></div></div>
    ${s.scorecard.map((r) => `<section class="card span-4 score-card"><div class="card-head"><div><span class="pill" data-cat="review">Backtest</span><h2 style="margin-top:10px">${esc(s.signal_labels[r.signal_type])}</h2><p>${Number(r.signals).toLocaleString("en-GB")} signals, ${Number(r.outcomes_observed).toLocaleString("en-GB")} hits${r.small_sample ? ", small sample" : ""}</p></div></div>
      <div class="lift-v count-up">${r.lift ? `${r.lift}×` : "n/a"}</div><div class="muted">lift against the matched baseline</div>
      <div class="ci-track"><div class="ci-rng" style="left:${r.ci_low * 100}%;width:${(r.ci_high - r.ci_low) * 100}%"></div>${r.baseline_rate ? `<div class="ci-base" style="left:${r.baseline_rate * 100}%"></div>` : ""}<div class="ci-pt" style="left:${r.hit_rate * 100}%"></div></div>
      <dl class="facts"><dt>Hit rate</dt><dd>${pct1(r.hit_rate)} <span class="muted">(${pct1(r.ci_low)} to ${pct1(r.ci_high)})</span></dd><dt>Baseline</dt><dd>${r.baseline_rate ? pct1(r.baseline_rate) : "n/a"} <span class="muted">(${esc(r.baseline_coverage)} matched)</span></dd><dt>Outcome</dt><dd>${esc(r.outcome)}</dd><dt>Window</dt><dd>${esc(r.window_start)} to ${esc(r.window_end)}</dd></dl></section>`).join("")}
    <section class="card span-12 prose"><h2>How to read it</h2><p>The dot is the hit rate with its 95% Wilson interval; the tick is the baseline: charges without that signal, in the same sector and company age band, weighted to match. Lift above 1 means the signal is followed by further borrowing more often than comparable charges. Signals use filing dates only and only count once their 90-day window has closed before the radar started watching. Outcome definitions are a proposal waiting for your sign-off (90 or 180 days, or insolvency within 12 months).</p>
    <p>Re-run with <code>python -m radar.scorecard backtest --window 180</code>. The live log (<code>signals_published.csv</code>) starts only after the validation gate and is never mixed with these numbers.</p></section>
  </div>`;
  wireRebuild();
}

// ------------------------------------------------------------------ refinance watch
let watchHideProp = false;
async function renderWatch() {
  const a = await loadAdditions(), s = a.snapshot, W = s && s.refi_watch;
  if (!W) { main.innerHTML = `<div class="grid anim-root">${snapshotBar(a, "Refinance watch")}<div class="card span-12"><p>Rebuild to create the refinance watch.</p></div></div>`; wireRebuild(); return; }
  const bt = W.backtest, months = Math.round(W.window_days / 30.4);
  setSub(`Odds of refinancing within ${months} months, model ${W.model_version}`);
  const draw = () => {
    const rows = W.rows.filter((r) => !watchHideProp || r.sector !== "Property and real estate").slice(0, 150);
    document.getElementById("rw-body").innerHTML = rows.map((r) => `<tr><td><a href="${esc(r.filing_url)}" target="_blank" rel="noopener"><b>${esc(r.company_name)}</b></a><div class="muted">${esc(r.company_number)} · ${esc(r.sector)}</div></td>
      <td class="num"><b>${pct1(r.odds_12m)}</b><div class="muted">${r.lift}× average</div></td><td>${esc(r.reasons)}</td><td>${esc(r.newest_lender)}<div class="muted">${esc(r.newest_created || "")}</div></td></tr>`).join("");
  };
  main.innerHTML = `<div class="grid anim-root">${snapshotBar(a, "Refinance watch")}
    <div class="card span-12"><div class="notice">${icon("alert")}<div><b>Read as odds.</b> ${esc(W.caveat)} Loans due within a year (from accounts) and ownership changes are being added as further clues.</div></div></div>
    ${bt.tiers.map((t) => `<section class="card span-3"><span class="pill" data-cat="review">Backtest, top ${esc(t.top)}</span><div class="lift-v count-up">${pct1(t.hit_rate)}</div><div class="muted">refinanced within ${months} months, ${t.lift}× the ${pct1(bt.base_rate)} average (${Number(t.companies).toLocaleString("en-GB")} company-dates)</div></section>`).join("")}
    <section class="card span-12"><div class="card-head"><div><h2>Most likely to refinance</h2><p>Tested from ${esc(bt.test_cutoffs[0])} on dates the score never saw (learned from the year to ${esc(bt.train_end)}).</p></div>
      <label class="row-gap"><input type="checkbox" id="rw-prop" ${watchHideProp ? "checked" : ""}> Hide property companies</label></div>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Company</th><th class="num">Odds</th><th>Why it scores</th><th>Newest lender</th></tr></thead><tbody id="rw-body"></tbody></table></div></section>
  </div>`;
  document.getElementById("rw-prop").onchange = (e) => { watchHideProp = e.target.checked; draw(); };
  draw(); wireRebuild();
}

// ------------------------------------------------------------------ Module E: weekly pack
async function renderPack() {
  const p = await get("/api/pack"), j = p.job;
  setSub(p.built ? `Built ${fmtTime(p.built)}` : "Not built yet");
  main.innerHTML = `<div class="grid anim-root">
    <section class="card span-12 pack-steps">
      ${[["pack", "Build the pack", "Facts from the lender view, signals and scorecard."], ["note", "Draft in a Claude chat", "Copy the pack and prompt, paste into a chat on your plan. Never an API call."], ["check", "Check every number", "Rewrite what you disagree with and add your view."], ["mail", "Publish", "After the gate, add featured signals to the live log."]]
        .map(([ic, t, d], i) => `<div class="step"><span class="disc" data-cat="${["cloud", "agent", "review", "draft"][i]}">${icon(ic)}</span><div><b>${i + 1}. ${t}</b><br><span class="muted">${d}</span></div></div>`).join("")}
    </section>
    ${p.failed ? `<div class="card span-12"><div class="notice bad">${icon("alert")}<div><b>The last pack build failed.</b><pre class="fail">${esc(p.failed.slice(0, 1200))}</pre></div></div></div>` : ""}
    <section class="card span-12">
      <div class="card-head"><div><h2>This week's pack</h2><p>${p.content ? "Read it, then copy it with the prompt into a Claude chat for a first draft." : "No pack yet. Build one for last week."}</p></div>
        <div class="row-gap"><button class="btn soft" id="pk-copy" ${p.content ? "" : "disabled"}>${icon("note")}Copy pack and prompt</button><button class="btn primary" id="pk-build" ${j.running ? "disabled" : ""}>${icon("pack")}${j.running ? "Building…" : "Build last week's pack"}</button></div></div>
      ${p.content ? `<div class="prose pack-md">${md(p.content)}</div>` : `<div class="empty"><span class="disc" data-cat="cloud">${icon("pack")}</span><p>The build takes about a minute.</p></div>`}
    </section></div>`;
  document.getElementById("pk-build").onclick = async (e) => { e.target.disabled = true; await post("/api/pack/build", {}); toast("Building the weekly pack");
    const wait = async () => { const q = await get("/api/pack"); if (q.job.running) return setTimeout(wait, 3000); toast(q.job.error ? "The pack build failed: see the notice" : "Pack built", !!q.job.error); refresh(); }; setTimeout(wait, 3000); };
  const cp = document.getElementById("pk-copy");
  if (cp) cp.onclick = async () => { try { await navigator.clipboard.writeText(`${p.prompt}\n\n---\n\n${p.content}`); toast("Copied. Paste it into a Claude chat."); } catch (e) { toast("Couldn't copy: select the text instead", true); } };
}

// ------------------------------------------------------------------ lending-history strip
function historyStrip(history) {
  const dated = (history || []).filter((h) => h.delivered_on);
  if (!dated.length) return "";
  const t = (d) => new Date(d.slice(0, 10) + "T12:00:00").getTime();
  const now = Date.now(), thisYear = new Date().getFullYear();
  const firstYear = Math.min(new Date(t(dated[0].delivered_on)).getFullYear(), thisYear - 2);
  const start = new Date(firstYear, 0, 1).getTime();
  const pct = (ms) => Math.min(100, Math.max(0, ((ms - start) / (now - start)) * 100));
  const years = [];
  for (let y = firstYear; y <= thisYear; y++) years.push(y);
  const step = years.length > 7 ? Math.ceil(years.length / 6) : 1;
  const ticks = years.filter((_, i) => i % step === 0).map((y) => {
    const p = pct(new Date(y, 0, 1).getTime());
    return p > 92 ? "" : `<span class="tick" style="left:${p}%"></span><span class="yr" style="left:${p}%">${y}</span>`;
  }).join("") + `<span class="yr end" style="left:100%">Today</span>`;
  let lastP = -99, lifted = false;
  const dots = dated.map((h) => {
    const p = pct(t(h.delivered_on));
    lifted = p - lastP < 1.6 ? !lifted : false;
    lastP = p;
    const repaid = h.status && h.status !== "outstanding";
    return `<span class="dot${repaid ? " repaid" : ""}${lifted ? " lift" : ""}" style="left:${p}%" title="${esc(h.delivered_on)}: ${repaid ? "repaid" : "outstanding"}"></span>`;
  }).join("");
  const repaid = dated.filter((h) => h.status && h.status !== "outstanding").length;
  const summary = `${plural(dated.length, "charge")} since ${dated[0].delivered_on.slice(0, 4)}, latest ${fmtDate(dated[dated.length - 1].delivered_on)}` + (repaid ? `, ${repaid} repaid` : "");
  return `<div class="history"><div class="history-head"><h3>Lending history</h3><span class="muted small">${summary}</span></div>
    <div class="strip" role="img" aria-label="${esc(summary)}"><span class="axis"></span>${ticks}${dots}</div></div>`;
}

// ------------------------------------------------------------------ who's behind a lender, and what the agents think
const ROLE = { first: ["Classifier", "agent"], second: ["Second opinion", "cloud"], referee: ["Referee", "review"] };
const CONSENSUS = {
  agreed: ["Two agents agreed", "bank"], settled: ["Referee decided", "direct_lender"],
  disputed: ["Agents disagree: the referee looks next", "review"], unresolved: ["Referee couldn't settle it", "alert"],
  unsure: ["Both agents unsure", "unknown"],
};
const consensusPill = (state) => (CONSENSUS[state] ? `<span class="pill" data-cat="${CONSENSUS[state][1]}">${CONSENSUS[state][0]}</span>` : "");
function opinionsBox(it) {
  if (!it.opinions || it.opinions.length < 2) return "";
  return `<div class="opinions"><div class="op-head"><h3>What the agents think</h3>${consensusPill(it.consensus)}</div>
    ${it.opinions.map((o) => `<div class="op" data-cat="${catKey(o.category)}"><div class="op-top"><span class="op-role">${(ROLE[o.role] || [o.role])[0]}</span>${catChip(o.category)}${o.confidence != null ? `<span class="muted small">${Math.round(o.confidence * 100)}% sure</span>` : ""}${o.parent_firm ? `<span class="muted small">part of ${esc(o.parent_firm)}</span>` : ""}</div>
      <p>${esc(o.reason || "")}</p></div>`).join("")}</div>`;
}
const controlsIt = (o) => (o.control || []).some((c) => c.startsWith("75-100%") || c.startsWith("50-75%") || c === "appoints the directors");
const ownerLine = (o) => `<li${controlsIt(o) ? ' class="ctrl"' : ""}><b>${esc(o.name)}</b> <span class="muted">${o.kind === "company" ? "company" : o.kind === "person" ? "person" : ""}${o.control && o.control.length ? `, ${esc(o.control.join(", "))}` : ""}</span>${controlsIt(o) ? ' <span class="pill tiny" data-cat="direct_lender">controls it</span>' : ""}</li>`;
function relatedBox(r) {
  if (!r) return "";
  if (!r.linked.length) return r.checked ? `<p class="muted small rel-none">Checked ${plural(r.checked, "borrower")}: none share an owner or director with this lender.</p>` : "";
  return `<div class="rel-warn"><b>Linked to ${r.linked.length} of ${plural(r.checked, "borrower")} checked</b>
    <p>They share an owner or director with this lender, which usually means funding inside a group or between related people rather than market lending. <b>Other</b> is likely the right type.</p>
    <ul class="owner-list">${r.linked.map((l) => `<li><button class="linkish" data-company="${esc(l.number)}">${esc(l.borrower)}</button> <span class="muted">shares ${esc(l.shared.join(", "))}</span></li>`).join("")}</ul></div>`;
}
function profileBox(p, parentInput) {
  if (!p) return `<div class="ch-box"><span class="disc" data-cat="unknown">${icon("lenders")}</span><div><b>Companies House</b><p class="muted small">Not looked up yet. The next cloud run checks who controls this lender.</p></div></div>`;
  if (!p.matched) return `<div class="ch-box"><span class="disc" data-cat="unknown">${icon("lenders")}</span><div><b>Companies House</b><p class="muted small">No UK company with exactly this name, so there's no owner record. Often a foreign fund, a trust or a person.</p></div></div>`;
  const sic = p.sic.map((s) => `${esc(s.code)}${s.label ? ` ${esc(s.label)}` : ""}`).join("; ");
  const summary = p.group ? `Controlled by <b>${esc(p.group)}</b>`
    : p.nominee ? `Shares held by <b>${esc(p.nominee)}</b>, a corporate services firm. Typical of fund and securitisation vehicles: the real backer isn't on the register`
    : p.controller_person ? `Controlled by a person: <b>${esc(p.controller_person)}</b>`
    : p.owners.length ? "No single owner controls it" : "No owner on record";
  const last = p.owner_chain[p.owner_chain.length - 1];
  return `<div class="ch-box"><span class="disc" data-cat="cloud">${icon("lenders")}</span><div class="ch-body">
    <div class="ch-title"><b>Who's behind it</b><a class="rlink" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.company_name)} ${esc(p.company_number)}</a></div>
    <p class="muted small">${p.company_status ? esc(p.company_status[0].toUpperCase() + p.company_status.slice(1)) : ""}${p.incorporated ? `, incorporated ${fmtDate(p.incorporated)}` : ""}${sic ? `. Its own filing says: ${sic}` : ""}${p.matched_by === "number in name" ? ". Matched by the company number in the filing" : ""}</p>
    <p class="ch-summary">${summary}</p>
    ${p.owners.length ? `<p class="ch-sub">Owned by</p><ul class="owner-list">${p.owners.map(ownerLine).join("")}</ul>` : ""}
    ${p.owner_chain.map((c) => `<p class="ch-sub">${esc(c.of)} is owned by</p>${c.owners.length ? `<ul class="owner-list">${c.owners.map(ownerLine).join("")}</ul>` : `<p class="muted small">No owner on record.</p>`}`).join("")}
    ${last && last.split ? `<p class="muted small">No single owner controls ${esc(last.of)}, so the radar stops climbing there.</p>` : ""}
    ${p.officers.length ? `<p class="ch-sub">Run by (corporate directors or members)</p><ul class="owner-list">${p.officers.map((o) => `<li><b>${esc(o.name)}</b> <span class="muted">${esc(o.role)}</span></li>`).join("")}</ul>` : ""}
    ${p.same_group && p.same_group.length ? `<p class="ch-sub">Other lenders on the register in the same group</p><ul class="owner-list">${p.same_group.map((n) => `<li><button class="linkish" data-lender="${esc(n)}">${esc(n)}</button></li>`).join("")}</ul>` : ""}
    ${relatedBox(p.related)}
    ${p.group && parentInput ? `<button class="btn soft small-btn" data-useparent="${parentInput}" data-value="${esc(p.group)}">Use ${esc(p.group)} as the parent firm</button>` : ""}
  </div></div>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-useparent]");
  if (!b) return;
  const input = document.querySelector(b.dataset.useparent);
  if (input) { input.value = b.dataset.value; input.dispatchEvent(new Event("input")); input.focus(); toast("Parent firm filled in: save to keep it"); }
});

// ------------------------------------------------------------------ decisions
// The queue is ordered by what changes the headline: names that might be direct lenders or
// security agents first; property-only and low-stakes names go to Quick triage, cleared in bulk.
// Name variants ("X CAPITAL 6", "X CAPITAL 7") arrive as one item; one decision covers them all.
async function loadDecisions() {
  S.decisions = await get("/api/decisions");
  S.choices = S.decisions.choices;
  const L = S.decisions.lenders;
  const total = L.review.length + L.confirm.length + S.decisions.messages.length + S.decisions.drafts.length;
  document.getElementById("nav-decisions").textContent = total || "";
  return S.decisions;
}
function queueItems() {
  const L = S.decisions.lenders;
  return [...L.review.map((x) => ({ ...x, group: "review" })), ...L.confirm.map((x) => ({ ...x, group: "confirm" })),
    ...L.low.map((x) => ({ ...x, group: "low" })), ...L.waiting.map((x) => ({ ...x, group: "waiting" }))];
}
function decisionsFinding(d) {
  const L = d.lenders, parts = [];
  if (L.review.length) parts.push(`<b>${plural(L.review.length, "lender needs", "lenders need")} your call</b>`);
  if (L.confirm.length) parts.push(`${plural(L.confirm.length, "agent answer")} to check`);
  if (d.messages.length) parts.push(`${plural(d.messages.length, "message")} from the agents`);
  if (d.drafts.length) parts.push(`${plural(d.drafts.length, "draft")} to edit`);
  let s = parts.length ? parts.join(", ").replace(/, ([^,]*)$/, " and $1") + "." : "Nothing important needs you right now.";
  if (L.low.length) s += ` ${plural(L.low.length, "low-priority name")} for quick triage.`;
  return s.charAt(0).toUpperCase() + s.slice(1);
}
async function renderDecisions() {
  const d = await loadDecisions();
  const L = d.lenders, items = queueItems().filter((x) => x.group !== "low" && x.group !== "waiting" || !L.review.length && !L.confirm.length);
  if (!queueItems().find((x) => x.name === S.current)) S.current = items.length ? items[0].name : null;
  setSub(decisionsFinding(d).replace(/<[^>]+>/g, ""));
  const tiles = [
    ["lenders", "review", "lenders", L.review.length + L.confirm.length, "Lenders that matter", L.review.length + L.confirm.length ? "Might be direct lenders or security agents" : "None right now"],
    ["triage", "unknown", "layers", L.low.length, "Quick triage", L.low.length ? "Low-stakes names: clear them in bulk" : "Nothing to triage"],
    ["auto", "agent", "check", L.auto.length, "Double-check", L.auto.length ? "Accepted automatically. Optional" : "Nothing accepted automatically"],
    ["messages", "message", "mail", d.messages.length, "Messages from the agents", d.messages.length ? "Read, then mark as done" : "Nothing new"],
    ["drafts", "draft", "pen", d.drafts.length, "Newsletter drafts", d.drafts.length ? "Edit, then mark as published" : "None to edit"],
  ];
  main.innerHTML = `<div class="anim-root">
    <div class="qtiles five" role="tablist" aria-label="What needs you">${tiles.map(([k, cat, ic, n, label, sub]) =>
      `<button class="qtile" role="tab" data-cat="${cat}" data-tab="${k}" aria-selected="${S.decTab === k}"><span class="disc">${icon(ic)}</span>
         <span class="qn num">${n}</span><span class="ql">${label}</span><span class="qs">${sub}</span></button>`).join("")}</div>
    <div id="dec-body"></div></div>`;
  main.querySelectorAll(".qtile").forEach((b) => (b.onclick = () => { S.decTab = b.dataset.tab; renderDecisions().then(() => fadeIn(document.getElementById("dec-body"))); }));
  const body = document.getElementById("dec-body");
  if (S.decTab === "messages") return renderMessages(body, d.messages);
  if (S.decTab === "drafts") return renderDraftDecisions(body, d.drafts);
  if (S.decTab === "triage") return renderTriage(body, L.low);
  if (S.decTab === "auto") return renderTriage(body, L.auto, "auto");
  if (!queueItems().length) {
    body.innerHTML = `<div class="card empty"><span class="disc" data-cat="bank">${icon("check")}</span><h2>Every lender is classified</h2><p>When the radar picks up a lender it doesn't recognise, the classifier agent looks at it first and anything it isn't sure about comes here.</p></div>`;
    return;
  }
  const item = queueItems().find((x) => x.name === S.current) || queueItems()[0];
  S.current = item.name;
  body.innerHTML = `${!L.review.length && !L.confirm.length ? `<div class="notice">${icon("check")}<div>Nothing that could change the headline needs you. ${L.low.length ? `The ${plural(L.low.length, "name")} left are low priority: <button class="link-btn" id="go-triage">clear them in Quick triage</button>.` : ""}</div></div>` : ""}
    <div class="decide"><section class="card" id="decision" aria-label="Decision">${decisionCard(item)}</section>
    <nav class="card queue" aria-label="Lenders waiting">${queueList(L)}</nav></div>`;
  const gt = document.getElementById("go-triage");
  if (gt) gt.onclick = () => { S.decTab = "triage"; renderDecisions(); };
  wireDecision(item);
}
function decisionCard(it) {
  const pick = S.picks[it.name] || { category: it.source === "agent" && it.category !== "unknown" ? it.category : "", parent: it.parent || "", all: true, future: !!it.family };
  S.picks[it.name] = pick;
  const state = { review: ["Needs your call", "review"], confirm: ["Agent's answer to check", "direct_lender"], low: ["Low priority", "unknown"], waiting: ["Not classified yet", "unknown"] }[it.group];
  const cat = it.source === "agent" ? catKey(it.category) : "unknown";
  const conf = it.confidence != null ? `${Math.round(it.confidence * 100)}% sure` : "";
  const agent = it.source === "agent"
    ? `<div class="agent" data-cat="${cat}"><span class="disc">${icon("agent")}</span><div><p class="says">The classifier agent says ${esc(labelFor(it.category).toLowerCase())}${conf ? `, ${conf}` : ""}${it.parent ? `, part of ${esc(it.parent)}` : ""}.</p><p class="why">${esc(reasonText(it.reason, it.source))}</p></div></div>`
    : `<div class="agent" data-cat="unknown"><span class="disc">${icon("clock")}</span><div><p class="why">The classifier agent hasn't looked at this name yet. It will on its next run, or you can decide now.</p></div></div>`;
  const fam = it.members && it.members.length > 1 ? `
    <div class="family">
      <p><b>${it.group_family ? `${plural(it.members.length, "lender")} controlled by ${esc(it.group_family)}` : `${plural(it.members.length, "name")} look like the same lender`}</b>, ${plural(it.family_charges, "charge")} between them:</p>
      <ul>${it.members.map((m) => `<li>${esc(m.display)} <span class="muted">${plural(m.charges, "charge")}</span></li>`).join("")}</ul>
      <label class="check"><input type="checkbox" id="fam-all" ${pick.all ? "checked" : ""}> Apply my decision to all ${it.members.length}</label>
      ${it.family ? `<label class="check"><input type="checkbox" id="fam-future" ${pick.future ? "checked" : ""}> Also classify future names containing <b>${esc(it.family)}</b></label>` : ""}
    </div>` : "";
  const rows = it.examples.map((e) => `<tr>
      <td class="lead"><button class="linkish strong" data-company="${esc(e.company_number)}">${coName(e.company_name, e.company_number)}</button><span class="sub"><span class="id">${esc(e.company_number)}</span>${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</span></td>
      <td class="num side-fact">${isoDate(e.delivered_on)}</td>
      <td class="meta">${e.other_chargees.length ? e.other_chargees.map(esc).join("; ") : '<span class="muted">None</span>'}</td>
      <td class="meta">${e.all_assets === 1 ? "All assets" : e.all_assets === 0 ? "Specific assets" : '<span class="muted">Not stated</span>'}</td>
      <td class="act"><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">View filing</a></td></tr>`).join("");
  const agentAnswer = it.source === "agent" && it.category && it.category !== "unknown" ? it.category : null;
  return `
    <div class="facts"><span class="pill" data-cat="${state[1]}">${state[0]}</span><span>${plural(it.family_charges || it.charges, "charge")}</span>${it.property_only ? '<span class="pill" data-cat="property_lender">Only lends to property companies</span>' : ""}${it.latest ? `<span>Latest filing ${fmtDate(it.latest)}</span>` : ""}</div>
    <h2 class="lender-name">${esc(it.display)}</h2>
    <div class="research" style="margin-top:-10px">${lenderLinks(it.display)}</div>
    ${it.opinions && it.opinions.length > 1 ? opinionsBox(it) : agent}
    ${profileBox(it.profile, "#parent")}
    ${fam}
    <div data-cat="${cat}">${historyStrip(it.history)}</div>
    <div class="evidence"><h3>Charges in their favour</h3>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Borrower</th><th class="num">Delivered</th><th>Other chargees</th><th>Security</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    </div>
    <h3>What kind of lender is this?</h3>
    <div class="picker" role="group" aria-label="Lender category">${S.choices.map((c, i) => `
      <button type="button" class="choice" data-key="${c.key}" data-cat="${c.key}" aria-pressed="${pick.category === c.key}">
        <span class="top"><span class="nm">${esc(c.label)}</span><span class="key" aria-hidden="true">${i + 1}</span></span>
        <span class="help">${esc(c.help)}</span>${agentAnswer === c.key ? `<span class="agent-pick">The agent's answer</span>` : ""}
      </button>`).join("")}</div>
    <div class="decide-row">
      <label class="field">Parent firm, if it's part of a group<input class="input" id="parent" value="${esc(pick.parent)}" placeholder="For example, the fund manager behind a fund vehicle" maxlength="100"></label>
      <button class="btn primary" id="save" ${pick.category ? "" : "disabled"}>Save decision</button>
      ${agentAnswer ? `<button class="btn soft" id="approve">Approve the agent's answer</button>` : ""}
      <button class="btn ghost" id="skip">Skip for now</button>
    </div>
    <p class="hint">Keys: <span class="key">1</span>–<span class="key">${S.choices.length}</span> pick a type, <span class="key">Enter</span> save${agentAnswer ? `, <span class="key">A</span> approve the agent's answer` : ""}, <span class="key">J</span> and <span class="key">K</span> move through the list.</p>`;
}
function queueList(L) {
  const item = (x) => `<button class="q" data-name="${esc(x.name)}" data-cat="${x.source === "agent" ? catKey(x.category) : "unknown"}" aria-current="${x.name === S.current}"><span>${esc(x.display)}${x.members && x.members.length > 1 ? ` <span class="muted">+${x.members.length - 1}</span>` : ""}</span><span class="n num">${x.family_charges || x.charges}</span></button>`;
  const group = (title, list, extra = "") => !list.length ? "" : `<h3>${title}</h3>${extra}${list.map(item).join("")}`;
  return group(`Needs your call, ${L.review.length}`, L.review)
    + group(`Agent's answer to check, ${L.confirm.length}`, L.confirm, L.confirm.length > 1 ? `<div class="bulk"><button class="btn soft" id="approve-all">Approve all ${L.confirm.length}</button></div>` : "")
    + (L.low.length ? `<h3>Low priority, ${L.low.length}</h3><div class="bulk"><button class="btn soft" id="to-triage">Clear in Quick triage</button></div>` : "")
    + (L.waiting.length ? `<h3>Not classified yet, ${L.waiting.length}</h3><p class="muted small" style="padding:0 12px 8px">The classifier agent will look at these on its next run.</p>` : "");
}
function wireDecision(it) {
  main.querySelectorAll(".choice").forEach((b) => (b.onclick = () => choose(it, b.dataset.key)));
  main.querySelectorAll(".queue button[data-name]").forEach((b) => (b.onclick = () => { S.current = b.dataset.name; showCurrent(); }));
  const parent = document.getElementById("parent");
  parent.oninput = () => (S.picks[it.name].parent = parent.value);
  parent.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); save(it); } };
  document.getElementById("save").onclick = () => save(it);
  const ap = document.getElementById("approve");
  if (ap) ap.onclick = () => approve(familyNames(it), it);
  document.getElementById("skip").onclick = () => step(1);
  const fa = document.getElementById("fam-all"), ff = document.getElementById("fam-future");
  if (fa) fa.onchange = () => (S.picks[it.name].all = fa.checked);
  if (ff) ff.onchange = () => (S.picks[it.name].future = ff.checked);
  const all = document.getElementById("approve-all");
  if (all) all.onclick = () => {
    const names = S.decisions.lenders.confirm.flatMap((x) => familyNames(x, true));
    if (confirm(`Approve the agent's answer for all ${names.length} names in this list?`)) approve(names);
  };
  const tt = document.getElementById("to-triage");
  if (tt) tt.onclick = () => { S.decTab = "triage"; renderDecisions().then(() => fadeIn(document.getElementById("dec-body"))); };
}
const familyNames = (it, force) => (it.members && (force || (S.picks[it.name] || {}).all !== false) ? it.members.map((m) => m.name) : [it.name]);
// Switching lender swaps only the decision card, with a short slide, so the queue stays put.
function showCurrent() {
  const it = queueItems().find((x) => x.name === S.current);
  const card = document.getElementById("decision");
  if (!it || !card) return renderDecisions();
  card.innerHTML = decisionCard(it);
  main.querySelectorAll(".queue button[data-name]").forEach((b) => b.setAttribute("aria-current", b.dataset.name === S.current));
  wireDecision(it);
  card.classList.remove("enter"); void card.offsetWidth; card.classList.add("enter");
  if (window.innerWidth < 1100) card.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
}
function choose(it, key) {
  S.picks[it.name].category = key;
  main.querySelectorAll(".choice").forEach((b) => b.setAttribute("aria-pressed", b.dataset.key === key));
  document.getElementById("save").disabled = false;
}
function step(dir) {
  const items = queueItems().filter((x) => x.group === "review" || x.group === "confirm");
  const list = items.length ? items : queueItems();
  const i = list.findIndex((x) => x.name === S.current);
  if (!list.length) return;
  S.current = list[(i + dir + list.length) % list.length].name;
  showCurrent();
}
async function save(it) {
  const pick = S.picks[it.name];
  if (!pick || !pick.category) return toast("Pick a type first", true);
  const names = familyNames(it);
  try {
    await post("/api/decide", { name: it.name, category: pick.category, parent: pick.parent,
      also: names.filter((n) => n !== it.name), future: it.family && pick.future ? it.family : "" });
    toast(`Saved: ${names.length > 1 ? `${it.display} and ${plural(names.length - 1, "variant")}` : it.display} ${names.length > 1 ? "are" : "is"} ${labelFor(pick.category).toLowerCase()}`);
    advanceFrom(it.name);
  } catch (e) { toast(e.message, true); }
}
async function approve(names, it) {
  try {
    const r = await post("/api/approve", { names });
    toast(r.approved.length === 1 && it ? `Approved: ${it.display} is ${labelFor(it.category).toLowerCase()}` : `Approved ${plural(r.approved.length, "name")}`);
    advanceFrom(it ? it.name : names[names.length - 1]);
  } catch (e) { toast(e.message, true); }
}
async function advanceFrom(name) {
  const items = queueItems();
  const i = items.findIndex((x) => x.name === name);
  const next = items.slice(i + 1).find((x) => x.name !== name && (x.group === "review" || x.group === "confirm"))
    || items.find((x) => x.name !== name && (x.group === "review" || x.group === "confirm"));
  delete S.picks[name];
  S.current = next ? next.name : null;
  await renderDecisions();
  const card = document.getElementById("decision");
  if (card) { card.classList.add("enter"); }
}
document.addEventListener("keydown", (e) => {
  if (S.view !== "decisions" || S.decTab !== "lenders" || isTyping() || e.ctrlKey || e.metaKey || e.altKey) return;
  if (document.getElementById("drawer").classList.contains("open") || document.body.classList.contains("menu-open")) return;
  const it = S.decisions && queueItems().find((x) => x.name === S.current);
  if (!it) return;
  if (/^[1-9]$/.test(e.key) && S.choices[+e.key - 1]) { choose(it, S.choices[+e.key - 1].key); e.preventDefault(); }
  else if (e.key === "Enter" && document.activeElement.tagName !== "BUTTON" && document.activeElement.tagName !== "A" && document.activeElement.tagName !== "INPUT") { save(it); e.preventDefault(); }
  else if (e.key.toLowerCase() === "a" && document.getElementById("approve")) { approve(familyNames(it), it); e.preventDefault(); }
  else if (e.key.toLowerCase() === "s" || e.key.toLowerCase() === "j" || e.key === "ArrowDown") { step(1); e.preventDefault(); }
  else if (e.key.toLowerCase() === "k" || e.key === "ArrowUp") { step(-1); e.preventDefault(); }
});

// ------------------------------------------------------------------ quick triage
// Low-priority names in one table, each with a suggested type already picked: property-only
// lenders as property lenders, otherwise the agent's guess. Untick or change any, then save all.
function triageSuggestion(x) {
  const rel = x.profile && x.profile.related;
  if (rel && rel.checked && rel.linked.length === rel.checked) return "other";  // funding inside a group
  const guess = x.category && x.category !== "unknown" ? x.category : "";
  if (x.property_only && (!guess || guess === "direct_lender")) return "property_lender";
  return guess;
}
const TRIAGE_MODES = {
  triage: {
    store: "triage", empty: ["Nothing to triage", "Low-stakes names land here: lenders that only lend on property, and unsure guesses of types that never reach the headline."],
    title: "Quick triage",
    intro: "None of these can change the headline: they only lend to property companies, or the agent's guess is a type that never reaches it. Each row has a suggestion picked. Check the ones you're unsure of, then save them all at once.",
    suggest: triageSuggestion, on: (x) => !!triageSuggestion(x), verb: "Save",
  },
  auto: {
    store: "dbl", empty: ["Nothing to double-check", "When the agent is sure enough about a low-stakes type (60% or more), its answer is used straight away and listed here so you can check it."],
    title: "Double-check the agent's answers",
    intro: "The agent was sure enough of these to use its answer without asking you, least sure first. Nothing here is waiting on you. Tick the ones you've checked, or change the type, and save: they become your decisions and won't show here again.",
    suggest: (x) => x.category || "", on: () => false, verb: "Confirm",
  },
};
function renderTriage(body, low, mode = "triage") {
  const M = TRIAGE_MODES[mode];
  if (!low.length) {
    body.innerHTML = `<div class="card empty"><span class="disc" data-cat="bank">${icon("check")}</span><h2>${M.empty[0]}</h2><p>${M.empty[1]}</p></div>`;
    return;
  }
  const st = (S[M.store] = S[M.store] || {});
  for (const x of low) if (!(x.name in st)) st[x.name] = { on: M.on(x), category: M.suggest(x) };
  const opts = (sel) => `<option value="">Choose…</option>` + S.choices.map((c) => `<option value="${c.key}" ${sel === c.key ? "selected" : ""}>${esc(c.label)}</option>`).join("");
  const draw = () => {
    const picked = low.filter((x) => st[x.name].on && st[x.name].category);
    const names = picked.reduce((n, x) => n + (x.members ? x.members.length : 1), 0);
    const changed = picked.filter((x) => st[x.name].category !== x.category).length;
    body.innerHTML = `<section class="card">
      <div class="card-head"><div><h2>${M.title}</h2><p>${M.intro}</p></div>
        <div class="doc-actions"><button class="btn ghost" id="tr-all">Tick all</button><button class="btn ghost" id="tr-none">Untick all</button>
        <button class="btn primary" id="tr-save" ${picked.length ? "" : "disabled"}>${M.verb} ${plural(names, "name")}${mode === "auto" && changed ? `, ${changed} changed` : ""}</button></div></div>
      <div class="table-wrap"><table class="stack triage"><thead><tr><th></th><th>Lender</th><th>Agent's ${mode === "auto" ? "answer" : "guess"}</th><th>Borrowers</th><th>${mode === "auto" ? "Type" : "File as"}</th></tr></thead><tbody>
      ${low.map((x) => { const t = st[x.name]; const diff = mode === "auto" && t.category && t.category !== x.category; return `<tr data-row="${esc(x.name)}" class="${t.on ? "" : "off"}${diff ? " changed" : ""}">
        <td class="side-fact"><input type="checkbox" class="tr-on" ${t.on ? "checked" : ""} aria-label="Include ${esc(x.display)}"></td>
        <td class="lead"><button class="linkish strong" data-lender="${esc(x.name)}">${esc(x.display)}</button>${x.members && x.members.length > 1 ? `<span class="sub">and ${plural(x.members.length - 1, "variant")}: ${x.members.slice(1).map((m) => esc(m.display)).join(", ")}</span>` : ""}<span class="sub">${plural(x.family_charges || x.charges, "charge")}${x.parent ? ` · part of ${esc(x.parent)}` : ""}</span><span class="sub">${lenderLinks(x.display)}</span></td>
        <td class="meta">${x.category ? catChip(x.category) : ""}${x.confidence != null ? `<span class="sub">${Math.round(x.confidence * 100)}% sure</span>` : ""}${x.consensus ? `<span class="sub">${consensusPill(x.consensus)}</span>` : ""}${x.profile && x.profile.group ? `<span class="sub">Owner group: <b>${esc(x.profile.group)}</b></span>` : ""}${x.reason ? `<span class="sub why-line">${esc(reasonText(x.reason, x.source))}</span>` : ""}</td>
        <td class="meta">${x.property_only ? '<span class="pill" data-cat="property_lender">Property only</span>' : '<span class="muted">Mixed or other</span>'}</td>
        <td class="meta"><select class="input tr-cat" aria-label="Type for ${esc(x.display)}">${opts(t.category)}</select>${diff ? `<span class="sub changed-note">Changed from the agent's answer</span>` : ""}</td></tr>`; }).join("")}
      </tbody></table></div></section>`;
    body.querySelectorAll("tr[data-row]").forEach((tr) => {
      const t = st[tr.dataset.row];
      tr.querySelector(".tr-on").onchange = (e) => { t.on = e.target.checked; draw(); };
      tr.querySelector(".tr-cat").onchange = (e) => { t.category = e.target.value; t.on = !!t.category; draw(); };
    });
    document.getElementById("tr-all").onclick = () => { for (const x of low) st[x.name].on = !!st[x.name].category; draw(); };
    document.getElementById("tr-none").onclick = () => { for (const x of low) st[x.name].on = false; draw(); };
    document.getElementById("tr-save").onclick = async () => {
      const byCat = {};
      for (const x of picked) (byCat[st[x.name].category] ||= []).push(...(x.members ? x.members.map((m) => m.name) : [x.name]));
      const btn = document.getElementById("tr-save");
      btn.disabled = true; btn.textContent = "Saving…";
      try {
        for (const [category, list] of Object.entries(byCat)) await post("/api/file-as", { names: list, category });
        for (const x of picked) delete st[x.name];
        toast(`${mode === "auto" ? "Confirmed" : "Saved"} ${plural(names, "name")}`);
        renderDecisions();
      } catch (e) { toast(e.message, true); draw(); }
    };
  };
  draw();
}

function renderMessages(body, messages) {
  if (!messages.length) {
    body.innerHTML = `<div class="card empty"><span class="disc" data-cat="message">${icon("mail")}</span><h2>No messages</h2><p>The agents leave a note here when something needs a person, like a week that looks wrong or a rule that seems off.</p></div>`;
    return;
  }
  body.innerHTML = messages.map((m) => `<article class="card doc">
      <div class="card-head"><div class="facts" style="margin:0"><span class="disc" data-cat="agent" style="width:38px;height:38px">${icon("agent")}</span><span>From the ${esc((m.name.match(/from-([a-z]+)/) || [, "agents"])[1])} agent, ${fmtTime(m.modified)}</span></div>
      <button class="btn soft" data-done="${esc(m.path)}">Mark as done</button></div>
      <div class="prose">${md(m.content)}</div></article>`).join("");
  body.querySelectorAll("[data-done]").forEach((b) => (b.onclick = async () => {
    const card = b.closest(".doc");
    try {
      await post("/api/message/done", { path: b.dataset.done });
      toast("Marked as done");
      if (!reduced()) { card.classList.add("leave"); await new Promise((r) => setTimeout(r, 140)); }
      renderDecisions();
    } catch (e) { toast(e.message, true); }
  }));
}
function renderDraftDecisions(body, drafts) {
  if (!drafts.length) {
    body.innerHTML = `<div class="card empty"><span class="disc" data-cat="draft">${icon("pen")}</span><h2>No drafts waiting</h2><p>The newsletter agent writes a draft once a fully covered week has been reviewed. The first full week runs from 26 October.</p></div>`;
    return;
  }
  body.innerHTML = drafts.map((d) => draftCard(d)).join("");
  wireDrafts(body, () => renderDecisions());
}

// ------------------------------------------------------------------ drafts (shared)
const STATUS_TEXT = { new: ["New draft", "review"], ready: ["Ready to publish", "direct_lender"], published: ["Published", "bank"] };
function draftCard(d) {
  const editing = S.editing === d.path;
  const week = d.name.replace(/-draft\.md$/, "").split("_");
  const st = STATUS_TEXT[d.status] || [d.status, "unknown"];
  return `<article class="card doc" data-path="${esc(d.path)}">
    <div class="card-head"><div><h2>Week of ${fmtDate(week[0])} to ${fmtDate(week[1])}</h2>
      <div class="facts" style="margin:6px 0 0"><span class="pill" data-cat="${st[1]}">${st[0]}</span><span>Updated ${fmtTime(d.modified)}</span></div></div>
      <div class="doc-actions">${editing
        ? `<button class="btn primary" data-act="save">Save draft</button><button class="btn ghost" data-act="cancel">Cancel</button>`
        : `<button class="btn" data-act="edit">Edit</button>
           ${d.status === "new" ? `<button class="btn soft" data-act="ready">Mark ready</button>` : ""}
           ${d.status !== "published" ? `<button class="btn primary" data-act="published">Mark published</button>` : `<button class="btn ghost" data-act="new">Undo published</button>`}`}
      </div></div>
    ${editing ? `<textarea class="editor" aria-label="Draft text">${esc(d.content)}</textarea>` : `<div class="prose">${md(d.content)}</div>`}
  </article>`;
}
function wireDrafts(root, rerender) {
  root.querySelectorAll("article[data-path]").forEach((card) => {
    const path = card.dataset.path;
    card.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      const act = b.dataset.act;
      try {
        if (act === "edit") { S.editing = path; await rerender(); return; }
        if (act === "cancel") { S.editing = null; await rerender(); return; }
        if (act === "save") {
          await post("/api/draft/save", { path, content: card.querySelector("textarea").value });
          S.editing = null; toast("Draft saved"); await rerender(); return;
        }
        await post("/api/draft/status", { path, status: act });
        toast(act === "published" ? "Marked as published" : act === "ready" ? "Marked ready to publish" : "Marked as a new draft");
        await rerender();
      } catch (e) { toast(e.message, true); }
    }));
  });
}

// ------------------------------------------------------------------ tier bar (Charges and Status)
function tierBar(counts, opts = {}) {
  const total = Object.values(counts || {}).reduce((a, n) => a + n, 0);
  if (!total) return "";
  const seg = Object.entries(counts).filter(([, n]) => n).map(([t, n]) => `<span class="t-${t}" style="flex:${n}" title="Tier ${t}: ${n}"></span>`).join("");
  const legend = Object.entries(counts).map(([t, n]) => `<button type="button" ${opts.clickable ? `data-tierpick="${t}"` : "disabled"} aria-pressed="${opts.selected === t}"><span class="sw t-${t}"></span>${opts.short ? t : esc(TIER_LABELS[t])} <b class="num">${n}</b></button>`).join("");
  return `<div class="tierbar"><div class="bar" role="img" aria-label="${Object.entries(counts).map(([t, n]) => `Tier ${t}: ${n}`).join(", ")}">${seg}</div>
    ${opts.legend === false ? "" : `<div class="legend">${legend}</div>`}</div>`;
}

// ------------------------------------------------------------------ charges
async function renderCharges() {
  const q = { q: "", tier: "", check: "", from: "", to: "", status: "", category: "", sector: "", ...S.chargesQuery };
  S.chargesQuery = q;
  document.getElementById("search-q").value = q.q;
  const params = new URLSearchParams({ q: q.q, tier: q.tier, check: q.check, from: q.from, to: q.to, status: q.status, category: q.category, sector: q.sector, hide_property: S.hideProperty ? "1" : "", limit: 100, offset: 0 });
  const [data, tierA] = await Promise.all([get("/api/charges?" + params), get("/api/charges?tier=A&limit=1")]);
  if (!S.choices.length) await loadDecisions();
  const rowsHtml = (rows) => rows.map((r) => `<tr>
      <td class="side-fact">${tierBadge(r.tier)}</td>
      <td class="lead"><button class="linkish strong" data-company="${esc(r.company_number)}">${coName(r.company_name, r.company_number)}</button><span class="sub"><span class="id">${esc(r.company_number)}</span> · ${esc(r.sector_label)}</span></td>
      <td class="meta">${r.chargees.map((c) => `<button class="linkish" data-lender="${esc(c.name)}">${esc(c.display)}</button>`).join("<br>")}</td>
      <td class="meta">${catChip(r.primary, r.primary_label)}</td>
      <td class="num meta">${isoDate(r.delivered_on)}</td>
      <td class="meta">${statusPill(r.status)}${r.satisfied_on ? `<span class="sub">${isoDate(r.satisfied_on)}</span>` : ""}</td>
      <td class="meta">${r.needs_check ? '<span class="pill" data-cat="review">Check</span>' : '<span class="pill" data-cat="bank">Confirmed</span>'}</td>
      <td class="act"><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("");
  const sectorLabel = (data.sector_counts.find((x) => x.key === q.sector) || {}).label;
  const active = [q.q && `matching “${q.q}”`, q.category && `with ${labelFor(q.category).toLowerCase()} lenders`,
    q.sector && `in ${sectorLabel ? sectorLabel.toLowerCase() : q.sector}`, S.hideProperty && "leaving out property companies", q.status === "repaid" && "that are repaid",
    q.from && (q.from === q.to ? `delivered on ${fmtDate(q.from)}` : `delivered from ${fmtDate(q.from)}${q.to ? ` to ${fmtDate(q.to)}` : ""}`),
    !q.from && q.to && `delivered up to ${fmtDate(q.to)}`, q.check === "yes" && "needing a check", q.check === "no" && "confirmed by you"].filter(Boolean);
  setSub(`${plural(tierA.total, "charge")} in favour of direct lenders on the register`);
  main.innerHTML = `<div class="anim-root">
    <div class="page-intro"><div><h1>${q.tier ? `Tier ${q.tier}: ${esc(TIER_LABELS[q.tier])}` : "All charges"}</h1>
      <p><b>${plural(data.total, "charge")}</b>${active.length ? " " + active.join(", ") : " on the register"}.${active.length || q.tier ? ` <button class="link-btn" id="clear-f">Clear filters</button>` : ""}</p></div></div>
    <section class="card">
      ${tierBar(data.tier_counts, { clickable: true, selected: q.tier })}
      <form class="filters" id="cf">
        <label class="field">Search<input type="search" name="q" value="${esc(q.q)}" placeholder="Borrower, company number or lender"></label>
        <label class="field">Lender type<select name="category"><option value="">All types</option>${CATEGORY_KEYS.map((k) => `<option value="${k}" ${q.category === k ? "selected" : ""}>${esc(CAT_LABELS[k])}</option>`).join("")}</select></label>
        <label class="field">Borrower sector<select name="sector"><option value="">All sectors</option>${data.sector_counts.map((x) => `<option value="${x.key}" ${q.sector === x.key ? "selected" : ""} ${x.count || q.sector === x.key ? "" : "disabled"}>${esc(x.label)} (${x.count})</option>`).join("")}</select></label>
        <label class="field">Status<select name="status"><option value="">All</option><option value="outstanding" ${q.status === "outstanding" ? "selected" : ""}>Outstanding</option><option value="repaid" ${q.status === "repaid" ? "selected" : ""}>Repaid</option></select></label>
        <label class="field">Checked<select name="check"><option value="">All</option><option value="yes" ${q.check === "yes" ? "selected" : ""}>Needs a check</option><option value="no" ${q.check === "no" ? "selected" : ""}>Confirmed by you</option></select></label>
        <div class="field">Delivered between<span class="range"><input type="date" name="from" value="${esc(q.from)}" aria-label="Delivered from"><span>and</span><input type="date" name="to" value="${esc(q.to)}" aria-label="Delivered to"></span></div>
        <button class="btn soft" type="submit">Apply</button>
        <div class="toggles"><label class="toggle"><input type="checkbox" id="c-hideprop" ${S.hideProperty ? "checked" : ""}><span></span>Hide property companies</label></div>
      </form>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Tier</th><th>Borrower</th><th>Chargees</th><th>Lender type</th><th class="num">Delivered</th><th>Status</th><th>Checked</th><th></th></tr></thead>
        <tbody id="crows">${rowsHtml(data.rows) || `<tr><td colspan="8"><div class="empty"><p>No charges match these filters.</p></div></td></tr>`}</tbody></table></div>
      ${data.total > data.rows.length ? `<div class="more-row"><button class="btn soft" id="cmore">Show ${Math.min(100, data.total - data.rows.length)} more of ${data.total - data.rows.length}</button></div>` : ""}
    </section></div>`;
  const form = document.getElementById("cf");
  form.onsubmit = (e) => { e.preventDefault(); Object.assign(q, Object.fromEntries(new FormData(form))); renderCharges().then(() => fadeIn(document.getElementById("crows"))); };
  form.querySelectorAll("select").forEach((s) => (s.onchange = () => form.requestSubmit()));
  document.getElementById("c-hideprop").onchange = (e) => { setHideProperty(e.target.checked); renderCharges().then(() => fadeIn(document.getElementById("crows"))); };
  main.querySelectorAll("[data-tierpick]").forEach((b) => (b.onclick = () => { q.tier = q.tier === b.dataset.tierpick ? "" : b.dataset.tierpick; renderCharges().then(() => fadeIn(document.getElementById("crows"))); }));
  const clear = document.getElementById("clear-f");
  if (clear) clear.onclick = () => { setHideProperty(false); goCharges({}); };
  let offset = data.rows.length;
  const more = document.getElementById("cmore");
  if (more) more.onclick = async () => {
    params.set("offset", offset);
    const next = await get("/api/charges?" + params);
    document.getElementById("crows").insertAdjacentHTML("beforeend", rowsHtml(next.rows));
    offset += next.rows.length;
      if (offset >= next.total) more.parentElement.remove(); else more.textContent = `Show ${Math.min(100, next.total - offset)} more of ${next.total - offset}`;
  };
}

// ------------------------------------------------------------------ research links
const CH = "https://find-and-update.company-information.service.gov.uk";
// "Ares Management Limited (As Security Agent)" -> "Ares Management Limited", for searching.
const searchName = (n) => String(n || "").replace(/\s*\([^)]*\)\s*/g, " ").replace(/\s+as\s+(security\s+)?(agent|trustee)\b.*$/i, "").trim();
const ext = (href, label, title) => `<a class="rlink" href="${esc(href)}" target="_blank" rel="noopener" title="${esc(title || label)}">${esc(label)}</a>`;
function lenderLinks(display) {
  const n = encodeURIComponent(searchName(display));
  return `<span class="rlinks">${ext(`${CH}/search/companies?q=${n}`, "Companies House", "Search Companies House for this lender")}${ext(`https://www.google.com/search?q=%22${n}%22`, "Google")}${ext(`https://www.linkedin.com/search/results/companies/?keywords=${n}`, "LinkedIn")}</span>`;
}
function companyLinks(number, name) {
  const n = encodeURIComponent(number);
  return `<span class="rlinks">${ext(`${CH}/company/${n}`, "Profile", "Companies House profile")}${ext(`${CH}/company/${n}/charges`, "All charges")}${ext(`${CH}/company/${n}/filing-history`, "Filing history")}${ext(`https://www.google.com/search?q=%22${encodeURIComponent(name || number)}%22`, "Google")}</span>`;
}

// ------------------------------------------------------------------ signals
const SIG = {
  refinanced: ["agent", "refi"], maturity: ["review", "clock"], alongside: ["security_agent", "layers"],
  top_up: ["asset_finance", "plus"], first_loan: ["bank", "star"], returning: ["direct_lender", "refi"], repaid: ["unknown", "check"],
};
const sigCat = (t) => (SIG[t] || ["unknown"])[0];
const sigIcon = (t) => icon((SIG[t] || [, "signals"])[1]);
function evidenceTable(charges) {
  return `<div class="table-wrap"><table class="stack"><thead><tr><th>Lender</th><th class="num">Created</th><th>Status</th><th></th></tr></thead><tbody>
    ${charges.map((c) => `<tr><td class="lead">${c.lenders.map((x) => `<span class="strong">${esc(x.display)}</span>`).join("<br>") || '<span class="muted">Not named</span>'}<span class="sub">${catChip(c.primary)}${c.charge_type ? ` ${esc(c.charge_type)}` : ""}</span></td>
      <td class="num side-fact">${isoDate(c.created_on || c.delivered_on)}</td>
      <td class="meta">${statusPill(c.status)}${c.satisfied_on ? `<span class="sub">${isoDate(c.satisfied_on)}</span>` : ""}</td>
      <td class="act"><a href="${esc(c.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")}
  </tbody></table></div>`;
}
function signalCard(s, opts = {}) {
  const cat = sigCat(s.type);
  return `<article class="card signal" data-cat="${cat}">
    <div class="sig-head"><span class="disc">${sigIcon(s.type)}</span>
      <div><div class="facts"><span class="pill" data-cat="${cat}">${esc(s.label)}</span>${s.private_credit ? '<span class="pill" data-cat="direct_lender">Private credit</span>' : ""}<span class="pill" data-cat="other">${esc(s.sector_label)}</span><span>${fmtDate(s.date)}</span></div>
      ${opts.noCompany ? "" : `<h3 class="sig-co"><button class="linkish" data-company="${esc(s.company_number)}">${esc(s.company_name)}</button> <span class="id muted">${esc(s.company_number)}</span></h3>`}</div></div>
    <p class="meaning">${esc(s.meaning)}</p>
    <details class="behind"><summary>The ${plural(s.charges.length, "filing")} behind this</summary>${evidenceTable(s.charges)}</details>
  </article>`;
}
const THEME_ICON = { new: "plus", ahead: "clock", repaid: "check" };
const THEME_CAT = { new: "direct_lender", ahead: "review", repaid: "unknown" };
const PERIODS = [["", "Any time"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last 12 months"]];
// Filter the signals by everything except `skip`, so each control can count what picking it would show.
function sigFilter(rows, f, skip) {
  const needle = f.q.trim().toLowerCase();
  const since = f.period ? new Date(Date.now() - f.period * 864e5).toISOString().slice(0, 10) : "";
  return rows.filter((s) =>
    (skip === "theme" || !f.theme || s.theme === f.theme)
    && (skip === "type" || !f.type || s.type === f.type)
    && (skip === "sector" || !f.sector || s.sectors.includes(f.sector))
    && (skip === "lender" || !f.lender || s.lender_types.includes(f.lender))
    && (!since || (s.date || "") >= since)
    && (!f.pc || s.private_credit)
    && (!S.hideProperty || s.sector !== "real_estate")
    && (!needle || [s.company_name, s.company_number, s.meaning, ...s.charges.flatMap((c) => c.lenders.map((l) => l.display))].some((v) => (v || "").toLowerCase().includes(needle))));
}
const countBy = (rows, key) => rows.reduce((m, s) => { for (const k of [].concat(s[key])) m[k] = (m[k] || 0) + 1; return m; }, {});
// The signal list is large: fetch it once per data version and reuse it for every filter click.
let SIG_CACHE = null;
async function getSignals() {
  if (SIG_CACHE && SIG_CACHE.key === S.dataKey) return SIG_CACHE.d;
  const d = await get("/api/signals");
  SIG_CACHE = { key: S.dataKey, d };
  return d;
}
const SIG_PAGE = 120;
async function renderSignals(keepShown = false) {
  const d = await getSignals();
  if (!keepShown) S.sigShown = SIG_PAGE;
  document.getElementById("nav-signals").textContent = d.total || "";
  const f = S.sigF;
  const rows = sigFilter(d.rows, f);
  if (f.sort === "old") rows.reverse();
  const themeN = countBy(sigFilter(d.rows, f, "theme"), "theme");
  const typeN = countBy(sigFilter(d.rows, f, "type"), "type");
  const sectorN = countBy(sigFilter(d.rows, f, "sector"), "sectors");
  const lenderN = countBy(sigFilter(d.rows, f, "lender"), "lender_types");
  const allN = sigFilter(d.rows, f, "theme").length;
  const active = f.type || f.sector || f.lender || f.period || f.pc || f.q || S.hideProperty;
  const pc = d.rows.filter((s) => s.private_credit).length;
  setSub(`${plural(d.total, "signal")}, ${pc} involving private credit`);
  const missing = d.companies - d.histories;
  main.innerHTML = `<div class="anim-root">
    <div class="page-intro"><div><h1>What the filings suggest</h1>
      <p>Each signal reads a borrower's charges, old and new, and says in plain English what the pattern usually means. They are leads to check, not facts: open the filings behind any of them.</p></div></div>
    ${missing > 0 ? `<div class="notice">${icon("clock")}<div>The radar has fetched the older charges of ${d.histories} of ${plural(d.companies, "borrower")}. Refinancing and first-loan signals need that history, so more will appear after the next cloud runs.</div></div>` : ""}
    <div class="qtiles themes" role="tablist" aria-label="Signal groups">
      <button class="qtile" role="tab" data-cat="cloud" data-theme="" aria-selected="${!f.theme}"><span class="disc">${icon("signals")}</span><span class="qn num">${allN}</span><span class="ql">All signals</span><span class="qs">Everything below, filtered</span></button>
      ${d.themes.map((t) => `<button class="qtile" role="tab" data-cat="${THEME_CAT[t.key]}" data-theme="${t.key}" aria-selected="${f.theme === t.key}"><span class="disc">${icon(THEME_ICON[t.key])}</span><span class="qn num">${themeN[t.key] || 0}</span><span class="ql">${esc(t.label)}</span><span class="qs">${esc(t.help)}</span></button>`).join("")}
    </div>
    <section class="card filterbar" aria-label="Filters">
      <div class="chips" role="group" aria-label="Signal type">
        <span class="fb-label">Type</span>
        <button class="chip" data-type="" aria-pressed="${!f.type}">Any</button>
        ${d.types.filter((t) => !f.theme || t.theme === f.theme).map((t) => `<button class="chip" data-cat="${sigCat(t.key)}" data-type="${t.key}" aria-pressed="${f.type === t.key}" title="${esc(t.help)}" ${typeN[t.key] || f.type === t.key ? "" : "disabled"}><span class="sw"></span>${esc(t.label)} <b class="num">${typeN[t.key] || 0}</b></button>`).join("")}
      </div>
      <form class="filters" id="sf">
        <label class="field">Search<input type="search" name="q" value="${esc(f.q)}" placeholder="Company, lender or words in the signal"></label>
        <label class="field">Borrower sector<select name="sector"><option value="">All sectors</option>${d.sectors.map((x) => `<option value="${x.key}" ${f.sector === x.key ? "selected" : ""} ${sectorN[x.key] || f.sector === x.key ? "" : "disabled"}>${esc(x.label)} (${sectorN[x.key] || 0})</option>`).join("")}</select></label>
        <label class="field">Lender type<select name="lender"><option value="">All types</option>${d.lender_types.map((x) => `<option value="${x.key}" ${f.lender === x.key ? "selected" : ""} ${lenderN[x.key] || f.lender === x.key ? "" : "disabled"}>${esc(x.label)} (${lenderN[x.key] || 0})</option>`).join("")}</select></label>
        <label class="field">When<select name="period">${PERIODS.map(([v, l]) => `<option value="${v}" ${String(f.period) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field">Order<select name="sort"><option value="new" ${f.sort !== "old" ? "selected" : ""}>Private credit first, newest</option><option value="old" ${f.sort === "old" ? "selected" : ""}>Oldest first</option></select></label>
        <div class="toggles">
          <label class="toggle"><input type="checkbox" name="pc" ${f.pc ? "checked" : ""}><span></span>Private credit only</label>
          <label class="toggle"><input type="checkbox" name="hide_property" ${S.hideProperty ? "checked" : ""}><span></span>Hide property companies</label>
        </div>
      </form>
      <div class="fb-foot"><span>Showing <b>${rows.length}</b> of ${plural(d.total, "signal")}</span>${active || f.theme ? `<button class="link-btn" id="sf-clear">Clear filters</button>` : ""}</div>
    </section>
    <div id="sig-list" class="sig-list">${rows.slice(0, S.sigShown).map((s) => signalCard(s)).join("") || `<div class="card empty"><span class="disc" data-cat="cloud">${icon("signals")}</span><h2>${d.total ? "No signals match these filters" : "No signals yet"}</h2><p>${d.total ? "Try another group or clear the filters." : "Signals appear as the radar collects charges and fetches each borrower's older ones."}</p></div>`}</div>
    ${rows.length > S.sigShown ? `<div class="more-row"><button class="btn" id="sig-more">Show ${Math.min(SIG_PAGE, rows.length - S.sigShown)} more <span class="muted">(${rows.length - S.sigShown} not shown yet)</span></button></div>` : ""}
  </div>`;
  const again = () => renderSignals().then(() => fadeIn(document.getElementById("sig-list")));
  const more = document.getElementById("sig-more");
  if (more) more.onclick = () => { const y = window.scrollY; S.sigShown += SIG_PAGE; renderSignals(true).then(() => window.scrollTo(0, y)); };
  main.querySelectorAll("[data-theme]").forEach((b) => (b.onclick = () => { f.theme = b.dataset.theme; if (f.type && f.theme && THEME_OF_TYPE(d, f.type) !== f.theme) f.type = ""; again(); }));
  main.querySelectorAll("[data-type]").forEach((b) => (b.onclick = () => { f.type = b.dataset.type; again(); }));
  const form = document.getElementById("sf");
  form.querySelectorAll("select").forEach((el) => (el.onchange = () => { f[el.name] = el.value; again(); }));
  form.querySelector("[name=pc]").onchange = (e) => { f.pc = e.target.checked; again(); };
  form.querySelector("[name=hide_property]").onchange = (e) => { setHideProperty(e.target.checked); again(); };
  let t;
  form.querySelector("[name=q]").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { f.q = e.target.value; renderSignals().then(() => { const i = document.querySelector("#sf [name=q]"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 250); };
  form.onsubmit = (e) => e.preventDefault();
  const clear = document.getElementById("sf-clear");
  if (clear) clear.onclick = () => { S.sigF = { ...SIG_DEFAULT }; setHideProperty(false); again(); };
}
const THEME_OF_TYPE = (d, type) => (d.types.find((t) => t.key === type) || {}).theme;
const SIG_DEFAULT = { theme: "", type: "", sector: "", lender: "", period: "", pc: false, q: "", sort: "new" };

// ------------------------------------------------------------------ drawer (company and lender panels)
// Panels stack: open a lender from a company (or the reverse) and Back returns to the one before.
const drawer = document.getElementById("drawer"), drawerBody = document.getElementById("drawer-body");
const drawerStack = [];
async function pushPanel(panel) {
  try {
    const html = await panel.render();
    drawerStack.push(panel);
    showPanel(html, panel);
  } catch (e) { toast(e.message, true); }
}
function showPanel(html, panel) {
  drawerBody.innerHTML = (drawerStack.length > 1 ? `<button class="btn ghost back" id="drawer-back">${icon("back")}Back to ${esc(drawerStack[drawerStack.length - 2].title)}</button>` : "") + html;
  panel.wire && panel.wire();
  const back = document.getElementById("drawer-back");
  if (back) back.onclick = async () => { drawerStack.pop(); const prev = drawerStack[drawerStack.length - 1]; showPanel(await prev.render(), prev); };
  drawer.querySelector(".drawer-inner").scrollTop = 0;
  fadeIn(drawerBody);
  if (!drawer.classList.contains("open")) {
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    document.getElementById("scrim").classList.add("on");
    setTimeout(() => document.getElementById("drawer-close").focus(), 50);
  }
}
function closeDrawer() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  drawerStack.length = 0;
  if (!document.body.classList.contains("menu-open")) document.getElementById("scrim").classList.remove("on");
}
document.getElementById("drawer-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); setMenu(false); } });
// Any borrower or lender name anywhere in the app opens its panel.
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-company], [data-lender]");
  if (!el || e.target.closest("a")) return;
  e.preventDefault();
  if (el.dataset.company) openCompany(el.dataset.company); else openLender(el.dataset.lender);
});
document.addEventListener("keydown", (e) => {
  const el = e.target.closest && e.target.closest("tr[data-company], tr[data-lender]");
  if (el && e.key === "Enter") { e.preventDefault(); el.click(); }
});

function openCompany(number) {
  let title = number;
  return pushPanel({
    get title() { return title; },
    render: async () => {
      const c = await get("/api/company?number=" + encodeURIComponent(number));
      title = c.name || c.number;
      const strip = '<div data-cat="cloud">' + historyStrip(c.charges.slice().reverse().map((x) => ({ delivered_on: x.created_on || x.delivered_on, status: x.status }))) + "</div>";
      return `
        <div class="facts"><span class="pill" data-cat="${c.status === "active" ? "bank" : "unknown"}">${esc(c.status ? c.status[0].toUpperCase() + c.status.slice(1) : "Profile not fetched yet")}</span><span class="id">${esc(c.number)}</span>${c.created ? `<span>Incorporated ${fmtDate(c.created)}</span>` : ""}<span class="pill" data-cat="other">${esc(c.sector_label)}</span>${c.sic_codes.length ? `<span>SIC ${sic(c.sic_codes)}</span>` : ""}</div>
        <h2 class="lender-name" id="drawer-title">${esc(c.name || c.number)}</h2>
        <div class="research"><span class="muted small">Research</span>${companyLinks(c.number, c.name)}</div>
        <section class="card panel-card"><div class="card-head"><div><h2>What it means</h2><p>${c.signals.length ? "Read from the charges below." : "No pattern stands out in this company's charges."}</p></div></div>
          ${c.signals.length ? c.signals.map((s) => `<div class="sig-inline" data-cat="${sigCat(s.type)}"><span class="disc">${sigIcon(s.type)}</span><div><span class="pill" data-cat="${sigCat(s.type)}">${esc(s.label)}</span><p class="meaning">${esc(s.meaning)}</p></div></div>`).join("") : ""}
          ${c.history_fetched ? "" : `<p class="muted small">The radar hasn't fetched this company's older charges yet, so only charges seen on the stream are shown. The next cloud run fetches them.</p>`}
        </section>
        <section class="card panel-card"><div class="card-head"><div><h2>${plural(c.charges.length, "charge")}, ${c.outstanding} outstanding</h2><p>Newest first. Click a lender for its panel.</p></div></div>
          ${strip}
          <div class="table-wrap"><table class="stack"><thead><tr><th>Lender</th><th class="num">Created</th><th>Status</th><th>Seen</th><th></th></tr></thead><tbody>
          ${c.charges.map((x) => `<tr><td class="lead">${x.lenders.map((l) => l.known ? `<button class="linkish strong" data-lender="${esc(l.name)}">${esc(l.display)}</button>` : `<span class="strong">${esc(l.display)}</span>`).join("<br>") || '<span class="muted">Not named</span>'}<span class="sub">${catChip(x.primary, x.primary_label)}</span></td>
            <td class="num side-fact">${isoDate(x.created_on || x.delivered_on)}</td>
            <td class="meta">${statusPill(x.status)}${x.satisfied_on ? `<span class="sub">${isoDate(x.satisfied_on)}</span>` : ""}</td>
            <td class="meta">${x.on_register ? '<span class="pill" data-cat="cloud">On the radar</span>' : '<span class="muted">Older charge</span>'}</td>
            <td class="act"><a href="${esc(x.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")}
          </tbody></table></div></section>`;
    },
  });
}

function openLender(name) {
  let title = name, it = null;
  const pick = {};
  return pushPanel({
    get title() { return title; },
    render: async () => {
      if (!S.choices.length) await loadDecisions();
      it = await get("/api/lender?name=" + encodeURIComponent(name));
      title = it.display;
      pick.category = it.category && it.category !== "unknown" ? it.category : "";
      pick.parent = it.parent || "";
      return `
        <div class="facts">${it.category ? catChip(it.category) + `<span>${esc(it.source_label)}</span>` : '<span class="pill" data-cat="unknown">Not classified yet</span><span>The classifier agent will look at it on its next run</span>'}${it.confidence != null && it.source === "agent" ? `<span>${Math.round(it.confidence * 100)}% sure</span>` : ""}</div>
        <h2 class="lender-name" id="drawer-title">${esc(it.display)}</h2>
        <div class="research"><span class="muted small">Research</span>${lenderLinks(it.display)}${it.parent ? `<span class="muted small">Parent: <b>${esc(it.parent)}</b></span>` : ""}</div>
        ${profileBox(it.profile, "#dparent")}
        ${opinionsBox(it)}
        <div class="kpis mini-kpis">
          <div class="kpi-s"><span class="l">Borrowers</span><span class="v num">${it.borrowers}</span></div>
          <div class="kpi-s"><span class="l">Charges</span><span class="v num">${it.charges}</span></div>
          <div class="kpi-s"><span class="l">Outstanding</span><span class="v num">${it.outstanding}</span></div>
          <div class="kpi-s"><span class="l">Active</span><span class="v">${it.first ? it.first.slice(0, 4) : "—"}${it.latest && it.latest.slice(0, 4) !== (it.first || "").slice(0, 4) ? `–${it.latest.slice(0, 4)}` : ""}</span></div>
        </div>
        <section class="card panel-card"><div class="card-head"><div><h2>Your research notes</h2><p>Fund size, strategy, people, links. Saved to GitHub with your decisions.</p></div></div>
          <textarea class="editor note-box" id="lnote" placeholder="For example: mid-market sponsor lender, unitranche up to £150m, contact via ...">${esc(it.note || "")}</textarea>
          <div class="decide-row"><button class="btn soft" id="lnote-save">Save note</button></div></section>
        <section class="card panel-card">
          ${it.reason ? `<div class="agent" data-cat="${catKey(it.category)}"><span class="disc">${icon(it.source === "agent" ? "agent" : "check")}</span><div><p class="why">${esc(reasonText(it.reason, it.source))}</p></div></div>` : ""}
          <div data-cat="${catKey(it.category)}">${historyStrip(it.history)}</div>
          <h3>Change the type</h3>
          <div class="picker">${S.choices.map((c) => `<button type="button" class="choice" data-key="${c.key}" data-cat="${c.key}" aria-pressed="${pick.category === c.key}"><span class="top"><span class="nm">${esc(c.label)}</span></span><span class="help">${esc(c.help)}</span></button>`).join("")}</div>
          <div class="decide-row"><label class="field">Parent firm, if it's part of a group<input class="input" id="dparent" value="${esc(pick.parent)}" maxlength="100"></label>
            <button class="btn primary" id="dsave">Save decision</button></div>
        </section>
        <section class="card panel-card"><div class="card-head"><div><h2>${plural(it.charges, "charge")} in their favour</h2><p>Click a borrower for all of its charges and what they suggest.</p></div></div>
          <div class="table-wrap"><table class="stack"><thead><tr><th>Borrower</th><th class="num">Delivered</th><th>Other chargees</th><th>Status</th><th></th></tr></thead><tbody>
          ${it.examples.map((e) => `<tr><td class="lead"><button class="linkish strong" data-company="${esc(e.company_number)}">${coName(e.company_name, e.company_number)}</button><span class="sub"><span class="id">${esc(e.company_number)}</span>${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</span></td>
            <td class="num side-fact">${isoDate(e.delivered_on)}</td><td class="meta">${e.other_chargees.map(esc).join("; ") || '<span class="muted">None</span>'}</td>
            <td class="meta">${statusPill(e.status)}</td><td class="act"><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")}
          </tbody></table></div></section>`;
    },
    wire: () => {
      drawerBody.querySelectorAll(".choice").forEach((b) => (b.onclick = () => {
        pick.category = b.dataset.key;
        drawerBody.querySelectorAll(".choice").forEach((x) => x.setAttribute("aria-pressed", x === b));
      }));
      document.getElementById("dparent").oninput = (e) => (pick.parent = e.target.value);
      document.getElementById("dsave").onclick = async () => {
        if (!pick.category) return toast("Pick a type first", true);
        try {
          await post("/api/decide", { name: it.name, category: pick.category, parent: pick.parent });
          toast(`Saved: ${it.display} is ${labelFor(pick.category).toLowerCase()}`);
          closeDrawer(); refresh();
        } catch (e) { toast(e.message, true); }
      };
      document.getElementById("lnote-save").onclick = async () => {
        try {
          await post("/api/lender/note", { name: it.name, note: document.getElementById("lnote").value });
          toast("Note saved");
          if (S.view === "lenders") refresh();
        } catch (e) { toast(e.message, true); }
      };
    },
  });
}

// ------------------------------------------------------------------ lender directory
const DIR_STATUS = { needs_review: ["Needs your call", "review"], unclassified: ["Waiting for the agent", "unknown"], ok: ["Decided by you", "bank"], auto_ok: ["Not checked by you", "direct_lender"] };
async function renderLenders() {
  if (!S.choices.length) await loadDecisions();
  const d = await get("/api/directory");
  const q = S.lendersQuery;
  const catOf = (r) => r.category || "unclassified";
  const needle = q.q.trim().toLowerCase();
  const rows = d.rows.filter((r) => (!q.category || catOf(r) === q.category) && (!q.status || r.status === q.status)
    && (!q.sector || r.sectors.some((x) => x.key === q.sector))
    && (!needle || [r.display, r.parent, r.note, r.group].some((v) => (v || "").toLowerCase().includes(needle))));
  const watch = d.watch.filter((w) => (!q.category || w.category === q.category) && (!needle || [w.pattern, w.parent, w.note].some((v) => (v || "").toLowerCase().includes(needle))));
  const direct = d.rows.filter((r) => r.category === "direct_lender").length;
  setSub(`${plural(d.total, "lender")} on the register, ${plural(direct, "direct lender")}`);
  const catLabel = q.category ? (d.categories.find((c) => c.key === q.category) || { label: CAT_LABELS[q.category] || q.category }).label : "All lenders";
  main.innerHTML = `<div class="anim-root">
    <div class="page-intro"><div><h1>Lender directory</h1><p>Every lender on the register by type, with research links and your notes. Click a lender for its panel: borrowers, every charge, notes.</p></div>
      <button class="btn soft" id="dir-csv">${icon("download")}Download CSV</button></div>
    <div class="dir">
      <nav class="card dir-cats" aria-label="Lender types">
        <button class="dcat" data-dcat="" aria-pressed="${!q.category}"><span class="sw" style="background:var(--brand)"></span><span>All lenders</span><b class="num">${d.total}</b></button>
        ${d.categories.map((c) => `<button class="dcat" data-cat="${c.key === "unclassified" ? "unknown" : c.key}" data-dcat="${c.key}" aria-pressed="${q.category === c.key}"><span class="sw"></span><span>${esc(c.label)}</span><b class="num">${c.count}</b></button>`).join("")}
        ${CATEGORY_KEYS.filter((k) => k !== "unknown" && !d.categories.find((c) => c.key === k)).map((k) => `<button class="dcat" data-cat="${k}" data-dcat="${k}" aria-pressed="${q.category === k}"><span class="sw"></span><span>${esc(CAT_LABELS[k])}</span><b class="num muted">0</b></button>`).join("")}
      </nav>
      <section class="card dir-main">
        <div class="card-head"><div><h2>${esc(catLabel)}</h2><p>${plural(rows.length, "lender")}${needle ? ` matching “${esc(q.q)}”` : ""}${q.sector ? `, lending to ${esc((d.sectors.find((x) => x.key === q.sector) || {}).label || "").toLowerCase()}` : ""}${q.q || q.sector || q.status ? ` <button class="link-btn" id="dir-clear">Clear</button>` : ""}</p></div>
          <form class="filters" id="lf" style="margin:0">
            <label class="field">Search<input type="search" name="q" value="${esc(q.q)}" placeholder="Lender, parent, group or note"></label>
            <label class="field">Lends to<select name="sector"><option value="">Any sector</option>${d.sectors.map((x) => { const n = d.rows.filter((r) => (!q.category || catOf(r) === q.category) && r.sectors.some((y) => y.key === x.key)).length; return `<option value="${x.key}" ${q.sector === x.key ? "selected" : ""} ${n || q.sector === x.key ? "" : "disabled"}>${esc(x.label)} (${n})</option>`; }).join("")}</select></label>
            <label class="field">Status<select name="status"><option value="">All</option>${Object.entries(DIR_STATUS).map(([k, [l]]) => `<option value="${k}" ${q.status === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          </form></div>
        <div class="table-wrap"><table class="stack"><thead><tr><th>Lender</th><th>Type and status</th><th class="num">Lending</th><th class="num">Active</th></tr></thead><tbody id="lrows">
          ${rows.map((r) => `<tr class="clickable" data-lender="${esc(r.name)}" tabindex="0">
            <td class="lead dir-name"><span class="strong">${esc(r.display)}</span>${r.parent ? `<span class="sub">Part of ${esc(r.parent)}</span>` : ""}${r.group && r.group !== r.parent ? `<span class="sub">Controlled by ${esc(r.group)}${r.group_size > 1 ? ` · <button class="link-btn" data-groupsearch="${esc(r.group)}">${r.group_size} lenders in this group</button>` : ""}</span>` : ""}${r.sectors.length ? `<span class="sub">Lends to ${r.sectors.slice(0, 3).map((x) => `${esc(x.label.toLowerCase())}${r.sectors.length > 1 ? ` (${x.count})` : ""}`).join(", ")}</span>` : ""}${r.note ? `<span class="sub note-line">${icon("note")}${esc(r.note.split("\n")[0].slice(0, 90))}</span>` : ""}<span class="sub">${lenderLinks(r.display)}</span></td>
            <td class="meta">${r.category ? catChip(r.category, r.category_label) : '<span class="muted">Not classified yet</span>'}<span class="sub"><span class="pill" data-cat="${(DIR_STATUS[r.status] || ["", "unknown"])[1]}">${(DIR_STATUS[r.status] || [r.status])[0]}</span></span></td>
            <td class="num side-fact"><b>${plural(r.borrowers, "borrower")}</b><span class="sub">${plural(r.charges, "charge")}, ${r.outstanding} live</span></td>
            <td class="num meta">${r.first ? r.first.slice(0, 4) : "—"}${r.latest && r.first && r.latest.slice(0, 4) !== r.first.slice(0, 4) ? `–${r.latest.slice(0, 4)}` : ""}</td></tr>`).join("")
            || `<tr><td colspan="4"><div class="empty"><p>No lenders on the register match.</p></div></td></tr>`}
        </tbody></table></div>
      </section>
    </div>
    ${watch.length ? `<details class="card watch" style="margin-top:var(--gap)" ${q.category || needle ? "open" : ""}>
      <summary class="card-head"><div><h2>Known from your rules, not seen yet <span class="muted num">${watch.length}</span></h2><p>Names and phrases in the lender rules that no charge on the register has matched so far, by type. They're classified automatically the moment one appears. Click to ${q.category || needle ? "hide" : "show"}.</p></div></summary>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Name or phrase</th><th>Type</th><th>Parent firm</th><th>Rule</th><th>Research</th></tr></thead><tbody>
      ${watch.map((w) => `<tr><td class="lead"><span class="strong">${esc(w.pattern)}</span>${w.note ? `<span class="sub">${esc(w.note)}</span>` : ""}</td>
        <td class="meta">${catChip(w.category, w.category_label)}</td><td class="meta">${esc(w.parent) || '<span class="muted">—</span>'}</td>
        <td class="meta">${w.match === "exact" ? "Exact name" : "Name contains"}<span class="sub">${esc(w.source)}</span></td>
        <td class="meta">${lenderLinks(w.parent || w.pattern)}</td></tr>`).join("")}
      </tbody></table></div></details>` : ""}
  </div>`;
  main.querySelectorAll("[data-dcat]").forEach((b) => (b.onclick = () => { q.category = b.dataset.dcat; renderLenders().then(() => fadeIn(main.querySelector(".dir-main"))); }));
  const form = document.getElementById("lf");
  let t;
  form.querySelector("input").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { q.q = e.target.value; renderLenders().then(() => { const i = document.querySelector("#lf input"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 250); };
  form.onsubmit = (e) => e.preventDefault();
  main.querySelectorAll("[data-groupsearch]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); q.q = b.dataset.groupsearch; q.category = ""; renderLenders(); }));
  const dclear = document.getElementById("dir-clear");
  if (dclear) dclear.onclick = () => { Object.assign(q, { q: "", sector: "", status: "" }); renderLenders(); };
  form.querySelectorAll("select").forEach((el) => (el.onchange = () => { q[el.name] = el.value; renderLenders().then(() => fadeIn(document.getElementById("lrows"))); }));
  document.getElementById("dir-csv").onclick = () => {
    const cols = [["Lender", (r) => r.display], ["Type", (r) => r.category_label || "Not classified yet"], ["Parent firm", (r) => r.parent || ""],
      ["Borrowers", (r) => r.borrowers], ["Charges", (r) => r.charges], ["Outstanding", (r) => r.outstanding], ["First charge", (r) => r.first || ""],
      ["Latest charge", (r) => r.latest || ""], ["Decided by", (r) => r.source_label], ["Notes", (r) => r.note || ""]];
    const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [cols.map((c) => cell(c[0])).join(","), ...rows.map((r) => cols.map((c) => cell(c[1](r))).join(","))].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv" }));
    a.download = `armariu-lenders${q.category ? "-" + q.category : ""}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
}

// ------------------------------------------------------------------ reports
async function renderReports() {
  const docs = await get("/api/documents");
  const tabs = [["drafts", "Newsletter drafts", docs.drafts, "draft", "pen"], ["reviews", "Reviewer reports", docs.reviews, "agent", "check"],
    ["digests", "Weekly digests", docs.digests, "cloud", "reports"], ["messages", "Past messages", docs.messages_done, "message", "mail"]];
  const list = (tabs.find((t) => t[0] === S.reportsTab) || tabs[0])[2];
  if (!list.find((x) => x.path === S.reportSel)) S.reportSel = list.length ? list[0].path : null;
  const newest = docs.reviews[0];
  setSub(newest ? `Latest reviewer report ${fmtTime(newest.modified)}` : "No reviewer report yet");
  main.innerHTML = `<div class="anim-root">
    <div class="qtiles" role="tablist" style="grid-template-columns:repeat(4,minmax(0,1fr))">${tabs.map(([k, label, l, cat, ic]) => `<button class="qtile" role="tab" data-cat="${cat}" data-tab="${k}" aria-selected="${S.reportsTab === k}"><span class="disc">${icon(ic)}</span><span class="qn num">${l.length}</span><span class="ql">${label}</span><span class="qs">${l[0] ? `Latest ${fmtTime(l[0].modified)}` : "None yet"}</span></button>`).join("")}</div>
    <div id="rep"></div></div>`;
  main.querySelectorAll(".qtile").forEach((b) => (b.onclick = () => { S.reportsTab = b.dataset.tab; S.reportSel = null; S.editing = null; renderReports().then(() => fadeIn(document.getElementById("rep"))); }));
  const rep = document.getElementById("rep");
  if (!list.length) { rep.innerHTML = `<div class="card empty"><h2>Nothing here yet</h2><p>These appear as the cloud runs complete.</p></div>`; return; }
  if (S.reportsTab === "drafts") { rep.innerHTML = list.map(draftCard).join(""); return wireDrafts(rep, renderReports); }
  const sel = list.find((x) => x.path === S.reportSel);
  rep.innerHTML = `<div class="split"><article class="card"><div class="prose" id="doc-view">${md(sel.content)}</div></article>
    <nav class="card queue" aria-label="Documents">${list.map((x) => `<button class="q" data-path="${esc(x.path)}" aria-current="${x.path === S.reportSel}"><span>${esc(x.name.replace(/\.md$/, "").replace(/_/g, " to ").replace(/-(review|draft|from-\w+)$/, ""))}</span><span class="n">${fmtTime(x.modified)}</span></button>`).join("")}</nav></div>`;
  rep.querySelectorAll("[data-path]").forEach((b) => (b.onclick = () => {
    S.reportSel = b.dataset.path;
    const doc = list.find((x) => x.path === S.reportSel);
    document.getElementById("doc-view").innerHTML = md(doc.content);
    fadeIn(document.getElementById("doc-view"));
    rep.querySelectorAll("[data-path]").forEach((x) => x.setAttribute("aria-current", x.dataset.path === S.reportSel));
  }));
}

// ------------------------------------------------------------------ status
async function renderStatus() {
  const [s, d] = await Promise.all([get("/api/summary"), loadDecisions()]);
  const w = s.this_week, lw = s.last_week;
  const waiting = [["Lenders that need your call", d.lenders.review.length, "review"], ["Agent answers to check", d.lenders.confirm.length, "direct_lender"],
    ["Messages from the agents", d.messages.length, "message"], ["Newsletter drafts", d.drafts.length, "draft"]];
  const waitingTotal = waiting.reduce((a, [, n]) => a + n, 0);
  const notices = [];
  if (!w.covered) notices.push(`<div class="notice">${icon("alert")}<div>This week isn't fully covered. ${esc(w.coverage_note)}</div></div>`);
  if (s.stream_gap_at) notices.push(`<div class="notice bad">${icon("alert")}<div>Filings were missed around ${fmtDate(s.stream_gap_at)}: the radar was offline long enough for its place in the stream to expire.</div></div>`);
  if (s.git.state === "error") notices.push(`<div class="notice bad">${icon("alert")}<div>Your last decision didn't reach GitHub. ${esc(s.git.message)}</div></div>`);
  if (s.sync.state === "error") notices.push(`<div class="notice bad">${icon("alert")}<div>Couldn't get the latest data. ${esc(s.sync.message)}</div></div>`);
  const running = s.last_pipeline_commit && (Date.now() - new Date(s.last_pipeline_commit)) < 4 * 3600 * 1000;
  setSub(s.last_pipeline_commit ? `${running ? "Running" : "Not run recently"}: last cloud run ${fmtTime(s.last_pipeline_commit)}` : "No cloud run yet");
  const feedIcon = { cloud: "cloud", agent: "agent", you: "you" };
  main.innerHTML = `<div class="anim-root">
    <div class="page-intro"><div><h1>${s.last_pipeline_commit ? (running ? "The radar is running" : "The radar hasn't run for a while") : "No cloud run yet"}</h1>
      <p>${s.last_pipeline_commit ? `The last cloud run finished ${fmtTime(s.last_pipeline_commit)}. It runs every 3 hours on GitHub.` : "Start one from the Actions tab on GitHub."}</p></div></div>
    ${notices.join("")}
    <div class="kpis" style="margin-bottom:var(--gap)">
      <div class="card kpi"><span class="disc" data-cat="cloud">${icon("charges")}</span><dl style="margin:0"><dt>Charges collected</dt><dd class="num">${s.charges.toLocaleString("en-GB")}</dd><div class="sub">${plural(s.lenders, "lender name")}</div></dl></div>
      <div class="card kpi"><span class="disc" data-cat="direct_lender">${icon("overview")}</span><dl style="margin:0;flex:1"><dt>This week so far</dt><dd class="num">${w.total}</dd>${tierBar(w.tiers, { short: true })}</dl></div>
      <div class="card kpi"><span class="disc" data-cat="agent">${icon("reports")}</span><dl style="margin:0;flex:1"><dt>Last week</dt><dd class="num">${lw.total}</dd>${tierBar(lw.tiers, { short: true }) || '<div class="sub">None</div>'}</dl></div>
      <div class="card kpi"><span class="disc" data-cat="bank">${icon("clock")}</span><dl style="margin:0"><dt>Watching since</dt><dd>${s.coverage_start ? fmtDate(s.coverage_start) : "—"}</dd><div class="sub">Newest filing ${s.latest_delivered ? fmtDate(s.latest_delivered) : "none yet"}</div></dl></div>
      <div class="card kpi"><span class="disc" data-cat="asset_finance">${icon("status")}</span><dl style="margin:0"><dt>Data on this PC</dt><dd>${s.sync.mode === "cloud" ? "Cloud copy" : "Local"}</dd><div class="sub">${esc(s.sync.message || "")}${s.sync.at ? `, ${fmtTime(s.sync.at)}` : ""}</div></dl></div>
      <div class="card kpi"><span class="disc" data-cat="other">${icon("coins")}</span><dl style="margin:0"><dt>Stream position</dt><dd class="num">${s.stream_timepoint ? Number(s.stream_timepoint).toLocaleString("en-GB") : "—"}</dd><div class="sub">${s.repo.has_remote ? "Decisions are saved to GitHub" : "No GitHub remote: decisions stay on this PC"}</div></dl></div>
    </div>
    <div class="grid">
      <section class="card span-7"><div class="card-head"><h2>Recent activity</h2></div>${s.activity.length
        ? `<ul class="feed">${s.activity.slice(0, 6).map((a) => `<li><span class="when">${fmtTime(a.at)}</span><span class="disc" data-cat="${a.kind}">${icon(feedIcon[a.kind] || "cloud")}</span><span><span class="what">${esc(a.text.replace(/^You: /, ""))}</span><br><span class="who">${a.kind === "you" ? "You, in the review app" : a.kind === "agent" ? "Claude agent on GitHub" : "Scheduled cloud run"}</span></span></li>`).join("")}</ul>`
        : `<p class="muted">No activity yet. Cloud runs, agent runs and your decisions appear here once this folder is linked to your GitHub repo.</p>`}</section>
      <section class="card span-5"><div class="card-head"><h2>Waiting for you</h2></div>
        <ul class="waiting-list">${waiting.map(([label, n, cat]) => `<li class="${n ? "" : "zero"}"><span>${label}</span>${n ? `<span class="pill num" data-cat="${cat}">${n}</span>` : `<span class="num">0</span>`}</li>`).join("")}</ul>
        ${waitingTotal ? `<a class="btn primary" href="#/decisions">Open decisions</a>` : `<p class="muted">Nothing needs you. New items appear as the radar runs.</p>`}</section>
    </div></div>`;
}

// ------------------------------------------------------------------ start
S.chargesQuery = { q: "", tier: "", check: "", from: "", to: "", status: "", category: "", sector: "" };
window.addEventListener("hashchange", route);
route();
setInterval(poll, 5000);
poll();

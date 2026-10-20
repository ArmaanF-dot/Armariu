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
  lendersQuery: { q: "", category: "", status: "" },
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
  asset_finance: "Asset finance", public_sector: "Public sector", individual: "Individual", other: "Other", unknown: "Can't tell" };
const TIER_LABELS = { A: "Direct lender", B: "Behind a security agent", C: "Specialist lender", D: "Needs review", E: "Bank or individual" };
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
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

// ------------------------------------------------------------------ navigation and routing
const NAV = [
  ["overview", "Overview", null], ["decisions", "Decisions", "count"], ["reports", "Reports and drafts", null],
  ["|Register"], ["charges", "Charges", null], ["lenders", "Lenders", null],
  ["|Radar"], ["status", "Status", null],
];
document.getElementById("nav").innerHTML = NAV.map(([k, label, extra]) => k.startsWith("|")
  ? `<li class="group">${k.slice(1)}</li>`
  : `<li><a href="#/${k}" data-view="${k}">${icon(k)}<span>${label}</span>${extra ? `<span class="count" id="nav-${k}"></span>` : ""}</a></li>`).join("");

const VIEWS = { overview: renderOverview, decisions: renderDecisions, charges: renderCharges, lenders: renderLenders, reports: renderReports, status: renderStatus };
const VIEW_TITLES = { overview: "Overview", decisions: "Decisions", charges: "Charges", lenders: "Lenders", reports: "Reports and drafts", status: "Status" };
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
function animateIn(el) {
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
  S.chargesQuery = { q: document.getElementById("search-q").value.trim(), tier: "", check: "", from: "", to: "", status: "", category: "" };
  if (S.view === "charges") refresh(); else location.hash = "#/charges";
};
document.getElementById("refresh").onclick = async (e) => {
  const b = e.currentTarget;
  b.classList.add("spin");
  try { await post("/api/sync", {}); toast("Checking for updates"); } catch (err) { toast(err.message, true); }
  setTimeout(() => b.classList.remove("spin"), 2500);
};
const goCharges = (patch) => {
  S.chargesQuery = { q: "", tier: "", check: "", from: "", to: "", status: "", category: "", ...patch };
  if (S.view === "charges") refresh(); else location.hash = "#/charges";
};

// ------------------------------------------------------------------ live updates
async function poll() {
  try {
    const v = await get("/api/version");
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
    <section class="card span-5">
      <div class="card-head"><div><h2>Recent activity</h2><p>Cloud runs, agent runs and your decisions.</p></div><a class="link-btn" href="#/status">View status</a></div>
      ${o.activity.length ? `<ul class="feed">${o.activity.map((a) => `<li><span class="when">${fmtTime(a.at)}</span><span class="disc" data-cat="${a.kind}">${icon(feedIcon[a.kind] || "cloud")}</span>
        <span><span class="what">${esc(a.text.replace(/^You: /, ""))}</span><br><span class="who">${a.kind === "you" ? "You, in the review app" : a.kind === "agent" ? "Claude agent on GitHub" : "Scheduled cloud run"}</span></span></li>`).join("")}</ul>`
        : `<div class="empty"><span class="disc" data-cat="cloud">${icon("cloud")}</span><p>No activity yet. Runs and decisions appear here once this folder is linked to your GitHub repo.</p></div>`}
    </section>
    <section class="card span-7">
      <div class="card-head"><div><h2>Latest charges</h2><p>Newest filings first.</p></div><a class="link-btn" href="#/charges">View all ${o.total}</a></div>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Tier</th><th>Borrower</th><th>Lender</th><th class="num">Delivered</th><th></th></tr></thead><tbody>
      ${o.latest.map((r) => `<tr><td class="side-fact">${tierBadge(r.tier)}</td>
        <td class="lead"><span class="strong">${esc(r.company_name || "Name not fetched yet")}</span><span class="sub id">${esc(r.company_number)}</span></td>
        <td class="meta">${r.chargees.slice(0, 1).map((c) => `<button class="linkish" data-lender="${esc(c.name)}">${esc(c.display)}</button>`).join("")}${r.chargees.length > 1 ? ` <span class="muted">+${r.chargees.length - 1}</span>` : ""}</td>
        <td class="num meta">${isoDate(r.delivered_on)}</td><td class="act"><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")
        || `<tr><td colspan="5" class="muted">No charges yet.</td></tr>`}
      </tbody></table></div>
    </section>
  </div>`;
  drawOverviewChart(o);
  main.querySelectorAll("[data-period]").forEach((b) => (b.onclick = () => {
    S.chartWeekly = b.dataset.period === "weekly";
    main.querySelectorAll("[data-period]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    main.querySelector(".card-head p").textContent = `${S.chartWeekly ? "Last 12 weeks" : "Last 30 days"}. Hover for the breakdown, click a point to list those charges.`;
    drawOverviewChart(o); fadeIn(document.getElementById("ov-chart"));
  }));
  document.getElementById("ov-open-week").onclick = () => goCharges({ from: tw.start, to: tw.end });
  main.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => (location.hash = b.dataset.go)));
  main.querySelectorAll("[data-tier]").forEach((b) => (b.onclick = () => goCharges({ tier: b.dataset.tier })));
  main.querySelectorAll("[data-status]").forEach((b) => (b.onclick = () => goCharges({ status: b.dataset.status })));
  main.querySelectorAll("[data-mix], .donut path").forEach((b) => {
    b.onclick = () => goCharges({ category: b.dataset.mix || b.dataset.key });
    b.onmouseenter = () => main.querySelectorAll(".donut path").forEach((p) => (p.style.opacity = p.dataset.key === (b.dataset.mix || b.dataset.key) ? 1 : .35));
    b.onmouseleave = () => main.querySelectorAll(".donut path").forEach((p) => (p.style.opacity = 1));
  });
  wireLenderLinks(main);
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

// ------------------------------------------------------------------ decisions
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
  return [...L.review.map((x) => ({ ...x, group: "review" })), ...L.confirm.map((x) => ({ ...x, group: "confirm" })), ...L.waiting.map((x) => ({ ...x, group: "waiting" }))];
}
function decisionsFinding(d) {
  const L = d.lenders, parts = [];
  if (L.review.length) parts.push(`<b>${plural(L.review.length, "lender needs", "lenders need")} your call</b>`);
  if (L.confirm.length) parts.push(`${plural(L.confirm.length, "agent answer")} to check`);
  if (d.messages.length) parts.push(`${plural(d.messages.length, "message")} from the agents`);
  if (d.drafts.length) parts.push(`${plural(d.drafts.length, "draft")} to edit`);
  let s = parts.length ? parts.join(", ").replace(/, ([^,]*)$/, " and $1") + "." : "Nothing needs you right now.";
  if (L.waiting.length) s += ` ${plural(L.waiting.length, "more name is", "more names are")} waiting for the classifier agent.`;
  return s.charAt(0).toUpperCase() + s.slice(1);
}
async function renderDecisions() {
  const d = await loadDecisions();
  const L = d.lenders, items = queueItems();
  if (!items.find((x) => x.name === S.current)) S.current = items.length ? items[0].name : null;
  setSub(decisionsFinding(d).replace(/<[^>]+>/g, ""));
  const tiles = [
    ["lenders", "review", "lenders", L.review.length + L.confirm.length, "Lenders to classify", L.waiting.length ? `${L.waiting.length} more waiting for the classifier` : "None waiting for the classifier"],
    ["messages", "message", "mail", d.messages.length, "Messages from the agents", d.messages.length ? "Read, then mark as done" : "Nothing new"],
    ["drafts", "draft", "pen", d.drafts.length, "Newsletter drafts", d.drafts.length ? "Edit, then mark as published" : "None to edit"],
  ];
  main.innerHTML = `<div class="anim-root">
    <div class="qtiles" role="tablist" aria-label="What needs you">${tiles.map(([k, cat, ic, n, label, sub]) =>
      `<button class="qtile" role="tab" data-cat="${cat}" data-tab="${k}" aria-selected="${S.decTab === k}"><span class="disc">${icon(ic)}</span>
         <span class="qn num">${n}</span><span class="ql">${label}</span><span class="qs">${sub}</span></button>`).join("")}</div>
    <div id="dec-body"></div></div>`;
  main.querySelectorAll(".qtile").forEach((b) => (b.onclick = () => { S.decTab = b.dataset.tab; renderDecisions().then(() => fadeIn(document.getElementById("dec-body"))); }));
  const body = document.getElementById("dec-body");
  if (S.decTab === "messages") return renderMessages(body, d.messages);
  if (S.decTab === "drafts") return renderDraftDecisions(body, d.drafts);
  if (!items.length) {
    body.innerHTML = `<div class="card empty"><span class="disc" data-cat="bank">${icon("check")}</span><h2>Every lender is classified</h2><p>When the radar picks up a lender it doesn't recognise, the classifier agent looks at it first and anything it isn't sure about comes here.</p></div>`;
    return;
  }
  const item = items.find((x) => x.name === S.current);
  body.innerHTML = `<div class="decide"><section class="card" id="decision" aria-label="Decision">${decisionCard(item)}</section>
    <nav class="card queue" aria-label="Lenders waiting">${queueList(L)}</nav></div>`;
  wireDecision(item);
}
function decisionCard(it) {
  const pick = S.picks[it.name] || { category: it.source === "agent" && it.category !== "unknown" ? it.category : "", parent: it.parent || "" };
  S.picks[it.name] = pick;
  const state = { review: ["Needs your call", "review"], confirm: ["Agent's answer to check", "direct_lender"], waiting: ["Not classified yet", "unknown"] }[it.group];
  const cat = it.source === "agent" ? catKey(it.category) : "unknown";
  const conf = it.confidence != null ? `${Math.round(it.confidence * 100)}% sure` : "";
  const agent = it.source === "agent"
    ? `<div class="agent" data-cat="${cat}"><span class="disc">${icon("agent")}</span><div><p class="says">The classifier agent says ${esc(labelFor(it.category).toLowerCase())}${conf ? `, ${conf}` : ""}${it.parent ? `, part of ${esc(it.parent)}` : ""}.</p><p class="why">${esc(reasonText(it.reason, it.source))}</p></div></div>`
    : `<div class="agent" data-cat="unknown"><span class="disc">${icon("clock")}</span><div><p class="why">The classifier agent hasn't looked at this name yet. It will on its next run, or you can decide now.</p></div></div>`;
  const rows = it.examples.map((e) => `<tr>
      <td class="lead"><span class="strong">${esc(e.company_name || "Name not fetched yet")}</span><span class="sub"><span class="id">${esc(e.company_number)}</span>${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</span></td>
      <td class="num side-fact">${isoDate(e.delivered_on)}</td>
      <td class="meta">${e.other_chargees.length ? e.other_chargees.map(esc).join("; ") : '<span class="muted">None</span>'}</td>
      <td class="meta">${e.all_assets === 1 ? "All assets" : e.all_assets === 0 ? "Specific assets" : '<span class="muted">Not stated</span>'}</td>
      <td class="act"><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">View filing</a></td></tr>`).join("");
  const agentAnswer = it.source === "agent" && it.category && it.category !== "unknown" ? it.category : null;
  return `
    <div class="facts"><span class="pill" data-cat="${state[1]}">${state[0]}</span><span>${plural(it.charges, "charge")}</span>${it.latest ? `<span>Latest filing ${fmtDate(it.latest)}</span>` : ""}</div>
    <h2 class="lender-name">${esc(it.display)}</h2>
    ${agent}
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
    <p class="hint">Keys: <span class="key">1</span>–<span class="key">8</span> pick a type, <span class="key">Enter</span> save${agentAnswer ? `, <span class="key">A</span> approve the agent's answer` : ""}, <span class="key">J</span> and <span class="key">K</span> move through the list.</p>`;
}
function queueList(L) {
  const group = (title, list, bulk) => !list.length ? "" : `<h3>${title}</h3>
    ${bulk && list.length > 1 ? `<div class="bulk"><button class="btn soft" id="approve-all">Approve all ${list.length}</button></div>` : ""}
    ${list.map((x) => `<button class="q" data-name="${esc(x.name)}" data-cat="${x.source === "agent" ? catKey(x.category) : "unknown"}" aria-current="${x.name === S.current}"><span>${esc(x.display)}</span><span class="n num">${x.charges}</span></button>`).join("")}`;
  return group(`Needs your call, ${L.review.length}`, L.review) + group(`Agent's answer to check, ${L.confirm.length}`, L.confirm, true) + group(`Not classified yet, ${L.waiting.length}`, L.waiting);
}
function wireDecision(it) {
  main.querySelectorAll(".choice").forEach((b) => (b.onclick = () => choose(it, b.dataset.key)));
  main.querySelectorAll(".queue button[data-name]").forEach((b) => (b.onclick = () => { S.current = b.dataset.name; showCurrent(); }));
  const parent = document.getElementById("parent");
  parent.oninput = () => (S.picks[it.name].parent = parent.value);
  parent.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); save(it); } };
  document.getElementById("save").onclick = () => save(it);
  const ap = document.getElementById("approve");
  if (ap) ap.onclick = () => approve([it.name], it);
  document.getElementById("skip").onclick = () => step(1);
  const all = document.getElementById("approve-all");
  if (all) all.onclick = () => {
    const names = S.decisions.lenders.confirm.map((x) => x.name);
    if (confirm(`Approve the agent's answer for all ${names.length} lenders in this list?`)) approve(names);
  };
}
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
  const items = queueItems();
  const i = items.findIndex((x) => x.name === S.current);
  if (!items.length) return;
  S.current = items[(i + dir + items.length) % items.length].name;
  showCurrent();
}
async function save(it) {
  const pick = S.picks[it.name];
  if (!pick || !pick.category) return toast("Pick a type first", true);
  try {
    await post("/api/decide", { name: it.name, category: pick.category, parent: pick.parent });
    toast(`Saved: ${it.display} is ${labelFor(pick.category).toLowerCase()}`);
    advanceFrom(it.name);
  } catch (e) { toast(e.message, true); }
}
async function approve(names, it) {
  try {
    const r = await post("/api/approve", { names });
    toast(r.approved.length === 1 && it ? `Approved: ${it.display} is ${labelFor(it.category).toLowerCase()}` : `Approved ${plural(r.approved.length, "lender")}`);
    advanceFrom(names[names.length - 1]);
  } catch (e) { toast(e.message, true); }
}
async function advanceFrom(name) {
  const items = queueItems();
  const i = items.findIndex((x) => x.name === name);
  const next = items.slice(i + 1).find((x) => x.name !== name) || items.find((x) => x.name !== name);
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
  if (/^[1-8]$/.test(e.key) && S.choices[+e.key - 1]) { choose(it, S.choices[+e.key - 1].key); e.preventDefault(); }
  else if (e.key === "Enter" && document.activeElement.tagName !== "BUTTON" && document.activeElement.tagName !== "A") { save(it); e.preventDefault(); }
  else if (e.key.toLowerCase() === "a" && document.getElementById("approve")) { approve([it.name], it); e.preventDefault(); }
  else if (e.key.toLowerCase() === "s" || e.key.toLowerCase() === "j" || e.key === "ArrowDown") { step(1); e.preventDefault(); }
  else if (e.key.toLowerCase() === "k" || e.key === "ArrowUp") { step(-1); e.preventDefault(); }
});

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
  const q = { q: "", tier: "", check: "", from: "", to: "", status: "", category: "", ...S.chargesQuery };
  S.chargesQuery = q;
  document.getElementById("search-q").value = q.q;
  const params = new URLSearchParams({ q: q.q, tier: q.tier, check: q.check, from: q.from, to: q.to, status: q.status, category: q.category, limit: 100, offset: 0 });
  const [data, tierA] = await Promise.all([get("/api/charges?" + params), get("/api/charges?tier=A&limit=1")]);
  if (!S.choices.length) await loadDecisions();
  const rowsHtml = (rows) => rows.map((r) => `<tr>
      <td class="side-fact">${tierBadge(r.tier)}</td>
      <td class="lead"><span class="strong">${esc(r.company_name || "Name not fetched yet")}</span><span class="sub id">${esc(r.company_number)}</span></td>
      <td class="meta">${r.chargees.map((c) => `<button class="linkish" data-lender="${esc(c.name)}">${esc(c.display)}</button>`).join("<br>")}</td>
      <td class="meta">${catChip(r.primary, r.primary_label)}</td>
      <td class="num meta">${isoDate(r.delivered_on)}</td>
      <td class="meta">${statusPill(r.status)}${r.satisfied_on ? `<span class="sub">${isoDate(r.satisfied_on)}</span>` : ""}</td>
      <td class="meta">${r.needs_check ? '<span class="pill" data-cat="review">Check</span>' : '<span class="pill" data-cat="bank">Confirmed</span>'}</td>
      <td class="act"><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("");
  const active = [q.q && `matching “${q.q}”`, q.category && `with ${labelFor(q.category).toLowerCase()} lenders`, q.status === "repaid" && "that are repaid",
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
        <label class="field">Status<select name="status"><option value="">All</option><option value="outstanding" ${q.status === "outstanding" ? "selected" : ""}>Outstanding</option><option value="repaid" ${q.status === "repaid" ? "selected" : ""}>Repaid</option></select></label>
        <label class="field">Checked<select name="check"><option value="">All</option><option value="yes" ${q.check === "yes" ? "selected" : ""}>Needs a check</option><option value="no" ${q.check === "no" ? "selected" : ""}>Confirmed by you</option></select></label>
        <div class="field">Delivered between<span class="range"><input type="date" name="from" value="${esc(q.from)}" aria-label="Delivered from"><span>and</span><input type="date" name="to" value="${esc(q.to)}" aria-label="Delivered to"></span></div>
        <button class="btn soft" type="submit">Apply</button>
      </form>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Tier</th><th>Borrower</th><th>Chargees</th><th>Lender type</th><th class="num">Delivered</th><th>Status</th><th>Checked</th><th></th></tr></thead>
        <tbody id="crows">${rowsHtml(data.rows) || `<tr><td colspan="8"><div class="empty"><p>No charges match these filters.</p></div></td></tr>`}</tbody></table></div>
      ${data.total > data.rows.length ? `<div class="more-row"><button class="btn soft" id="cmore">Show ${Math.min(100, data.total - data.rows.length)} more of ${data.total - data.rows.length}</button></div>` : ""}
    </section></div>`;
  const form = document.getElementById("cf");
  form.onsubmit = (e) => { e.preventDefault(); Object.assign(q, Object.fromEntries(new FormData(form))); renderCharges().then(() => fadeIn(document.getElementById("crows"))); };
  form.querySelectorAll("select").forEach((s) => (s.onchange = () => form.requestSubmit()));
  main.querySelectorAll("[data-tierpick]").forEach((b) => (b.onclick = () => { q.tier = q.tier === b.dataset.tierpick ? "" : b.dataset.tierpick; renderCharges().then(() => fadeIn(document.getElementById("crows"))); }));
  const clear = document.getElementById("clear-f");
  if (clear) clear.onclick = () => goCharges({});
  let offset = data.rows.length;
  const more = document.getElementById("cmore");
  if (more) more.onclick = async () => {
    params.set("offset", offset);
    const next = await get("/api/charges?" + params);
    document.getElementById("crows").insertAdjacentHTML("beforeend", rowsHtml(next.rows));
    offset += next.rows.length;
    wireLenderLinks(main);
    if (offset >= next.total) more.parentElement.remove(); else more.textContent = `Show ${Math.min(100, next.total - offset)} more of ${next.total - offset}`;
  };
  wireLenderLinks(main);
}
function wireLenderLinks(root) {
  root.querySelectorAll("[data-lender]:not(tr)").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); openLender(b.dataset.lender); }));
}

// ------------------------------------------------------------------ lenders
async function renderLenders() {
  const q = S.lendersQuery;
  if (!S.choices.length) await loadDecisions();
  const data = await get("/api/lenders?" + new URLSearchParams(q));
  const statusPillFor = (r) => r.status === "needs_review" ? '<span class="pill" data-cat="review">Needs your call</span>'
    : r.status === "unclassified" ? '<span class="pill" data-cat="unknown">Waiting for the agent</span>'
    : r.status === "ok" ? '<span class="pill" data-cat="bank">Decided by you</span>' : '<span class="pill" data-cat="direct_lender">Not checked by you</span>';
  const direct = data.rows.filter((r) => r.category === "direct_lender").length;
  setSub(`${plural(data.total, "lender name")}, ${plural(direct, "direct lender")}`);
  main.innerHTML = `<div class="anim-root">
    <div class="page-intro"><div><h1>Lenders</h1><p><b>${plural(direct, "direct lender")}</b> among ${plural(data.total, "lender name")}. Click any lender for its lending history and every charge, or to change its type.</p></div></div>
    <section class="card">
      <form class="filters" id="lf">
        <label class="field">Search<input type="search" name="q" value="${esc(q.q)}" placeholder="Lender or parent firm"></label>
        <label class="field">Type<select name="category"><option value="">All types</option>${CATEGORY_KEYS.map((k) => `<option value="${k}" ${q.category === k ? "selected" : ""}>${esc(CAT_LABELS[k])}</option>`).join("")}</select></label>
        <label class="field">Status<select name="status"><option value="">All</option>
          <option value="needs_review" ${q.status === "needs_review" ? "selected" : ""}>Needs your call</option>
          <option value="auto_ok" ${q.status === "auto_ok" ? "selected" : ""}>Not checked by you</option>
          <option value="ok" ${q.status === "ok" ? "selected" : ""}>Decided by you</option>
          <option value="unclassified" ${q.status === "unclassified" ? "selected" : ""}>Waiting for the agent</option></select></label>
        <button class="btn soft" type="submit">Apply</button>
      </form>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Lender</th><th>Type</th><th>Parent firm</th><th>Decided by</th><th>Status</th><th class="num">Charges</th><th class="num">Latest</th></tr></thead><tbody id="lrows">
        ${data.rows.map((r) => `<tr class="clickable" data-lender="${esc(r.name)}" tabindex="0">
          <td class="lead"><span class="strong">${esc(r.display)}</span></td><td class="meta">${r.category ? catChip(r.category, r.category_label) : '<span class="muted">Not classified yet</span>'}</td>
          <td class="meta">${esc(r.parent || "") || '<span class="muted">—</span>'}</td><td class="meta">${esc(r.source_label)}</td><td class="meta">${statusPillFor(r)}</td>
          <td class="num side-fact"><b>${r.charges}</b></td><td class="num meta">${isoDate(r.latest)}</td></tr>`).join("")
          || `<tr><td colspan="7"><div class="empty"><p>No lenders match these filters.</p></div></td></tr>`}
      </tbody></table></div>
    </section></div>`;
  const form = document.getElementById("lf");
  form.onsubmit = (e) => { e.preventDefault(); Object.assign(q, Object.fromEntries(new FormData(form))); renderLenders().then(() => fadeIn(document.getElementById("lrows"))); };
  form.querySelectorAll("select").forEach((s) => (s.onchange = () => form.requestSubmit()));
  main.querySelectorAll("tr[data-lender]").forEach((tr) => {
    tr.onclick = () => openLender(tr.dataset.lender);
    tr.onkeydown = (e) => { if (e.key === "Enter") openLender(tr.dataset.lender); };
  });
}

// ------------------------------------------------------------------ lender drawer (slides in)
async function openLender(name) {
  if (!S.choices.length) await loadDecisions();
  const it = await get("/api/lender?name=" + encodeURIComponent(name));
  const drawer = document.getElementById("drawer"), body = document.getElementById("drawer-body");
  const pick = { category: it.category && it.category !== "unknown" ? it.category : "", parent: it.parent || "" };
  body.innerHTML = `
    <div class="facts">${it.category ? catChip(it.category) + `<span>${esc(it.source_label)}</span>` : '<span class="pill" data-cat="unknown">Not classified yet</span><span>The classifier agent will look at it on its next run</span>'}${it.confidence != null && it.source === "agent" ? `<span>${Math.round(it.confidence * 100)}% sure</span>` : ""}</div>
    <h2 class="lender-name" id="drawer-title">${esc(it.display)}</h2>
    <div class="card" style="margin-bottom:20px">
      ${it.reason ? `<div class="agent" data-cat="${catKey(it.category)}"><span class="disc">${icon(it.source === "agent" ? "agent" : "check")}</span><div><p class="why">${esc(reasonText(it.reason, it.source))}</p></div></div>` : ""}
      <div data-cat="${catKey(it.category)}">${historyStrip(it.history)}</div>
      <h3>Change the type</h3>
      <div class="picker">${S.choices.map((c) => `<button type="button" class="choice" data-key="${c.key}" data-cat="${c.key}" aria-pressed="${pick.category === c.key}"><span class="top"><span class="nm">${esc(c.label)}</span></span><span class="help">${esc(c.help)}</span></button>`).join("")}</div>
      <div class="decide-row"><label class="field">Parent firm, if it's part of a group<input class="input" id="dparent" value="${esc(pick.parent)}" maxlength="100"></label>
        <button class="btn primary" id="dsave">Save decision</button></div>
    </div>
    <section class="card"><div class="card-head"><h2>${plural(it.charges, "charge")} in their favour</h2></div>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Borrower</th><th class="num">Delivered</th><th>Other chargees</th><th>Status</th><th></th></tr></thead><tbody>
      ${it.examples.map((e) => `<tr><td class="lead"><span class="strong">${esc(e.company_name || "Name not fetched yet")}</span><span class="sub"><span class="id">${esc(e.company_number)}</span>${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</span></td>
        <td class="num side-fact">${isoDate(e.delivered_on)}</td><td class="meta">${e.other_chargees.map(esc).join("; ") || '<span class="muted">None</span>'}</td>
        <td class="meta">${statusPill(e.status)}</td><td class="act"><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")}
      </tbody></table></div></section>`;
  body.querySelectorAll(".choice").forEach((b) => (b.onclick = () => {
    pick.category = b.dataset.key;
    body.querySelectorAll(".choice").forEach((x) => x.setAttribute("aria-pressed", x === b));
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
  drawer.querySelector(".drawer-inner").scrollTop = 0;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.getElementById("scrim").classList.add("on");
  setTimeout(() => document.getElementById("drawer-close").focus(), 50);
}
function closeDrawer() {
  const d = document.getElementById("drawer");
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
  if (!document.body.classList.contains("menu-open")) document.getElementById("scrim").classList.remove("on");
}
document.getElementById("drawer-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); setMenu(false); } });

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
S.chargesQuery = { q: "", tier: "", check: "", from: "", to: "", status: "", category: "" };
window.addEventListener("hashchange", route);
route();
setInterval(poll, 5000);
poll();

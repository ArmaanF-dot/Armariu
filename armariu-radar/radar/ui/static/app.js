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
  chargesQuery: { q: "", tier: "", check: "", from: "", to: "", offset: 0 },
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
const labelFor = (key) => (S.choices.find((c) => c.key === key) || {}).label || (key === "unknown" ? "Can't tell" : key || "");
const TIER_LABELS = { A: "Direct lender", B: "Behind a security agent", C: "Specialist lender", D: "Needs review", E: "Bank or individual" };
const tierBadge = (t) => `<span class="tier ${esc(t)}" title="Tier ${esc(t)}: ${esc(TIER_LABELS[t] || "")}">${esc(t)}</span>`;
// Rules record their reason as "rule:<type>:<pattern>"; show it in words.
const reasonText = (r, source) => {
  const m = /^rule:(exact|contains|regex):(.*)$/.exec(r || "");
  if (!m) return r || "";
  const who = source === "human" ? "your rule" : "the starter rule";
  return m[1] === "exact" ? `Matched ${who} for this exact name.` : `Matched ${who} for names containing ${m[2]}.`;
};
const catKey = (k) => (k && CATEGORY_KEYS.includes(k) ? k : "unknown");
const CATEGORY_KEYS = ["direct_lender", "bank", "security_agent", "abl_specialist", "asset_finance", "public_sector", "individual", "other", "unknown"];
const catChip = (k, label) => `<span class="cat" data-cat="${catKey(k)}">${esc(label || labelFor(k) || "Can't tell")}</span>`;
// The tier bar: every charge in view, split by tier, each segment in its tier's colour.
function tierBar(counts, opts = {}) {
  const total = Object.values(counts || {}).reduce((a, n) => a + n, 0);
  if (!total) return "";
  const seg = Object.entries(counts).filter(([, n]) => n).map(([t, n]) =>
    `<span class="t-${t}" style="flex:${n}" title="Tier ${t}: ${n}"></span>`).join("");
  const legend = Object.entries(counts).map(([t, n]) => opts.clickable
    ? `<button type="button" data-tierpick="${t}" aria-pressed="${opts.selected === t}"><span class="sw t-${t}"></span>${esc(TIER_LABELS[t])} <b class="num">${n}</b></button>`
    : `<button type="button" disabled><span class="sw t-${t}"></span>${t} <b class="num">${n}</b></button>`).join("");
  return `<div class="tierbar"><div class="bar" role="img" aria-label="${Object.entries(counts).map(([t, n]) => `Tier ${t}: ${n}`).join(", ")}">${seg}</div>
    ${opts.legend === false ? "" : `<div class="legend">${legend}</div>`}</div>`;
}
const sic = (codes) => (codes && codes.length ? codes.map(esc).join(", ") : "");

// ------------------------------------------------------------------ routing
const VIEWS = { decisions: renderDecisions, charges: renderCharges, lenders: renderLenders, reports: renderReports, status: renderStatus };
const VIEW_TITLES = { decisions: "Decisions", charges: "Charges", lenders: "Lenders", reports: "Reports and drafts", status: "Status" };
function route() {
  const name = (location.hash.replace(/^#\//, "").split("?")[0]) || "decisions";
  S.view = VIEWS[name] ? name : "decisions";
  document.querySelectorAll("[data-view]").forEach((a) => a.classList.toggle("active", a.dataset.view === S.view));
  document.getElementById("mbar-title").textContent = VIEW_TITLES[S.view];
  document.title = `${VIEW_TITLES[S.view]} · Armariu review`;
  setMenu(false);
  closeDrawer();
  refresh(true);
}
async function refresh(focus = false) {
  try {
    await VIEWS[S.view]();
    if (focus) main.focus({ preventScroll: true });
  } catch (e) {
    main.innerHTML = `<div class="page-head"><div><h1>${esc(VIEW_TITLES[S.view])}</h1></div></div>
      <div class="notice bad">Couldn't load this page: ${esc(e.message)}. Check that the window running the app is still open, then reload.</div>`;
  }
}
function setMenu(open) {
  document.body.classList.toggle("menu-open", open);
  document.getElementById("menu-open").setAttribute("aria-expanded", String(open));
  if (open) document.getElementById("menu-close").focus();
}
document.getElementById("menu-open").onclick = () => setMenu(true);
document.getElementById("menu-close").onclick = () => setMenu(false);

// ------------------------------------------------------------------ live updates
async function poll() {
  try {
    const v = await get("/api/version");
    renderSync(v.sync, v.git);
    const key = v.version + "|" + (v.sync && v.sync.at) + "|" + (v.git && v.git.state);
    if (S.version && key !== S.version && !S.editing && !isTyping()) await refresh();
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
  else if (sync && sync.state === "ok") {
    cls = "ok";
    text = `Up to date, checked ${fmtTime(sync.at)}` + (sync.mode === "cloud" ? " from the cloud" : "");
  }
  el.className = "sync " + cls;
  el.querySelector(".sync-text").textContent = text;
}

// ------------------------------------------------------------------ shared pieces
const isoDate = (d) => (d ? esc(String(d).slice(0, 10)) : '<span class="muted">—</span>');
const STATUS_WORDS = { outstanding: "Outstanding", "fully-satisfied": "Repaid", "part-satisfied": "Part repaid", satisfied: "Repaid" };
const statusPill = (s) => `<span class="pill${s === "outstanding" ? "" : " good"}">${esc(STATUS_WORDS[s] || s || "—")}</span>`;

// The lending-history strip: one dot per charge in this lender's favour, filled while
// outstanding and hollow once repaid, on a time axis that ends today. Drawn in HTML with
// percentage positions so it never stretches, whatever the width.
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
    lifted = p - lastP < 1.6 ? !lifted : false;   // stack near-identical dates so each one shows
    lastP = p;
    const repaid = h.status && h.status !== "outstanding";
    return `<span class="dot${repaid ? " repaid" : ""}${lifted ? " lift" : ""}" style="left:${p}%" title="${esc(h.delivered_on)}: ${repaid ? "repaid" : "outstanding"}"></span>`;
  }).join("");
  const repaid = dated.filter((h) => h.status && h.status !== "outstanding").length;
  const summary = `${plural(dated.length, "charge")} since ${dated[0].delivered_on.slice(0, 4)}, latest ${fmtDate(dated[dated.length - 1].delivered_on)}` +
    (repaid ? `, ${repaid} repaid` : "");
  return `<div class="history"><div class="history-head"><h3>Lending history</h3><span class="muted">${summary}</span></div>
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
  return [
    ...L.review.map((x) => ({ ...x, group: "review" })),
    ...L.confirm.map((x) => ({ ...x, group: "confirm" })),
    ...L.waiting.map((x) => ({ ...x, group: "waiting" })),
  ];
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
  const L = d.lenders;
  const items = queueItems();
  if (!items.find((x) => x.name === S.current)) S.current = items.length ? items[0].name : null;
  const tiles = [
    ["lenders", "review", L.review.length + L.confirm.length, "Lenders to classify",
      L.waiting.length ? `${L.waiting.length} more waiting for the classifier` : "None waiting for the classifier"],
    ["messages", "message", d.messages.length, "Messages from the agents", d.messages.length ? "Read, then mark as done" : "Nothing new"],
    ["drafts", "draft", d.drafts.length, "Newsletter drafts", d.drafts.length ? "Edit, then mark as published" : "None to edit"],
  ];
  main.innerHTML = `
    <div class="page-head"><div><h1>Decisions</h1><p class="finding">${decisionsFinding(d)}</p></div></div>
    <div class="qtiles" role="tablist" aria-label="What needs you">${tiles.map(([k, cat, n, label, sub]) =>
      `<button class="qtile" role="tab" data-cat="${cat}" data-tab="${k}" aria-selected="${S.decTab === k}">
         <span class="qn num">${n}</span><span class="ql">${label}</span><span class="qs">${sub}</span></button>`).join("")}</div>
    <div id="dec-body"></div>`;
  main.querySelectorAll(".qtile").forEach((b) => (b.onclick = () => { S.decTab = b.dataset.tab; renderDecisions(); }));
  const body = document.getElementById("dec-body");
  if (S.decTab === "messages") return renderMessages(body, d.messages);
  if (S.decTab === "drafts") return renderDraftDecisions(body, d.drafts);
  if (!items.length) {
    body.innerHTML = `<div class="panel empty"><h2>Every lender is classified</h2><p>When the radar picks up a lender it doesn't recognise, the classifier agent looks at it first and anything it isn't sure about comes here.</p></div>`;
    return;
  }
  const item = items.find((x) => x.name === S.current);
  body.innerHTML = `<div class="decide"><section class="panel decision" aria-label="Decision">${decisionCard(item)}</section>
    <nav class="panel queue" aria-label="Lenders waiting">${queueList(L)}</nav></div>`;
  wireDecision(item);
}
function decisionCard(it) {
  const pick = S.picks[it.name] || { category: it.source === "agent" && it.category !== "unknown" ? it.category : "", parent: it.parent || "" };
  S.picks[it.name] = pick;
  const state = { review: "Needs your call", confirm: "Agent's answer to check", waiting: "Not classified yet" }[it.group];
  const cat = it.source === "agent" ? catKey(it.category) : "unknown";
  const conf = it.confidence != null ? `${Math.round(it.confidence * 100)}% sure` : "";
  const agent = it.source === "agent"
    ? `<div class="agent" data-cat="${cat}"><p class="says">The classifier agent says ${esc(labelFor(it.category).toLowerCase())}${conf ? `, ${conf}` : ""}${it.parent ? `, part of ${esc(it.parent)}` : ""}.</p><p class="why">${esc(reasonText(it.reason, it.source))}</p></div>`
    : `<div class="agent"><p class="why">The classifier agent hasn't looked at this name yet. It will on its next run, or you can decide now.</p></div>`;
  const rows = it.examples.map((e) => `<tr>
      <td class="lead">${esc(e.company_name || "Name not fetched yet")}<span class="sub"><span class="id">${esc(e.company_number)}</span>${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</span></td>
      <td class="num side-fact">${isoDate(e.delivered_on)}</td>
      <td class="meta">${e.other_chargees.length ? e.other_chargees.map(esc).join("; ") : '<span class="muted">None</span>'}</td>
      <td class="meta">${e.all_assets === 1 ? "All assets" : e.all_assets === 0 ? "Specific assets" : '<span class="muted">Not stated</span>'}</td>
      <td class="act"><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">View filing</a></td></tr>`).join("");
  const agentAnswer = it.source === "agent" && it.category && it.category !== "unknown" ? it.category : null;
  return `
    <div class="facts"><span class="state ${it.group}">${state}</span><span>${plural(it.charges, "charge")}</span>${it.latest ? `<span>Latest filing ${fmtDate(it.latest)}</span>` : ""}</div>
    <h2 class="lender-name">${esc(it.display)}</h2>
    ${agent}
    <div data-cat="${cat}">${historyStrip(it.history)}</div>
    <div class="evidence"><h3>Charges in their favour</h3>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Borrower</th><th class="num">Delivered</th><th>Other chargees</th><th>Security</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    </div>
    <h3>What kind of lender is this?</h3>
    <div class="picker" role="group" aria-label="Lender category">${S.choices.map((c, i) => `
      <button type="button" class="choice" data-key="${c.key}" data-cat="${c.key}" aria-pressed="${pick.category === c.key}">
        <span class="top"><span>${esc(c.label)}</span><span class="key" aria-hidden="true">${i + 1}</span></span>
        <span class="help">${esc(c.help)}</span>${agentAnswer === c.key ? `<span class="agent-pick">The agent's answer</span>` : ""}
      </button>`).join("")}</div>
    <div class="decide-row">
      <label class="field">Parent firm, if it's part of a group<input class="input" id="parent" value="${esc(pick.parent)}" placeholder="For example, the fund manager behind a fund vehicle" maxlength="100"></label>
      <button class="btn primary" id="save" ${pick.category ? "" : "disabled"}>Save decision</button>
      ${agentAnswer ? `<button class="btn secondary" id="approve">Approve the agent's answer</button>` : ""}
      <button class="btn ghost" id="skip">Skip for now</button>
    </div>
    <p class="hint">Keys: <span class="key">1</span>–<span class="key">8</span> pick a type, <span class="key">Enter</span> save${agentAnswer ? `, <span class="key">A</span> approve the agent's answer` : ""}, <span class="key">J</span> and <span class="key">K</span> move through the list.</p>`;
}
function queueList(L) {
  const group = (title, list, bulk) => !list.length ? "" : `<h3>${title}</h3>
    ${bulk && list.length > 1 ? `<div class="bulk"><button class="btn secondary" id="approve-all">Approve all ${list.length}</button></div>` : ""}
    ${list.map((x) => `<button class="q" data-name="${esc(x.name)}" data-cat="${x.source === "agent" ? catKey(x.category) : "unknown"}" aria-current="${x.name === S.current}"><span>${esc(x.display)}</span><span class="n num">${x.charges}</span></button>`).join("")}`;
  return group(`Needs your call, ${L.review.length}`, L.review) + group(`Agent's answer to check, ${L.confirm.length}`, L.confirm, true) + group(`Not classified yet, ${L.waiting.length}`, L.waiting);
}
function wireDecision(it) {
  main.querySelectorAll(".choice").forEach((b) => (b.onclick = () => choose(it, b.dataset.key)));
  main.querySelectorAll(".queue button[data-name]").forEach((b) => (b.onclick = () => { S.current = b.dataset.name; renderDecisions(); }));
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
  renderDecisions();
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
function advanceFrom(name) {
  const items = queueItems();
  const i = items.findIndex((x) => x.name === name);
  const next = items.slice(i + 1).find((x) => x.name !== name) || items.find((x) => x.name !== name);
  delete S.picks[name];
  S.current = next ? next.name : null;
  renderDecisions();
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
    body.innerHTML = `<div class="panel empty"><h2>No messages</h2><p>The agents leave a note here when something needs a person, like a week that looks wrong or a rule that seems off.</p></div>`;
    return;
  }
  body.innerHTML = messages.map((m) => `<article class="panel doc">
      <div class="doc-head"><span class="muted">From the ${esc((m.name.match(/from-([a-z]+)/) || [, "agents"])[1])} agent, ${fmtTime(m.modified)}</span>
      <button class="btn secondary" data-done="${esc(m.path)}">Mark as done</button></div>
      <div class="prose">${md(m.content)}</div></article>`).join("");
  body.querySelectorAll("[data-done]").forEach((b) => (b.onclick = async () => {
    try { await post("/api/message/done", { path: b.dataset.done }); toast("Marked as done"); renderDecisions(); }
    catch (e) { toast(e.message, true); }
  }));
}
function renderDraftDecisions(body, drafts) {
  if (!drafts.length) {
    body.innerHTML = `<div class="panel empty"><h2>No drafts waiting</h2><p>The newsletter agent writes a draft once a fully covered week has been reviewed. The first full week runs from 26 October.</p></div>`;
    return;
  }
  body.innerHTML = drafts.map((d) => draftCard(d)).join("");
  wireDrafts(body, () => renderDecisions());
}

// ------------------------------------------------------------------ drafts (shared)
const STATUS_TEXT = { new: "New draft", ready: "Ready to publish", published: "Published" };
function draftCard(d) {
  const editing = S.editing === d.path;
  const week = d.name.replace(/-draft\.md$/, "").split("_");
  return `<article class="panel doc" data-path="${esc(d.path)}">
    <div class="doc-head"><div><h2>Week of ${fmtDate(week[0])} to ${fmtDate(week[1])}</h2>
      <span class="pill${d.status === "published" ? " good" : d.status === "ready" ? "" : " warn"}">${STATUS_TEXT[d.status] || d.status}</span>
      <span class="muted small">&nbsp; Updated ${fmtTime(d.modified)}</span></div>
      <div class="doc-actions">${editing
        ? `<button class="btn primary" data-act="save">Save draft</button><button class="btn ghost" data-act="cancel">Cancel</button>`
        : `<button class="btn secondary" data-act="edit">Edit</button>
           ${d.status === "new" ? `<button class="btn secondary" data-act="ready">Mark ready</button>` : ""}
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
        if (act === "edit") { S.editing = path; return rerender(); }
        if (act === "cancel") { S.editing = null; return rerender(); }
        if (act === "save") {
          await post("/api/draft/save", { path, content: card.querySelector("textarea").value });
          S.editing = null; toast("Draft saved"); return rerender();
        }
        await post("/api/draft/status", { path, status: act });
        toast(act === "published" ? "Marked as published" : act === "ready" ? "Marked ready to publish" : "Marked as a new draft");
        rerender();
      } catch (e) { toast(e.message, true); }
    }));
  });
}

// ------------------------------------------------------------------ charges
async function renderCharges() {
  const q = S.chargesQuery;
  const params = new URLSearchParams({ q: q.q, tier: q.tier, check: q.check, from: q.from, to: q.to, limit: 100, offset: 0 });
  const [data, tierA] = await Promise.all([get("/api/charges?" + params), get("/api/charges?tier=A&limit=1")]);
  const rowsHtml = (rows) => rows.map((r) => `<tr>
      <td class="side-fact">${tierBadge(r.tier)}</td>
      <td class="lead">${esc(r.company_name || "Name not fetched yet")}<span class="sub id">${esc(r.company_number)}</span></td>
      <td class="meta">${r.chargees.map((c) => `<button class="linkish" data-lender="${esc(c.name)}">${esc(c.display)}</button>`).join("<br>")}</td>
      <td class="meta">${catChip(r.primary, r.primary_label)}</td>
      <td class="num meta">${isoDate(r.delivered_on)}</td>
      <td class="meta">${statusPill(r.status)}${r.satisfied_on ? `<span class="sub">${isoDate(r.satisfied_on)}</span>` : ""}</td>
      <td class="meta">${r.needs_check ? '<span class="pill warn">Check</span>' : '<span class="pill good">Confirmed</span>'}</td>
      <td class="act"><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("");
  const filtered = q.q || q.tier || q.check || q.from || q.to;
  main.innerHTML = `
    <div class="page-head"><div><h1>Charges</h1><p class="finding">${q.tier === "A"
      ? `Showing <b>the ${plural(data.total, "charge")} in favour of direct lenders</b>${q.q || q.check || q.from || q.to ? " that match your filters" : ""}.`
      : `<b>${plural(tierA.total, "charge")} in favour of direct lenders</b> among the ${plural(data.total, "charge")} ${filtered ? "matching these filters" : "the radar has picked up"}.`}${
      tierA.total && q.tier !== "A" ? ` <button class="linkish" id="only-a">Show only these</button>` : q.tier === "A" ? ` <button class="linkish" id="show-all">Show all charges</button>` : ""}</p></div></div>
    <form class="filters" id="cf">
      <label class="field">Search<input type="search" name="q" value="${esc(q.q)}" placeholder="Borrower, company number or lender"></label>
      <label class="field">Tier<select name="tier"><option value="">All tiers</option>${Object.entries(TIER_LABELS).map(([k, v]) => `<option value="${k}" ${q.tier === k ? "selected" : ""}>${k}: ${v}</option>`).join("")}</select></label>
      <label class="field">Checked<select name="check"><option value="">All</option><option value="yes" ${q.check === "yes" ? "selected" : ""}>Needs a check</option><option value="no" ${q.check === "no" ? "selected" : ""}>Confirmed by you</option></select></label>
      <label class="field">Delivered from<input type="date" name="from" value="${esc(q.from)}"></label>
      <label class="field">Delivered to<input type="date" name="to" value="${esc(q.to)}"></label>
      <button class="btn secondary" type="submit">Apply</button>
    </form>
    ${tierBar(data.tier_counts, { clickable: true, selected: q.tier })}
    <div class="table-wrap"><table class="stack"><thead><tr><th>Tier</th><th>Borrower</th><th>Chargees</th><th>Lender type</th><th class="num">Delivered</th><th>Status</th><th>Checked</th><th></th></tr></thead>
      <tbody id="crows">${rowsHtml(data.rows) || `<tr><td colspan="8" class="muted">No charges match these filters.</td></tr>`}</tbody></table></div>
    ${data.total > data.rows.length ? `<button class="btn secondary more" id="cmore">Show more</button>` : ""}`;
  const form = document.getElementById("cf");
  form.onsubmit = (e) => { e.preventDefault(); Object.assign(q, Object.fromEntries(new FormData(form))); renderCharges(); };
  main.querySelectorAll("[data-tierpick]").forEach((b) => (b.onclick = () => { q.tier = q.tier === b.dataset.tierpick ? "" : b.dataset.tierpick; renderCharges(); }));
  const onlyA = document.getElementById("only-a"), showAll = document.getElementById("show-all");
  if (onlyA) onlyA.onclick = () => { q.tier = "A"; renderCharges(); };
  if (showAll) showAll.onclick = () => { q.tier = ""; renderCharges(); };
  form.querySelectorAll("select").forEach((s) => (s.onchange = () => form.requestSubmit()));
  let offset = data.rows.length;
  const more = document.getElementById("cmore");
  if (more) more.onclick = async () => {
    params.set("offset", offset);
    const next = await get("/api/charges?" + params);
    document.getElementById("crows").insertAdjacentHTML("beforeend", rowsHtml(next.rows));
    offset += next.rows.length;
    wireLenderLinks(main);
    if (offset >= next.total) more.remove();
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
  const all = q.q || q.category || q.status ? null : data;
  const statusPillFor = (r) => r.status === "needs_review" ? '<span class="pill warn">Needs your call</span>'
    : r.status === "unclassified" ? '<span class="pill">Waiting for the agent</span>'
    : r.status === "ok" ? '<span class="pill good">Decided by you</span>' : '<span class="pill">Not checked by you</span>';
  const direct = data.rows.filter((r) => r.category === "direct_lender").length;
  main.innerHTML = `
    <div class="page-head"><div><h1>Lenders</h1><p class="finding"><b>${plural(direct, "direct lender")}</b> among ${plural(data.total, "lender name")}${all ? "" : " matching these filters"}. Your decisions always override the agent and the starter rules.</p></div></div>
    <form class="filters" id="lf">
      <label class="field">Search<input type="search" name="q" value="${esc(q.q)}" placeholder="Lender or parent firm"></label>
      <label class="field">Type<select name="category"><option value="">All types</option>${S.choices.map((c) => `<option value="${c.key}" ${q.category === c.key ? "selected" : ""}>${esc(c.label)}</option>`).join("")}<option value="unknown" ${q.category === "unknown" ? "selected" : ""}>Can't tell</option></select></label>
      <label class="field">Status<select name="status"><option value="">All</option>
        <option value="needs_review" ${q.status === "needs_review" ? "selected" : ""}>Needs your call</option>
        <option value="auto_ok" ${q.status === "auto_ok" ? "selected" : ""}>Not checked by you</option>
        <option value="ok" ${q.status === "ok" ? "selected" : ""}>Decided by you</option>
        <option value="unclassified" ${q.status === "unclassified" ? "selected" : ""}>Waiting for the agent</option></select></label>
      <button class="btn secondary" type="submit">Apply</button>
    </form>
    <div class="table-wrap"><table class="stack"><thead><tr><th>Lender</th><th>Type</th><th>Parent firm</th><th>Decided by</th><th>Status</th><th class="num">Charges</th><th class="num">Latest</th></tr></thead><tbody>
      ${data.rows.map((r) => `<tr class="clickable" data-lender="${esc(r.name)}" tabindex="0">
        <td class="lead">${esc(r.display)}</td><td class="meta">${r.category ? catChip(r.category, r.category_label) : '<span class="muted">Not classified yet</span>'}</td><td class="meta">${esc(r.parent || "") || '<span class="muted">—</span>'}</td>
        <td class="meta">${esc(r.source_label)}</td><td class="meta">${statusPillFor(r)}</td><td class="num side-fact">${r.charges}</td><td class="num meta">${isoDate(r.latest)}</td></tr>`).join("")
        || `<tr><td colspan="7" class="muted">No lenders match these filters.</td></tr>`}
    </tbody></table></div>`;
  const form = document.getElementById("lf");
  form.onsubmit = (e) => { e.preventDefault(); Object.assign(q, Object.fromEntries(new FormData(form))); renderLenders(); };
  form.querySelectorAll("select").forEach((s) => (s.onchange = () => form.requestSubmit()));
  main.querySelectorAll("tr[data-lender]").forEach((tr) => {
    tr.onclick = () => openLender(tr.dataset.lender);
    tr.onkeydown = (e) => { if (e.key === "Enter") openLender(tr.dataset.lender); };
  });
}

// ------------------------------------------------------------------ lender drawer
async function openLender(name) {
  if (!S.choices.length) await loadDecisions();
  const it = await get("/api/lender?name=" + encodeURIComponent(name));
  const drawer = document.getElementById("drawer");
  const body = document.getElementById("drawer-body");
  const pick = { category: it.category && it.category !== "unknown" ? it.category : "", parent: it.parent || "" };
  body.innerHTML = `
    <div class="facts"><span class="muted">${esc(it.source_label)}</span>${it.category ? catChip(it.category) : ""}${it.confidence != null && it.source === "agent" ? `<span>${Math.round(it.confidence * 100)}% sure</span>` : ""}</div>
    <h2 class="lender-name" id="drawer-title">${esc(it.display)}</h2>
    ${it.reason ? `<div class="agent" data-cat="${catKey(it.category)}"><p class="why">${esc(reasonText(it.reason, it.source))}</p></div>` : ""}
    <div data-cat="${catKey(it.category)}">${historyStrip(it.history)}</div>
    <h3>Change the type</h3>
    <div class="picker">${S.choices.map((c) => `<button type="button" class="choice" data-key="${c.key}" data-cat="${c.key}" aria-pressed="${pick.category === c.key}"><span class="top"><span>${esc(c.label)}</span></span><span class="help">${esc(c.help)}</span></button>`).join("")}</div>
    <div class="decide-row"><label class="field">Parent firm, if it's part of a group<input class="input" id="dparent" value="${esc(pick.parent)}" maxlength="100"></label>
      <button class="btn primary" id="dsave">Save decision</button></div>
    <div class="evidence" style="margin-top:24px"><h3>${plural(it.charges, "charge")} in their favour</h3>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Borrower</th><th class="num">Delivered</th><th>Other chargees</th><th></th></tr></thead><tbody>
      ${it.examples.map((e) => `<tr><td class="lead">${esc(e.company_name || "Name not fetched yet")}<span class="sub"><span class="id">${esc(e.company_number)}</span>${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</span></td>
        <td class="num side-fact">${isoDate(e.delivered_on)}</td><td class="meta">${e.other_chargees.map(esc).join("; ") || '<span class="muted">None</span>'}</td>
        <td class="act"><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")}
      </tbody></table></div></div>`;
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
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.getElementById("drawer-close").focus();
}
function closeDrawer() {
  const d = document.getElementById("drawer");
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
}
document.getElementById("drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.getElementById("drawer-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); setMenu(false); } });

// ------------------------------------------------------------------ reports
async function renderReports() {
  const docs = await get("/api/documents");
  const tabs = [["drafts", "Newsletter drafts", docs.drafts], ["reviews", "Reviewer reports", docs.reviews],
    ["digests", "Weekly digests", docs.digests], ["messages", "Past messages", docs.messages_done]];
  const list = (tabs.find((t) => t[0] === S.reportsTab) || tabs[0])[2];
  if (!list.find((x) => x.path === S.reportSel)) S.reportSel = list.length ? list[0].path : null;
  const newest = docs.reviews[0];
  main.innerHTML = `
    <div class="page-head"><div><h1>Reports and drafts</h1><p class="finding">${newest ? `Latest reviewer report: <b>${esc(newest.name.replace(/-review\.md$/, "").replace("_", " to "))}</b>, ${fmtTime(newest.modified)}.` : "No reviewer report yet. The reviewer agent checks each fully covered week."}</p></div></div>
    <div class="tabs" role="tablist">${tabs.map(([k, label, l]) => `<button role="tab" aria-selected="${S.reportsTab === k}" data-tab="${k}">${label}${l.length ? ` <span class="tab-n">${l.length}</span>` : ""}</button>`).join("")}</div>
    <div id="rep"></div>`;
  main.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => { S.reportsTab = b.dataset.tab; S.reportSel = null; S.editing = null; renderReports(); }));
  const rep = document.getElementById("rep");
  if (!list.length) { rep.innerHTML = `<div class="panel empty"><h2>Nothing here yet</h2><p>These appear as the cloud runs complete.</p></div>`; return; }
  if (S.reportsTab === "drafts") { rep.innerHTML = list.map(draftCard).join(""); return wireDrafts(rep, renderReports); }
  const sel = list.find((x) => x.path === S.reportSel);
  rep.innerHTML = `<div class="decide"><article class="panel doc"><div class="prose">${md(sel.content)}</div></article>
    <nav class="panel queue" aria-label="Documents">${list.map((x) => `<button class="q" data-path="${esc(x.path)}" aria-current="${x.path === S.reportSel}"><span>${esc(x.name.replace(/\.md$/, "").replace(/_/g, " to ").replace(/-(review|draft|from-\w+)$/, ""))}</span><span class="n">${fmtTime(x.modified)}</span></button>`).join("")}</nav></div>`;
  rep.querySelectorAll("[data-path]").forEach((b) => (b.onclick = () => { S.reportSel = b.dataset.path; renderReports(); }));
}

// ------------------------------------------------------------------ status
async function renderStatus() {
  const [s, d] = await Promise.all([get("/api/summary"), loadDecisions()]);
  const waiting = { rows: [["Lenders that need your call", d.lenders.review.length, "alert"], ["Agent answers to check", d.lenders.confirm.length, "direct_lender"],
    ["Messages from the agents", d.messages.length, "message"], ["Newsletter drafts", d.drafts.length, "draft"]] };
  waiting.total = waiting.rows.reduce((a, [, n]) => a + n, 0);
  const w = s.this_week, lw = s.last_week;
  const tierLine = (t) => Object.entries(t).filter(([, n]) => n).map(([k, n]) => `${n} in tier ${k}`).join(", ") || "None";
  const notices = [];
  if (!w.covered) notices.push(`<div class="notice">This week isn't fully covered. ${esc(w.coverage_note)}</div>`);
  if (s.stream_gap_at) notices.push(`<div class="notice bad">Filings were missed around ${fmtDate(s.stream_gap_at)}: the radar was offline long enough for its place in the stream to expire.</div>`);
  if (s.git.state === "error") notices.push(`<div class="notice bad">Your last decision didn't reach GitHub. ${esc(s.git.message)}</div>`);
  if (s.sync.state === "error") notices.push(`<div class="notice bad">Couldn't get the latest data. ${esc(s.sync.message)}</div>`);
  const running = s.last_pipeline_commit && (Date.now() - new Date(s.last_pipeline_commit)) < 4 * 3600 * 1000;
  main.innerHTML = `
    <div class="page-head"><div><h1>Status</h1><p class="finding">${s.last_pipeline_commit
      ? `<b>${running ? "The radar is running." : "The radar hasn't run for a while."}</b> The last cloud run finished ${fmtTime(s.last_pipeline_commit)}; it runs every 3 hours.`
      : "<b>No cloud run yet.</b> Start one from the Actions tab on GitHub."}</p></div>
      <button class="btn secondary" id="syncnow">Check for updates now</button></div>
    ${notices.join("")}
    <dl class="panel kpis">
      <div class="kpi"><dt>Charges collected</dt><dd class="lead num">${s.charges.toLocaleString("en-GB")}</dd><div class="sub">${plural(s.lenders, "lender name")}</div></div>
      <div class="kpi"><dt>This week so far</dt><dd class="num">${w.total.toLocaleString("en-GB")}</dd>${tierBar(w.tiers)}</div>
      <div class="kpi"><dt>Last week</dt><dd class="num">${lw.total.toLocaleString("en-GB")}</dd>${tierBar(lw.tiers) || '<div class="sub">None</div>'}</div>
      <div class="kpi"><dt>Watching since</dt><dd>${s.coverage_start ? fmtDate(s.coverage_start) : "—"}</dd><div class="sub">Newest filing ${s.latest_delivered ? fmtDate(s.latest_delivered) : "none yet"}</div></div>
      <div class="kpi"><dt>Data on this PC</dt><dd>${s.sync.mode === "cloud" ? "Cloud copy" : "Local"}</dd><div class="sub">${esc(s.sync.message || "")}${s.sync.at ? `, ${fmtTime(s.sync.at)}` : ""}</div></div>
      <div class="kpi"><dt>Stream position</dt><dd class="num">${s.stream_timepoint ? Number(s.stream_timepoint).toLocaleString("en-GB") : "—"}</dd><div class="sub">${s.repo.has_remote ? "Decisions are saved to GitHub" : "No GitHub remote: decisions stay on this PC"}</div></div>
    </dl>
    <div class="status-grid">
      <section class="panel"><h2>Recent activity</h2>${s.activity.length
        ? `<table><tbody>${s.activity.slice(0, 6).map((a) => `<tr><td class="num" title="${esc(a.at)}">${fmtTime(a.at)}</td><td>${esc(a.text)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="muted">No activity yet. Cloud runs, agent runs and your decisions appear here once this folder is linked to your GitHub repo.</p>`}</section>
      <section class="panel waiting"><h2>Waiting for you</h2><ul class="waiting-list">${waiting.rows.map(([label, n, cat]) => `<li class="${n ? "" : "zero"}"><span>${label}</span>${n ? `<span class="tab-n num" data-cat="${cat}">${n}</span>` : `<span class="num">0</span>`}</li>`).join("")}</ul>
        ${waiting.total ? `<a class="btn primary" href="#/decisions">Open decisions</a>` : `<p class="muted">Nothing needs you. New items appear as the radar runs.</p>`}</section>
    </div>`;
  document.getElementById("syncnow").onclick = async () => { await post("/api/sync", {}); toast("Checking for updates"); };
}

// ------------------------------------------------------------------ start
window.addEventListener("hashchange", route);
route();
loadDecisions().catch(() => {});
setInterval(poll, 5000);
poll();

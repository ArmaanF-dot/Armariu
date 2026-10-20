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
const sic = (codes) => (codes && codes.length ? codes.map(esc).join(", ") : "");

// ------------------------------------------------------------------ routing
const VIEWS = { decisions: renderDecisions, charges: renderCharges, lenders: renderLenders, reports: renderReports, status: renderStatus };
function route() {
  const name = (location.hash.replace(/^#\//, "").split("?")[0]) || "decisions";
  S.view = VIEWS[name] ? name : "decisions";
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === S.view));
  closeDrawer();
  refresh(true);
}
async function refresh(focus = false) {
  try {
    await VIEWS[S.view]();
    if (focus) main.focus({ preventScroll: true });
  } catch (e) {
    main.innerHTML = `<div class="notice bad"><b>Couldn't load this page.</b> ${esc(e.message)}. Check the window running the app is still open.</div>`;
  }
}

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
  else if (sync && sync.state === "syncing") { cls = "busy"; text = "Checking for updates"; }
  else if (sync && sync.state === "error") { cls = "error"; text = sync.message; }
  else if (sync && sync.state === "ok") {
    cls = "ok";
    text = `Up to date. Checked ${fmtTime(sync.at)}` + (sync.mode === "cloud" ? ", using the cloud database" : "");
  }
  el.className = "sync " + cls;
  el.querySelector(".sync-text").textContent = text;
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
async function renderDecisions() {
  const d = await loadDecisions();
  const L = d.lenders;
  const items = queueItems();
  if (!items.find((x) => x.name === S.current)) S.current = items.length ? items[0].name : null;
  const needYou = L.review.length + L.confirm.length;
  const tabs = [
    ["lenders", "Lenders", needYou],
    ["messages", "Messages from agents", d.messages.length],
    ["drafts", "Newsletter drafts", d.drafts.length],
  ];
  const intro = needYou + d.messages.length + d.drafts.length === 0
    ? "Nothing needs you right now. New items appear here as the radar runs."
    : "Everything waiting for your judgement. Your decisions are saved to GitHub and used on the next cloud run.";
  main.innerHTML = `
    <div class="page-head"><div><h1>Decisions</h1><p class="lede">${intro}</p></div></div>
    <div class="tabs" role="tablist">${tabs.map(([k, label, n]) =>
      `<button role="tab" aria-selected="${S.decTab === k}" data-tab="${k}">${label}<span class="count">${n || ""}</span></button>`).join("")}</div>
    <div id="dec-body"></div>`;
  main.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => { S.decTab = b.dataset.tab; renderDecisions(); }));
  const body = document.getElementById("dec-body");
  if (S.decTab === "messages") return renderMessages(body, d.messages);
  if (S.decTab === "drafts") return renderDraftDecisions(body, d.drafts);
  if (!items.length) {
    body.innerHTML = `<div class="card empty"><h2>All lenders are classified</h2><p class="muted">When the radar finds a lender it doesn't recognise, it shows up here.</p></div>`;
    return;
  }
  const item = items.find((x) => x.name === S.current);
  body.innerHTML = `<div class="decide"><section class="card decision" aria-label="Decision">${decisionCard(item)}</section>
    <nav class="card queue" aria-label="Queue">${queueList(L)}</nav></div>`;
  wireDecision(item);
}
function decisionCard(it) {
  const pick = S.picks[it.name] || { category: it.source === "agent" && it.category !== "unknown" ? it.category : "", parent: it.parent || "" };
  S.picks[it.name] = pick;
  const tag = { review: ["Needs your call", ""], confirm: ["Check the agent's answer", "confirm"], waiting: ["Not classified yet", "waiting"] }[it.group];
  const conf = it.confidence != null ? `${Math.round(it.confidence * 100)}% sure` : "";
  const agent = it.source === "agent"
    ? `<div class="agent-view has"><p class="says">The classifier agent says: ${esc(labelFor(it.category))}${conf ? `, ${conf}` : ""}${it.parent ? `. Parent firm: ${esc(it.parent)}` : ""}</p><p class="muted">${esc(it.reason || "")}</p></div>`
    : `<div class="agent-view"><p class="muted">The classifier agent hasn't looked at this name yet. It will on its next run, or you can decide now.</p></div>`;
  const rows = it.examples.map((e) => `<tr>
      <td>${esc(e.company_name || "Name not fetched yet")}<div class="small muted">Company ${esc(e.company_number)}${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</div></td>
      <td>${fmtDate(e.delivered_on)}</td>
      <td>${e.other_chargees.length ? e.other_chargees.map(esc).join("<br>") : '<span class="muted">None</span>'}</td>
      <td>${e.all_assets === 1 ? "Yes" : e.all_assets === 0 ? "No" : '<span class="muted">Not stated</span>'}</td>
      <td><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">View filing</a></td></tr>`).join("");
  const agentAnswer = it.source === "agent" && it.category && it.category !== "unknown" ? it.category : null;
  return `
    <div class="where"><span class="state-tag ${tag[1]}">${tag[0]}</span><span><b>${plural(it.charges, "charge")}</b></span>${it.latest ? `<span>Latest filing ${fmtDate(it.latest)}</span>` : ""}</div>
    <h2 class="lender-name">${esc(it.display)}</h2>
    ${agent}
    <div class="evidence"><h3>Charges in their favour</h3>
      <div class="table-wrap"><table><thead><tr><th>Borrower</th><th>Delivered</th><th>Other chargees</th><th>All assets</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    </div>
    <h3>What kind of lender is this?</h3>
    <div class="picker" role="group" aria-label="Lender category">${S.choices.map((c, i) => `
      <button type="button" class="choice${agentAnswer === c.key ? " agent" : ""}" data-key="${c.key}" aria-pressed="${pick.category === c.key}" title="${esc(c.help)}">
        <span class="top">${esc(c.label)}<span class="key">${i + 1}</span></span><span class="help">${esc(c.help)}</span>
      </button>`).join("")}</div>
    <div class="decide-row">
      <label class="field">Parent firm (optional)<input class="input" id="parent" value="${esc(pick.parent)}" placeholder="e.g. the fund manager behind a fund vehicle" maxlength="100"></label>
      <button class="btn primary" id="save" ${pick.category ? "" : "disabled"}>Save decision <kbd>Enter</kbd></button>
      ${agentAnswer ? `<button class="btn" id="approve">Approve agent's answer <kbd>A</kbd></button>` : ""}
      <button class="btn quiet" id="skip">Skip for now <kbd>S</kbd></button>
    </div>
    <p class="hint">Keys: <span class="key">1</span>–<span class="key">8</span> pick a category, <span class="key">Enter</span> save, <span class="key">J</span>/<span class="key">K</span> next and previous.</p>`;
}
function queueList(L) {
  const group = (title, list, bulk) => !list.length ? "" : `<h3>${title} (${list.length})</h3>
    ${bulk && list.length > 1 ? `<div class="bulk"><button class="btn" id="approve-all">Approve all ${list.length}</button></div>` : ""}
    ${list.map((x) => `<button data-name="${esc(x.name)}" aria-current="${x.name === S.current}"><span>${esc(x.display)}</span><span class="n">${x.charges}</span></button>`).join("")}`;
  return group("Needs your call", L.review) + group("Check the agent's answer", L.confirm, true) + group("Not classified yet", L.waiting);
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
  if (!pick || !pick.category) return toast("Pick a category first", true);
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
  if (document.getElementById("drawer").classList.contains("open")) return;
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
    body.innerHTML = `<div class="card empty"><h2>No messages</h2><p class="muted">Agents leave a note here when something needs a person, like a week that looks wrong.</p></div>`;
    return;
  }
  body.innerHTML = messages.map((m) => `<article class="card doc-card">
      <div class="doc-head"><span class="muted small">${esc(m.name)}, ${fmtTime(m.modified)}</span>
      <button class="btn" data-done="${esc(m.path)}">Mark as done</button></div>
      <div class="prose">${md(m.content)}</div></article>`).join("");
  body.querySelectorAll("[data-done]").forEach((b) => (b.onclick = async () => {
    try { await post("/api/message/done", { path: b.dataset.done }); toast("Message archived"); renderDecisions(); }
    catch (e) { toast(e.message, true); }
  }));
}
function renderDraftDecisions(body, drafts) {
  if (!drafts.length) {
    body.innerHTML = `<div class="card empty"><h2>No drafts waiting</h2><p class="muted">The newsletter agent writes a draft after each fully covered week has been reviewed.</p></div>`;
    return;
  }
  body.innerHTML = drafts.map((d) => draftCard(d)).join("");
  wireDrafts(body, () => renderDecisions());
}

// ------------------------------------------------------------------ drafts (shared)
const STATUS_TEXT = { new: "New draft", ready: "Ready to publish", published: "Published" };
function draftCard(d) {
  const editing = S.editing === d.path;
  return `<article class="card doc-card" data-path="${esc(d.path)}">
    <div class="doc-head"><div><h2>${esc(d.name.replace(/-draft\.md$/, "").replace("_", " to "))}</h2>
      <span class="chip ${d.status === "published" ? "good" : d.status === "ready" ? "" : "warn"}">${STATUS_TEXT[d.status] || d.status}</span>
      <span class="muted small"> Updated ${fmtTime(d.modified)}</span></div>
      <div class="decide-row">${editing
        ? `<button class="btn primary" data-act="save">Save draft</button><button class="btn quiet" data-act="cancel">Cancel</button>`
        : `<button class="btn" data-act="edit">Edit</button>
           ${d.status !== "ready" && d.status !== "published" ? `<button class="btn" data-act="ready">Mark ready</button>` : ""}
           ${d.status !== "published" ? `<button class="btn primary" data-act="published">Mark published</button>` : `<button class="btn quiet" data-act="new">Undo published</button>`}`}
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
  const data = await get("/api/charges?" + params);
  const rowsHtml = (rows) => rows.map((r) => `<tr>
      <td>${tierBadge(r.tier)}</td>
      <td>${fmtDate(r.delivered_on)}</td>
      <td>${esc(r.company_name || "Name not fetched yet")}<div class="small muted">${esc(r.company_number)}</div></td>
      <td>${r.chargees.map((c) => `<button class="linkish" data-lender="${esc(c.name)}">${esc(c.display)}</button>`).join("<br>")}</td>
      <td>${esc(r.primary_label)}</td>
      <td>${r.needs_check ? '<span class="chip warn">Check</span>' : '<span class="chip good">Confirmed</span>'}</td>
      <td>${esc({ outstanding: "Outstanding", "fully-satisfied": "Fully satisfied", "part-satisfied": "Part satisfied", satisfied: "Satisfied" }[r.status] || r.status || "")}${r.satisfied_on ? `<div class="small muted">${fmtDate(r.satisfied_on)}</div>` : ""}</td>
      <td><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("");
  main.innerHTML = `
    <div class="page-head"><div><h1>Charges</h1><p class="lede">Every charge the radar has picked up. Click a lender to see or change how it's classified.</p></div></div>
    <form class="filters" id="cf">
      <label class="field">Search<input type="search" name="q" value="${esc(q.q)}" placeholder="Borrower, company number or lender"></label>
      <label class="field">Tier<select name="tier"><option value="">All tiers</option>${Object.entries(TIER_LABELS).map(([k, v]) => `<option value="${k}" ${q.tier === k ? "selected" : ""}>${k}: ${v}</option>`).join("")}</select></label>
      <label class="field">Checked<select name="check"><option value="">All</option><option value="yes" ${q.check === "yes" ? "selected" : ""}>Needs a check</option><option value="no" ${q.check === "no" ? "selected" : ""}>Confirmed by you</option></select></label>
      <label class="field">Delivered from<input type="date" name="from" value="${esc(q.from)}"></label>
      <label class="field">to<input type="date" name="to" value="${esc(q.to)}"></label>
      <button class="btn" type="submit">Apply</button>
    </form>
    <p class="muted small" id="count">${plural(data.total, "charge")}</p>
    <div class="table-wrap"><table><thead><tr><th>Tier</th><th>Delivered</th><th>Borrower</th><th>Chargees</th><th>Lender type</th><th>Checked</th><th>Status</th><th></th></tr></thead>
      <tbody id="crows">${rowsHtml(data.rows) || `<tr><td colspan="8" class="muted">No charges match these filters.</td></tr>`}</tbody></table></div>
    ${data.total > data.rows.length ? `<button class="btn more" id="cmore">Show more</button>` : ""}`;
  const form = document.getElementById("cf");
  form.onsubmit = (e) => { e.preventDefault(); Object.assign(q, Object.fromEntries(new FormData(form))); renderCharges(); };
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
  root.querySelectorAll("[data-lender]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); openLender(b.dataset.lender); }));
}

// ------------------------------------------------------------------ lenders
async function renderLenders() {
  const q = S.lendersQuery;
  if (!S.choices.length) await loadDecisions();
  const data = await get("/api/lenders?" + new URLSearchParams(q));
  const statusChip = (r) => r.status === "needs_review" ? '<span class="chip warn">Needs your call</span>'
    : r.status === "unclassified" ? '<span class="chip">Waiting for agent</span>'
    : r.status === "ok" ? '<span class="chip good">Decided by you</span>' : '<span class="chip">Not checked</span>';
  main.innerHTML = `
    <div class="page-head"><div><h1>Lenders</h1><p class="lede">Every chargee name the radar has seen, and how it's classified. Your decisions always override the agent and the starter rules.</p></div></div>
    <form class="filters" id="lf">
      <label class="field">Search<input type="search" name="q" value="${esc(q.q)}" placeholder="Lender or parent firm"></label>
      <label class="field">Type<select name="category"><option value="">All types</option>${S.choices.map((c) => `<option value="${c.key}" ${q.category === c.key ? "selected" : ""}>${esc(c.label)}</option>`).join("")}<option value="unknown" ${q.category === "unknown" ? "selected" : ""}>Can't tell</option></select></label>
      <label class="field">Status<select name="status"><option value="">All</option>
        <option value="needs_review" ${q.status === "needs_review" ? "selected" : ""}>Needs your call</option>
        <option value="auto_ok" ${q.status === "auto_ok" ? "selected" : ""}>Not checked by you</option>
        <option value="ok" ${q.status === "ok" ? "selected" : ""}>Decided by you</option>
        <option value="unclassified" ${q.status === "unclassified" ? "selected" : ""}>Waiting for agent</option></select></label>
      <button class="btn" type="submit">Apply</button>
    </form>
    <p class="muted small">${plural(data.total, "lender")}</p>
    <div class="table-wrap"><table><thead><tr><th>Lender</th><th>Type</th><th>Parent firm</th><th>Decided by</th><th>Status</th><th class="num">Charges</th><th>Latest</th></tr></thead><tbody>
      ${data.rows.map((r) => `<tr class="clickable" data-lender="${esc(r.name)}" tabindex="0">
        <td>${esc(r.display)}</td><td>${esc(r.category_label || "")}</td><td>${esc(r.parent || "")}</td>
        <td>${esc(r.source_label)}</td><td>${statusChip(r)}</td><td class="num">${r.charges}</td><td>${fmtDate(r.latest)}</td></tr>`).join("")
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
  let pick = { category: it.category && it.category !== "unknown" ? it.category : "", parent: it.parent || "" };
  body.innerHTML = `
    <p class="muted small">${esc(it.source_label)}${it.category ? `: ${esc(labelFor(it.category))}` : ""}${it.confidence != null && it.source === "agent" ? `, ${Math.round(it.confidence * 100)}% sure` : ""}</p>
    <h2 class="lender-name" id="drawer-title">${esc(it.display)}</h2>
    ${it.reason ? `<div class="agent-view${it.source === "agent" ? " has" : ""}"><p class="muted">${esc(it.reason)}</p></div>` : ""}
    <h3>Change the classification</h3>
    <div class="picker">${S.choices.map((c) => `<button type="button" class="choice" data-key="${c.key}" aria-pressed="${pick.category === c.key}" title="${esc(c.help)}"><span class="top">${esc(c.label)}</span><span class="help">${esc(c.help)}</span></button>`).join("")}</div>
    <div class="decide-row"><label class="field">Parent firm (optional)<input class="input" id="dparent" value="${esc(pick.parent)}" maxlength="100"></label>
      <button class="btn primary" id="dsave">Save decision</button></div>
    <div class="evidence" style="margin-top:24px"><h3>${plural(it.charges, "charge")} in their favour</h3>
      <div class="table-wrap"><table><thead><tr><th>Borrower</th><th>Delivered</th><th>Other chargees</th><th></th></tr></thead><tbody>
      ${it.examples.map((e) => `<tr><td>${esc(e.company_name || "Name not fetched yet")}<div class="small muted">Company ${esc(e.company_number)}${e.sic_codes.length ? `, SIC ${sic(e.sic_codes)}` : ""}</div></td>
        <td>${fmtDate(e.delivered_on)}</td><td>${e.other_chargees.map(esc).join("<br>") || '<span class="muted">None</span>'}</td>
        <td><a href="${esc(e.filing_url)}" target="_blank" rel="noopener">Filing</a></td></tr>`).join("")}
      </tbody></table></div></div>`;
  body.querySelectorAll(".choice").forEach((b) => (b.onclick = () => {
    pick.category = b.dataset.key;
    body.querySelectorAll(".choice").forEach((x) => x.setAttribute("aria-pressed", x === b));
  }));
  document.getElementById("dparent").oninput = (e) => (pick.parent = e.target.value);
  document.getElementById("dsave").onclick = async () => {
    if (!pick.category) return toast("Pick a category first", true);
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
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ------------------------------------------------------------------ reports
async function renderReports() {
  const docs = await get("/api/documents");
  const tabs = [["drafts", "Newsletter drafts", docs.drafts], ["reviews", "Reviewer reports", docs.reviews],
    ["digests", "Weekly digests", docs.digests], ["messages", "Archived messages", docs.messages_done]];
  const list = (tabs.find((t) => t[0] === S.reportsTab) || tabs[0])[2];
  if (!list.find((x) => x.path === S.reportSel)) S.reportSel = list.length ? list[0].path : null;
  main.innerHTML = `
    <div class="page-head"><div><h1>Reports and drafts</h1><p class="lede">What the reviewer and newsletter agents wrote, and the weekly digests they work from.</p></div></div>
    <div class="tabs" role="tablist">${tabs.map(([k, label, l]) => `<button role="tab" aria-selected="${S.reportsTab === k}" data-tab="${k}">${label}<span class="count" style="background:var(--line-soft);color:var(--muted)">${l.length || ""}</span></button>`).join("")}</div>
    <div id="rep"></div>`;
  main.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => { S.reportsTab = b.dataset.tab; S.reportSel = null; S.editing = null; renderReports(); }));
  const rep = document.getElementById("rep");
  if (!list.length) { rep.innerHTML = `<div class="card empty"><h2>Nothing here yet</h2><p class="muted">These appear as the cloud runs complete.</p></div>`; return; }
  if (S.reportsTab === "drafts") { rep.innerHTML = list.map(draftCard).join(""); return wireDrafts(rep, renderReports); }
  const sel = list.find((x) => x.path === S.reportSel);
  rep.innerHTML = `<div class="decide"><article class="card doc-card"><div class="prose">${md(sel.content)}</div></article>
    <nav class="card queue" aria-label="Documents">${list.map((x) => `<button data-path="${esc(x.path)}" aria-current="${x.path === S.reportSel}"><span>${esc(x.name.replace(/\.md$/, ""))}</span></button>`).join("")}</nav></div>`;
  rep.querySelectorAll("[data-path]").forEach((b) => (b.onclick = () => { S.reportSel = b.dataset.path; renderReports(); }));
}

// ------------------------------------------------------------------ status
async function renderStatus() {
  const s = await get("/api/summary");
  const w = s.this_week, lw = s.last_week;
  const tierLine = (t) => Object.entries(t).filter(([, n]) => n).map(([k, n]) => `${n} in tier ${k}`).join(", ") || "none";
  const notices = [];
  if (!w.covered) notices.push(`<div class="notice"><b>This week isn't fully covered.</b> ${esc(w.coverage_note)}</div>`);
  if (s.stream_gap_at) notices.push(`<div class="notice bad"><b>Filings were missed around ${fmtDate(s.stream_gap_at)}.</b> The radar was offline long enough for its place in the stream to expire.</div>`);
  if (s.git.state === "error") notices.push(`<div class="notice bad"><b>Your last decision didn't reach GitHub.</b> ${esc(s.git.message)}</div>`);
  if (s.sync.state === "error") notices.push(`<div class="notice bad"><b>Couldn't get the latest data.</b> ${esc(s.sync.message)}</div>`);
  main.innerHTML = `
    <div class="page-head"><div><h1>Status</h1><p class="lede">Whether the radar is running and how much it has seen.</p></div>
      <button class="btn" id="syncnow">Check for updates now</button></div>
    ${notices.join("")}
    <dl class="stats">
      <div class="card stat"><dt>Last cloud run</dt><dd>${s.last_pipeline_commit ? fmtTime(s.last_pipeline_commit) : "None yet"}<span class="small">Runs every 3 hours on GitHub</span></dd></div>
      <div class="card stat"><dt>Charges collected</dt><dd>${s.charges.toLocaleString("en-GB")}<span class="small">${plural(s.lenders, "lender name")}</span></dd></div>
      <div class="card stat"><dt>Watching since</dt><dd>${s.coverage_start ? fmtDate(s.coverage_start) : "Not started"}<span class="small">Newest filing ${s.latest_delivered ? fmtDate(s.latest_delivered) : "none yet"}</span></dd></div>
      <div class="card stat"><dt>This week so far</dt><dd>${plural(w.total, "charge")}<span class="small">${tierLine(w.tiers)}</span></dd></div>
      <div class="card stat"><dt>Last week</dt><dd>${plural(lw.total, "charge")}<span class="small">${tierLine(lw.tiers)}</span></dd></div>
      <div class="card stat"><dt>Data on this PC</dt><dd>${s.sync.mode === "cloud" ? "Cloud copy" : "Local"}<span class="small">${esc(s.sync.message || "")}${s.sync.at ? `, ${fmtTime(s.sync.at)}` : ""}</span></dd></div>
    </dl>
    <p class="muted small">Stream position ${esc(s.stream_timepoint || "not set")}. ${s.repo.has_remote ? "Decisions are saved to GitHub." : "No GitHub remote: decisions are saved on this PC only."}</p>`;
  document.getElementById("syncnow").onclick = async () => { await post("/api/sync", {}); toast("Checking for updates"); };
}

// ------------------------------------------------------------------ start
window.addEventListener("hashchange", route);
route();
loadDecisions().catch(() => {});
setInterval(poll, 5000);
poll();

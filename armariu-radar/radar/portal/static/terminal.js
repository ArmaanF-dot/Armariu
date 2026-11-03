/* Armariu client portal: terminal mode. A dense, keyboard-first view of the same data.json.
   Read-only: the only things it stores are this browser's own watchlist and filters (localStorage).
   Individuals are never named: the data is already masked, and anything that still reads as a
   private individual is dropped again here. Loaded after insights.js and portal.js. */
"use strict";

const Term = (() => {
  const MODE_KEY = "armariu-portal-mode", STATE_KEY = "armariu-term-v1", WL_KEY = "armariu-watchlist-v1";
  const SCREENS = [
    { id: "mon", code: "MON", label: "Monitor" },
    { id: "leag", code: "LEAG", label: "League table" },
    { id: "sig", code: "SIG", label: "Signals" },
    { id: "rfw", code: "RFW", label: "Refinance watch" },
    { id: "lend", code: "LEND", label: "Lenders" },
    { id: "wl", code: "WL", label: "Watchlist" },
    { id: "rec", code: "REC", label: "Track record" },
  ];
  const DEFAULTS = {
    screen: "mon", week: 0, hideProp: true,
    leag: { sort: "this_week", dir: -1, q: "", cls: "all" },
    sig: { sort: "signal_date", dir: -1, q: "", type: "all", sector: "" },
    rfw: { sort: "odds_12m", dir: -1, q: "", sector: "" },
    lend: { sort: "charges_13w", dir: -1, q: "", cls: "all" },
    wl: { sort: "label", dir: 1, q: "" },
  };
  const GROUP_OF = { direct_lender: "private_credit", trustee_agent: "private_credit", bank: "bank", property: "property",
    abl_invoice: "specialist", asset_finance: "specialist", government_backed: "specialist", other: "other", individual: "other" };
  const LIMIT = 400;   // rows drawn per table; the count line says how many more match
  const isPerson = (s) => /private individual/i.test(s || "");

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  function loadState() {
    const s = store.get(STATE_KEY, {}), out = JSON.parse(JSON.stringify(DEFAULTS));
    for (const k of Object.keys(out)) if (s[k] !== undefined) out[k] = typeof out[k] === "object" ? { ...out[k], ...s[k] } : s[k];
    if (!SCREENS.some((x) => x.id === out.screen)) out.screen = "mon";
    return out;
  }
  let T = loadState(), WL = store.get(WL_KEY, []), saveT = 0;
  const persist = () => { clearTimeout(saveT); saveT = setTimeout(() => store.set(STATE_KEY, T), 150); };
  const saveWL = () => store.set(WL_KEY, WL);

  let root = null, built = false, IDX = null, detail = null, active = null, tables = {};
  const selMem = {};   // selected row per table, kept across redraws

  // ---------------------------------------------------------------- mode
  const isOn = () => document.documentElement.dataset.mode === "terminal";
  function setMode(on) {
    if (on) document.documentElement.dataset.mode = "terminal"; else delete document.documentElement.dataset.mode;
    try { localStorage.setItem(MODE_KEY, on ? "terminal" : "clean"); } catch (e) {}
    hideTip();
    if (on) render(); else route();
  }

  // ---------------------------------------------------------------- data helpers
  const wk = () => D.weeks[D.weeks.length - 1 - T.week] || D.weeks[D.weeks.length - 1] || { league: [], flows: [] };
  const lkey = (name, cls) => `L|${name}|${cls}`, ckey = (num) => `C|${num}`;
  const inWL = (k) => WL.some((w) => w.k === k);
  function toggleWL(k, label) {
    const i = WL.findIndex((w) => w.k === k);
    if (i >= 0) WL.splice(i, 1); else WL.push({ k, label, added: new Date().toISOString().slice(0, 10) });
    saveWL(); refresh();
    flash(i >= 0 ? `Removed ${label} from the watchlist` : `Added ${label} to the watchlist`);
  }
  function buildIndex() {
    const lenders = new Map(), companies = new Map();
    const addL = (name, cls) => { if (!name || cls === "individual" || isPerson(name)) return; const k = lkey(name, cls); if (!lenders.has(k)) lenders.set(k, { k, name, cls, lc: name.toLowerCase() }); };
    D.lenders.forEach((l) => addL(l.lender, l.class));
    D.weeks.forEach((w) => w.league.forEach((r) => addL(r.lender, r.class)));
    const co = (num, name, sector) => {
      if (!num) return null;
      let c = companies.get(num);
      if (!c) { c = { k: ckey(num), num, name: name || num, sector: sector || "", lc: (name || "").toLowerCase(), signals: [], charges: [], watch: null }; companies.set(num, c); }
      if (sector && (!c.sector || c.sector === "Sector not known yet")) c.sector = sector;
      return c;
    };
    D.signals.forEach((s) => co(s.company_number, s.company_name, s.sector).signals.push(s));
    (D.refi_watch?.rows || []).forEach((r) => { co(r.company_number, r.company_name, r.sector).watch = r; });
    D.lenders.forEach((l) => { if (l.class === "individual") return; (l.recent || []).forEach((r) => { const c = co(r.number, r.company, r.sector); if (c) c.charges.push({ ...r, lender: l.lender, cls: l.class }); }); });
    IDX = { lenders, companies };
  }
  const lenderProfile = (name, cls) => D.lenders.find((l) => l.lender === name && l.class === cls);
  const leagueRow = (w, name, cls) => w.league.find((r) => r.lender === name && r.class === cls);
  const sigColor = (t) => ({ add_on: "add_on", refinancing: "refinancing", first_facility: "first_facility" }[t] || "");
  const typeTag = (cls) => `<span class="t-ty" data-c="${esc(cls)}"><span class="dot"></span>${esc(LBL(cls))}</span>`;
  const sigTag = (t) => `<span class="t-sig" data-s="${sigColor(t)}">${esc(D.signal_labels[t] || t)}</span>`;
  const num = (v) => (v === "" || v == null ? null : Number(v));

  // ---------------------------------------------------------------- shell
  function shell() {
    root = document.createElement("div");
    root.id = "term";
    root.setAttribute("aria-label", "Terminal view");
    root.innerHTML = `
      <header class="t-top">
        <span class="t-brand">ARMARIU <b>TERMINAL</b></span>
        <div class="t-cmd" role="combobox" aria-expanded="false" aria-owns="t-sugg"><span class="p">&gt;</span>
          <input id="t-q" type="text" autocomplete="off" spellcheck="false" aria-label="Search lenders and companies, or type a screen code" placeholder="Search a lender, company or number · screen codes like LEAG, SIG">
          <kbd>/</kbd><div class="t-sugg" id="t-sugg" role="listbox"></div></div>
        <span class="t-meta" id="t-meta"></span>
        <div class="t-right">
          <button class="t-btn" id="t-help-btn" title="Keyboard shortcuts (?)">? Keys</button>
          <button class="t-btn" id="t-theme" title="Light or dark">◐</button>
          <button class="t-btn" id="t-exit" title="Back to the clean view (\`)">Clean view</button>
        </div>
      </header>
      <nav class="t-keys" id="t-keys" aria-label="Screens"></nav>
      <div class="t-body"><main class="t-main" id="t-main" tabindex="-1"></main><aside class="t-detail" id="t-detail" aria-label="Details"></aside></div>
      <footer class="t-status" id="t-status"></footer>
      <div class="t-help" id="t-help" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"></div>`;
    document.body.appendChild(root);
    $("#t-exit").onclick = () => setMode(false);
    $("#t-theme").onclick = () => $("#theme").click();
    $("#t-help-btn").onclick = () => help(true);
    $("#t-help").onclick = (e) => { if (e.target.id === "t-help") help(false); };
    searchWire();
    built = true;
  }

  function render() {
    if (!D) return;
    if (!built) shell();
    if (!IDX) buildIndex();
    document.title = `${SCREENS.find((s) => s.id === T.screen).label} · Armariu terminal`;
    $("#t-meta").textContent = `DATA ${dLong(D.built).toUpperCase()} · RULES ${D.rule_version} · ${fmt(D.totals?.companies)} COS`;
    keysBar();
    screen();
    status();
    if (detail) openDetail(detail, false);
  }
  const refresh = () => { if (isOn()) { screen(); keysBar(); if (detail) openDetail(detail, false); } };

  function keysBar() {
    const weeks = D.weeks.map((w, i) => ({ i: D.weeks.length - 1 - i, w })).reverse();
    $("#t-keys").innerHTML = SCREENS.map((s, i) => `<button class="f" data-s="${s.id}" ${T.screen === s.id ? 'aria-current="page"' : ""}><b>${i + 1}</b>${s.code}${s.id === "wl" && WL.length ? ` ${WL.length}` : ""}</button>`).join("")
      + `<span class="sp"></span><span class="wk">WEEK <select id="t-wk" aria-label="Week">${weeks.map(({ i, w }) => `<option value="${i}" ${i === T.week ? "selected" : ""}>${dShort(w.start)}–${dShort(w.end)}${w.complete ? "" : " partial"}</option>`).join("")}</select></span>`
      + `<label title="p"><input type="checkbox" id="t-hp" ${T.hideProp ? "checked" : ""}>Hide property</label>`;
    $$("#t-keys .f").forEach((b) => (b.onclick = () => go(b.dataset.s)));
    $("#t-wk").onchange = (e) => { T.week = Number(e.target.value); persist(); refresh(); };
    $("#t-hp").onchange = (e) => { T.hideProp = e.target.checked; persist(); refresh(); };
  }
  function go(id) { if (!SCREENS.some((s) => s.id === id)) return; T.screen = id; persist(); render(); $("#t-main").scrollTop = 0; }
  function status() {
    $("#t-status").innerHTML = `<span class="ro">● READ-ONLY</span><span><kbd>/</kbd> search <kbd>1</kbd>–<kbd>7</kbd> screens <kbd>j</kbd><kbd>k</kbd> move <kbd>↵</kbd> open <kbd>w</kbd> watch <kbd>?</kbd> all keys</span><span class="cav">${esc(D.caveat)} ${esc(D.not_advice)}</span>`;
  }
  let flashT = 0;
  function flash(msg) { const el = $("#t-status"); if (!el) return; el.innerHTML = `<span class="ro">●</span><span>${esc(msg)}</span>`; clearTimeout(flashT); flashT = setTimeout(status, 1800); }

  // ---------------------------------------------------------------- table component
  /** cols: [{k, label, r (right-align), v(row) sort value, h(row) html, cls}] */
  function table(id, el, { cols, rows, state, keyOf, labelOf, limit = LIMIT, countEl, emptyText = "Nothing matches these filters." }) {
    const t = { id, el, cols, rows, state, keyOf, labelOf, limit, sel: selMem[id] ?? -1, sorted: [] };
    tables[id] = t;
    t.draw = () => {
      const col = cols.find((c) => c.k === state?.sort);
      t.sorted = rows.slice();
      if (col) {
        const v = col.v || ((r) => r[col.k]);
        t.sorted.sort((a, b) => { const x = v(a), y = v(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
          return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * state.dir; });
      }
      if (t.sel >= t.sorted.length) t.sel = t.sorted.length - 1;
      const shown = t.sorted.slice(0, limit);
      el.innerHTML = `<table class="t-tbl"><thead><tr>${cols.map((c) => `<th class="${c.r ? "r" : ""}" ${state && c.k === state.sort ? `aria-sort="${state.dir > 0 ? "ascending" : "descending"}"` : ""}>${c.nosort || !state ? `<span style="display:block;padding:4px 6px">${c.label}</span>` : `<button data-k="${c.k}" title="Sort by ${esc(c.title || c.label)}">${c.label}${state.sort === c.k ? (state.dir > 0 ? " ▲" : " ▼") : ""}</button>`}</th>`).join("")}</tr></thead>
        <tbody>${shown.map((r, i) => `<tr data-i="${i}" class="${i === t.sel ? "sel" : ""}">${cols.map((c) => `<td class="${c.r ? "r" : ""} ${c.cls || ""}">${c.h ? c.h(r, i) : esc(r[c.k])}</td>`).join("")}</tr>`).join("")
          || `<tr class="more"><td colspan="${cols.length}">${esc(emptyText)}</td></tr>`}
          ${t.sorted.length > limit ? `<tr class="more"><td colspan="${cols.length}">Showing ${fmt(limit)} of ${fmt(t.sorted.length)}. Narrow the filters or search to see the rest.</td></tr>` : ""}</tbody></table>`;
      if (countEl) countEl.textContent = `${fmt(t.sorted.length)} rows`;
      $$("th button", el).forEach((b) => (b.onclick = () => { const k = b.dataset.k; if (state.sort === k) state.dir *= -1; else { state.sort = k; state.dir = cols.find((c) => c.k === k).r ? -1 : 1; } persist(); t.draw(); }));
      $$("tbody tr[data-i]", el).forEach((tr) => (tr.onclick = (e) => {
        const i = Number(tr.dataset.i), r = t.sorted[i];
        if (e.target.closest(".star")) { e.stopPropagation(); toggleWL(keyOf(r), labelOf(r)); return; }
        if (e.target.closest("a")) return;
        select(t, i); open(t);
      }));
    };
    t.draw();
    if (!active || !document.body.contains(active.el)) active = t;
    return t;
  }
  function select(t, i) {
    active = t;
    t.sel = selMem[t.id] = Math.max(0, Math.min(t.sorted.length - 1, Math.min(i, t.limit - 1)));
    $$("tbody tr.sel", t.el).forEach((x) => x.classList.remove("sel"));
    const tr = $(`tbody tr[data-i="${t.sel}"]`, t.el);
    if (tr) { tr.classList.add("sel"); tr.scrollIntoView({ block: "nearest" }); }
  }
  function open(t) { const r = t.sorted[t.sel]; if (r) openDetail(t.keyOf(r)); }
  const star = (k) => `<button class="star ${inWL(k) ? "on" : ""}" aria-label="${inWL(k) ? "Remove from" : "Add to"} watchlist" title="Watchlist (w)">${inWL(k) ? "★" : "☆"}</button>`;

  // ---------------------------------------------------------------- column sets
  const LEAGUE_COLS = (w) => {
    const max = Math.max(1, ...w.league.map((r) => r.this_week)), tot = w.league.reduce((a, r) => a + r.this_week, 0) || 1;
    return [
      { k: "_s", label: "", nosort: true, cls: "w1", h: (r) => star(lkey(r.lender, r.class)) },
      { k: "rank", label: "#", title: "rank among all lenders this week", r: true, cls: "w1", v: (r) => r.rank, h: (r) => `<span class="rk">${r.rank}</span>` },
      { k: "lender", label: "Lender", cls: "nm", h: (r) => `${esc(r.lender)}${r.low_observations ? ' <span class="dim" title="Fewer than 5 charges in 13 weeks">·few</span>' : ""}` },
      { k: "class", label: "Type", v: (r) => LBL(r.class), h: (r) => typeTag(r.class) },
      { k: "this_week", label: "Wk", title: "this week", r: true },
      { k: "_bar", label: "", nosort: true, h: (r) => `<span class="t-bar" data-c="${esc(r.class)}"><i style="width:${(r.this_week / max) * 100}%"></i></span>` },
      { k: "share", label: "Shr", title: "share of the week's charges", r: true, v: (r) => r.this_week / tot, h: (r) => `${((r.this_week / tot) * 100).toFixed(1)}%` },
      { k: "trailing_4w", label: "4W", r: true },
      { k: "trailing_13w", label: "13W", r: true },
      { k: "_t", label: "13W trend", nosort: true, h: (r) => { const p = lenderProfile(r.lender, r.class); return p ? spark(p.weeks, r.class) : '<span class="dim">–</span>'; } },
    ];
  };
  const SIG_COLS = [
    { k: "_s", label: "", nosort: true, cls: "w1", h: (s) => star(ckey(s.company_number)) },
    { k: "signal_date", label: "Date", h: (s) => dShort(s.signal_date) },
    { k: "signal_type", label: "Signal", v: (s) => D.signal_labels[s.signal_type], h: (s) => sigTag(s.signal_type) },
    { k: "company_name", label: "Company", cls: "nm" },
    { k: "company_number", label: "No.", cls: "dim" },
    { k: "sector", label: "Sector", cls: "dim" },
    { k: "lender_names", label: "Lender", v: (s) => s.lender_names, h: (s) => esc(s.lender_names) },
    { k: "other_lender", label: "Other side", cls: "dim" },
    { k: "_f", label: "Filing", nosort: true, h: (s) => `<a href="${esc(s.filing_url)}" target="_blank" rel="noopener">CH ↗</a>` },
  ];
  const RFW_COLS = [
    { k: "_s", label: "", nosort: true, cls: "w1", h: (r) => star(ckey(r.company_number)) },
    { k: "company_name", label: "Company", cls: "nm" },
    { k: "company_number", label: "No.", cls: "dim" },
    { k: "sector", label: "Sector", cls: "dim" },
    { k: "odds_12m", label: "Odds 12M", r: true, h: (r) => pct(r.odds_12m, 1) },
    { k: "lift", label: "Lift", r: true, h: (r) => `${r.lift}×` },
    { k: "newest_lender", label: "Newest lender" },
    { k: "newest_created", label: "Newest loan", h: (r) => (r.newest_created ? dShort(r.newest_created) : "") },
    { k: "reasons", label: "Why it scores", cls: "why", nosort: true, h: (r) => `<span title="${esc(r.reasons)}">${esc(r.reasons)}</span>` },
  ];
  const LEND_COLS = (w) => [
    { k: "_s", label: "", nosort: true, cls: "w1", h: (l) => star(lkey(l.lender, l.class)) },
    { k: "lender", label: "Lender", cls: "nm" },
    { k: "class", label: "Type", v: (l) => LBL(l.class), h: (l) => typeTag(l.class) },
    { k: "this_week", label: "Wk", r: true, v: (l) => leagueRow(w, l.lender, l.class)?.this_week ?? 0, h: (l) => leagueRow(w, l.lender, l.class)?.this_week ?? 0 },
    { k: "charges_13w", label: "13W", r: true },
    { k: "top_sector", label: "Top borrower sector", v: (l) => l.sectors[0]?.[0] || "", h: (l) => esc(l.sectors[0]?.[0] || "") },
    { k: "top_share", label: "Of 13W", r: true, v: (l) => topShare(l), h: (l) => `${(topShare(l) * 100).toFixed(0)}%` },
    { k: "_t", label: "13W trend", nosort: true, h: (l) => spark(l.weeks, l.class) },
  ];
  const topShare = (l) => { const t = l.sectors.reduce((a, [, n]) => a + n, 0); return t ? (l.sectors[0]?.[1] || 0) / t : 0; };

  // ---------------------------------------------------------------- filters
  function leagueRows(st) {
    const q = st.q.toLowerCase(), w = wk();
    return w.league.filter((r) => r.class !== "individual" && !isPerson(r.lender))
      .map((r, i) => ({ ...r, rank: i + 1 }))
      .filter((r) => (!T.hideProp || r.class !== "property") && (st.cls === "all" || r.class === st.cls) && (!q || r.lender.toLowerCase().includes(q)));
  }
  function sigRows(st) {
    const q = st.q.toLowerCase();
    return D.signals.filter((s) => !isPerson(s.lender_names + s.other_lender) && (!T.hideProp || s.sector !== PROPERTY_SECTOR) && (st.type === "all" || s.signal_type === st.type)
      && (!st.sector || s.sector === st.sector) && (!q || (s.company_name + " " + s.company_number + " " + s.lender_names + " " + s.other_lender).toLowerCase().includes(q)));
  }
  function rfwRows(st) {
    const q = st.q.toLowerCase();
    return (D.refi_watch?.rows || []).filter((r) => !isPerson(r.newest_lender) && (!T.hideProp || r.sector !== PROPERTY_SECTOR) && (!st.sector || r.sector === st.sector)
      && (!q || (r.company_name + " " + r.company_number + " " + r.newest_lender).toLowerCase().includes(q)));
  }
  function lendRows(st) {
    const q = st.q.toLowerCase();
    return D.lenders.filter((l) => l.class !== "individual" && !isPerson(l.lender) && (!T.hideProp || l.class !== "property") && (st.cls === "all" || l.class === st.cls) && (!q || l.lender.toLowerCase().includes(q)));
  }
  /** One filter row: a search box plus chips or selects. Re-draws only the table it belongs to. */
  function filterBar(id, st, { chips, selects = [] }, redraw) {
    return {
      html: `<div class="t-f" id="f-${id}"><input type="search" data-f="q" placeholder="Filter (f)" value="${esc(st.q)}" aria-label="Filter rows">
        ${chips ? chips.opts.map((o) => `<button class="chip" data-chip="${esc(o.v)}" ${o.c ? `data-c="${esc(o.c)}"` : ""} aria-pressed="${st[chips.key] === o.v}">${o.c ? '<span class="dot"></span>' : ""}${esc(o.label)}</button>`).join("") : ""}
        ${selects.map((s) => `<select data-sel="${s.key}" aria-label="${esc(s.label)}"><option value="">${esc(s.label)}: all</option>${s.opts.map((o) => `<option ${st[s.key] === o ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`).join("")}
        <button class="chip" data-clear title="Clear filters (x)">Clear</button><span class="cnt"></span></div>`,
      wire() {
        const f = $(`#f-${id}`);
        $("[data-f=q]", f).oninput = (e) => { st.q = e.target.value; persist(); redraw(); };
        $$("[data-chip]", f).forEach((b) => (b.onclick = () => { st[chips.key] = b.dataset.chip; persist(); $$("[data-chip]", f).forEach((x) => x.setAttribute("aria-pressed", x === b)); redraw(); }));
        $$("[data-sel]", f).forEach((s) => (s.onchange = () => { st[s.dataset.sel] = s.value; persist(); redraw(); }));
        $("[data-clear]", f).onclick = () => clearFilters(id);
      },
      count: () => $(`#f-${id} .cnt`),
    };
  }
  function clearFilters(id) {
    const st = T[id]; if (!st) return;
    const d = DEFAULTS[id]; Object.keys(d).forEach((k) => { if (k !== "sort" && k !== "dir") st[k] = d[k]; });
    persist(); screen(); flash("Filters cleared");
  }
  const classChips = (rows) => ({ key: "cls", opts: [{ v: "all", label: "All" }, ...CLASS_ORDER.filter((k) => rows.some((r) => r.class === k) && (!T.hideProp || k !== "property")).map((k) => ({ v: k, label: LBL(k), c: k }))] });

  // ---------------------------------------------------------------- screens
  function screen() {
    const m = $("#t-main"); tables = {}; active = null;
    ({ mon, leag, sig, rfw, lend, wl, rec })[T.screen](m);
  }
  function pane(id, title, cls, extra = "", body = "") { return `<section class="t-pane ${cls}" id="p-${id}"><h3>${title}${extra}</h3>${body}</section>`; }

  function mon(m) {
    const w = wk(), ser = D.series[D.series.length - 1] || {}, I = D.insights || {};
    const recent = D.signals.filter((s) => s.signal_date >= w.start && s.signal_date <= w.end);
    const n = (t) => recent.filter((s) => s.signal_type === t).length;
    const tier = D.refi_watch?.backtest?.tiers?.[0];
    const kp = [
      ["Charges, week", fmt(w.raw_charges), `${dShort(w.start)}–${dShort(w.end)}${w.complete ? "" : " · partial"}`],
      ["Private credit", fmt(ser.direct_lender || 0), "charges, latest series week"],
      ["Active lenders", fmt(w.league.filter((r) => r.class !== "individual").length), "after name matching"],
      ["Refinancing", fmt(n("refinancing")), "signals this week"],
      ["First facility", fmt(n("first_facility")), "signals this week"],
      ["Add-on", fmt(n("add_on")), "signals this week"],
      ["Watch, top " + (tier?.top || ""), tier ? pct(tier.hit_rate) : "–", tier ? `refinanced · ${tier.lift}× avg · backtest` : "not built"],
      ["Watchlist", fmt(WL.length), "saved in this browser"],
    ];
    m.innerHTML = `<div class="t-kpis" style="margin-bottom:8px">${kp.map(([l, v, s]) => `<div class="t-kpi"><div class="l">${esc(l)}</div><div class="v">${v}</div><div class="s">${esc(s)}</div></div>`).join("")}</div>
      <div class="t-grid">
        ${pane("leag", "League · top 20", "c5", `<button class="lnk n" data-go="leag">2 LEAG ›</button>`, `<div class="scroll" id="m-leag" style="max-height:440px"></div>`)}
        ${pane("sig", "Signals · latest", "c4", `<button class="lnk n" data-go="sig">3 SIG ›</button>`, `<div class="scroll" id="m-sig" style="max-height:440px"></div>`)}
        ${pane("wl", "Watchlist", "c3", `<button class="lnk n" data-go="wl">6 WL ›</button>`, `<div class="scroll" id="m-wl" style="max-height:440px"></div>`)}
        ${pane("mix", "Charges by lender type · 13 weeks", "c5", `<span class="n">count</span>`, `<div class="t-chart" id="m-mix"></div>`)}
        ${pane("sigw", "Signals by week", "c4", `<span class="n">count</span>`, `<div class="t-chart" id="m-sigw"></div>`)}
        ${pane("rfw", "Refinance watch · top odds", "c3", `<button class="lnk n" data-go="rfw">4 RFW ›</button>`, `<div class="scroll" id="m-rfw" style="max-height:300px"></div>`)}
        ${pane("flows", "Top refinancing flows · 13 weeks", "c6", "", `<div class="scroll" id="m-flows" style="max-height:260px"></div>`)}
        ${pane("moves", "League movers · 4 weeks vs prior 4", "c6", I.league_moves_prior_partial ? `<span class="n">prior window partly observed</span>` : "", `<div class="scroll" id="m-moves" style="max-height:260px"></div>`)}
      </div>`;
    $$("[data-go]", m).forEach((b) => (b.onclick = () => go(b.dataset.go)));
    const lst = { sort: "this_week", dir: -1 };
    table("m-leag", $("#m-leag"), { cols: LEAGUE_COLS(w).filter((c) => !["_t", "trailing_13w"].includes(c.k)), rows: leagueRows({ q: "", cls: "all" }).slice(0, 20), state: lst, keyOf: (r) => lkey(r.lender, r.class), labelOf: (r) => r.lender });
    table("m-sig", $("#m-sig"), { cols: SIG_COLS.filter((c) => ["signal_date", "signal_type", "company_name", "lender_names"].includes(c.k)), rows: sigRows({ q: "", type: "all", sector: "" }).slice(0, 40), state: { sort: "signal_date", dir: -1 }, keyOf: (s) => ckey(s.company_number), labelOf: (s) => s.company_name });
    wlTable($("#m-wl"), true);
    table("m-rfw", $("#m-rfw"), { cols: RFW_COLS.filter((c) => ["company_name", "odds_12m", "lift"].includes(c.k)), rows: rfwRows({ q: "", sector: "" }).slice(0, 25), state: { sort: "odds_12m", dir: -1 }, keyOf: (r) => ckey(r.company_number), labelOf: (r) => r.company_name });
    const flows = (w.flows || []).filter((f) => !isPerson(f.from_lender + f.to_lender));
    table("m-flows", $("#m-flows"), {
      cols: [{ k: "from_lender", label: "From (satisfied)" }, { k: "_a", label: "", nosort: true, h: () => '<span class="dim">→</span>' }, { k: "to_lender", label: "To (new charge)", cls: "nm" }, { k: "signals_13w", label: "Signals", r: true }],
      rows: flows, state: { sort: "signals_13w", dir: -1 }, keyOf: (f) => findLender(f.to_lender), labelOf: (f) => f.to_lender, emptyText: "No flows yet." });
    const moves = (I.league_moves || []).filter((r) => !isPerson(r.lender));
    table("m-moves", $("#m-moves"), {
      cols: [{ k: "rank", label: "#", r: true }, { k: "lender", label: "Lender", cls: "nm" }, { k: "class", label: "Type", v: (r) => LBL(r.class), h: (r) => typeTag(r.class) },
        { k: "move", label: "Move", r: true, v: (r) => (r.prior_rank ? r.prior_rank - r.rank : null), h: (r) => (r.prior_rank ? (r.prior_rank > r.rank ? `<span style="color:var(--t-up)">▲ ${r.prior_rank - r.rank}</span>` : r.prior_rank < r.rank ? `<span style="color:var(--t-down)">▼ ${r.rank - r.prior_rank}</span>` : '<span class="dim">=</span>') : '<span class="dim">new</span>') },
        { k: "charges_4w", label: "4W", r: true }],
      rows: moves, state: { sort: "rank", dir: 1 }, keyOf: (r) => lkey(r.lender, r.class), labelOf: (r) => r.lender, emptyText: "Not enough weeks yet." });
    // Charts: the validated group palette (--g-*) shared with the Insights page.
    const mix = D.series.map((d) => { const o = { week_start: d.week_start, observed_only: d.observed_only }; GROUP_ORDER.forEach((g) => (o[g] = 0)); Object.entries(d).forEach(([k, v]) => { if (GROUP_OF[k]) o[GROUP_OF[k]] += v; }); return o; });
    const labels = I.group_labels || {};
    weeklyStack($("#m-mix"), mix, GROUP_ORDER, labels, { kind: "g", H: 210 });
    $("#m-mix").insertAdjacentHTML("beforeend", legendHTML([...GROUP_ORDER].reverse(), labels, "g") + tableHTML(["Week", ...GROUP_ORDER.map((g) => labels[g] || g)], mix.map((r) => [dShort(r.week_start), ...GROUP_ORDER.map((g) => fmt(r[g]))])));
    const sw = I.signal_weeks || [];
    if (sw.length) {
      weeklyStack($("#m-sigw"), sw, SIG_STACK, D.signal_labels, { kind: "s", H: 210 });
      $("#m-sigw").insertAdjacentHTML("beforeend", legendHTML([...SIG_STACK].reverse(), D.signal_labels, "s") + tableHTML(["Week", ...SIG_STACK.map((k) => D.signal_labels[k])], sw.map((r) => [dShort(r.week_start), ...SIG_STACK.map((k) => fmt(r[k] || 0))])));
    } else $("#m-sigw").innerHTML = `<p class="t-empty">No weekly signal series in this build.</p>`;
    active = tables["m-leag"];
  }
  function findLender(name) { for (const l of IDX.lenders.values()) if (l.name === name) return l.k; return null; }

  function leag(m) {
    const st = T.leag, w = wk(), all = leagueRows({ q: "", cls: "all" });
    const fb = filterBar("leag", st, { chips: classChips(all) }, () => redraw());
    m.innerHTML = pane("L", `League table · ${dLong(w.start)} to ${dLong(w.end)}${w.complete ? "" : " · partial week"}`, "c12", `<span class="n">${fmt(w.raw_charges)} charges · one row per lender after name matching</span>`, fb.html + `<div id="t-leag"></div><p class="t-empty">${esc(D.caveat)}</p>`);
    const redraw = () => { active = table("leag", $("#t-leag"), { cols: LEAGUE_COLS(w), rows: leagueRows(st), state: st, keyOf: (r) => lkey(r.lender, r.class), labelOf: (r) => r.lender, countEl: fb.count() }); };
    redraw(); fb.wire();
  }
  function sig(m) {
    const st = T.sig, sectors = [...new Set(D.signals.map((s) => s.sector))].sort();
    const fb = filterBar("sig", st, { chips: { key: "type", opts: [{ v: "all", label: "All" }, ...SIG_ORDER.map((t) => ({ v: t, label: D.signal_labels[t] }))] }, selects: [{ key: "sector", label: "Sector", opts: sectors }] }, () => redraw());
    m.innerHTML = pane("S", `Signals · rules ${esc(D.rule_version)} · last four weeks`, "c12", `<span class="n">a rule applied to filings, never proof</span>`, fb.html + `<div id="t-sig"></div>`);
    const redraw = () => { active = table("sig", $("#t-sig"), { cols: SIG_COLS, rows: sigRows(st), state: st, keyOf: (s) => ckey(s.company_number), labelOf: (s) => s.company_name, countEl: fb.count() }); };
    redraw(); fb.wire();
  }
  function rfw(m) {
    const W = D.refi_watch;
    if (!W) { m.innerHTML = `<p class="t-empty">The refinance watch has not been built yet.</p>`; return; }
    const st = T.rfw, sectors = [...new Set(W.rows.map((r) => r.sector))].sort();
    const fb = filterBar("rfw", st, { selects: [{ key: "sector", label: "Sector", opts: sectors }] }, () => redraw());
    const tiers = W.backtest?.tiers || [];
    m.innerHTML = `<div class="t-kpis" style="margin-bottom:8px">${tiers.map((t) => `<div class="t-kpi"><div class="l">Top ${esc(t.top)} · backtest</div><div class="v">${pct(t.hit_rate)}</div><div class="s">refinanced in 12M · ${t.lift}× the ${pct(W.backtest.base_rate)} avg</div></div>`).join("")}</div>`
      + pane("R", `Refinance watch · ${esc(W.model_version)}`, "c12", `<span class="n">odds, not a forecast</span>`, fb.html + `<div id="t-rfw"></div><p class="t-empty">${esc(W.caveat)}</p>`);
    const redraw = () => { active = table("rfw", $("#t-rfw"), { cols: RFW_COLS, rows: rfwRows(st), state: st, keyOf: (r) => ckey(r.company_number), labelOf: (r) => r.company_name, countEl: fb.count() }); };
    redraw(); fb.wire();
  }
  function lend(m) {
    const st = T.lend, w = wk();
    const fb = filterBar("lend", st, { chips: classChips(D.lenders) }, () => redraw());
    m.innerHTML = pane("D", "Lender directory · 150 most active over 13 weeks", "c12", "", fb.html + `<div id="t-lend"></div>`);
    const redraw = () => { active = table("lend", $("#t-lend"), { cols: LEND_COLS(w), rows: lendRows(st), state: st, keyOf: (l) => lkey(l.lender, l.class), labelOf: (l) => l.lender, countEl: fb.count() }); };
    redraw(); fb.wire();
  }
  /** Watchlist rows with live figures from this build. Entries no longer in the data say so. */
  function wlRows() {
    const w = wk();
    return WL.map((x) => {
      if (x.k.startsWith("L|")) {
        const [, name, cls] = x.k.split("|"), p = lenderProfile(name, cls), r = leagueRow(w, name, cls);
        return { ...x, kind: "Lender", label: name, cls, wk: r?.this_week ?? 0, w4: r?.trailing_4w ?? null, w13: p?.charges_13w ?? r?.trailing_13w ?? null, weeks: p?.weeks, latest: p?.recent?.[0]?.date || "", note: r ? LBL(cls) : p ? LBL(cls) : "not in this build" };
      }
      const c = IDX.companies.get(x.k.slice(2));
      const last = c?.signals.slice().sort((a, b) => b.signal_date.localeCompare(a.signal_date))[0];
      return { ...x, kind: "Company", label: c?.name || x.label, sigs: c?.signals.length || 0, odds: c?.watch?.odds_12m ?? null, latest: last?.signal_date || c?.charges[0]?.date || "", note: last ? D.signal_labels[last.signal_type] : c?.watch ? "Refinance watch" : c ? "charges seen" : "not in this build" };
    });
  }
  function wlTable(el, compact = false) {
    if (!WL.length) { el.innerHTML = `<p class="t-wl-empty">Nothing saved yet. Press <kbd>w</kbd> on any row, or the ☆ beside it, to watch a lender or company. The list stays in this browser only.</p>`; return; }
    const cols = compact
      ? [{ k: "label", label: "Name", cls: "nm" }, { k: "kind", label: "", cls: "dim", h: (r) => (r.kind === "Lender" ? "L" : "C") }, { k: "fig", label: "Now", r: true, v: (r) => (r.kind === "Lender" ? r.wk : r.sigs), h: (r) => (r.kind === "Lender" ? `${r.wk} wk` : r.odds != null ? pct(r.odds) : `${r.sigs} sig`) }]
      : [{ k: "_s", label: "", nosort: true, cls: "w1", h: (r) => star(r.k) }, { k: "kind", label: "Kind" }, { k: "label", label: "Name", cls: "nm" }, { k: "note", label: "Type / latest", cls: "dim" },
        { k: "wk", label: "Wk", r: true, h: (r) => (r.kind === "Lender" ? r.wk : "") }, { k: "w13", label: "13W", r: true, h: (r) => (r.kind === "Lender" ? r.w13 ?? "" : "") },
        { k: "sigs", label: "Signals 4W", r: true, h: (r) => (r.kind === "Company" ? r.sigs : "") }, { k: "odds", label: "Refi odds", r: true, h: (r) => (r.odds != null ? pct(r.odds, 1) : "") },
        { k: "latest", label: "Latest", h: (r) => (r.latest ? dShort(r.latest) : "") }, { k: "_t", label: "Trend", nosort: true, h: (r) => (r.weeks ? spark(r.weeks, r.cls) : "") }, { k: "added", label: "Added", cls: "dim" }];
    return table(compact ? "m-wl" : "wl", el, { cols, rows: wlRows().filter((r) => compact || !T.wl.q || r.label.toLowerCase().includes(T.wl.q.toLowerCase())), state: compact ? { sort: "label", dir: 1 } : T.wl, keyOf: (r) => r.k, labelOf: (r) => r.label });
  }
  function wl(m) {
    const st = T.wl, fb = filterBar("wl", st, {}, () => { active = wlTable($("#t-wl")) || active; });
    m.innerHTML = pane("W", "Watchlist", "c12", `<span class="n">${WL.length} saved · this browser only, nothing is sent anywhere</span>`, fb.html + `<div id="t-wl"></div>`);
    active = wlTable($("#t-wl")) || null; fb.wire();
  }
  function rec(m) {
    const rows = D.scorecard || [], W = D.refi_watch;
    m.innerHTML = `<div class="t-grid">${pane("SC", `Signal scorecard · backtest · rules ${esc(D.rule_version)}`, "c12", `<span class="n">further charge within 90 days vs matched baseline · 95% Wilson</span>`, `<div id="t-sc"></div>`)}
      ${W ? pane("RT", `Refinance watch tiers · backtest · ${esc(W.model_version)}`, "c6", "", `<div id="t-rt"></div>`) : ""}
      ${pane("BN", "Read this before the numbers", "c6", "", `<div class="pb" style="color:var(--t-muted);line-height:1.5;font-family:var(--sans)">${esc(D.bias_note)} Signals use filing dates only, so nothing looks ahead. A live, append-only log starts once the newsletter is published; it will never be mixed with these figures.</div>`)}</div>`;
    table("sc", $("#t-sc"), { cols: [
      { k: "signal_type", label: "Signal", v: (r) => D.signal_labels[r.signal_type], h: (r) => sigTag(r.signal_type) },
      { k: "signals", label: "n", r: true, v: (r) => num(r.signals), h: (r) => fmt(r.signals) },
      { k: "outcomes_observed", label: "Hits", r: true, v: (r) => num(r.outcomes_observed), h: (r) => fmt(r.outcomes_observed) },
      { k: "hit_rate", label: "Hit rate", r: true, v: (r) => num(r.hit_rate), h: (r) => pct(r.hit_rate, 1) },
      { k: "ci", label: "95% interval", r: true, nosort: true, h: (r) => `${pct(r.ci_low, 1)}–${pct(r.ci_high, 1)}` },
      { k: "baseline_rate", label: "Baseline", r: true, v: (r) => num(r.baseline_rate), h: (r) => (r.baseline_rate ? pct(r.baseline_rate, 1) : "n/a") },
      { k: "lift", label: "Lift", r: true, v: (r) => num(r.lift), h: (r) => (r.lift ? `${r.lift}×` : "") },
      { k: "window", label: "Window", nosort: true, cls: "dim", h: (r) => `${dShort(r.window_start)} ${r.window_start?.slice(0, 4)} – ${dShort(r.window_end)} ${r.window_end?.slice(0, 4)}` },
    ], rows, state: { sort: "lift", dir: -1 }, keyOf: () => null, labelOf: () => "" });
    if (W) table("rt", $("#t-rt"), { cols: [{ k: "top", label: "Tier" }, { k: "hit_rate", label: "Refinanced 12M", r: true, h: (t) => pct(t.hit_rate, 1) }, { k: "lift", label: "Lift", r: true, h: (t) => `${t.lift}×` }],
      rows: W.backtest?.tiers || [], state: null, keyOf: () => null, labelOf: () => "" });
  }

  // ---------------------------------------------------------------- detail pane
  function openDetail(k, focus = true) {
    if (!k) return;
    detail = k;
    const el = $("#t-detail");
    const html = k.startsWith("L|") ? lenderDetail(k) : companyDetail(k);
    if (!html) { closeDetail(); return; }
    el.innerHTML = `<div class="t-detail-in">${html}</div>`;
    el.classList.add("on");
    $(".x", el).onclick = closeDetail;
    $$("[data-open]", el).forEach((b) => (b.onclick = () => openDetail(b.dataset.open)));
    $$("[data-wl]", el).forEach((b) => (b.onclick = () => toggleWL(k, b.dataset.wl)));
    if (focus && innerWidth <= 760) el.scrollTop = 0;
  }
  function closeDetail() { detail = null; const el = $("#t-detail"); if (el) { el.classList.remove("on"); el.innerHTML = ""; } }
  function lenderDetail(k) {
    const [, name, cls] = k.split("|");
    if (cls === "individual" || isPerson(name)) return "";
    const p = lenderProfile(name, cls), w = wk(), r = leagueRow(w, name, cls);
    if (!p && !r) return "";
    const total = p ? p.sectors.reduce((a, [, n]) => a + n, 0) : 0;
    const sigs = D.signals.filter((s) => s.lender_names === name || s.other_lender === name).sort((a, b) => b.signal_date.localeCompare(a.signal_date));
    const flows = (w.flows || []).filter((f) => f.from_lender === name || f.to_lender === name);
    return `<button class="x" aria-label="Close (Esc)">Esc ✕</button>${typeTag(cls)}<h2>${esc(name)}</h2><div class="sub">Lender · ${esc(LBL(cls))}</div>
      <div class="acts"><button class="t-btn ${inWL(k) ? "on" : ""}" data-wl="${esc(name)}">${inWL(k) ? "★ Watching" : "☆ Watch"} <kbd>w</kbd></button></div>
      <div class="stats"><div><b>${r ? r.this_week : 0}</b><span>this week</span></div><div><b>${r ? r.trailing_4w : "–"}</b><span>4 weeks</span></div><div><b>${p ? p.charges_13w : r.trailing_13w}</b><span>13 weeks</span></div></div>
      ${p ? `<h4>Weekly charges, 13 weeks</h4>${spark(p.weeks, cls).replace('class="spark"', 'class="spark" style="width:100%;height:46px"')}
      <h4>Borrower sectors</h4>${p.sectors.map(([s, n]) => `<div class="row" style="grid-template-columns:1fr 90px 36px"><span>${esc(s)}</span><span class="t-bar" data-c="${esc(cls)}"><i style="width:${(n / total) * 100}%"></i></span><span style="text-align:right">${n}</span></div>`).join("")}
      <h4>Latest charges</h4>${p.recent.map((c) => `<div class="row"><span><button class="lnk" data-open="${esc(ckey(c.number))}">${esc(c.company)}</button><br><span class="m">${esc(c.sector)} · ${dShort(c.date)}</span></span><a href="${esc(c.url)}" target="_blank" rel="noopener">CH ↗</a></div>`).join("")}` : ""}
      ${flows.length ? `<h4>Refinancing flows, 13 weeks</h4>${flows.map((f) => `<div class="row"><span>${esc(f.from_lender)} <span class="m">→</span> ${esc(f.to_lender)}</span><span>${f.signals_13w}</span></div>`).join("")}` : ""}
      ${sigs.length ? `<h4>Signals naming this lender · ${sigs.length}</h4>${sigs.slice(0, 10).map((s) => `<div class="row"><span><button class="lnk" data-open="${esc(ckey(s.company_number))}">${esc(s.company_name)}</button><br><span class="m">${esc(D.signal_labels[s.signal_type])} · ${dShort(s.signal_date)}</span></span><a href="${esc(s.filing_url)}" target="_blank" rel="noopener">CH ↗</a></div>`).join("")}` : ""}
      <p class="note">${esc(D.caveat)}</p>`;
  }
  function companyDetail(k) {
    const c = IDX.companies.get(k.slice(2));
    if (!c) return "";
    const sigs = c.signals.slice().sort((a, b) => b.signal_date.localeCompare(a.signal_date)), wr = c.watch;
    const lenders = [...new Map(c.charges.map((x) => [lkey(x.lender, x.cls), x])).values()];
    return `<button class="x" aria-label="Close (Esc)">Esc ✕</button><span class="t-ty">Company · ${esc(c.num)}</span><h2>${esc(c.name)}</h2><div class="sub">${esc(c.sector || "Sector not known yet")}</div>
      <div class="acts"><button class="t-btn ${inWL(k) ? "on" : ""}" data-wl="${esc(c.name)}">${inWL(k) ? "★ Watching" : "☆ Watch"} <kbd>w</kbd></button><a class="t-btn" href="https://find-and-update.company-information.service.gov.uk/company/${encodeURIComponent(c.num)}/charges" target="_blank" rel="noopener">Charges at Companies House ↗</a></div>
      ${wr ? `<h4>Refinance watch · ${esc(wr.model_version)}</h4><div class="stats"><div><b>${pct(wr.odds_12m, 1)}</b><span>odds, 12 months</span></div><div><b>${wr.lift}×</b><span>vs ${pct(wr.base_rate)} avg</span></div><div><b>${wr.newest_created ? dShort(wr.newest_created) : "–"}</b><span>newest loan</span></div></div>
        <div class="row"><span class="m">Newest lender</span><span>${esc(wr.newest_lender)}</span></div><p class="note" style="margin-top:6px">${esc(wr.reasons)}</p>` : ""}
      ${sigs.length ? `<h4>Signals · last four weeks · ${sigs.length}</h4>${sigs.map((s) => `<div class="row"><span>${sigTag(s.signal_type)} <span class="m">${dShort(s.signal_date)}</span><br><span style="color:var(--t-muted);font-family:var(--sans)">${esc(s.explanation)}</span></span><a href="${esc(s.filing_url)}" target="_blank" rel="noopener">CH ↗</a></div>`).join("")}` : ""}
      ${lenders.length ? `<h4>Recent charges seen</h4>${c.charges.map((x) => `<div class="row"><span><button class="lnk" data-open="${esc(lkey(x.lender, x.cls))}">${esc(x.lender)}</button><br><span class="m">${esc(LBL(x.cls))} · ${dShort(x.date)}</span></span><a href="${esc(x.url)}" target="_blank" rel="noopener">CH ↗</a></div>`).join("")}` : ""}
      <p class="note">Company-level facts from public filings only. ${esc(D.not_advice)}</p>`;
  }

  // ---------------------------------------------------------------- global search
  let hits = [], hi = 0;
  function search(q) {
    q = q.trim().toLowerCase();
    if (!q) return [];
    const out = [];
    SCREENS.forEach((s, i) => { if (s.code.toLowerCase().startsWith(q) || s.label.toLowerCase().startsWith(q)) out.push({ kind: "screen", k: s.id, label: s.label, meta: `${i + 1} · ${s.code}`, score: 9 }); });
    const score = (lc, extra = "") => { if (lc === q || extra === q) return 6; if (lc.startsWith(q)) return 4; if (lc.includes(" " + q)) return 3; if (lc.includes(q) || (extra && extra.includes(q))) return 1; return 0; };
    for (const l of IDX.lenders.values()) { const s = score(l.lc); if (s) out.push({ kind: "lender", k: l.k, label: l.name, meta: LBL(l.cls), score: s + 0.5 }); }
    for (const c of IDX.companies.values()) { const s = score(c.lc, c.num.toLowerCase()); if (s) out.push({ kind: "company", k: c.k, label: c.name, meta: `${c.num}${c.watch ? " · watch" : ""}${c.signals.length ? ` · ${c.signals.length} sig` : ""}`, score: s }); }
    return out.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label)).slice(0, 14);
  }
  function searchWire() {
    const inp = $("#t-q"), box = $("#t-sugg"), cmd = $(".t-cmd");
    const draw = () => {
      box.innerHTML = hits.map((h, i) => `<div role="option" data-i="${i}" aria-selected="${i === hi}"><span class="k">${h.kind === "screen" ? "SCREEN" : h.kind === "lender" ? "LENDER" : "COMPANY"}</span><span>${esc(h.label)}</span><span class="m">${esc(h.meta)}</span></div>`).join("")
        || (inp.value.trim() ? `<div class="none">No lender or company matches. Individuals are never listed.</div>` : "");
      const on = !!box.innerHTML; box.classList.toggle("on", on); cmd.setAttribute("aria-expanded", on);
      $$("[data-i]", box).forEach((d) => (d.onmousedown = (e) => { e.preventDefault(); pick(hits[Number(d.dataset.i)]); }));
    };
    inp.oninput = () => { hits = search(inp.value); hi = 0; draw(); };
    inp.onkeydown = (e) => {
      if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) { hi = Math.min(hits.length - 1, hi + 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) { hi = Math.max(0, hi - 1); draw(); e.preventDefault(); }
      else if (e.key === "Enter") { pick(hits[hi]); e.preventDefault(); }
      else if (e.key === "Escape") { inp.value = ""; hits = []; draw(); inp.blur(); e.stopPropagation(); }
    };
    inp.onblur = () => setTimeout(() => { box.classList.remove("on"); cmd.setAttribute("aria-expanded", false); }, 120);
    inp.onfocus = () => { if (inp.value) { hits = search(inp.value); draw(); } };
    function pick(h) {
      if (!h) return;
      inp.value = ""; hits = []; draw(); inp.blur();
      if (h.kind === "screen") go(h.k); else openDetail(h.k);
    }
  }

  // ---------------------------------------------------------------- keyboard
  const KEYS = [
    ["/", "Search lenders and companies, or type a screen code"], ["Ctrl K", "Search, from anywhere"],
    ["1 – 7", "Screens: Monitor, League, Signals, Refinance watch, Lenders, Watchlist, Track record"],
    ["j / ↓", "Next row"], ["k / ↑", "Previous row"], ["g / G", "First / last row"], ["Enter", "Open the selected row"],
    ["w", "Watch or unwatch the selected row (or the open panel)"], ["n / N", "Next / previous table (on Monitor)"],
    ["f", "Filter the current table"], ["x", "Clear this screen's filters"], ["s / r", "Sort by the next column / reverse it"],
    ["[ / ]", "Earlier / later week"], ["p", "Hide or show property lenders"], ["d", "Download the current table as CSV"],
    ["Esc", "Close the panel, the search or this help"], ["?", "This help"], ["`", "Switch to the clean view"],
  ];
  function help(on) {
    const el = $("#t-help");
    if (on) el.innerHTML = `<div class="t-help-in"><h3>Keyboard shortcuts <button class="t-btn" onclick="Term.help(false)">Esc ✕</button></h3><dl>${KEYS.map(([k, d]) => `<div><dt>${k.split(" / ").map((x) => x.split(" ").map((y) => `<kbd>${esc(y)}</kbd>`).join(" ")).join(" / ")}</dt><dd>${esc(d)}</dd></div>`).join("")}</dl>
      <p>Filters, sort order, the week and your watchlist are kept in this browser between visits. Nothing is sent anywhere: the terminal is read-only, like the rest of the site. ${esc(D.not_advice)}</p></div>`;
    el.classList.toggle("on", on);
  }
  function sortNext(t, reverse) {
    if (!t?.state) return;
    if (reverse) t.state.dir *= -1;
    else { const cs = t.cols.filter((c) => !c.nosort), i = cs.findIndex((c) => c.k === t.state.sort); const n = cs[(i + 1) % cs.length]; t.state.sort = n.k; t.state.dir = n.r ? -1 : 1; }
    persist(); t.draw(); flash(`Sorted by ${t.cols.find((c) => c.k === t.state.sort).label || t.state.sort} ${t.state.dir > 0 ? "▲" : "▼"}`);
  }
  function csvOf(t) {
    const cols = t.cols.filter((c) => !c.k.startsWith("_"));
    const strip = (h) => { const d = document.createElement("div"); d.innerHTML = h; return d.textContent.trim(); };
    const rows = t.sorted.map((r) => Object.fromEntries(cols.map((c) => [c.label || c.k, c.h ? strip(c.h(r)) : r[c.k]])));
    save(`armariu_${t.id}_${D.built}.csv`, csv(rows, cols.map((c) => c.label || c.k)));
  }
  addEventListener("keydown", (e) => {
    const field = e.target instanceof Element && e.target.closest("input,select,textarea");
    if (e.key === "`" && !field) { e.preventDefault(); setMode(!isOn()); return; }
    if (!isOn() || !built) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#t-q").focus(); $("#t-q").select(); return; }
    const helpOn = $("#t-help").classList.contains("on");
    if (e.key === "Escape" && e.target.id === "t-q") return;   // the search box clears itself
    if (e.key === "Escape") {
      if (helpOn) help(false); else if (field) e.target.blur(); else if (detail) closeDetail();
      e.stopImmediatePropagation(); return;
    }
    if (field || e.ctrlKey || e.metaKey || e.altKey) return;
    if (helpOn && e.key !== "?") return;
    const t = active && document.body.contains(active.el) ? active : null;
    const k = e.key;
    let used = true;
    if (k === "/") $("#t-q").focus();
    else if (k === "?") help(!helpOn);
    else if (/^[1-7]$/.test(k)) go(SCREENS[Number(k) - 1].id);
    else if (k === "j" || k === "ArrowDown") { if (t) select(t, t.sel + 1); }
    else if (k === "k" || k === "ArrowUp") { if (t) select(t, t.sel - 1); }
    else if (k === "g") { if (t) select(t, 0); }
    else if (k === "G") { if (t) select(t, t.sorted.length - 1); }
    else if (k === "Enter") { if (t && t.sel >= 0) open(t); }
    else if (k === "w") {
      const r = t && t.sel >= 0 ? t.sorted[t.sel] : null;
      if (r && t.keyOf(r)) toggleWL(t.keyOf(r), t.labelOf(r));
      else if (detail) toggleWL(detail, $("#t-detail h2")?.textContent || detail);
    }
    else if (k === "n" || k === "N") { const all = Object.values(tables).filter((x) => document.body.contains(x.el)); if (all.length) { const i = all.indexOf(t); const nx = all[(i + (k === "n" ? 1 : all.length - 1)) % all.length]; select(nx, Math.max(0, nx.sel)); } }
    else if (k === "f") { const f = $(".t-f input[type=search]"); if (f) f.focus(); else used = false; }
    else if (k === "x") clearFilters(T.screen);
    else if (k === "s" || k === "r") sortNext(t, k === "r");
    else if (k === "[" || k === "]") { const nw = Math.max(0, Math.min(D.weeks.length - 1, T.week + (k === "[" ? 1 : -1))); if (nw !== T.week) { T.week = nw; persist(); refresh(); flash(`Week of ${dLong(wk().start)}`); } }
    else if (k === "p") { T.hideProp = !T.hideProp; persist(); refresh(); flash(T.hideProp ? "Property lenders hidden" : "Property lenders shown"); }
    else if (k === "d") { if (t) csvOf(t); }
    else used = false;
    if (used) e.preventDefault();
  }, true);
  addEventListener("resize", () => { if (isOn() && T.screen === "mon") { clearTimeout(Term._rz); Term._rz = setTimeout(screen, 200); } });

  return { isOn, setMode, render, help, _rz: 0 };
})();
if (D && Term.isOn()) Term.render();   // data arrived before this file loaded

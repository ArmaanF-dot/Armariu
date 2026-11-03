/* Armariu client portal. Read-only: everything comes from data.json. */
"use strict";
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cut = (s, n) => (s && s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s || "");
const fmt = (n) => Number(n || 0).toLocaleString("en-GB");
const pct = (x, d = 0) => `${(Number(x) * 100).toFixed(d)}%`;
const dLong = (s) => s ? new Date(s + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
const dShort = (s) => new Date(s + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const CLASS_ORDER = ["direct_lender", "bank", "abl_invoice", "asset_finance", "trustee_agent", "government_backed", "property", "other"];
const SIG_ORDER = ["refinancing", "first_facility", "add_on"];
const SIG_BLURB = { refinancing: "A lender's charge was satisfied within 90 days of a new lender's charge.", first_facility: "No registered charge in the three years before.", add_on: "An existing lender took further security." };
const PROPERTY_SECTOR = "Property and real estate";
let D = null;
const S = { week: 0, cls: "all", hideProp: true, q: "", sigType: "all", sigSector: "", sigN: 24, lq: "", lcls: "all" };
const app = $("#app");
const LBL = (k) => (D && D.class_labels[k]) || k;

// ------------------------------------------------------------------ boot and routing
fetch("data.json").then((r) => r.json()).then((d) => { D = d; foot(); route(); }).catch(() => {
  app.innerHTML = `<div class="boot">The data could not be loaded. Run <code>python -m radar.portal --rebuild</code>.</div>`;
});
addEventListener("hashchange", route);
function route() {
  if (!D) return;
  if (typeof Term !== "undefined" && Term.isOn()) return Term.render();   // terminal mode draws its own screens
  const r = location.hash.replace(/^#\/?/, "").split("?")[0] || "home";
  const views = { home, insights, league, signals, watch, lenders, record, method };
  const v = views[r] ? r : "home";
  document.body.classList.toggle("home", v === "home");
  $$("[data-route]").forEach((a) => a.classList.toggle("on", a.dataset.route === v));
  $("#sheet").classList.remove("open");
  closeDrawer();
  app.innerHTML = "";
  views[v]();
  app.firstElementChild?.classList.add("page-enter");
  document.title = `${{ home: "Overview", insights: "Insights", league: "League table", signals: "Signals", watch: "Refinance watch", lenders: "Lenders", record: "Track record", method: "Method" }[v]} · Armariu`;
  scrollTo({ top: 0 });
  wire();
  onScroll();
}
const nav = $("#nav");
function onScroll() { nav.classList.toggle("solid", scrollY > 40 || !document.body.classList.contains("home")); }
addEventListener("scroll", onScroll, { passive: true });
$("#burger").onclick = () => { const s = $("#sheet"); s.innerHTML = $$(".nav nav a").map((a) => a.outerHTML).join(""); s.classList.toggle("open"); };
$("#theme").onclick = () => { const t = document.documentElement.dataset.theme === "light" ? "dark" : "light"; document.documentElement.dataset.theme = t; try { localStorage.setItem("armariu-portal-theme", t); } catch (e) {} };
$("#download").onclick = downloads;
$("#to-term").onclick = () => Term.setMode(true);
function foot() { $("#foot-note").innerHTML = `${esc(D.caveat)} ${esc(D.not_advice)} Source: Companies House charges register; contains public sector information licensed under the Open Government Licence v3.0. Data built ${dLong(D.built)}, signal rules ${esc(D.rule_version)}.`; }

// ------------------------------------------------------------------ helpers
const week = () => D.weeks[D.weeks.length - 1 - S.week] || { league: [], sectors: [], flows: [], new_entrants: [], syndication: [] };
const prevWeek = () => D.weeks[D.weeks.length - 2 - S.week];
const profile = (name, cls) => D.lenders.find((l) => l.lender === name && l.class === cls) || D.lenders.find((l) => l.lender === name);
const tag = (cls) => `<span class="tag" data-c="${esc(cls)}"><span class="dot"></span>${esc(LBL(cls))}</span>`;
function spark(vals, cls) {
  if (!vals || !vals.length) return "";
  const m = Math.max(1, ...vals), w = 96, h = 26;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - 2 - (v / m) * (h - 4)}`).join(" ");
  return `<svg class="spark" data-c="${esc(cls)}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none"/></svg>`;
}
function countUp(el) {
  const to = Number(el.dataset.count), dec = Number(el.dataset.dec || 0), suf = el.dataset.suf || "";
  if (still) { el.textContent = to.toLocaleString("en-GB", { maximumFractionDigits: dec }) + suf; return; }
  const t0 = performance.now(), dur = 1400;
  const step = (t) => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 4);
    el.textContent = (to * e).toLocaleString("en-GB", { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suf; if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
function wire() {
  const io = new IntersectionObserver((ens) => ens.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); $$("[data-count]", en.target).forEach(countUp); if (en.target.dataset.count) countUp(en.target); io.unobserve(en.target); } }), { threshold: 0.12 });
  $$(".reveal, [data-count]").forEach((el) => io.observe(el));
  $$("[data-lender]").forEach((el) => (el.onclick = () => openLender(el.dataset.lender, el.dataset.cls)));
  $$("[data-go]").forEach((el) => (el.onclick = () => (location.hash = el.dataset.go)));
}
const tip = $("#tip");
function showTip(html, x, y) { tip.innerHTML = html; tip.classList.add("on"); const b = tip.getBoundingClientRect(); tip.style.left = Math.min(innerWidth - b.width - 10, x + 14) + "px"; tip.style.top = (y - b.height - 12) + "px"; }
const hideTip = () => tip.classList.remove("on");
function signalsLastWeek() { const w = week(); return D.signals.filter((s) => s.signal_date >= w.start && s.signal_date <= w.end); }

// ------------------------------------------------------------------ overview
function home() {
  const w = week(), p = prevWeek(), ser = D.series[D.series.length - 1] || {}, serPrev = D.series[D.series.length - 2] || {};
  const lw = signalsLastWeek(), refi = lw.filter((s) => s.signal_type === "refinancing").length;
  const fair = p && p.complete;   // never compare with a week the radar only partly watched
  const delta = (a, b) => (fair && b ? `<div class="d ${a < b ? "dn" : ""}">${a >= b ? "▲" : "▼"} ${Math.abs(Math.round(((a - b) / b) * 100))}% vs prior week</div>` : `<div class="d" style="color:#b9bcc6">${w.complete ? "first full week on record" : "partial week"}</div>`);
  const movers = w.league.filter((r) => r.class !== "property" && r.class !== "individual").slice(0, 6);
  const maxM = Math.max(1, ...movers.map((m) => m.this_week));
  const tick = D.signals.slice(0, 24).map((s) => `<span class="tk"><span class="dot" style="--c:var(--accent)"></span><b>${esc(D.signal_labels[s.signal_type])}</b>${esc(s.company_name)} · ${esc(cut(s.lender_names, 34))}</span>`).join("");
  const bt = D.scorecard.filter((r) => r.lift);
  app.innerHTML = `<div>
  <section class="hero" data-skyline data-seed="11" data-photo="img/hero.jpg">
    <div class="hero-inner">
      <span class="eyebrow"><i></i>Live from Companies House · week of ${dShort(w.start)}</span>
      <h1>Who is lending to UK companies, <em>this week.</em></h1>
      <p class="lede">Every new charge registered at Companies House, read lender by lender. League tables, refinancing signals and a public track record, built from filings alone.</p>
      <div class="kpis">
        <div class="kpi"><div class="v num" data-count="${w.raw_charges}">0</div><div class="l">charges registered</div>${delta(w.raw_charges, p && p.raw_charges)}</div>
        <div class="kpi"><div class="v num" data-count="${ser.direct_lender || 0}">0</div><div class="l">to private credit lenders</div>${delta(ser.direct_lender || 0, serPrev.direct_lender)}</div>
        <div class="kpi"><div class="v num" data-count="${refi}">0</div><div class="l">refinancing signals</div><div class="d" style="color:#b9bcc6">pattern, not proof</div></div>
        <div class="kpi"><div class="v num" data-count="${w.league.length}">0</div><div class="l">active lenders</div><div class="d" style="color:#b9bcc6">after name matching</div></div>
      </div>
    </div>
  </section>
  <div class="ticker" aria-label="Latest signals"><div class="ticker-track">${tick}${tick}</div></div>
  <div class="wrap">
    <section class="section reveal">
      <div class="sec-head"><div><span class="kicker">01 · The market</span><h2>Thirteen weeks of secured lending</h2><p>Charges registered each week, by the type of lender that took security. Hover to read a week.</p></div>
        <button class="pill-btn" data-go="#/league">Open the league table</button></div>
      <div class="grid">
        <div class="card s8 glow"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px"><div><h3>Lender mix by week</h3><p class="sub" style="margin:0">Share of charges registered, by type of lender</p></div><div class="seg" role="group" aria-label="Measure"><button data-m="share" aria-pressed="true">Share</button><button data-m="count" aria-pressed="false">Count</button></div></div><div class="chart" id="trend"></div><div class="legend" id="trend-legend"></div>
          <p class="caveat">${esc(D.caveat)} Shaded weeks: ${esc(D.sample_caveat)}</p></div>
        <div class="card s4"><h3>Most active, excluding property</h3><p class="sub">Week of ${dLong(w.start)}</p>
          ${movers.map((m, i) => `<div class="flow" style="grid-template-columns:22px 1fr 90px 36px;cursor:pointer" data-lender="${esc(m.lender)}" data-cls="${esc(m.class)}">
            <span class="num" style="color:var(--faint)">${i + 1}</span><span><span class="lname">${esc(m.lender)}</span><br><span style="font-size:12px;color:var(--faint)">${esc(LBL(m.class))}</span></span>
            <div class="bar" data-c="${esc(m.class)}"><i style="width:${(m.this_week / maxM) * 100}%;animation-delay:${i * 80}ms"></i></div><span class="num r" style="text-align:right">${m.this_week}</span></div>`).join("")}
        </div>
      </div>
    </section>
    <section class="section reveal">
      <div class="sec-head"><div><span class="kicker">02 · Movement</span><h2>Where borrowers moved</h2><p>Lender to lender, from satisfactions filed near a new charge over the last 13 weeks. A pattern in the filings, never proof of a refinancing.</p></div></div>
      <div class="grid">
        <div class="card s7"><h3>Top refinancing flows</h3><p class="sub">13 weeks to ${dLong(w.end)}</p>
          ${(w.flows || []).map((f) => `<div class="flow"><span>${esc(f.from_lender)}</span><span class="ar">→</span><span class="lname">${esc(f.to_lender)}</span><span class="num n">${f.signals_13w}</span></div>`).join("") || `<p class="empty">No flows yet.</p>`}</div>
        <div class="card s5"><h3>New to the register</h3><p class="sub">Lenders with no charge in the prior 12 months among observed borrowers</p>
          <ul class="mini-list">${(w.new_entrants || []).slice(0, 8).map((e) => `<li><span>${tag(e.class)} &nbsp;${esc(e.lender)}</span><span class="num">${e.this_week}</span></li>`).join("") || `<li>None this week</li>`}</ul></div>
      </div>
    </section>
    <section class="section reveal">
      <div class="sec-head"><div><span class="kicker">03 · Signals</span><h2>Worth a look this week</h2><p>Each signal is a rule applied to filings, with the filing linked. Facts only: no amounts, terms or purposes are inferred.</p></div><button class="pill-btn" data-go="#/signals">All signals</button></div>
      <div class="sig-grid">${pickSignals(6).map(sigCard).join("")}</div>
    </section>
    <section class="section reveal">
      <div class="sec-head"><div><span class="kicker">04 · Track record</span><h2>Do the signals mean anything?</h2><p>Each signal type is scored against a matched baseline of charges without it. Labelled as a backtest until the live record has matured.</p></div><button class="pill-btn" data-go="#/record">See the scorecard</button></div>
      <div class="grid">${bt.map((r) => `<div class="card s4"><span class="badge warn">Backtest</span><h3 style="margin-top:14px">${esc(D.signal_labels[r.signal_type])}</h3>
        <div class="lift num" data-count="${r.lift}" data-dec="2" data-suf="×">0</div><p class="sub" style="margin-top:8px">${pct(r.hit_rate)} saw a further charge within 90 days vs ${pct(r.baseline_rate)} for comparable charges. n = ${fmt(r.signals)}.</p></div>`).join("")}</div>
    </section>
    ${homeInsights()}
  </div></div>`;
  ArmariuSkyline.mount($(".hero"));
  drawHomeInsights();
  let mode = "share";
  trendChart($("#trend"), $("#trend-legend"), mode);
  $$(".seg button").forEach((b) => (b.onclick = () => { mode = b.dataset.m; $$(".seg button").forEach((x) => x.setAttribute("aria-pressed", x === b)); trendChart($("#trend"), $("#trend-legend"), mode); }));
}
function pickSignals(n) {
  const seen = new Set(), out = [];
  const pool = D.signals.filter((s) => s.sector !== PROPERTY_SECTOR).sort((a, b) => SIG_ORDER.indexOf(a.signal_type) - SIG_ORDER.indexOf(b.signal_type) || b.signal_date.localeCompare(a.signal_date));
  for (const s of pool) { if (seen.has(s.company_number)) continue; seen.add(s.company_number); out.push(s); if (out.length >= n) break; }
  return out;
}
function sigCard(s) {
  return `<article class="sig" data-t="${esc(s.signal_type)}"><div class="top"><span class="type">${esc(D.signal_labels[s.signal_type])}</span><span class="num" style="font-size:12px;color:var(--faint)">${dLong(s.signal_date)}</span></div>
  <h4>${esc(s.company_name)}</h4><p>${esc(s.explanation)}</p>
  <div class="meta"><span>${esc(s.sector)} · ${esc(s.company_number)}</span><a href="${esc(s.filing_url)}" target="_blank" rel="noopener">Filing ↗</a></div></article>`;
}

// ------------------------------------------------------------------ trend chart (stacked area)
function trendChart(el, legend, mode = "share") {
  const raw = D.series, keys = CLASS_ORDER.filter((k) => raw.some((d) => d[k]));
  const share = mode === "share";
  const data = raw.map((d) => { const t = keys.reduce((a, k) => a + (d[k] || 0), 0) || 1; return share ? { ...Object.fromEntries(keys.map((k) => [k, ((d[k] || 0) / t) * 100])), week_start: d.week_start, observed_only: d.observed_only, _raw: d } : { ...d, _raw: d }; });
  const W = el.clientWidth || 700, H = 280, pl = 44, pr = 26, pt = 10, pb = 26;
  const tot = data.map((d) => keys.reduce((a, k) => a + (d[k] || 0), 0)), max = share ? 100 : Math.max(1, ...tot) * 1.08;
  const x = (i) => pl + (i / Math.max(1, data.length - 1)) * (W - pl - pr), y = (v) => pt + (1 - v / max) * (H - pt - pb);
  let acc = data.map(() => 0), paths = "";
  [...keys].reverse().forEach((k, ki) => {
    const lo = acc.slice(), hi = acc.map((a, i) => a + (data[i][k] || 0)); acc = hi;
    const top = hi.map((v, i) => `${x(i)},${y(v)}`).join(" L"), bot = lo.map((v, i) => `${x(i)},${y(v)}`).reverse().join(" L");
    paths += `<path class="area" data-k="${k}" data-c="${k}" style="fill:color-mix(in srgb,var(--c) 70%,transparent);animation:pg ${0.5 + ki * 0.12}s ease both" d="M${top} L${bot} Z"/>`;
  });
  const covIdx = data.findIndex((d) => !d.observed_only);
  const shade = covIdx > 0 ? `<rect x="${pl}" y="${pt}" width="${x(covIdx) - pl}" height="${H - pt - pb}" fill="url(#hatch)"/>` : "";
  const ticks = share ? [0, 50, 100] : [0, 0.5, 1].map((f) => Math.round(max * f / 1.08 / 10) * 10);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}"><defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.5" height="6" fill="rgba(255,255,255,.09)"/></pattern></defs>
    <g class="grid">${ticks.map((t) => `<line x1="${pl}" x2="${W - pr}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>
    <g class="axis">${ticks.map((t) => `<text x="${pl - 6}" y="${y(t) + 4}" text-anchor="end">${fmt(t)}${share ? "%" : ""}</text>`).join("")}
    ${data.map((d, i) => (i % 2 === 0 || i === data.length - 1) ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${dShort(d.week_start)}</text>` : "").join("")}</g>
    ${paths}${shade}<line id="hov" y1="${pt}" y2="${H - pb}" stroke="var(--text)" stroke-opacity=".3" style="display:none"/></svg>`;
  legend.innerHTML = keys.map((k) => `<span data-c="${k}"><span class="dot"></span>${esc(LBL(k))}</span>`).join("");
  const svg = $("svg", el), hov = $("#hov", el);
  svg.onmousemove = (e) => {
    const b = svg.getBoundingClientRect(), mx = ((e.clientX - b.left) / b.width) * W;
    const i = Math.max(0, Math.min(data.length - 1, Math.round(((mx - pl) / (W - pl - pr)) * (data.length - 1)))), d = data[i];
    hov.style.display = ""; hov.setAttribute("x1", x(i)); hov.setAttribute("x2", x(i));
    showTip(`<b>Week of ${dLong(d.week_start)}</b>${d.observed_only ? '<br><span style="color:var(--faint)">observed borrowers only</span>' : ""}<br>${keys.filter((k) => d._raw[k]).map((k) => `<span data-c="${k}" style="display:flex;gap:8px;align-items:center"><span class="dot"></span>${esc(LBL(k))}<b class="num" style="margin-left:auto;padding-left:14px">${fmt(d._raw[k])}${share ? ` · ${d[k].toFixed(0)}%` : ""}</b></span>`).join("")}`, e.clientX, e.clientY);
  };
  svg.onmouseleave = () => { hov.style.display = "none"; hideTip(); };
}

// ------------------------------------------------------------------ league
function league() {
  const weeks = D.weeks.map((w, i) => `<option value="${D.weeks.length - 1 - i}">${dLong(w.start)} – ${dLong(w.end)}${w.complete ? "" : " (partial)"}</option>`).reverse().join("");
  app.innerHTML = `<div class="wrap page-top"><div class="sec-head"><div><span class="kicker">League table</span><h2>Who lent the most</h2><p>New charges registered per lender, with trailing 4 and 13 week counts. One lender, one row: spellings, subsidiaries and trading names are merged.</p></div></div>
    <div class="controls"><select id="wk" aria-label="Week">${weeks}</select>
      <label class="switch"><input type="checkbox" id="hp" ${S.hideProp ? "checked" : ""}>Hide property lenders</label>
      <div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg><input id="lq" placeholder="Find a lender" value="${esc(S.q)}"></div></div>
    <div class="controls" id="cls"></div>
    <div class="card" style="padding:6px 10px"><div style="overflow-x:auto"><table class="tbl"><thead><tr><th>#</th><th>Lender</th><th class="hide-sm">Type</th><th class="r">This week</th><th class="hide-sm"></th><th class="r">4 weeks</th><th class="r">13 weeks</th><th class="hide-sm">Trend</th></tr></thead><tbody id="lt"></tbody></table></div></div>
    <p class="caveat" id="lcav"></p></div>`;
  $("#wk").value = S.week;
  $("#wk").onchange = (e) => { S.week = Number(e.target.value); draw(); };
  $("#hp").onchange = (e) => { S.hideProp = e.target.checked; draw(); };
  $("#lq").oninput = (e) => { S.q = e.target.value; draw(); };
  function draw() {
    const w = week(), q = S.q.toLowerCase();
    const classes = ["all", ...CLASS_ORDER.filter((k) => w.league.some((r) => r.class === k))];
    $("#cls").innerHTML = classes.map((k) => `<button class="chip" data-k="${k}" data-c="${k}" aria-pressed="${S.cls === k}">${k === "all" ? "All lenders" : `<span class="dot"></span>${esc(LBL(k))}`}</button>`).join("");
    $$("#cls .chip").forEach((b) => (b.onclick = () => { S.cls = b.dataset.k; draw(); }));
    const rows = w.league.filter((r) => r.class !== "individual" && (!S.hideProp || r.class !== "property") && (S.cls === "all" || r.class === S.cls) && (!q || r.lender.toLowerCase().includes(q)));
    const max = Math.max(1, ...rows.map((r) => r.this_week));
    $("#lt").innerHTML = rows.slice(0, 50).map((r, i) => { const p = profile(r.lender, r.class);
      return `<tr data-lender="${esc(r.lender)}" data-cls="${esc(r.class)}" style="animation:pg .5s ${Math.min(i, 20) * 25}ms both"><td class="rank">${i + 1}</td><td><span class="lname">${esc(r.lender)}</span>${r.low_observations ? '<span class="flag" title="Fewer than 5 charges in 13 weeks">few obs.</span>' : ""}</td>
      <td class="hide-sm">${tag(r.class)}</td><td class="r num">${r.this_week}</td><td class="hide-sm" style="width:18%"><div class="bar" data-c="${esc(r.class)}"><i style="width:${(r.this_week / max) * 100}%"></i></div></td>
      <td class="r num">${r.trailing_4w}</td><td class="r num">${r.trailing_13w}</td><td class="hide-sm">${p ? spark(p.weeks, r.class) : ""}</td></tr>`; }).join("") || `<tr><td colspan="8" class="empty">No lenders match.</td></tr>`;
    $("#lcav").textContent = `Window ${dLong(w.start)} to ${dLong(w.end)}. ${w.raw_charges} charges registered; a charge naming several lenders counts once for each. ${D.caveat} ${w.complete ? "" : D.sample_caveat}`;
    wire();
  }
  draw();
}

// ------------------------------------------------------------------ signals
function signals() {
  const sectors = [...new Set(D.signals.map((s) => s.sector))].sort();
  app.innerHTML = `<div class="wrap page-top"><div class="sec-head"><div><span class="kicker">Signals</span><h2>What the filings suggest</h2><p>Rules ${esc(D.rule_version)}, applied to every new charge in the last four weeks. Each card states only what the filings show and links to them.</p></div></div>
    <div class="controls" id="types"></div>
    <div class="controls"><select id="sec"><option value="">All sectors</option>${sectors.map((s) => `<option ${s === S.sigSector ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
      <label class="switch"><input type="checkbox" id="hp" ${S.hideProp ? "checked" : ""}>Hide property borrowers</label>
      <div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg><input id="sq" placeholder="Company, number or lender"></div></div>
    <p class="caveat" id="scount" style="margin:0 0 14px"></p><div class="sig-grid" id="sg"></div><div class="more"><button class="pill-btn" id="more">Show more</button></div></div>`;
  $("#sec").onchange = (e) => { S.sigSector = e.target.value; S.sigN = 24; draw(); };
  $("#hp").onchange = (e) => { S.hideProp = e.target.checked; S.sigN = 24; draw(); };
  $("#sq").oninput = (e) => { S.sq = e.target.value.toLowerCase(); S.sigN = 24; draw(); };
  $("#more").onclick = () => { S.sigN += 24; draw(); };
  function draw() {
    const base = D.signals.filter((s) => (!S.hideProp || s.sector !== PROPERTY_SECTOR) && (!S.sigSector || s.sector === S.sigSector) && (!S.sq || (s.company_name + s.company_number + s.lender_names + s.other_lender).toLowerCase().includes(S.sq)));
    const n = (t) => base.filter((s) => t === "all" || s.signal_type === t).length;
    $("#types").innerHTML = ["all", ...SIG_ORDER].map((t) => `<button class="chip" data-t="${t}" aria-pressed="${S.sigType === t}" title="${esc(SIG_BLURB[t] || "")}">${t === "all" ? "All signals" : esc(D.signal_labels[t])} <span class="num" style="opacity:.6">${fmt(n(t))}</span></button>`).join("");
    $$("#types .chip").forEach((b) => (b.onclick = () => { S.sigType = b.dataset.t; S.sigN = 24; draw(); }));
    const rows = base.filter((s) => S.sigType === "all" || s.signal_type === S.sigType);
    $("#scount").textContent = `${fmt(rows.length)} signals. ${S.sigType !== "all" ? SIG_BLURB[S.sigType] : ""}`;
    $("#sg").innerHTML = rows.slice(0, S.sigN).map(sigCard).join("") || `<p class="empty">No signals match.</p>`;
    $("#more").style.display = rows.length > S.sigN ? "" : "none";
  }
  draw();
}

// ------------------------------------------------------------------ refinance watch
function watch() {
  const W = D.refi_watch;
  if (!W) { app.innerHTML = `<div class="wrap page-top"><p class="empty">The refinance watch has not been built yet.</p></div>`; return; }
  const bt = W.backtest, months = Math.round(W.window_days / 30.4);
  app.innerHTML = `<div class="wrap page-top"><div class="sec-head"><div><span class="kicker">Refinance watch</span><h2>Who is most likely to refinance next</h2><p>Every company with a loan still registered against it, scored on what its filings show: the age of its newest loan and the lender's type, its recent borrowing, how many loans it has outstanding, and whether its lender is cutting back. ${esc(W.caveat)}</p></div><span class="badge warn">Backtest · ${esc(W.model_version)}</span></div>
    <div class="kpis" style="margin-bottom:28px">${bt.tiers.map((t) => `<div class="kpi"><div class="v num">${pct(t.hit_rate)}</div><div class="l">of the top ${esc(t.top)} refinanced within ${months} months</div><div class="d" style="color:#b9bcc6">${t.lift}× the ${pct(bt.base_rate)} average</div></div>`).join("")}</div>
    ${watchCharts()}
    <p class="caveat" style="margin:-14px 0 22px">Tested on ${fmt(bt.rows)} company-dates from ${esc(bt.test_cutoffs[0])} onwards, which the score never saw while it was built (it learned from the year to ${esc(bt.train_end)}).</p>
    <div class="controls"><label class="switch"><input type="checkbox" id="hp" ${S.hideProp ? "checked" : ""}>Hide property borrowers</label>
      <div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg><input id="wq" placeholder="Company, number or lender"></div></div>
    <div class="card" style="padding:6px 8px;overflow-x:auto"><table class="tbl"><thead><tr><th>Company</th><th>Sector</th><th class="r">Odds, ${months} months</th><th>Why it scores</th><th>Newest lender</th></tr></thead><tbody id="wt"></tbody></table></div></div>`;
  let q = "";
  drawWatchCharts();
  $("#hp").onchange = (e) => { S.hideProp = e.target.checked; draw(); };
  $("#wq").oninput = (e) => { q = e.target.value.toLowerCase(); draw(); };
  function draw() {
    const rows = W.rows.filter((r) => (!S.hideProp || r.sector !== PROPERTY_SECTOR) && (!q || (r.company_name + r.company_number + r.newest_lender).toLowerCase().includes(q))).slice(0, 100);
    $("#wt").innerHTML = rows.map((r) => `<tr><td><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">${esc(r.company_name)}</a><div style="font:11px var(--mono);color:var(--faint)">${esc(r.company_number)}</div></td><td>${esc(r.sector)}</td><td class="r num">${pct(r.odds_12m)}<div style="font:11px var(--mono);color:var(--faint)">${r.lift}× average</div></td><td style="font-size:13px;color:var(--muted)">${esc(r.reasons)}</td><td style="font-size:13px">${esc(r.newest_lender)}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No companies match.</td></tr>`;
  }
  draw();
}

// ------------------------------------------------------------------ lenders
function lenders() {
  app.innerHTML = `<div class="wrap page-top"><div class="sec-head"><div><span class="kicker">Lenders</span><h2>The lender directory</h2><p>The 150 most active lenders over 13 weeks, each one entity after name matching. Open one for its sector mix and latest charges.</p></div></div>
    <div class="controls" id="lc"></div><div class="controls"><label class="switch"><input type="checkbox" id="hp" ${S.hideProp ? "checked" : ""}>Hide property lenders</label>
    <div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg><input id="lq2" placeholder="Find a lender" value="${esc(S.lq)}"></div></div><div class="lgrid" id="lg"></div></div>`;
  $("#hp").onchange = (e) => { S.hideProp = e.target.checked; draw(); };
  $("#lq2").oninput = (e) => { S.lq = e.target.value.toLowerCase(); draw(); };
  function draw() {
    $("#lc").innerHTML = ["all", ...CLASS_ORDER.filter((k) => D.lenders.some((l) => l.class === k))].map((k) => `<button class="chip" data-k="${k}" data-c="${k}" aria-pressed="${S.lcls === k}">${k === "all" ? "All" : `<span class="dot"></span>${esc(LBL(k))}`}</button>`).join("");
    $$("#lc .chip").forEach((b) => (b.onclick = () => { S.lcls = b.dataset.k; draw(); }));
    const rows = D.lenders.filter((l) => (!S.hideProp || l.class !== "property") && (S.lcls === "all" || l.class === S.lcls) && (!S.lq || l.lender.toLowerCase().includes(S.lq)));
    $("#lg").innerHTML = rows.map((l, i) => `<button class="lcard" data-c="${esc(l.class)}" data-lender="${esc(l.lender)}" data-cls="${esc(l.class)}" style="animation:pg .5s ${Math.min(i, 24) * 22}ms both">${tag(l.class)}
      <div class="n num">${fmt(l.charges_13w)}</div><div class="nm">${esc(l.lender)}</div><div style="display:flex;justify-content:space-between;align-items:end;margin-top:10px"><span style="font-size:12px;color:var(--faint)">charges, 13 weeks</span>${spark(l.weeks, l.class)}</div></button>`).join("") || `<p class="empty">No lenders match.</p>`;
    wire();
  }
  draw();
}
function openLender(name, cls) {
  const p = profile(name, cls), w = week(), row = w.league.find((r) => r.lender === name && (!cls || r.class === cls));
  if (!p && !row) return;
  const total = p ? p.sectors.reduce((a, [, n]) => a + n, 0) : 0;
  $("#drawer-body").innerHTML = `${tag(cls || p.class)}<h2>${esc(name)}</h2>
    <div class="grid" style="margin:18px 0">${[["This week", row ? row.this_week : 0], ["4 weeks", row ? row.trailing_4w : "—"], ["13 weeks", p ? p.charges_13w : row.trailing_13w]].map(([l, v]) => `<div class="card s4" style="padding:14px"><div class="lift num" style="font-size:30px">${v}</div><div class="sub" style="margin:4px 0 0">${l}</div></div>`).join("")}</div>
    ${p ? `<h3 style="font:400 22px var(--serif)">Weekly charges</h3><div style="margin:8px 0 20px">${spark(p.weeks, p.class).replace('class="spark"', 'class="spark" style="width:100%;height:70px"')}</div>
    <h3 style="font:400 22px var(--serif)">Borrower sectors</h3>${p.sectors.map(([s, n]) => `<div style="display:grid;grid-template-columns:1fr 120px 40px;gap:10px;align-items:center;font-size:13.5px;padding:6px 0"><span>${esc(s)}</span><div class="bar" data-c="${esc(p.class)}"><i style="width:${(n / total) * 100}%"></i></div><span class="num" style="text-align:right">${n}</span></div>`).join("")}
    <h3 style="font:400 22px var(--serif);margin-top:22px">Latest charges</h3><ul class="mini-list">${p.recent.map((r) => `<li><span><b>${esc(r.company)}</b><br><span style="color:var(--faint);font-size:12px">${esc(r.sector)} · ${dLong(r.date)}</span></span><a href="${esc(r.url)}" target="_blank" rel="noopener">Filing ↗</a></li>`).join("")}</ul>` : ""}
    <p class="caveat">${esc(D.caveat)}</p>`;
  $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }
$("#drawer-close").onclick = closeDrawer;
$("#drawer").onclick = (e) => { if (e.target.id === "drawer") closeDrawer(); };
addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ------------------------------------------------------------------ track record
function record() {
  const rows = D.scorecard;
  app.innerHTML = `<div class="wrap page-top"><div class="sec-head"><div><span class="kicker">Track record</span><h2>Scored against a baseline, in the open</h2><p>For each signal type: how often the company registered a further charge within 90 days, compared with charges without that signal in the same sector and company age band. 95% Wilson intervals throughout.</p></div><span class="badge warn">Backtest · rules ${esc(D.rule_version)}</span></div>
    <div class="grid">${rows.map((r) => `<div class="card s12 reveal" style="display:grid;grid-template-columns:220px 1fr 140px;gap:28px;align-items:center">
      <div><h3 style="font:400 28px var(--serif)">${esc(D.signal_labels[r.signal_type])}</h3><p class="sub" style="margin:4px 0 0">${fmt(r.signals)} signals · ${fmt(r.outcomes_observed)} hits${r.small_sample ? " · small sample" : ""}</p></div>
      <div><div class="ci" title="Hit rate with 95% interval; the grey tick is the baseline"><div class="rng" style="left:${r.ci_low * 100}%;width:${(r.ci_high - r.ci_low) * 100}%"></div>${r.baseline_rate ? `<div class="base" style="left:${r.baseline_rate * 100}%"></div>` : ""}<div class="pt" style="left:${r.hit_rate * 100}%"></div></div>
        <div style="display:flex;justify-content:space-between;font:11px var(--mono);color:var(--faint)"><span>0%</span><span>Hit rate ${pct(r.hit_rate, 1)} (${pct(r.ci_low, 1)} to ${pct(r.ci_high, 1)}) · baseline ${r.baseline_rate ? pct(r.baseline_rate, 1) : "n/a"}</span><span>100%</span></div></div>
      <div style="text-align:right"><div class="lift num" data-count="${r.lift || 0}" data-dec="2" data-suf="×">0</div><div class="sub" style="margin:4px 0 0">lift vs baseline</div></div></div>`).join("")}</div>
    ${recordWatchCard()}
    <div class="card reveal" style="margin-top:18px"><h3>Read this before the numbers</h3><p class="prose" style="margin:6px 0 0">${esc(D.bias_note)} Signal window: ${dLong(rows[0]?.window_start)} to ${dLong(rows[0]?.window_end)}. Outcome: ${esc(rows[0]?.outcome || "")}. Signals use filing dates only, so nothing here looks ahead. A live, append-only log starts once the newsletter is published; it will never be mixed with these figures.</p></div></div>`;
  drawRecordWatch();
}

// ------------------------------------------------------------------ method
function method() {
  app.innerHTML = `<div class="wrap page-top"><div class="sec-head"><div><span class="kicker">Method</span><h2>How Armariu reads the register</h2></div></div>
  <div class="prose">
    <p>Every UK company that grants security over its assets must register a charge at Companies House. Armariu follows the live charges stream from ${dLong(D.coverage_start)}, fetches each borrower's earlier charges, and reads them lender by lender.</p>
    <h3>One lender, one entity</h3><p>Lender names are filed as free text. A hand-curated alias table, a lender-group table and a strict fuzzy matcher merge spellings, subsidiaries and trading names. Anything uncertain goes to a person and is never merged automatically. Security trustees and agents stay a class of their own: they often front a club or syndicated loan where the lender is not named.</p>
    <h3>Signals are rules, not guesses</h3><p><b>First facility</b>: no registered charge in the three years before. <b>Refinancing</b>: a charge for one lender satisfied within 90 days of a new charge for another. <b>Add-on</b>: a lender takes further security while its earlier charge is outstanding. Every signal records its rule version.</p>
    <h3>What a charge is not</h3><p>A charge shows that security was granted and to whom. It does not show the amount, pricing, tenor or purpose, and Armariu never infers them. ${esc(D.caveat)}</p>
    <h3>People</h3><p>Some charges are held by private individuals. They are never named here: Armariu publishes company-level facts only.</p>
    <h3>Not advice</h3><p>${esc(D.not_advice)}</p>
    <p class="small">Source: Companies House. Contains public sector information licensed under the Open Government Licence v3.0.</p>
  </div></div>`;
}

// ------------------------------------------------------------------ downloads (CSV, built in the browser)
function csv(rows, cols) { return [cols.join(","), ...rows.map((r) => cols.map((c) => `"${String(r[c] ?? "").replace(/"/g, '""')}"`).join(","))].join("\n"); }
function save(name, text) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
function downloads() {
  const w = week();
  $("#drawer-body").innerHTML = `<span class="kicker">Data</span><h2>Download</h2><p class="sub">CSV files of what this portal shows. Company-level facts only.</p>
    <ul class="mini-list"><li><span><b>League table</b><br><span style="color:var(--faint);font-size:12px">${dLong(w.start)} to ${dLong(w.end)}</span></span><button class="pill-btn" id="d1">CSV</button></li>
    <li><span><b>Signals</b><br><span style="color:var(--faint);font-size:12px">${fmt(D.signals.length)} rows, last four weeks</span></span><button class="pill-btn" id="d2">CSV</button></li>
    <li><span><b>Insights tables</b><br><span style="color:var(--faint);font-size:12px">weekly series, sectors, lender moves</span></span><button class="pill-btn" id="d4">CSV</button></li>
    <li><span><b>Scorecard (backtest)</b><br><span style="color:var(--faint);font-size:12px">rules ${esc(D.rule_version)}</span></span><button class="pill-btn" id="d3">CSV</button></li></ul><p class="caveat">${esc(D.caveat)} ${esc(D.not_advice)}</p>`;
  $("#d1").onclick = () => save(`armariu_league_${w.start}.csv`, csv(w.league.filter((r) => r.class !== "individual").map((r) => ({ ...r, window_start: w.start, window_end: w.end })), ["window_start", "window_end", "lender", "class", "this_week", "trailing_4w", "trailing_13w", "low_observations"]));
  $("#d2").onclick = () => save(`armariu_signals_${D.built}.csv`, csv(D.signals, ["signal_date", "signal_type", "company_number", "company_name", "sector", "lender_names", "other_lender", "explanation", "filing_url"]));
  $("#d3").onclick = () => save(`armariu_scorecard_backtest.csv`, csv(D.scorecard, Object.keys(D.scorecard[0] || {})));
  $("#d4").onclick = () => { const I = D.insights || {}; const rows = [];
    (I.flow_weeks || []).forEach((r) => { const sw = (I.signal_weeks || []).find((x) => x.week_start === r.week_start) || {};
      rows.push({ table: "weekly", key: r.week_start, observed_only: r.observed_only, new_charges: r.new, satisfied: r.satisfied, all_asset_charges: r.all_assets, private_credit_charges: r.private_credit, refinancing: sw.refinancing, first_facility: sw.first_facility, add_on: sw.add_on }); });
    (I.sectors || []).forEach((r) => rows.push({ table: "sector", key: r.sector, new_charges: r.total, ...Object.fromEntries(Object.keys(I.group_labels).map((g) => [g, r[g]])) }));
    (I.league_moves || []).forEach((r) => rows.push({ table: "league_moves", key: r.lender, class: r.class, rank: r.rank, prior_rank: r.prior_rank, charges_4w: r.charges_4w }));
    (I.refi_moves || []).forEach((r) => rows.push({ table: "refinancing_moves", key: `${r.from}>${r.to}`, refinancing: r.signals }));
    save(`armariu_insights_${D.built}.csv`, csv(rows, ["table", "key", "observed_only", "new_charges", "satisfied", "all_asset_charges", "private_credit_charges", "refinancing", "first_facility", "add_on", "bank", "private_credit", "specialist", "property", "other", "class", "rank", "prior_rank", "charges_4w"])); };
  $("#drawer").classList.add("open");
}

/* Armariu client portal: the Insights page and the charts it shares with Refinance watch and
   Track record. Plain SVG, no libraries. Reads the `insights` part of data.json (company-level
   facts only). Loaded before portal.js; everything here runs after both have loaded. */
"use strict";

const GROUP_ORDER = ["bank", "private_credit", "specialist", "property", "other"];
const SIG_STACK = ["add_on", "refinancing", "first_facility"];
const NS = "http://www.w3.org/2000/svg";
const pct1 = (x) => `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`;
const niceMax = (v) => { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p; return 10 * p; };
const compact = (v) => (v >= 10000 ? `${Math.round(v / 1000)}K` : fmt(v));

/** Bar with a 4px rounded data-end and a square base. Vertical when `up`, else horizontal (grows right). */
function barPath(x, y, w, h, up = true, r = 4) {
  if (w <= 0 || h <= 0) return "";
  r = Math.min(r, up ? w / 2 : h / 2, up ? h : w);
  return up
    ? `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
    : `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
}
const hatchDef = (id) => `<pattern id="${id}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="7" fill="var(--text)" opacity=".07"/></pattern>`;
const swatch = (g, kind = "g") => `<span class="sw" data-${kind}="${g}"></span>`;
const tipRow = (sw, label, value) => `<span class="trow">${sw}<span>${esc(label)}</span><b class="num">${value}</b></span>`;
function legendHTML(keys, labels, kind = "g") { return `<div class="legend">${keys.map((k) => `<span>${swatch(k, kind)}${esc(labels[k] || k)}</span>`).join("")}</div>`; }
function tableHTML(cols, rows) {
  return `<details class="tview"><summary>View as a table</summary><div style="overflow-x:auto"><table class="tbl small"><thead><tr>${cols.map((c, i) => `<th class="${i ? "r" : ""}">${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${i ? "r num" : ""}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
}
function hover(svg, onMove) { svg.addEventListener("mousemove", onMove); svg.addEventListener("mouseleave", () => { hideTip(); svg.querySelectorAll(".hl").forEach((n) => n.classList.remove("hl")); const c = svg.querySelector(".xhair"); if (c) c.style.display = "none"; }); }

// ------------------------------------------------------------------ weekly stacked columns
/** rows: [{week_start, observed_only, [key]: n}]; share=true draws 100% stacks. */
function weeklyStack(el, rows, keys, labels, { kind = "s", share = false, H = 260 } = {}) {
  const W = Math.max(320, el.clientWidth || 640), pl = 40, pr = 8, pt = 22, pb = 26;
  const tot = rows.map((r) => keys.reduce((a, k) => a + (r[k] || 0), 0));
  const max = share ? 1 : niceMax(Math.max(1, ...tot));
  const bw = (W - pl - pr) / rows.length, w = Math.min(24, bw * 0.62);
  const y = (v) => pt + (1 - v / max) * (H - pt - pb);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const cov = rows.findIndex((r) => !r.observed_only);
  let marks = "";
  rows.forEach((r, i) => {
    const x = pl + bw * i + (bw - w) / 2; let acc = 0; const t = tot[i] || 1;
    const segs = keys.filter((k) => r[k]);
    segs.forEach((k, si) => {
      const v = share ? r[k] / t : r[k], top = y(acc + v), bot = y(acc);
      const gap = si ? 2 : 0;   // 2px surface gap between stacked segments
      const h = bot - top - gap;
      marks += h > 0 ? `<path data-${kind}="${k}" class="mk" data-i="${i}" d="${si === segs.length - 1 ? barPath(x, top, w, h) : `M${x},${top}h${w}v${h}h${-w}Z`}"/>` : "";
      acc += v;
    });
  });
  const shade = cov > 0 ? `<rect x="${pl}" y="${pt}" width="${bw * cov}" height="${H - pt - pb}" fill="url(#h-${el.id})"/><text class="note" x="${pl}" y="${pt - 4}">◩ observed borrowers only</text>` : "";
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly ${share ? "share" : "count"} chart"><defs>${hatchDef("h-" + el.id)}</defs>
    <g class="grid">${ticks.map((t) => `<line x1="${pl}" x2="${W - pr}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>${shade}
    <g class="axis">${ticks.map((t) => `<text x="${pl - 6}" y="${y(t) + 4}" text-anchor="end">${share ? Math.round(t * 100) + "%" : compact(t)}</text>`).join("")}
    ${rows.map((r, i) => ((rows.length - 1 - i) % Math.max(2, Math.ceil(56 / bw)) === 0) ? `<text x="${pl + bw * i + bw / 2}" y="${H - 7}" text-anchor="middle">${dShort(r.week_start)}</text>` : "").join("")}</g>
    <g class="marks">${marks}</g></svg>`;
  const svg = el.firstElementChild;
  hover(svg, (e) => {
    const b = svg.getBoundingClientRect(), mx = ((e.clientX - b.left) / b.width) * W, i = Math.floor((mx - pl) / bw);
    svg.querySelectorAll(".hl").forEach((n) => n.classList.remove("hl"));
    if (i < 0 || i >= rows.length) return hideTip();
    svg.querySelectorAll(`[data-i="${i}"]`).forEach((n) => n.classList.add("hl"));
    const r = rows[i], t = tot[i] || 1;
    showTip(`<b>Week of ${dLong(r.week_start)}</b>${r.observed_only ? '<br><span class="faint">observed borrowers only</span>' : ""}${[...keys].reverse().map((k) => tipRow(swatch(k, kind), labels[k], `${fmt(r[k] || 0)} · ${Math.round(((r[k] || 0) / t) * 100)}%`)).join("")}${tipRow("", "Total", fmt(tot[i]))}`, e.clientX, e.clientY);
  });
}

// ------------------------------------------------------------------ line chart (percent)
/** series: [{key, label, values:[0..1]}] over rows (weeks). One y-axis, in percent. */
function weeklyLines(el, rows, series, { H = 240, kind = "s", fmtV = pct1, maxV } = {}) {
  const W = Math.max(320, el.clientWidth || 640), pl = 44, pr = 92, pt = 14, pb = 26;
  const all = series.flatMap((s) => s.values.filter((v) => v != null));
  const max = maxV || niceMax(Math.max(0.01, ...all) * 1.05);
  const x = (i) => pl + (i / Math.max(1, rows.length - 1)) * (W - pl - pr), y = (v) => pt + (1 - v / max) * (H - pt - pb);
  const ticks = [0, 0.5, 1].map((f) => f * max);
  const cov = rows.findIndex((r) => !r.observed_only);
  const shade = cov > 0 ? `<rect x="${pl}" y="${pt}" width="${x(cov) - pl}" height="${H - pt - pb}" fill="url(#h-${el.id})"/>` : "";
  // End labels: spread apart only if they would overlap, with a leader line back to the end point.
  const ends = series.map((s) => ({ s, v: s.values[s.values.length - 1], y: y(s.values[s.values.length - 1] || 0) })).sort((a, b) => a.y - b.y);
  ends.forEach((e, i) => { e.ly = i ? Math.max(e.y, ends[i - 1].ly + 30) : e.y; });
  const lines = series.map((s) => `<path data-${kind}="${s.key}" class="ln" d="${s.values.map((v, i) => v == null ? "" : `${i && s.values[i - 1] != null ? "L" : "M"}${x(i)},${y(v)}`).join("")}"/>
    <circle data-${kind}="${s.key}" class="dot-end" cx="${x(rows.length - 1)}" cy="${y(s.values[s.values.length - 1] || 0)}" r="4.5"/>`).join("");
  const labels = ends.map((e) => `${Math.abs(e.ly - e.y) > 2 ? `<line class="leader" x1="${x(rows.length - 1) + 6}" y1="${e.y}" x2="${x(rows.length - 1) + 12}" y2="${e.ly}"/>` : ""}
    <text class="end-lbl" x="${x(rows.length - 1) + 14}" y="${e.ly + 4}"><tspan class="num" font-weight="600">${fmtV(e.v || 0)}</tspan><tspan x="${x(rows.length - 1) + 14}" dy="13" class="faint">${esc(e.s.short || e.s.label)}</tspan></text>`).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map((s) => s.label).join(", "))} by week"><defs>${hatchDef("h-" + el.id)}</defs>
    <g class="grid">${ticks.map((t) => `<line x1="${pl}" x2="${W - pr}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>${shade}
    <g class="axis">${ticks.map((t) => `<text x="${pl - 6}" y="${y(t) + 4}" text-anchor="end">${fmtV(t)}</text>`).join("")}
    ${rows.map((r, i) => ((rows.length - 1 - i) % Math.max(3, Math.ceil(60 / ((W - pl - pr) / rows.length))) === 0) ? `<text x="${x(i)}" y="${H - 7}" text-anchor="middle">${dShort(r.week_start)}</text>` : "").join("")}</g>
    <line class="xhair" y1="${pt}" y2="${H - pb}" style="display:none"/>${lines}${labels}</svg>`;
  const svg = el.firstElementChild, xh = svg.querySelector(".xhair");
  hover(svg, (e) => {
    const b = svg.getBoundingClientRect(), mx = ((e.clientX - b.left) / b.width) * W;
    const i = Math.max(0, Math.min(rows.length - 1, Math.round(((mx - pl) / (W - pl - pr)) * (rows.length - 1))));
    xh.style.display = ""; xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i));
    const r = rows[i];
    showTip(`<b>Week of ${dLong(r.week_start)}</b>${r.observed_only ? '<br><span class="faint">observed borrowers only</span>' : ""}${series.map((s) => tipRow(swatch(s.key, kind), s.label, s.values[i] == null ? "n/a" : fmtV(s.values[i]))).join("")}`, e.clientX, e.clientY);
  });
}

// ------------------------------------------------------------------ horizontal 100% stacks
/** rows: [{label, total, [group]: n}] -> one 100% bar per row, groups in GROUP_ORDER. */
function shareBars(el, rows, labels) {
  const W = Math.max(300, el.clientWidth || 560), lw = Math.min(190, W * 0.36), rw = 52, row = 30, bh = 16, H = rows.length * row + 4;
  let marks = "";
  rows.forEach((r, i) => {
    const t = r.total || 1, y0 = i * row + (row - bh) / 2; let acc = 0;
    const segs = GROUP_ORDER.filter((g) => r[g]);
    segs.forEach((g, si) => {
      const x0 = lw + (acc / t) * (W - lw - rw), w = (r[g] / t) * (W - lw - rw) - (si < segs.length - 1 ? 2 : 0);
      if (w > 0) marks += `<path class="mk" data-g="${g}" data-i="${i}" data-k="${g}" d="${si === segs.length - 1 ? barPath(x0, y0, w, bh, false) : `M${x0},${y0}h${w}v${bh}h${-w}Z`}"/>`;
      acc += r[g];
    });
  });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="height:${H}px" role="img" aria-label="Lender mix by row">
    ${rows.map((r, i) => `<text class="rlbl" x="0" y="${i * row + row / 2 + 4}">${esc(cut(r.label, Math.floor(lw / 6.6)))}</text><text class="rval num" x="${W}" y="${i * row + row / 2 + 4}" text-anchor="end">${compact(r.total)}</text>`).join("")}
    ${marks}</svg>`;
  const svg = el.firstElementChild;
  hover(svg, (e) => {
    const t = e.target.closest(".mk"); svg.querySelectorAll(".hl").forEach((n) => n.classList.remove("hl"));
    if (!t) return hideTip();
    const r = rows[+t.dataset.i]; svg.querySelectorAll(`[data-i="${t.dataset.i}"]`).forEach((n) => n.classList.add("hl"));
    showTip(`<b>${esc(r.label)}</b>${GROUP_ORDER.filter((g) => r[g]).map((g) => tipRow(swatch(g), labels[g], `${fmt(r[g])} · ${Math.round((r[g] / r.total) * 100)}%`)).join("")}${tipRow("", "Charges", fmt(r.total))}`, e.clientX, e.clientY);
  });
}

// ------------------------------------------------------------------ simple horizontal bars (one series)
function hBars(el, rows, { kind = "g", key = "bank", fmtV = pct1, max, ref, refLabel } = {}) {
  const W = Math.max(300, el.clientWidth || 520), lw = Math.min(200, W * 0.38), rw = 64, row = 30, bh = 14, H = rows.length * row + (ref != null ? 20 : 4);
  const m = max || niceMax(Math.max(...rows.map((r) => r.value), ref || 0));
  const x = (v) => lw + (v / m) * (W - lw - rw);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="height:${H}px" role="img">
    ${rows.map((r, i) => { const y0 = i * row + (row - bh) / 2 + (ref != null ? 16 : 0);
      return `<text class="rlbl" x="0" y="${y0 + bh / 2 + 4}">${esc(cut(r.label, Math.floor(lw / 6.6)))}</text>
      <path class="mk" data-${kind}="${r.key || key}" data-i="${i}" d="${barPath(lw, y0, Math.max(1, x(r.value) - lw), bh, false)}"/>
      <text class="rval num" x="${x(r.value) + 6}" y="${y0 + bh / 2 + 4}">${fmtV(r.value)}</text>`; }).join("")}
    ${ref != null ? `<line class="ref" x1="${x(ref)}" x2="${x(ref)}" y1="12" y2="${H}"/><text class="note" x="${x(ref)}" y="9" text-anchor="middle">${esc(refLabel || "")}</text>` : ""}</svg>`;
  const svg = el.firstElementChild;
  hover(svg, (e) => {
    const t = e.target.closest(".mk"); svg.querySelectorAll(".hl").forEach((n) => n.classList.remove("hl"));
    if (!t) return hideTip(); t.classList.add("hl");
    const r = rows[+t.dataset.i]; showTip(`<b>${esc(r.label)}</b>${(r.tip || [[r.label, fmtV(r.value)]]).map(([l, v]) => tipRow("", l, v)).join("")}`, e.clientX, e.clientY);
  });
}

// ------------------------------------------------------------------ heatmap (refinancing moves)
function moveMatrix(el, moves, labels) {
  const keys = GROUP_ORDER, cell = {}; let max = 1;
  moves.forEach((m) => { cell[m.from + ">" + m.to] = m.signals; max = Math.max(max, m.signals); });
  const short = { bank: "Banks", private_credit: "Private credit", specialist: "Specialist", property: "Property", other: "Other" };
  el.innerHTML = `<div class="hm" style="grid-template-columns:minmax(92px,auto) repeat(${keys.length},1fr)"><span class="hm-corner">from ↓ · to →</span>${keys.map((k) => `<span class="hm-h">${short[k]}</span>`).join("")}
    ${keys.map((a) => `<span class="hm-r">${short[a]}</span>${keys.map((b) => { const n = cell[a + ">" + b] || 0, p = n ? 14 + Math.round((n / max) * 86) : 0;
      return `<span class="hm-c ${p > 55 ? "inv" : ""}" style="--p:${p}%" data-tip="${esc(`<b>${labels[a]} → ${labels[b]}</b>`)}" data-n="${n}"><span class="num">${n ? fmt(n) : "·"}</span></span>`; }).join("")}`).join("")}</div>`;
  el.querySelectorAll(".hm-c").forEach((c) => { c.onmousemove = (e) => showTip(`${c.dataset.tip}${tipRow("", "Refinancing signals, 13 weeks", fmt(+c.dataset.n))}`, e.clientX, e.clientY); c.onmouseleave = hideTip; });
}

// ------------------------------------------------------------------ measured hit rates (random sample)
// The only absolute rates shown anywhere: radar/refi_sample.py, from a random sample of companies. Ranges are
// 95%. The radar's own data runs about 3x high, so its rates are never exported.
const rng1 = (c, u = "%") => `${c[0]}${u === "×" ? "" : u} to ${c[1]}${u}`;
const SAMPLE_ORDER = ["ordinary", "property", "ordinary_np"];
function sampleCards(S, { compact = false } = {}) {
  const card = (k) => {
    const g = S.groups[k], y = g["12m"], n = g["90d"];
    return `<div class="card hr-card"><div class="hr-top"><h3>${esc(g.label)}</h3><span class="hr-note">${esc(g.note)}</span></div>
      <div class="hr-big num">${y.top10}%</div>
      <div class="hr-l">of the top 10% switched lender within 12 months</div>
      <div class="hr-rng">95% range ${rng1(y.top10_ci)}</div>
      <div class="hr-sep"></div>
      <div class="hr-row"><span>Lift on the group average</span><b class="num">${y.lift.toFixed(1)}× <i>(${rng1(y.lift_ci, "×")})</i></b></div>
      ${y.all != null ? `<div class="hr-row"><span>Average company, 12 months</span><b class="num">${y.all}% <i>(${rng1(y.all_ci)})</i></b></div>` : ""}
      ${compact ? "" : `<div class="hr-row"><span>90 days, top 10%</span><b class="num">${n ? `${n.top10}% <i>(${rng1(n.top10_ci)}) · ${n.lift.toFixed(1)}×</i>` : `<i>not tested</i>`}</b></div>`}</div>`;
  };
  return `<div class="hr-grid">${SAMPLE_ORDER.map(card).join("")}</div>`;
}
function sampleNote(S) {
  return `<p class="caveat">Measured on a random sample of ${fmt(S.companies)} England and Wales companies with a charge register, ${fmt(S.scored["12m"].company_dates)} company-dates at 12 months and ${fmt(S.scored["90d"].company_dates)} at 90 days, in months the score never saw (${esc(S.as_of)}). A switch is a repaid loan followed by a new charge from a lender not on the repaid loans. ${S.caveats.slice(1).map(esc).join(" ")}</p>`;
}
function bandTag(r, S) {
  const g = S.groups[r.group], y = g && g["12m"];
  if (r.group === "serial") return `<span class="band dim">Serial borrower</span><div class="band-s">11+ loans: not measured</div>`;
  if (r.band === "top10" && y) return `<span class="band hot">Top 10%</span><div class="band-s">${y.top10}% switched in 12M (${rng1(y.top10_ci)}) · ${y.lift.toFixed(1)}×</div>`;
  if (r.band === "top25") return `<span class="band">Top 25%</span><div class="band-s">above average, rate not measured</div>`;
  return `<span class="band dim">Rest</span>`;
}

// ------------------------------------------------------------------ the Insights page
function insightsUnavailable() { app.innerHTML = `<div class="wrap page-top"><p class="empty">Insights appear once the data is rebuilt: run <code>python -m radar.portal --rebuild</code>.</p></div>`; }
function insights() {
  const I = D.insights; if (!I) return insightsUnavailable();
  const G = I.group_labels, SL = D.signal_labels;
  const full = I.flow_weeks.filter((r) => !r.observed_only), lastF = full[full.length - 1] || I.flow_weeks[I.flow_weeks.length - 1];
  const lastS = I.signal_weeks.filter((r) => !r.observed_only).pop() || I.signal_weeks[I.signal_weeks.length - 1];
  const c = I.concentration, sharePC = lastF.new ? lastF.private_credit / lastF.new : 0;
  const repaidPer100 = lastF.new ? (lastF.satisfied / lastF.new) * 100 : 0;
  const movesPC = I.refi_moves.filter((m) => m.to === "private_credit").reduce((a, m) => a + m.signals, 0);
  const movesOut = I.refi_moves.filter((m) => m.from === "private_credit" && m.to !== "private_credit").reduce((a, m) => a + m.signals, 0);
  const movesTot = I.refi_moves.reduce((a, m) => a + m.signals, 0);
  const sectorsNP = I.sectors.filter((s) => s.sector !== PROPERTY_SECTOR);
  const topPCsector = [...sectorsNP].sort((a, b) => b.private_credit / b.total - a.private_credit / a.total)[0];
  const winTxt = `${dLong(I.window.start)} to ${dLong(I.window.end)}`;
  const tile = (v, l, d, cnt) => `<div class="stat"><div class="v num" ${cnt != null ? `data-count="${cnt.v}" data-dec="${cnt.dec || 0}" data-suf="${cnt.suf || ""}"` : ""}>${cnt != null ? "0" : v}</div><div class="l">${l}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`;
  app.innerHTML = `<div class="wrap page-top insights">
    <div class="sec-head"><div><span class="kicker">Insights</span><h2>The market in charts</h2><p>Thirteen weeks of charge filings, read lender by lender: who is lending, to which borrowers, what kind of security they take, and where refinancing is likely next. ${esc(D.caveat)}</p></div><span class="badge">${winTxt}</span></div>
    <div class="stats reveal">
      ${tile("", "of new charges went to private credit lenders or agents", `week of ${dShort(lastF.week_start)}`, { v: +(sharePC * 100).toFixed(1), dec: 1, suf: "%" })}
      ${tile("", "refinancing signals", `week of ${dShort(lastS.week_start)}`, { v: lastS.refinancing })}
      ${tile("", "charges repaid for every 100 new ones", `week of ${dShort(lastF.week_start)}`, { v: Math.round(repaidPer100) })}
      ${tile("", "of private credit charges went to the 10 busiest lenders", `${fmt(c.charges)} charges, ${c.lenders} lenders, 13 weeks`, { v: c.charges ? Math.round((c.top10 / c.charges) * 100) : 0, suf: "%" })}
    </div>

    <section class="section reveal"><div class="sec-head"><div><span class="kicker">01 · Deal flow</span><h3 class="h">What kind of borrowing is happening</h3><p>Signals each week, by type. A refinancing is one lender replaced by another; a first facility is a company's first secured loan in three years; an add-on is more security for an existing lender.</p></div>
      <div class="seg" role="group" aria-label="Measure" id="sig-m"><button data-m="share" aria-pressed="true">Share</button><button data-m="count" aria-pressed="false">Count</button></div></div>
      <div class="grid"><div class="card s8"><h3>Signals by week</h3><p class="sub">${esc(SIG_BLURB.refinancing)}</p><div class="chart" id="ch-sig"></div>${legendHTML(SIG_STACK, SL, "s")}
        <p class="caveat">Hatched weeks: ${esc(D.sample_caveat)} Shares are steadier than counts across that line.</p>
        ${tableHTML(["Week", ...SIG_STACK.map((k) => SL[k])], I.signal_weeks.map((r) => [dLong(r.week_start) + (r.observed_only ? " *" : ""), ...SIG_STACK.map((k) => fmt(r[k]))]))}</div>
        <div class="card s4"><h3>Private credit concentration</h3><p class="sub">Share of private credit charges by the busiest lenders, 13 weeks</p>
          <div class="seg-bar">${[["Top 5", c.top5], ["6 to 10", c.top10 - c.top5], ["11 to 25", c.top25 - c.top10], ["The rest", c.charges - c.top25]].map(([l, n], i) => `<i style="flex:${n || 0.0001};--o:${1 - i * 0.22}" title="${l}: ${n}"></i>`).join("")}</div>
          <ul class="mini-list tight">${[["Top 5 lenders", c.top5], ["Next 5", c.top10 - c.top5], ["Next 15", c.top25 - c.top10], [`The other ${Math.max(0, c.lenders - 25)}`, c.charges - c.top25]].map(([l, n], i) => `<li><span><span class="sw" data-g="private_credit" style="opacity:${1 - i * 0.22}"></span>${l}</span><span class="num">${fmt(n)} · ${c.charges ? Math.round((n / c.charges) * 100) : 0}%</span></li>`).join("")}</ul>
          <p class="caveat">Private credit is spread thin: ${c.lenders} lenders filed ${fmt(c.charges)} charges. Club and syndicated loans often sit behind a security agent and are not counted here.</p></div></div></section>

    <section class="section reveal"><div class="sec-head"><div><span class="kicker">02 · Lenders</span><h3 class="h">Who is moving up, and where borrowers go</h3><p>Rank by charges in the last four weeks against the four weeks before, excluding property lenders. And, for every refinancing signal, the type of lender replaced and the type that replaced it.</p></div><button class="pill-btn" data-go="#/league">Full league table</button></div>
      <div class="grid"><div class="card s6"><h3>League movement</h3><p class="sub">Last four weeks vs the four before${I.league_moves_prior_partial ? " (the earlier window saw observed borrowers only, so compare ranks, not counts)" : ""}</p>
        <table class="tbl moves"><thead><tr><th>#</th><th>Lender</th><th class="r">Charges</th><th class="r">Move</th></tr></thead><tbody>${I.league_moves.map((m) => { const d = m.prior_rank ? m.prior_rank - m.rank : null;
          return `<tr data-lender="${esc(m.lender)}" data-cls="${esc(m.class)}"><td class="rank">${m.rank}</td><td><span class="lname">${esc(m.lender)}</span><div class="faint small">${esc(LBL(m.class))}</div></td><td class="r num">${fmt(m.charges_4w)}</td><td class="r num">${d == null ? '<span class="mv new">new</span>' : d === 0 ? '<span class="mv">–</span>' : `<span class="mv ${d > 0 ? "up" : "dn"}">${d > 0 ? "▲" : "▼"} ${Math.abs(d)}</span>`}</td></tr>`; }).join("")}</tbody></table></div>
        <div class="card s6"><h3>Refinancing between lender types</h3><p class="sub">Refinancing signals, 13 weeks. Rows: the lender replaced. Columns: the new lender.</p><div id="ch-moves"></div>
          <p class="caveat">${fmt(movesPC)} of ${fmt(movesTot)} refinancings went to private credit lenders or agents; ${fmt(movesOut)} moved away from them. A pattern in the filings, never proof of a refinancing.</p>
          ${tableHTML(["From", "To", "Signals"], I.refi_moves.map((m) => [G[m.from], G[m.to], fmt(m.signals)]))}</div></div></section>

    <section class="section reveal"><div class="sec-head"><div><span class="kicker">03 · Borrowers</span><h3 class="h">Who borrows from whom</h3><p>Every charge filed in the 13 weeks, by the borrower's sector and age, split by the type of lender that took security.</p></div></div>
      ${legendHTML(GROUP_ORDER, G)}
      <div class="grid" style="margin-top:14px"><div class="card s7"><h3>Lender mix by borrower sector</h3><p class="sub">Biggest sectors first; the number is charges filed${topPCsector ? `. Private credit's largest share outside property: ${esc(topPCsector.sector.toLowerCase())}, ${pct(topPCsector.private_credit / topPCsector.total)}` : ""}</p><div id="ch-sec"></div>
        ${tableHTML(["Sector", "Charges", ...GROUP_ORDER.map((g) => G[g])], I.sectors.map((s) => [s.sector, fmt(s.total), ...GROUP_ORDER.map((g) => fmt(s[g]))]))}</div>
        <div class="card s5"><h3>Lender mix by borrower age</h3><p class="sub">Age of the company when the charge was created</p><div id="ch-age"></div>
        ${tableHTML(["Company age", "Charges", ...GROUP_ORDER.map((g) => G[g])], I.borrower_age.map((s) => [s.band, fmt(s.total), ...GROUP_ORDER.map((g) => fmt(s[g]))]))}</div></div></section>

    <section class="section reveal"><div class="sec-head"><div><span class="kicker">04 · Security</span><h3 class="h">What lenders take as security</h3><p>An all-asset floating charge (a debenture over the whole business) is typical of cash-flow lending. Property and asset lenders usually take a fixed charge over the specific asset instead.</p></div></div>
      <div class="grid"><div class="card s7"><h3>Private credit and all-asset debentures, share of new charges</h3><p class="sub">Each week, as a share of charges filed. The all-asset line starts when the radar began reading charge details from the live register.</p><div class="chart" id="ch-lines"></div>
        ${tableHTML(["Week", "New charges", "Private credit share", "All-asset share", "Repaid per 100 new"], I.flow_weeks.map((r) => [dLong(r.week_start) + (r.observed_only ? " *" : ""), fmt(r.new), r.new ? pct1(r.private_credit / r.new) : "n/a", r.register ? pct1(r.all_assets / r.register) : "n/a", r.new ? Math.round((r.satisfied / r.new) * 100) : "n/a"]))}</div>
        <div class="card s5"><h3>All-asset debentures by lender type</h3><p class="sub">Share of each type's charges with a floating charge over all assets, charges read from the live register</p><div id="ch-deb"></div></div></div></section>

    <section class="section reveal"><div class="sec-head"><div><span class="kicker">05 · Refinance watch</span><h3 class="h">Who tends to switch lender, and how well the list ranks them</h3><p>${fmt(I.watch.scored)} companies with a loan still registered are scored on their filings alone. The figures below were measured on a random sample, not on this page's own data.</p></div><button class="pill-btn" data-go="#/watch">Open the Refinance watch</button></div>
      ${D.refi_watch?.sample ? sampleCards(D.refi_watch.sample, { compact: true }) + sampleNote(D.refi_watch.sample) : ""}
      <div class="grid" style="margin-top:18px"><div class="card s12"><h3>Relative score by sector</h3><p class="sub">Average score against the average company (1.0×). A ranking aid only: not a refinancing rate. Sectors with 30 or more companies scored</p><div id="ch-wsec"></div></div></div></section>
  </div>`;
  const draw = () => {
    let mode = $("#sig-m [aria-pressed=true]").dataset.m;
    weeklyStack($("#ch-sig"), I.signal_weeks, SIG_STACK, SL, { kind: "s", share: mode === "share" });
    moveMatrix($("#ch-moves"), I.refi_moves, G);
    shareBars($("#ch-sec"), I.sectors.slice(0, 10).map((s) => ({ ...s, label: s.sector })), G);
    shareBars($("#ch-age"), I.borrower_age.map((s) => ({ ...s, label: s.band })), G);
    weeklyLines($("#ch-lines"), I.flow_weeks, [
      { key: "private_credit", label: "Private credit or agent", short: "private credit", values: I.flow_weeks.map((r) => (r.new ? r.private_credit / r.new : null)) },
      { key: "bank", label: "All-asset floating charge", short: "all-asset", values: I.flow_weeks.map((r) => (r.register >= 20 ? r.all_assets / r.register : null)) }], { kind: "g" });
    hBars($("#ch-deb"), I.debentures.map((d) => ({ label: LBL(d.class), value: d.all_assets / d.charges, key: GROUPS_OF[d.class] || "other", tip: [["All-asset", fmt(d.all_assets)], ["Charges", fmt(d.charges)], ["Share", pct1(d.all_assets / d.charges)]] })), { max: 1 });
    sectorScores($("#ch-wsec"), I.watch);
  };
  draw();
  $$("#sig-m button").forEach((b) => (b.onclick = () => { $$("#sig-m button").forEach((x) => x.setAttribute("aria-pressed", x === b)); weeklyStack($("#ch-sig"), I.signal_weeks, SIG_STACK, SL, { kind: "s", share: b.dataset.m === "share" }); }));
  redrawOnResize(draw);
}
const GROUPS_OF = { direct_lender: "private_credit", trustee_agent: "private_credit", bank: "bank", property: "property", abl_invoice: "specialist", asset_finance: "specialist", government_backed: "specialist", other: "other" };
let _resizeFn = null, _resizeT;
function redrawOnResize(fn) { _resizeFn = fn; }
addEventListener("resize", () => { clearTimeout(_resizeT); _resizeT = setTimeout(() => _resizeFn && document.body.contains($(".chart, #ch-sec, #ch-wt")) && _resizeFn(), 160); });

// ------------------------------------------------------------------ extra charts on other pages
function sectorScores(el, w) {
  const rows = w.sectors.filter((x) => x.mean_lift != null && !["Sector not known yet", "Dormant or not trading"].includes(x.sector)).slice(0, 9);
  hBars(el, rows.map((x) => ({ label: x.sector, value: x.mean_lift, tip: [["Companies scored", fmt(x.companies)], ["Relative score", `${x.mean_lift.toFixed(2)}×`]] })), { kind: "s", key: "add_on", fmtV: (v) => `${v.toFixed(1)}×`, ref: 1, refLabel: "average company" });
}
function watchCharts() {
  const I = D.insights; if (!I) return "";
  return `<div class="card" style="margin-bottom:22px"><h3>Relative score by sector</h3><p class="sub">${fmt(I.watch.scored)} companies scored. Average score against the average company (1.0×): a ranking aid, not a refinancing rate. Sectors with 30 or more companies scored</p><div id="ch-wsec"></div></div>`;
}
function drawWatchCharts() {
  const I = D.insights; if (!I || !$("#ch-wsec")) return;
  const draw = () => sectorScores($("#ch-wsec"), I.watch);
  draw(); redrawOnResize(draw);
}
function recordWatchCard() {
  const W = D.refi_watch; if (!W || !W.sample) return "";
  return `<div class="card reveal" style="margin-top:18px"><span class="badge warn">Backtest · random sample · ${esc(W.model_version)}</span><h3 style="margin-top:14px;font:400 28px var(--serif)">Refinance watch</h3>
    <p class="sub">How often companies in the top 10% of the list switched lender, against the group average. Measured on companies drawn at random, in months the score never saw.</p>${sampleCards(W.sample)}${sampleNote(W.sample)}</div>`;
}
function drawRecordWatch() {}

// ------------------------------------------------------------------ home page teaser
function homeInsights() {
  const I = D.insights; if (!I) return "";
  return `<section class="section reveal"><div class="sec-head"><div><span class="kicker">05 · Insights</span><h2>The market in charts</h2><p>Deal flow by week, who lends to which sectors, league movement, security types and the Refinance watch, all on one page.</p></div><button class="pill-btn" data-go="#/insights">Open insights</button></div>
    <div class="grid"><div class="card s7"><h3>Signals by week</h3><p class="sub">Share of each week's signals, by type</p><div class="chart" id="ch-home-sig"></div>${legendHTML(SIG_STACK, D.signal_labels, "s")}</div>
      <div class="card s5"><h3>Lender mix in the biggest sectors</h3><p class="sub">Charges filed in 13 weeks, by type of lender</p><div id="ch-home-sec"></div>${legendHTML(GROUP_ORDER, I.group_labels)}</div></div></section>`;
}
function drawHomeInsights() {
  const I = D.insights; if (!I || !$("#ch-home-sig")) return;
  const draw = () => {
    weeklyStack($("#ch-home-sig"), I.signal_weeks, SIG_STACK, D.signal_labels, { kind: "s", share: true });
    shareBars($("#ch-home-sec"), I.sectors.slice(0, 6).map((s) => ({ ...s, label: s.sector })), I.group_labels);
  };
  draw(); redrawOnResize(draw);
}

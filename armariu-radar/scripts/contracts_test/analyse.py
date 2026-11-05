"""Offline analysis for the contract-wins test. Reads contracts_test_out/ (made by fetch.py on Actions).

    python scripts/contracts_test/analyse.py contracts_test_out > report.json

Design (fixed before looking at results):
  event      a matched company's first award notice worth >= MIN_VALUE published with no other such
             notice in the previous 365 days. Dated by notice publication (when a lead could be raised).
  outcome    any new charge filed at Companies House in (t, t + 91 / 182 / 365 days].
  controls   a seeded 6% random sample of every live company, minus anyone matched to any notice in
             2022-2026. Charged controls (and charged winners) are weighted by their fetch fraction.
  strata     SIC section x age band x accounts size x charge state at t (none / satisfied only /
             1 outstanding / 2+ outstanding) x filed a charge in the past 12 months, within the same
             calendar quarter. Winner rate vs the control rate of its own cell, averaged over winners.
  periods    2023 events = development; 2024-01 to 2025-10-15 = held out (headline).
  ranges     company-clustered Poisson bootstrap, winners and controls resampled independently.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from radar.normalise import normalise_name  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "contracts_test_out")
MIN_VALUE = 100_000
HORIZONS = (91, 182, 365)
EV_FROM, EV_TO = date(2023, 1, 1), date(2025, 10, 15)
HELD_OUT_FROM = date(2024, 1, 1)
DEFENCE_SIC = {"25400", "30300", "30400", "33160", "84220"}
DEFENCE_BUYER = re.compile(r"\b(ministry of defence|defence equipment|defence infrastructure|submarine delivery|"
                           r"dstl|defence science|royal navy|british army|royal air force|\bmod\b|defence digital|"
                           r"defence electronics|defence support|ukho|atomic weapons)", re.I)
B = int(__import__("os").environ.get("BOOT", "400"))
rng = np.random.default_rng(7)


def D(s):
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def section(sic: str) -> str:
    if not sic or not sic.isdigit():
        return "?"
    d = int(sic[:2])
    for lo, hi, s in ((1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"), (41, 43, "F"),
                      (45, 47, "G"), (49, 53, "H"), (55, 56, "I"), (58, 63, "J"), (64, 66, "K"), (68, 68, "L"),
                      (69, 75, "M"), (77, 82, "N"), (84, 84, "O"), (85, 85, "P"), (86, 88, "Q"), (90, 93, "R"),
                      (94, 96, "S"), (97, 98, "T"), (99, 99, "U")):
        if lo <= d <= hi:
            return s
    return "?"


def size(cat: str) -> str:
    c = cat.upper()
    if "MICRO" in c: return "micro"
    if "DORMANT" in c: return "dormant"
    if "GROUP" in c: return "group"
    if "FULL" in c and "EXEMPTION" not in c: return "full"
    if "MEDIUM" in c: return "medium"
    if "SMALL" in c or "EXEMPTION" in c or "UNAUDITED" in c or "AUDIT EXEMPTION" in c: return "small"
    if "NO ACCOUNTS" in c: return "none"
    return "other"


def ageband(inc, t):
    if not inc: return "?"
    y = (t - inc).days / 365.25
    return "<2" if y < 2 else "2-5" if y < 5 else "5-10" if y < 10 else "10-20" if y < 20 else "20+"


def load_csv_gz(p):
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ load + match

awards = load_csv_gz(OUT / "awards.csv.gz")
seen, dedup = set(), []
for a in awards:
    k = (a["ocid"], normalise_name(a["supplier_name"]), a["value"])
    if k in seen:
        continue
    seen.add(k); dedup.append(a)
awards = dedup
cands = load_csv_gz(OUT / "bcd_candidates.csv.gz")
by_num = {r["company_number"]: r for r in cands}
by_name = defaultdict(list)
for r in cands:
    by_name[r["name_norm"]].append(r)

how = Counter(); how_src = defaultdict(Counter); pc_check = Counter()
supplier_seen = {}
for a in awards:
    num, method = None, "unmatched"
    coh = [i[7:].strip().upper().zfill(8) for i in a["supplier_ids"].split("|") if i.startswith("GB-COH:")]
    for n in coh:
        if n in by_num:
            num, method = n, "number"
    if not num and coh:
        method = "number_not_live"
    if not num and method == "unmatched":
        c = by_name.get(normalise_name(a["supplier_name"])) or []
        if len(c) == 1:
            num, method = c[0]["company_number"], "name_unique"
            if a["postcode"] and c[0]["postcode"]:
                pc_check["same" if a["postcode"] == c[0]["postcode"] else "differs"] += 1
        elif len(c) > 1:
            same = [x for x in c if a["postcode"] and x["postcode"] == a["postcode"]]
            if len(same) == 1:
                num, method = same[0]["company_number"], "name_postcode"
            else:
                method = "name_ambiguous"
    a["_num"], a["_how"] = num, method
    how[method] += 1; how_src[a["source"]][method] += 1

sup_key = lambda a: (a["supplier_ids"] or normalise_name(a["supplier_name"]))  # noqa: E731
sup_how = {}
for a in awards:
    sup_how.setdefault(sup_key(a), a["_how"] if a["_num"] else a["_how"])
match_report = {"award_supplier_rows": len(awards), "rows_by_method": dict(how),
                "rows_by_source": {k: dict(v) for k, v in how_src.items()},
                "distinct_suppliers": len(sup_how), "suppliers_by_method": dict(Counter(sup_how.values())),
                "name_matches_postcode_check": dict(pc_check)}

# ------------------------------------------------------------------ histories

hist, hist_grp = {}, {}
with gzip.open(OUT / "histories.jsonl.gz", "rt", encoding="utf-8") as fh:
    for line in fh:
        try:
            j = json.loads(line)
        except ValueError:
            continue
        hist[j["n"]] = [(D(i.get("delivered_on")), D(i.get("created_on")), D(i.get("satisfied_on")),
                         i.get("persons") or []) for i in j["items"]]
        hist_grp[j["n"]] = j["g"]

winners_any = {a["_num"] for a in awards if a["_num"]}
events = []   # (company, t, value, award)
by_co = defaultdict(list)
for a in awards:
    v = float(a["value"]) if a["value"] else None
    t = D(a["published"])
    if a["_num"] and v is not None and v >= MIN_VALUE and t:
        by_co[a["_num"]].append((t, v, a))
for num, lst in by_co.items():
    lst.sort(key=lambda x: x[0])
    last = None
    for t, v, a in lst:
        if last is None or (t - last).days > 365:
            if EV_FROM <= t <= EV_TO:
                events.append((num, t, v, a))
        last = t   # any qualifying notice resets the clock, so an event is a fresh win

pool = load_csv_gz(OUT / "bcd_pool.csv.gz")
bcd_meta = json.loads((OUT / "bcd_meta.json").read_text())


def state(num, ch_total, t):
    if ch_total == 0:
        return "none", False, (0, 0, 0)
    h = hist[num]
    before = [c for c in h if c[0] and c[0] <= t]
    out = [c for c in before if not (c[2] and c[2] <= t)]
    st = "none" if not before else "sat_only" if not out else "out1" if len(out) == 1 else "out2+"
    recent = any(c[0] > t - timedelta(days=365) for c in before)
    y = tuple(int(any(c[0] and t < c[0] <= t + timedelta(days=h_) for c in h)) for h_ in HORIZONS)
    return st, recent, y


# fetch weights for charged firms
w_charged_all = {n for n in {e[0] for e in events} if int(by_num[n]["charges_total"]) > 0}
w_charged_got = {n for n in w_charged_all if n in hist}
pool_ctrl = [r for r in pool if r["company_number"] not in winners_any]
c_charged_all = [r for r in pool_ctrl if int(r["charges_total"]) > 0]
c_charged_got = [r for r in c_charged_all if r["company_number"] in hist]
w_weight = len(w_charged_all) / max(1, len(w_charged_got))
c_weight = len(c_charged_all) / max(1, len(c_charged_got))

# ------------------------------------------------------------------ winner rows


def quarter(t):
    return f"{t.year}Q{(t.month - 1) // 3 + 1}"


def cell(r, t, st, recent):
    return (section(r["sic1"]), ageband(D(r["incorporated"]), t), size(r["accounts_category"]), st, recent)


W = []   # dict rows
for num, t, v, a in events:
    r = by_num[num]
    inc = D(r["incorporated"])
    if inc and inc >= t:
        continue
    ch = int(r["charges_total"])
    if ch > 0 and num not in hist:
        continue
    st, recent, y = state(num, ch, t)
    W.append({"num": num, "t": t, "q": quarter(t), "v": v, "w": w_weight if ch > 0 else 1.0,
              "cell": cell(r, t, st, recent), "y": y, "st": st,
              "def_sic": bool(set(r["sic_all"].split(";")) & DEFENCE_SIC),
              "def_buyer": bool(DEFENCE_BUYER.search(a["buyer"] or "")) or (a["cpv"] or "").startswith("35"),
              "held_out": t >= HELD_OUT_FROM, "source": a["source"], "scale": a["scale"]})

# ------------------------------------------------------------------ control rows (one per firm per quarter)

quarters = sorted({w["q"] for w in W})
qdate = {q: date(int(q[:4]), 3 * (int(q[-1]) - 1) + 2, 15) for q in quarters}   # mid-quarter
C_cell, C_q, C_y, C_w, C_co = [], [], [], [], []
co_index = {}
for r in pool_ctrl:
    num, ch = r["company_number"], int(r["charges_total"])
    if ch > 0 and num not in hist:
        continue
    inc = D(r["incorporated"])
    for q in quarters:
        t = qdate[q]
        if inc and inc >= t:
            continue
        st, recent, y = state(num, ch, t)
        C_cell.append(cell(r, t, st, recent)); C_q.append(q); C_y.append(y)
        C_w.append(c_weight if ch > 0 else 1.0); C_co.append(co_index.setdefault(num, len(co_index)))
keys = {}
C_k = np.array([keys.setdefault((c, q), len(keys)) for c, q in zip(C_cell, C_q)])
C_y = np.array(C_y, dtype=float); C_w = np.array(C_w); C_co = np.array(C_co)
W_k = np.array([keys.get((w["cell"], w["q"]), -1) for w in W])
W_y = np.array([w["y"] for w in W], dtype=float).reshape(-1, len(HORIZONS))
W_w = np.array([w["w"] for w in W])
w_co_index = {}
W_co = np.array([w_co_index.setdefault(w["num"], len(w_co_index)) for w in W])
K = len(keys)


def control_rates(cw):
    den = np.bincount(C_k, weights=C_w * cw, minlength=K)
    return [np.bincount(C_k, weights=C_w * cw * C_y[:, h], minlength=K) / np.maximum(den, 1e-12) for h in range(len(HORIZONS))], den


def estimate(mask, cw, ww):
    rates, den = control_rates(cw)
    ok = mask & (W_k >= 0)
    ok = ok & (den[np.maximum(W_k, 0)] > 0)
    wt = W_w * ww * ok
    s = wt.sum()
    obs = [(wt * W_y[:, h]).sum() / s if s else np.nan for h in range(len(HORIZONS))]
    exp = [(wt * rates[h][np.maximum(W_k, 0)]).sum() / s if s else np.nan for h in range(len(HORIZONS))]
    return obs, exp, int(ok.sum())


def report(mask, label):
    obs, exp, n = estimate(mask, np.ones(len(C_co)), np.ones(len(W)))
    boots = []
    for _ in range(B):
        cw = rng.poisson(1.0, len(co_index))[C_co]
        ww = rng.poisson(1.0, len(w_co_index))[W_co]
        boots.append(estimate(mask, cw, ww)[:2])
    out = {"label": label, "events": n, "companies": int(len(set(W_co[mask & (W_k >= 0)]))),
           "dropped_no_control_cell": int((mask & (W_k < 0)).sum())}
    for h, H in enumerate(HORIZONS):
        bo = np.array([b[0][h] for b in boots]); be = np.array([b[1][h] for b in boots])
        lift = bo / np.maximum(be, 1e-12)
        out[f"{H}d"] = {"winners": round(100 * obs[h], 2), "controls": round(100 * exp[h], 2),
                        "winners_95": [round(100 * np.nanpercentile(bo, 2.5), 2), round(100 * np.nanpercentile(bo, 97.5), 2)],
                        "controls_95": [round(100 * np.nanpercentile(be, 2.5), 2), round(100 * np.nanpercentile(be, 97.5), 2)],
                        "lift": round(obs[h] / exp[h], 2) if exp[h] else None,
                        "lift_95": [round(float(np.nanpercentile(lift, 2.5)), 2), round(float(np.nanpercentile(lift, 97.5)), 2)],
                        "diff_pts_95": [round(100 * float(np.nanpercentile(bo - be, 2.5)), 2), round(100 * float(np.nanpercentile(bo - be, 97.5)), 2)]}
    return out


A = lambda f: np.array([bool(f(w)) for w in W])  # noqa: E731
held, dev = A(lambda w: w["held_out"]), A(lambda w: not w["held_out"])
cuts = [
    (dev, "2023 (development)"),
    (held, "Held out: 2024-01 to 2025-10, all wins >= £100k"),
    (held & A(lambda w: w["v"] < 1e6), "Held out: £100k to £1m"),
    (held & A(lambda w: w["v"] >= 1e6), "Held out: £1m or more"),
    (held & A(lambda w: w["st"] == "none"), "Held out: no charge before the win"),
    (held & A(lambda w: w["st"] in ("out1", "out2+")), "Held out: had an outstanding charge"),
    (held & A(lambda w: w["st"] == "sat_only"), "Held out: only repaid charges"),
    (held & A(lambda w: w["cell"][2] in ("micro", "small")), "Held out: micro and small accounts"),
    (held & A(lambda w: w["cell"][2] in ("medium", "full", "group")), "Held out: medium, full or group accounts"),
    (held & A(lambda w: w["scale"] == "sme"), "Held out: notice says SME"),
    (held & A(lambda w: w["source"] == "cf"), "Held out: Contracts Finder"),
    (held & A(lambda w: w["source"] == "fts"), "Held out: Find a Tender"),
    (A(lambda w: w["def_sic"]), "Defence and aerospace SIC (all years)"),
    (A(lambda w: w["def_buyer"]), "Defence buyer or defence CPV (all years)"),
    (held & A(lambda w: w["def_buyer"]), "Held out: defence buyer or defence CPV"),
    (A(lambda w: w["def_buyer"] and w["cell"][2] in ("micro", "small", "medium")), "Defence buyer, micro/small/medium (all years)"),
]
results = [report(m, l) for m, l in cuts]

# ------------------------------------------------------------------ second comparison: firms that win later
# Random live firms differ from contract winners in ways the strata can't see (they trade with the
# public sector at all). So also compare against firms whose FIRST notice >= MIN_VALUE came more than
# a year later (2024-2025): same kind of firm, but no win yet at t.
first_q = {num: lst[0][0] for num, lst in by_co.items()}
PL_FROM, PL_TO = date(2023, 1, 1), date(2024, 6, 30)
fut = [n for n, d0 in first_q.items() if d0 >= date(2024, 1, 1)]
pl_rows = []   # (company, quarter, cell, y, w)
for n in fut:
    r = by_num[n]; ch = int(r["charges_total"])
    if ch > 0 and n not in hist:
        continue
    for q in sorted({w["q"] for w in W if PL_FROM <= w["t"] <= PL_TO}):
        t = qdate[q]
        if first_q[n] <= t + timedelta(days=365):
            continue
        inc = D(r["incorporated"])
        if inc and inc >= t:
            continue
        st, recent, y = state(n, ch, t)
        pl_rows.append((n, q, (size(r["accounts_category"]), ageband(inc, t), st, recent), y, w_weight if ch > 0 else 1.0))
pk = {}
P_k = np.array([pk.setdefault((c, q), len(pk)) for _, q, c, _, _ in pl_rows])
P_y = np.array([y for *_, y, _ in pl_rows], dtype=float); P_w = np.array([w for *_, w in pl_rows])
p_co = {}; P_co = np.array([p_co.setdefault(n, len(p_co)) for n, *_ in pl_rows])
E = [w for w in W if PL_FROM <= w["t"] <= PL_TO]
E_k = np.array([pk.get(((w["cell"][2], w["cell"][1], w["cell"][3], w["cell"][4]), w["q"]), -1) for w in E])
E_y = np.array([w["y"] for w in E], dtype=float); E_w = np.array([w["w"] for w in E])
e_co = {}; E_co = np.array([e_co.setdefault(w["num"], len(e_co)) for w in E])


def pl_est(pw, ew, mask):
    den = np.bincount(P_k, weights=P_w * pw, minlength=len(pk))
    ok = mask & (E_k >= 0); ok &= den[np.maximum(E_k, 0)] > 0
    wt = E_w * ew * ok; s = wt.sum()
    o, e = [], []
    for h in range(len(HORIZONS)):
        rate = np.bincount(P_k, weights=P_w * pw * P_y[:, h], minlength=len(pk)) / np.maximum(den, 1e-12)
        o.append((wt * E_y[:, h]).sum() / s); e.append((wt * rate[np.maximum(E_k, 0)]).sum() / s)
    return o, e, int(ok.sum())


def pl_report(mask, label):
    o, e, n = pl_est(np.ones(len(P_k)), np.ones(len(E)), mask)
    bs = [pl_est(rng.poisson(1.0, len(p_co))[P_co], rng.poisson(1.0, len(e_co))[E_co], mask)[:2] for _ in range(B)]
    out = {"label": label, "events": n, "later_winner_firms": len(p_co)}
    for h, H in enumerate(HORIZONS):
        bo = np.array([b[0][h] for b in bs]); be = np.array([b[1][h] for b in bs]); lift = bo / np.maximum(be, 1e-12)
        out[f"{H}d"] = {"winners": round(100 * o[h], 2), "controls": round(100 * e[h], 2), "lift": round(o[h] / e[h], 2) if e[h] else None,
                        "lift_95": [round(float(np.nanpercentile(lift, 2.5)), 2), round(float(np.nanpercentile(lift, 97.5)), 2)],
                        "controls_95": [round(100 * np.nanpercentile(be, 2.5), 2), round(100 * np.nanpercentile(be, 97.5), 2)],
                        "winners_95": [round(100 * np.nanpercentile(bo, 2.5), 2), round(100 * np.nanpercentile(bo, 97.5), 2)]}
    return out


EA = lambda f: np.array([bool(f(w)) for w in E])  # noqa: E731
placebo = [pl_report(EA(lambda w: True), "Wins 2023-01 to 2024-06 vs firms that first win a year or more later"),
           pl_report(EA(lambda w: w["st"] == "none"), "  same, no charge before the win"),
           pl_report(EA(lambda w: w["st"] != "none"), "  same, had a charge before the win"),
           pl_report(EA(lambda w: w["def_buyer"]), "  same, defence buyer or CPV")]

# ------------------------------------------------------------------ what the new charges look like
post = Counter(); lenders = Counter(); n_post = 0
from radar.aliases import looks_like_person  # noqa: E402
for w in W:
    if not w["held_out"] or not w["y"][2]:
        continue
    for c in hist.get(w["num"], []):
        if c[0] and w["t"] < c[0] <= w["t"] + timedelta(days=365):
            n_post += 1
            post["first ever charge" if w["st"] == "none" else "additional/refinance"] += 1
            for p in c[3]:
                lenders["(private individual)" if looks_like_person(p) else normalise_name(p)] += 1
            break

summary = {
    "match": match_report, "bcd": bcd_meta,
    "events_total": len(events), "events_used": len(W), "fetch_weights": {"winners": round(w_weight, 3), "controls": round(c_weight, 3)},
    "winners_charged_fetched": [len(w_charged_got), len(w_charged_all)],
    "controls_charged_fetched": [len(c_charged_got), len(c_charged_all)], "control_firms": len(co_index),
    "results": results, "vs_later_winners": placebo, "first_post_win_charge_held_out": dict(post), "top_lenders_post_win": lenders.most_common(25),
}
print(json.dumps(summary, indent=1, default=str))

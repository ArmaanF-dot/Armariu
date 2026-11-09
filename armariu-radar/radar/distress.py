"""Borrower risk score: can public filings rank which UK companies with a secured loan go into formal
insolvency within 12 months? (BACKTEST 7 Nov 2026: AUC 0.70, range 0.66 to 0.73; see
research/distress-score-backtest-2026-11-07.md.) Moved into the package from scripts/distress_backtest.py
(PR 22) so it can score firms as well as test the idea.

    python -m radar.distress backtest             # the strict time-split test, writes output/distress/report.json
    python -m radar.distress score [--as-of D]    # score today's firms, write output/distress/risk_list.csv

Inputs are in output/distress/ (from radar.distress_fetch, run by the "distress" GitHub workflow):
  live_filings.jsonl.gz    filing histories of the random backfill sample
  radar_filings.jsonl.gz   filing histories of the radar's own companies (the ones with names and lenders)
  diss_days.jsonl.gz, diss_companies.jsonl.gz   the dissolved-firm sample that corrects survivorship
plus data/backfill.db and data/radar.db.

Population at each cutoff C: a company incorporated before C, not dissolved and not already in an
insolvency process at C, with at least one outstanding charge filed by C. Each company stands for
its share of all such companies (live sample weight = frame size / fetched; dissolved sample weight
= days in range / days drawn / share kept), so the rates are for all UK companies with a secured loan.

Outcome: a formal insolvency process starts within 12 months of C (administration, creditors'
voluntary or compulsory liquidation, CVA, receivership). Members' voluntary liquidations (solvent
wind-ups) are not counted. Secondary outcome adds compulsory strike-off (struck off for not filing).

Strict time split: models learn on cutoffs whose 12-month outcomes had closed before the first test
cutoff. Everything a feature uses was filed on or before its cutoff. Insolvency is not loan default,
and there is no bureau benchmark: a screening aid, not a default model.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import config
from .aliases import looks_like_person
from .normalise import normalise_name

ROOT = config.ROOT
OUT = config.OUTPUT_DIR / "distress"   # inputs from `python -m radar.distress_fetch` (git-ignored)
TRAIN = [date(2022, 7, 1), date(2022, 10, 1), date(2023, 1, 1), date(2023, 4, 1), date(2023, 7, 1)]
TEST = [date(2024, 7, 1), date(2024, 10, 1), date(2025, 1, 1), date(2025, 4, 1), date(2025, 7, 1)]
DISS_FROM, DISS_TO = date(2022, 7, 1), date(2026, 10, 31)
KEEP_SHARE = 0.2
YEAR = timedelta(days=365)


def d(s):
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def add_months(x: date, m: int) -> date:
    y, mo = divmod(x.month - 1 + m, 12)
    day = min(x.day, [31, 29 if (x.year + y) % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo])
    return date(x.year + y, mo + 1, day)


# ---------------------------------------------------------------- filing events
INSOLV_CATS = {"insolvency", "liquidation"}
SOLVENT = re.compile(r"declaration-of-solvency|members-voluntary|liquidation-voluntary-declaration-of-solvency|"
                     r"liquidation-members|special-resolution-to-wind-up(?!.*case)", re.I)
ACC_TYPES = ["dormant", "micro-entity", "total-exemption-full", "total-exemption-small", "small", "unaudited-abridged",
             "audited-abridged", "abridged", "medium", "full", "group", "initial", "interim"]


def acc_type(desc: str) -> str:
    desc = desc or ""
    for t in ACC_TYPES:
        if f"type-{t}" in desc:
            return {"total-exemption-full": "small", "total-exemption-small": "small", "unaudited-abridged": "small",
                    "audited-abridged": "small", "abridged": "small", "initial": "other", "interim": "other"}.get(t, t)
    return "other"


class Co:
    __slots__ = ("n", "w", "created", "ceased", "sic", "filings", "charges", "insolv", "solvent_liq", "strike",
                 "src")

    def __init__(self, n, w, created, ceased, sic, filings, charges, src):
        self.n, self.w, self.created, self.ceased, self.sic, self.src = n, w, created, ceased, sic, src
        fl = []
        for it in filings:
            t, cat, sub, dt, act, desc, mud, nd = (it + [None] * 8)[:8]
            dd = d(dt)
            if dd:
                fl.append((dd, t or "", cat or "", sub or "", desc or "", d(mud), d(nd)))
        fl.sort()
        self.filings = fl
        self.charges = charges
        self.solvent_liq = any(SOLVENT.search(f[4]) or f[1] in ("LIQ01", "4.70") for f in fl)
        ins = [f[0] for f in fl if f[2] in INSOLV_CATS or f[1] in ("COCOMP", "AM01", "2.12B", "AM02", "CVA1", "1.1")]
        self.insolv = None if self.solvent_liq else (min(ins) if ins else None)
        # compulsory strike-off: a compulsory first notice that ended in dissolution, not stopped
        self.strike = None
        if self.ceased:
            comp = [f[0] for f in fl if f[1].startswith("GAZ1") and "compulsory" in f[4]]
            vol = any(f[1] in ("DS01",) for f in fl)
            if comp and not vol and not self.insolv:
                self.strike = max(comp)


def read_lines(path):
    """Lines of a .jsonl.gz, tolerating a file cut off mid-write (a commit taken while it was open)."""
    out = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                out.append(line)
    except (EOFError, OSError):
        out = out[:-1]   # last line may be partial
    return out


def load_lender_cats():
    try:
        con = sqlite3.connect(ROOT / "data" / "radar.db")
        return {r[0]: r[1] for r in con.execute("SELECT name_norm, category FROM lender_classifications")}
    except Exception:
        return {}


def load():
    con = sqlite3.connect(ROOT / "data" / "backfill.db")
    meta = dict(con.execute("SELECT key, value FROM meta"))
    frame_size = int(meta["frame_size"])
    frame = {r[0]: r for r in con.execute("SELECT company_number, incorporated, sic_codes, company_status FROM frame")}
    hist = defaultdict(list)
    for r in con.execute("SELECT company_number, status, created_on, delivered_on, satisfied_on, persons FROM history"):
        hist[r[0]].append({"status": r[1], "created_on": r[2], "delivered_on": r[3], "satisfied_on": r[4],
                           "persons": json.loads(r[5] or "[]")})
    live = []
    for line in read_lines(OUT / "live_filings.jsonl.gz"):
        try:
            live.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    fetched = con.execute("SELECT COUNT(*) FROM fetched").fetchone()[0]
    # The live weight uses the fetched prefix that has filings so far (still a uniform random prefix
    # only if filings are fetched in rank order, which they are).
    w_live = frame_size / max(1, len(live))
    cos = []
    for rec in live:
        fr = frame.get(rec["n"])
        if not fr:
            continue
        cos.append(Co(rec["n"], w_live, d(fr[1]), None, json.loads(fr[2] or "[]"), rec["items"], hist.get(rec["n"], []),
                      "live"))
    days = []
    if (OUT / "diss_days.jsonl.gz").exists():
        for line in read_lines(OUT / "diss_days.jsonl.gz"):
            try:
                days.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    day_ok = {x["day"]: x for x in days}
    n_days_range = (DISS_TO - DISS_FROM).days + 1
    diss_n = 0
    if days and (OUT / "diss_companies.jsonl.gz").exists():
        w_day = n_days_range / len(day_ok)
        for line in read_lines(OUT / "diss_companies.jsonl.gz"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            dy = day_ok.get(rec["day"])
            if not dy or not rec.get("charges"):
                continue
            trunc = (dy["hits"] or 0) / max(1, dy["listed"])
            cos.append(Co(rec["n"], w_day / KEEP_SHARE * trunc, d(rec.get("created")), d(rec.get("ceased")),
                          rec.get("sic") or [], rec.get("items") or [], rec["charges"], "dissolved"))
            diss_n += 1
    info = {"frame_size": frame_size, "fetched_charges": fetched, "live_with_filings": len(live), "w_live": w_live,
            "diss_days": len(day_ok), "diss_with_charges": diss_n}
    return cos, info


# ---------------------------------------------------------------- features
SIC_SECTION = [(1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"), (41, 43, "F"), (45, 47, "G"),
               (49, 53, "H"), (55, 56, "I"), (58, 63, "J"), (64, 66, "K"), (68, 68, "L"), (69, 75, "M"), (77, 82, "N"),
               (84, 84, "O"), (85, 85, "P"), (86, 88, "Q"), (90, 93, "R"), (94, 96, "S"), (97, 99, "T")]


def sic_section(sics):
    for s in sics or []:
        try:
            k = int(str(s)[:2])
        except ValueError:
            continue
        for a, b, sec in SIC_SECTION:
            if a <= k <= b:
                return sec
    return "?"


LENDER_GROUP = {"bank": "bank", "property_lender": "property", "direct_lender": "alt", "abl_specialist": "abl",
                "asset_finance": "asset", "individual": "person", "security_agent": "agent", "public_sector": "public"}


def lender_group(name, cats):
    if looks_like_person(name):
        return "person"
    return LENDER_GROUP.get(cats.get(normalise_name(name), ""), "other")


def features(co: Co, C: date, cats):
    """None if the company isn't in the population at C."""
    if not co.created or co.created >= C:
        return None
    if co.ceased and co.ceased <= C:
        return None
    if co.insolv and co.insolv <= C:
        return None
    if co.solvent_liq and any(f[0] <= C and (f[2] in INSOLV_CATS) for f in co.filings):
        return None
    out, new12, sat12, lenders, groups_out, groups_new = 0, 0, 0, set(), Counter(), Counter()
    newest, oldest_out = None, None
    for ch in co.charges:
        cr, dl, sa = d(ch.get("created_on")), d(ch.get("delivered_on")), d(ch.get("satisfied_on"))
        filed = dl or cr
        if not cr or not filed or filed > C:
            continue
        persons = ch.get("persons") or []
        is_out = not (sa and sa <= C)
        if sa and C - YEAR < sa <= C:
            sat12 += 1
        g = {lender_group(p, cats) for p in persons} or {"other"}
        if is_out:
            out += 1
            for x in persons:
                lenders.add(normalise_name(x))
            for x in g:
                groups_out[x] += 1
            oldest_out = cr if not oldest_out or cr < oldest_out else oldest_out
        if cr > C - YEAR:
            new12 += 1
            for x in g:
                groups_new[x] += 1
        newest = cr if not newest or cr > newest else newest
    if out == 0:
        return None
    fl = [f for f in co.filings if f[0] <= C]
    age = (C - co.created).days / 365.25
    # accounts
    accs = [f for f in fl if f[2] == "accounts" and f[1].startswith("AA") and f[1] != "AA01"]
    last_mud = max((f[5] for f in accs if f[5]), default=None)
    if last_mud:
        due = add_months(last_mud, 12 + 9)  # next accounts: made up a year later, due 9 months after
    else:
        due = add_months(co.created, 21)
    acc_overdue_days = max(0, (C - due).days)
    late_3y, lateness_last = 0, 0
    for f in accs:
        if f[5] and f[0] > C - 3 * YEAR:
            late = (f[0] - add_months(f[5], 9)).days
            if late > 0:
                late_3y += 1
    if accs:
        f = max(accs, key=lambda x: x[0])
        if f[5]:
            lateness_last = max(0, (f[0] - add_months(f[5], 9)).days)
    last_acc_type = acc_type(max(accs, key=lambda x: x[0])[4]) if accs else "none"
    cs = [f[0] for f in fl if f[1] in ("CS01", "AR01")]
    cs_overdue = 1 if age > 1.2 and (not cs or (C - max(cs)).days > 400) else 0

    def n_recent(pred, days=365):
        return sum(1 for f in fl if f[0] > C - timedelta(days=days) and pred(f))

    gaz_comp = n_recent(lambda f: f[1].startswith("GAZ1") and "compulsory" in f[4], 730)
    diss40 = n_recent(lambda f: f[1].startswith("DISS40"), 730)
    diss16 = n_recent(lambda f: f[1].startswith("DISS16"), 730)
    aa01 = n_recent(lambda f: f[1] == "AA01")
    aa01_ext = n_recent(lambda f: f[1] == "AA01" and "extended" in f[4])
    tm01 = n_recent(lambda f: f[1] in ("TM01", "TM02"))
    ap01 = n_recent(lambda f: f[1] in ("AP01", "AP02", "AP03", "AP04"))
    ad01 = n_recent(lambda f: f[1] == "AD01")
    sh01 = n_recent(lambda f: f[1] == "SH01")
    psc = n_recent(lambda f: f[2] == "persons-with-significant-control")
    name_chg = n_recent(lambda f: f[2] == "change-of-name")
    restored = n_recent(lambda f: f[2] == "restoration", 1095)
    activity = n_recent(lambda f: True)
    sec = sic_section(co.sic)
    return {
        # public-record group: roughly what a basic bureau score uses (no CCJs, no payment data)
        "age": age, "acc_overdue_days": min(acc_overdue_days, 730), "acc_overdue": int(acc_overdue_days > 0),
        "late_3y": late_3y, "lateness_last": min(lateness_last, 730), "cs_overdue": cs_overdue,
        "gaz_comp_2y": int(gaz_comp > 0), "diss40_2y": int(diss40 > 0), "diss16_2y": int(diss16 > 0),
        "aa01_12m": int(aa01 > 0), "aa01_ext_12m": int(aa01_ext > 0), "restored_3y": int(restored > 0),
        "acc_" + last_acc_type: 1, "sec_" + sec: 1,
        # filings activity group
        "tm01_12m": tm01, "ap01_12m": ap01, "ad01_12m": ad01, "sh01_12m": int(sh01 > 0), "psc_12m": psc,
        "name_chg_12m": int(name_chg > 0), "activity_12m": activity,
        # charges group (Armariu's own data)
        "ch_out": out, "ch_new_12m": new12, "ch_sat_12m": sat12, "lenders_out": len(lenders),
        "yrs_since_newest": (C - newest).days / 365.25 if newest else 99,
        "yrs_oldest_out": (C - oldest_out).days / 365.25 if oldest_out else 0,
        **{f"out_{g}": int(v > 0) for g, v in groups_out.items()},
        **{f"new_{g}": int(v > 0) for g, v in groups_new.items()},
    }


def outcome(co: Co, C: date, secondary=False):
    end = C + YEAR
    if co.insolv and C < co.insolv <= end:
        return 1
    if secondary and co.strike and C < co.strike <= end:
        return 1
    return 0


PUBLIC = ("age", "acc_overdue_days", "acc_overdue", "late_3y", "lateness_last", "cs_overdue", "gaz_comp_2y",
          "diss40_2y", "diss16_2y", "aa01_12m", "aa01_ext_12m", "restored_3y", "acc_", "sec_")
ACTIVITY = ("tm01_12m", "ap01_12m", "ad01_12m", "sh01_12m", "psc_12m", "name_chg_12m", "activity_12m")
CHARGES = ("ch_out", "ch_new_12m", "ch_sat_12m", "lenders_out", "yrs_since_newest", "yrs_oldest_out", "out_", "new_")


def cols_for(groups, allcols):
    pre = sum(groups, ())
    return [c for c in allcols if any(c == p or (p.endswith("_") and c.startswith(p)) for p in pre)]


def build(cos, cutoffs, cats, secondary=False):
    rows, y, w, gid, cut = [], [], [], [], []
    for i, co in enumerate(cos):
        for C in cutoffs:
            f = features(co, C, cats)
            if f is None:
                continue
            rows.append(f); y.append(outcome(co, C, secondary)); w.append(co.w); gid.append(i); cut.append(C)
    return rows, np.array(y), np.array(w), np.array(gid), cut


def matrix(rows, cols):
    X = np.zeros((len(rows), len(cols)))
    idx = {c: j for j, c in enumerate(cols)}
    for i, r in enumerate(rows):
        for k, v in r.items():
            j = idx.get(k)
            if j is not None:
                X[i, j] = v
    return X


# ---------------------------------------------------------------- metrics
def top_share_rate(score, y, w, share):
    """Weighted outcome rate in the top `share` of weighted population, within each cutoff."""
    order = np.argsort(-score, kind="stable")
    cw = np.cumsum(w[order])
    k = np.searchsorted(cw, share * cw[-1]) + 1
    sel = order[:k]
    return (w[sel] * y[sel]).sum() / w[sel].sum(), (w[sel] * y[sel]).sum() / max(1e-9, (w * y).sum())


def auc(score, y, w):
    order = np.argsort(score, kind="stable")
    s, yy, ww = score[order], y[order], w[order]
    pos_w = ww * yy
    neg_w = ww * (1 - yy)
    # handle ties by ranking groups
    tot_pos, tot_neg = pos_w.sum(), neg_w.sum()
    if tot_pos == 0 or tot_neg == 0:
        return float("nan")
    cum_neg, a, i = 0.0, 0.0, 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        p, n = pos_w[i:j].sum(), neg_w[i:j].sum()
        a += p * (cum_neg + n / 2)
        cum_neg += n
        i = j
    return a / (tot_pos * tot_neg)


def metrics(score, y, w, cut):
    out = {"auc": auc(score, y, w), "base": (w * y).sum() / w.sum(), "n": len(y), "events": int(y.sum())}
    # rank within each cutoff, then pool (each cutoff is a separate list a lender would get)
    for share in (0.01, 0.05, 0.10, 0.20):
        num = den = cap_num = 0.0
        for C in sorted(set(cut)):
            m = np.array([c == C for c in cut])
            order = np.argsort(-score[m], kind="stable")
            ww, yy = w[m][order], y[m][order]
            cw = np.cumsum(ww)
            k = np.searchsorted(cw, share * cw[-1]) + 1
            num += (ww[:k] * yy[:k]).sum(); den += ww[:k].sum(); cap_num += (ww[:k] * yy[:k]).sum()
        out[f"top{int(share*100)}"] = num / den
        out[f"cap{int(share*100)}"] = cap_num / max(1e-9, (w * y).sum())
    return out


def bootstrap(fn, gid, n=300, seed=1):
    rng = np.random.default_rng(seed)
    groups = np.unique(gid)
    pos = defaultdict(list)
    for i, g in enumerate(gid):
        pos[g].append(i)
    vals = []
    for _ in range(n):
        pick = rng.choice(groups, size=len(groups), replace=True)
        idx = np.concatenate([pos[g] for g in pick])
        vals.append(fn(idx))
    vals = np.array(vals)
    return np.nanpercentile(vals, [2.5, 97.5], axis=0)


def backtest():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    cats = load_lender_cats()
    cos, info = load()
    print("loaded", json.dumps(info))
    ins = Counter(co.src for co in cos if co.insolv)
    print("companies", Counter(co.src for co in cos), "with an insolvency date", ins,
          "solvent liq", sum(co.solvent_liq for co in cos))
    report = {"info": info}
    for secondary in (False, True):
        tag = "insolvency_or_strikeoff" if secondary else "insolvency"
        tr_rows, ytr, wtr, gtr, ctr = build(cos, TRAIN, cats, secondary)
        te_rows, yte, wte, gte, cte = build(cos, TEST, cats, secondary)
        allcols = sorted({k for r in tr_rows + te_rows for k in r})
        print(f"\n== {tag}: train {len(ytr):,} rows {int(ytr.sum())} events; test {len(yte):,} rows {int(yte.sum())} events")
        res = {}
        specs = {
            "overdue_rule": None,
            "public_logit": ((PUBLIC,), "logit"),
            "public_gbm": ((PUBLIC,), "gbm"),
            "public+activity_gbm": ((PUBLIC, ACTIVITY), "gbm"),
            "all_logit": ((PUBLIC, ACTIVITY, CHARGES), "logit"),
            "all_gbm": ((PUBLIC, ACTIVITY, CHARGES), "gbm"),
            "charges_only_gbm": ((CHARGES,), "gbm"),
        }
        scores = {}
        for name, spec in specs.items():
            if spec is None:
                Xte = matrix(te_rows, ["acc_overdue_days", "gaz_comp_2y", "cs_overdue", "late_3y"])
                s = Xte[:, 0] / 30 + 30 * Xte[:, 1] + 10 * Xte[:, 2] + Xte[:, 3]
            else:
                groups, kind = spec
                cols = cols_for(groups, allcols)
                Xtr, Xte = matrix(tr_rows, cols), matrix(te_rows, cols)
                wn = wtr / wtr.mean()
                if kind == "logit":
                    sc = StandardScaler().fit(Xtr)
                    m = LogisticRegression(max_iter=2000, C=0.5).fit(sc.transform(Xtr), ytr, sample_weight=wn)
                    s = m.predict_proba(sc.transform(Xte))[:, 1]
                else:
                    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.04, max_leaf_nodes=15,
                                                       min_samples_leaf=80, l2_regularization=1.0, random_state=0)
                    m.fit(Xtr, ytr, sample_weight=wn)
                    s = m.predict_proba(Xte)[:, 1]
            scores[name] = s
            r = metrics(s, yte, wte, cte)
            res[name] = r
            print(f"{name:22s} auc {r['auc']:.3f}  base {r['base']*100:.2f}%  top1 {r['top1']*100:5.1f}%  "
                  f"top5 {r['top5']*100:5.1f}%  top10 {r['top10']*100:5.1f}% (captures {r['cap10']*100:4.1f}%)  "
                  f"top20 captures {r['cap20']*100:4.1f}%")
        # intervals for the headline models and the gain of all over public
        for name in ("public_gbm", "all_gbm", "all_logit"):
            s = scores[name]
            lo_hi = bootstrap(lambda idx: [auc(s[idx], yte[idx], wte[idx]),
                                           metrics(s[idx], yte[idx], wte[idx], [cte[i] for i in idx])["top10"]],
                              gte, n=150)
            res[name]["ci_auc"] = list(lo_hi[:, 0]); res[name]["ci_top10"] = list(lo_hi[:, 1])
        sa, sp = scores["all_gbm"], scores["public_gbm"]
        diff = bootstrap(lambda idx: [auc(sa[idx], yte[idx], wte[idx]) - auc(sp[idx], yte[idx], wte[idx])], gte, n=150)
        res["gain_all_vs_public_auc_ci"] = list(diff[:, 0])
        for k in ("public_gbm", "all_gbm", "all_logit", "gain_all_vs_public_auc_ci"):
            print(k, res[k] if k.startswith("gain") else {"ci_auc": res[k]["ci_auc"], "ci_top10": res[k]["ci_top10"]})
        # deciles of the best model, by source
        s = scores["all_gbm"]
        dec = []
        for C in sorted(set(cte)):
            m = np.array([c == C for c in cte])
            order = np.argsort(-s[m]); ww = wte[m][order]; cw = np.cumsum(ww) / ww.sum()
            q = np.minimum(9, (cw * 10 - 1e-9).astype(int))
            for qq, yy, w_ in zip(q, yte[m][order], ww):
                dec.append((qq, yy, w_))
        tab = []
        for qq in range(10):
            sel = [(yy, w_) for q_, yy, w_ in dec if q_ == qq]
            tab.append({"decile": qq + 1, "rate": sum(a * b for a, b in sel) / sum(b for _, b in sel),
                        "rows": len(sel), "events": int(sum(a for a, _ in sel))})
        res["deciles_all_gbm"] = tab
        print("deciles", [f"{t['rate']*100:.2f}%" for t in tab])
        # single-clue rates on test
        clue = {}
        for c in ("acc_overdue", "cs_overdue", "gaz_comp_2y", "aa01_ext_12m", "new_alt", "new_abl", "new_asset",
                  "new_person", "out_person", "out_abl", "out_alt", "ch_sat_12m", "sh01_12m"):
            x = matrix(te_rows, [c])[:, 0] > 0
            if x.sum() and (~x).sum():
                clue[c] = {"with": (wte[x] * yte[x]).sum() / wte[x].sum(), "without": (wte[~x] * yte[~x]).sum() / wte[~x].sum(),
                           "share": wte[x].sum() / wte.sum(), "rows": int(x.sum())}
        res["clues"] = clue
        for k, v in clue.items():
            print(f"  {k:14s} {v['with']*100:5.2f}% vs {v['without']*100:5.2f}%  ({v['share']*100:.1f}% of firms, {v['rows']} rows)")
        report[tag] = res
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))




# ---------------------------------------------------------------- scoring today's firms
FINAL_TRAIN = [date(2022, 7, 1), date(2022, 10, 1), date(2023, 1, 1), date(2023, 4, 1), date(2023, 7, 1),
               date(2023, 10, 1), date(2024, 1, 1), date(2024, 4, 1), date(2024, 7, 1), date(2024, 10, 1),
               date(2025, 1, 1), date(2025, 4, 1), date(2025, 7, 1), date(2025, 10, 1)]
# Plain-language reasons: clue -> (words, insolvency rate with the clue vs without, 7 Nov 2026 backtest)
REASONS = {
    "acc_overdue": "accounts overdue (3.8% went insolvent within a year, against 1.2% without)",
    "gaz_comp_2y": "compulsory strike-off notice in the last two years (3.8% against 1.3%)",
    "new_abl": "new invoice finance or asset-based charge in the last year (6.7% against 1.3%)",
    "new_asset": "new asset finance charge in the last year (8.9% against 1.4%, few cases)",
    "sh01_12m": "new shares issued in the last year (5.5% against 1.3%)",
    "aa01_ext_12m": "accounting period extended in the last year",
    "cs_overdue": "confirmation statement overdue",
}
DEFAULT_STEP = "radar_filings.jsonl.gz"
# Insolvency within 12 months by risk decile (riskiest first), the 7 Nov 2026 backtest on unseen months.
# The raw model output is not a probability (it reaches 60% for a few firms on thin clues), so a firm is
# shown the rate its decile actually had, never the raw number.
DECILE_RATES = (0.040, 0.024, 0.017, 0.014, 0.010, 0.008, 0.007, 0.006, 0.004, 0.007)


def reasons(f: dict, top: int = 3) -> str:
    out = [text for k, text in REASONS.items() if f.get(k)]
    if f.get("late_3y", 0) >= 2:
        out.append(f"{f['late_3y']} sets of accounts filed late in three years")
    return "; ".join(out[:top])


def _cols(allcols):
    return cols_for((PUBLIC, ACTIVITY, CHARGES), allcols)


def fit_final(cos, cats):
    """The 'all clues' gradient boosting model, learned on every cutoff whose 12-month outcome has closed."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    rows, y, w, _, _ = build(cos, FINAL_TRAIN, cats)
    allcols = sorted({k for r in rows for k in r})
    cols = _cols(allcols)
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=80,
                                       l2_regularization=1.0, random_state=0)
    m.fit(matrix(rows, cols), y, sample_weight=w / w.mean())
    return m, cols, {"rows": len(y), "events": int(y.sum()), "base": float((w * y).sum() / w.sum())}


def load_radar(conn):
    """The radar's own companies as Co objects (names kept aside), filings from radar_filings.jsonl.gz."""
    names = {r[0]: r[1] for r in conn.execute("SELECT company_number, name FROM companies")}
    meta = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT company_number, date_of_creation, sic_codes, company_status FROM companies")}
    charges: dict = defaultdict(dict)
    for r in conn.execute("SELECT company_number, charge_id, created_on, delivered_on, satisfied_on, persons "
                          "FROM borrower_history"):
        charges[r[0]][r[1]] = {"created_on": r[2], "delivered_on": r[3], "satisfied_on": r[4],
                               "persons": json.loads(r[5] or "[]")}
    people = defaultdict(list)
    for r in conn.execute("SELECT company_number, charge_id, name_raw FROM charge_persons ORDER BY name_raw"):
        people[(r[0], r[1])].append(r[2])
    for r in conn.execute("SELECT company_number, charge_id, created_on, delivered_on, satisfied_on FROM charges "
                          "WHERE COALESCE(deleted, 0) = 0"):
        charges[r[0]][r[1]] = {"created_on": r[2], "delivered_on": r[3], "satisfied_on": r[4],
                               "persons": people.get((r[0], r[1]), [])}
    cos = []
    for line in read_lines(OUT / DEFAULT_STEP):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        n = rec["n"]
        if n not in meta or not charges.get(n):
            continue
        created, sic, status = meta[n]
        cos.append(Co(n, 1.0, d(created), None, json.loads(sic or "[]"), rec["items"], list(charges[n].values()),
                      "radar"))
    return cos, names


def score(as_of: date, out_dir=None) -> Path:
    import csv
    import sqlite3
    cats = load_lender_cats()
    cos, info = load()
    model, cols, fit = fit_final(cos, cats)
    print("model learned on", json.dumps(fit))
    con = sqlite3.connect(config.DB_PATH) if hasattr(config, "DB_PATH") else sqlite3.connect(ROOT / "data" / "radar.db")
    radar, names = load_radar(con) if (OUT / DEFAULT_STEP).exists() else ([], {})
    rows_out = []
    for pop, cs in (("radar", radar), ("random sample", [c for c in cos if c.src == "live"])):
        feats = [(co, features(co, as_of, cats)) for co in cs]
        feats = [(co, f) for co, f in feats if f]
        if not feats:
            continue
        p = model.predict_proba(matrix([f for _, f in feats], cols))[:, 1]
        order = np.argsort(p)
        rank = np.empty(len(p)); rank[order] = np.arange(len(p))
        for (co, f), pr, rk in zip(feats, p, rank):
            rows_out.append({"population": pop, "company_number": co.n, "company_name": names.get(co.n, ""),
                             "risk_percentile": round(100 * (rk + 1) / len(p), 1),
                             "risk_decile": 10 - min(9, int((rk + 1) / len(p) * 10 - 1e-9)),
                             "past_12m_insolvency_rate_in_this_decile": DECILE_RATES[10 - 1 - min(9, int((rk + 1) / len(p) * 10 - 1e-9))],
                             "score": round(float(pr), 4), "reasons": reasons(f),
                             "outstanding_charges": f["ch_out"], "new_charges_12m": f["ch_new_12m"],
                             "accounts_overdue_days": f["acc_overdue_days"], "as_of": as_of.isoformat()})
    rows_out.sort(key=lambda r: (r["population"], -r["score"]))
    out = Path(out_dir or OUT) / "risk_list.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0]) if rows_out else ["company_number"])
        w.writeheader(); w.writerows(rows_out)
    print(f"{len(rows_out):,} firms scored -> {out}")
    return out


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m radar.distress")
    p.add_argument("cmd", choices=("backtest", "score"))
    p.add_argument("--as-of", default=date.today().isoformat())
    a = p.parse_args(argv)
    if a.cmd == "backtest":
        backtest()
    else:
        score(date.fromisoformat(a.as_of))


if __name__ == "__main__":
    main()

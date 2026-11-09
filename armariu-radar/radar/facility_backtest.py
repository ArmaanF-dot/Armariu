"""First-facility score for invoice and asset finance (BACKTEST, plan task 10).

    python -m radar.facility_backtest [--out FILE]

Question: among UK companies that already have a secured loan but have never had an invoice-finance,
asset-based or asset-finance (leasing, hire purchase) charge, can public filings rank which will register
their FIRST such charge within 12 months? Outcome = a charge created in the next 12 months whose lender is
classed asset-based/invoice or asset finance. Population and features are those of radar.distress (a
company with an outstanding charge at each cutoff), with the lender-type features for the two classes
removed (they are zero by construction). Strict time split as the risk score: learn on 2022-23 cutoffs,
test on 2024-25 cutoffs. Bar set in advance: top 10% at least 3 times the average rate.

Caveat: the population is companies that already borrowed on a registered charge, so this is "next kind of
facility", not "first ever borrowing". Lender classes come from data/radar.db and are incomplete.
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np

from . import distress as D

TARGET = ("abl", "asset")
DROP = ("out_abl", "out_asset", "new_abl", "new_asset")
BAR = 3.0


def had_target(co, C, cats, before=True):
    for ch in co.charges:
        cr = D.d(ch.get("created_on"))
        if not cr:
            continue
        if any(D.lender_group(p, cats) in TARGET for p in (ch.get("persons") or [])):
            if before and cr <= C:
                return True
    return False


def target_in_window(co, C, cats):
    end = C + D.YEAR
    for ch in co.charges:
        cr = D.d(ch.get("created_on"))
        if cr and C < cr <= end and any(D.lender_group(p, cats) in TARGET for p in (ch.get("persons") or [])):
            return 1
    return 0


def build(cos, cutoffs, cats):
    rows, y, w, gid, cut = [], [], [], [], []
    for i, co in enumerate(cos):
        for C in cutoffs:
            f = D.features(co, C, cats)
            if f is None or had_target(co, C, cats):
                continue
            for k in DROP:
                f.pop(k, None)
            rows.append(f); y.append(target_in_window(co, C, cats)); w.append(co.w); gid.append(i); cut.append(C)
    return rows, np.array(y), np.array(w), np.array(gid), cut


def top_mask(score, cut, share=0.10):
    mask = np.zeros(len(score), bool)
    cut = np.array(cut)
    for C in sorted(set(cut)):
        idx = np.where(cut == C)[0]
        order = idx[np.argsort(-score[idx], kind="stable")]
        cw = np.cumsum(wts_global[order])
        k = np.searchsorted(cw, share * cw[-1]) + 1
        mask[order[:k]] = True
    return mask


def main(argv=None):
    from sklearn.ensemble import HistGradientBoostingClassifier
    global wts_global
    p = argparse.ArgumentParser(prog="python -m radar.facility_backtest")
    p.add_argument("--out", default=str(D.OUT / "facility_backtest.json"))
    a = p.parse_args(argv)
    cats = D.load_lender_cats()
    cos, info = D.load()
    tr, ytr, wtr, gtr, ctr = build(cos, D.TRAIN, cats)
    te, yte, wte, gte, cte = build(cos, D.TEST, cats)
    cols = sorted({k for r in tr + te for k in r})
    Xtr, Xte = D.matrix(tr, cols), D.matrix(te, cols)
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=80,
                                       l2_regularization=1.0, random_state=0)
    m.fit(Xtr, ytr, sample_weight=wtr / wtr.mean())
    s = m.predict_proba(Xte)[:, 1]
    wts_global = wte
    res = D.metrics(s, yte, wte, cte)
    res.update(train_rows=len(ytr), train_events=int(ytr.sum()))
    mask = top_mask(s, cte)

    def lift(idx):
        ww, yy, mm = wte[idx], yte[idx], mask[idx]
        base = (ww * yy).sum() / ww.sum()
        return (ww[mm] * yy[mm]).sum() / ww[mm].sum() / base if base > 0 and ww[mm].sum() > 0 else np.nan

    lo, hi = D.bootstrap(lambda i: lift(i), gte, n=300)
    res["lift10"] = res["top10"] / res["base"]
    res["lift10_ci"] = [float(lo), float(hi)]
    res["bar"] = BAR
    res["passes"] = bool(res["lift10"] >= BAR and lo > 1)
    res["info"] = info
    print(json.dumps({k: v for k, v in res.items() if k != "info"}, indent=1, default=float))
    from pathlib import Path
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()

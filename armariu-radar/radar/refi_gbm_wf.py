"""BACKTEST: walk-forward test of LightGBM improvements against rw3, with a locked final window.

Each year is a fold: models learn on every monthly date (from 2018) whose outcome had closed before the year
began, then score that year's monthly dates. rw3 learns as it does live (one earlier year). The best variant
is chosen on the walk-forward years only (ordinary companies, top 10%, switched lender), then scored ONCE on
the locked window, which no model choice has looked at:
  90 days     folds 2021 to 2025, locked window Jan to Jul 2026
  12 months   folds 2021 to 2024 and Jan to Jun 2025, locked window Jul to Oct 2025

Variants: rw3; logistic regression (the plain baseline); LightGBM (RoadSense-style, from radar.refi_gbm);
LightGBM with recent years weighted up (drift); LightGBM ranking objective (rank_xendcg, one group per date);
CatBoost with the lender's identity as a category; and a rank blend of rw3 and the best LightGBM.

    pip install lightgbm catboost scikit-learn numpy pandas
    python -m radar.refi_gbm_wf
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
from datetime import date, timedelta
from pathlib import Path

from . import refi_accuracy as ra
from . import refi_gbm as g
from . import refi_watch as rw

FIRST = date(2018, 1, 1)
USE = ("clues", "numeric", "segment", "monotone", "weights")
FOLDS = {90: [(date(y, 1, 1), date(y, 12, 1)) for y in range(2021, 2026)],
         365: [(date(y, 1, 1), date(y, 12, 1)) for y in range(2021, 2025)] + [(date(2025, 1, 1), date(2025, 6, 1))]}
LOCKED = {90: (date(2026, 1, 1), date(2026, 7, 1)), 365: (date(2025, 7, 1), date(2025, 10, 1))}
OUT = g.OUT / "walk_forward"


def build_all(companies, vol, last: date):
    key = hashlib.sha1(repr((len(companies), sorted(companies)[:50], last, "wf1")).encode()).hexdigest()[:12]
    cache = g.OUT / "cache" / f"wf_{key}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    rows = []
    for co in companies.values():
        for on in g._monthly(FIRST, last):
            kc = ra.known_on(co, on)
            f = rw.features(kc, on, vol)
            if f:
                f = {k: v for k, v in f.items() if k != "_newest"}
                rows.append({"co": co.number, "on": on, "f": f, "x": g.numeric(kc, on, vol),
                             "y90": ra.outcomes(co, on, 90), "y365": ra.outcomes(co, on, 365)})
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(rows))
    return rows


def _w(rows, window):
    return [{**r, "y": r[f"y{window}"]} for r in rows]


def learn_rows(rows, start: date, window: int):
    return [r for r in rows if r["on"] + timedelta(days=window) < start]


def rw3_rows(rows, start: date, window: int):
    """rw3 as it runs live: the latest year of dates whose outcome closed before `start`."""
    ok = [r for r in rows if r["on"] + timedelta(days=window) < start]
    last = max(r["on"] for r in ok)
    return [r for r in ok if r["on"] > last - timedelta(days=365)]


def fit_lgb(train, start, recency=None, rank=False):
    import lightgbm as lgb
    import numpy as np
    p = {**g.PARAMS}
    cols = list(g.frame(train[:1], USE).columns)
    p["monotone_constraints"] = [g.MONOTONE.get(c, 0) for c in cols]
    p["monotone_constraints_method"] = "advanced"
    if rank:
        p.update(objective="rank_xendcg")
        p.pop("monotone_constraints_method")
    dates = sorted({r["on"] for r in train})
    cut = dates[int(len(dates) * 0.8)]
    tr, va = [r for r in train if r["on"] < cut], [r for r in train if r["on"] >= cut]

    def weights(rs):
        w = g.seg_weights(rs, "switched")
        if recency:
            w = w * np.array([0.5 ** ((start - r["on"]).days / (365.25 * recency)) for r in rs])
        return w

    def ds(rs, ref=None):
        X = g._cats(g.frame(rs, USE), ref)
        y = [int(r["y"]["switched"]) for r in rs]
        if rank:   # rows must be grouped by date
            order = sorted(range(len(rs)), key=lambda i: rs[i]["on"])
            X, y = X.iloc[order].reset_index(drop=True), [y[i] for i in order]
            grp = [sum(1 for r in rs if r["on"] == d) for d in sorted({r["on"] for r in rs})]
            return lgb.Dataset(X, y, group=grp), X
        return lgb.Dataset(X, y, weight=weights(rs)), X
    _, Xa = ds(train)
    dtr, _ = ds(tr, Xa)
    dva, _ = ds(va, Xa)
    b = lgb.train(p, dtr, num_boost_round=1500, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
    dall, _ = ds(train, Xa)
    return lgb.train(p, dall, num_boost_round=max(50, b.best_iteration)), Xa


def pred_lgb(m, ref, rows):
    return list(m.predict(g._cats(g.frame(rows, USE), ref)))


CB_CATS = [f"c_{c}" for c in rw.CLUES_RW3] + ["lender_cls", "lender_key"]


def _cb_frame(rows):
    import pandas as pd
    d = {f"c_{c}": [r["f"][c] for r in rows] for c in rw.CLUES_RW3}
    for c in g.NUMERIC:
        d[c] = [r["x"][c] for r in rows]
    d["lender_cls"] = [r["x"]["lender_cls"] for r in rows]
    d["lender_key"] = [str(r["x"].get("lender_key", "none")) for r in rows]
    d["serial"] = [int(bool(r["f"]["_serial"])) for r in rows]
    return pd.DataFrame(d)


def fit_cb(train):
    from catboost import CatBoostClassifier, Pool
    rows = sorted(train, key=lambda r: r["on"])   # CatBoost's ordered target stats follow row order: keep time order
    dates = sorted({r["on"] for r in rows})
    cut = dates[int(len(dates) * 0.8)]
    tr, va = [r for r in rows if r["on"] < cut], [r for r in rows if r["on"] >= cut]
    mk = lambda rs: Pool(_cb_frame(rs), [int(r["y"]["switched"]) for r in rs], cat_features=CB_CATS,
                         weight=g.seg_weights(rs, "switched"))
    m = CatBoostClassifier(iterations=1500, learning_rate=0.05, depth=5, l2_leaf_reg=10, random_seed=7,
                           has_time=True, verbose=False, thread_count=4, early_stopping_rounds=100)
    m.fit(mk(tr), eval_set=mk(va))
    n = max(50, m.get_best_iteration() or 50)
    m2 = CatBoostClassifier(iterations=n, learning_rate=0.05, depth=5, l2_leaf_reg=10, random_seed=7,
                            has_time=True, verbose=False, thread_count=4)
    m2.fit(mk(rows))
    return m2


def fit_logit(train):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    X = _cb_frame(train).drop(columns=["lender_key"])
    num = [c for c in X.columns if c not in CB_CATS]
    pre = ColumnTransformer([("c", OneHotEncoder(handle_unknown="ignore", min_frequency=30), [c for c in CB_CATS if c != "lender_key"]),
                             ("n", make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler()), num)])
    m = make_pipeline(pre, LogisticRegression(C=0.1, max_iter=2000, class_weight=None))
    m.fit(X, np.array([int(r["y"]["switched"]) for r in train]), logisticregression__sample_weight=g.seg_weights(train, "switched"))
    return m


def score_period(rows, start, end, window, log):
    train = learn_rows(rows, start, window)
    test = [r for r in rows if start <= r["on"] <= end]
    m3 = rw.Model([(r["f"], r["y"]["any"]) for r in rw3_rows(rows, start, window)], rw.CLUES_RW3)
    s = {"rw3 (live)": [m3.score(r["f"]) for r in test]}
    lg = fit_logit(train)
    s["Logistic regression"] = list(lg.predict_proba(_cb_frame(test).drop(columns=["lender_key"]))[:, 1])
    m, ref = fit_lgb(train, start)
    s["LightGBM"] = pred_lgb(m, ref, test)
    m, ref = fit_lgb(train, start, recency=1.0)
    s["LightGBM, recent years weighted (half-life 1 yr)"] = pred_lgb(m, ref, test)
    m, ref = fit_lgb(train, start, rank=True)
    s["LightGBM, ranking objective"] = pred_lgb(m, ref, test)
    cb = fit_cb(train)
    s["CatBoost with lender identity"] = list(cb.predict_proba(_cb_frame(test))[:, 1])
    for name in ("LightGBM", "CatBoost with lender identity"):
        s[f"Blend: rw3 + {name} (ranks within date)"] = blend(test, s["rw3 (live)"], s[name])
    log(f"  {window}d {start}..{end}: learn {len(train):,} rows, test {len(test):,}")
    return test, s


def blend(test, a, b):
    by = {}
    for i, r in enumerate(test):
        by.setdefault(r["on"], []).append(i)
    out = [0.0] * len(test)
    for idx in by.values():
        ra_, rb = g._rank([a[i] for i in idx]), g._rank([b[i] for i in idx])
        for k, i in enumerate(idx):
            out[i] = (ra_[k] + rb[k]) / 2
    return out


def pooled_compare(parts, reps=1000):
    """Ranks within each test date's group... pooled: top-k taken per fold (so a model can't lean on one
    year's base rate), then hits summed over folds."""
    rows, scores = [], {}
    for test, s in parts:
        # turn scores into within-date percentile ranks so folds pool fairly
        by = {}
        for i, r in enumerate(test):
            by.setdefault(r["on"], []).append(i)
        for name, v in s.items():
            pr = [0.0] * len(test)
            for idx in by.values():
                rk = g._rank([v[i] for i in idx])
                for k, i in enumerate(idx):
                    pr[i] = rk[k]
            scores.setdefault(name, []).extend(pr)
        rows.extend(test)
    return g.compare(rows, scores, reps=reps), rows, scores


def per_fold(parts, group="Ordinary companies (10 loans or fewer)", tier=0.10):
    keep = dict(g.GROUPS)[group]
    out = []
    for test, s in parts:
        idx = [i for i, r in enumerate(test) if keep(r["f"])]
        ys = [int(test[i]["y"]["switched"]) for i in idx]
        row = {"from": min(test[i]["on"] for i in idx).isoformat(), "base": sum(ys) / len(ys)}
        for name, v in s.items():
            ranked = sorted(idx, key=lambda i: -v[i])
            k = max(1, int(len(idx) * tier))
            row[name] = sum(int(test[i]["y"]["switched"]) for i in ranked[:k]) / k
        out.append(row)
    return out


def main(argv=None):
    import argparse
    from .aliases import Resolver
    from .charge_history import load_companies
    from .db import connect
    from .lender_view import coverage_start
    p = argparse.ArgumentParser(prog="python -m radar.refi_gbm_wf")
    p.add_argument("--window", type=int, action="append")
    a = p.parse_args(argv)
    conn = connect()
    companies = load_companies(conn, Resolver(conn))
    stop = coverage_start(conn)
    vol = rw.lender_volumes(companies)
    last = max(LOCKED[w][1] for w in (a.window or (90, 365)))
    log = lambda m: print(m, flush=True)
    allrows = build_all(companies, vol, last)
    log(f"built {len(allrows):,} company-dates")
    OUT.mkdir(parents=True, exist_ok=True)
    for w in a.window or (90, 365):
        rows = _w(allrows, w)
        # the window must close before the radar began watching (refi_accuracy rule)
        rows_ok = [r for r in rows if r["on"] + timedelta(days=w) < stop]
        parts = [score_period(rows_ok, s, e, w, log) for s, e in FOLDS[w]]
        table, _, _ = pooled_compare(parts)
        folds = per_fold(parts)
        ordinary = [t for t in table if t["group"].startswith("Ordinary companies") and t["tier"] == "Top 10%"]
        best = max((t for t in ordinary if t["model"] != "rw3 (live)"), key=lambda t: t["rate"])["model"]
        log(f"{w}d best on walk-forward years (ordinary top 10%): {best}")
        s, e = LOCKED[w]
        test, sc = score_period(rows_ok, s, e, w, log)
        locked, _, _ = pooled_compare([(test, {"rw3 (live)": sc["rw3 (live)"], best: sc[best]})])
        res = {"window": w, "folds": [[x.isoformat(), y.isoformat()] for x, y in FOLDS[w]], "walk_forward": table,
               "per_fold_ordinary_top10": folds, "best": best, "locked_window": [s.isoformat(), e.isoformat()],
               "locked": locked, "locked_all_models": per_fold([(test, sc)])}
        (OUT / f"wf_{w}d.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        log(json.dumps({"best": best, "locked": [t for t in locked if t["tier"] == "Top 10%"]}, default=str))


if __name__ == "__main__":
    main()

"""BACKTEST: a LightGBM Refinance watch against the live points score (rw3). Experiment, not live.

Same protocol as radar.refi_accuracy, so the numbers line up with docs/refi_odds_table.md:
  learn   rw3's learning dates (2024 monthly for 90 days, 2023 monthly for 12 months: every outcome
          closed before 2025), or a longer run of earlier dates for the "long history" variants
  test    the first of every month from Jan 2025 while the window closes before the radar began watching
  known   only charges filed before each date count
  target  switched lender (the lead), unless the variant says "any refinance"
  ranks   top 1%, 5% and 10% within each group, pooled over the test dates
  ranges  95% interval for (model's hit rate - rw3's hit rate), resampling companies, paired

LightGBM is a gradient-boosted tree classifier: it learns P(switch within the window | clues). It is a
discriminative model, not a generative one. Per-row explanations come from LightGBM's own SHAP values
(pred_contrib), the "evidence behind the score" idea in the SotonShip shadow-fleet predictor.

    pip install lightgbm numpy pandas
    python -m radar.refi_gbm                    # radar data, 90 days and 12 months
    python -m radar.refi_gbm --population sample   # the random backfill sample, once it has data
"""
from __future__ import annotations

import argparse
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from . import config
from . import refi_accuracy as ra
from . import refi_watch as rw

CLUES = rw.CLUES_RW3
NUMERIC = ("n_known", "n_open", "n_sat", "newest_age", "oldest_age", "mean_age", "recent_3m", "recent_6m",
           "recent_12m", "sat_12m", "sat_open_ratio", "since_sat", "since_filed", "n_lenders_open", "pullback_x",
           "lender_size", "rate_max", "rate_newest", "co_age", "past_switch")
CATS = ("lender_cls",)
TIERS = (0.01, 0.05, 0.10)
GROUPS = [g for g in ra.GROUPS if g[0] in ("Everyone", "Ordinary companies (10 loans or fewer)", "Serial borrowers (11+ loans)",
                                           "Property", "Ordinary, not property")]
OUT = config.OUTPUT_DIR / "refi_watch" / "gbm"


def _monthly(start: date, end: date) -> list[date]:
    out, on = [], start
    while on <= end:
        out.append(on)
        on = date(on.year + (on.month == 12), on.month % 12 + 1, 1)
    return out


def long_train_dates(window: int) -> list[date]:
    """Earlier monthly dates too, still with every outcome closed before 2025."""
    last = date(2024, 10, 1) if window <= 90 else date(2023, 12, 1)
    return _monthly(date(2019, 1, 1), last)


def numeric(kc, on: date, vol) -> dict:
    """Numeric charge clues from what was filed before `on` (kc is already cut to that)."""
    cs = sorted(((rw._d(c.filed), rw._start(c) or rw._d(c.filed), rw._d(c.satisfied), c) for c in kc.charges),
                key=lambda t: t[0])
    sat = [t[2] for t in cs if t[2] and t[2] <= on]
    open_ = [t for t in cs if not (t[2] and t[2] <= on)]
    ages = [(on - t[1]).days for t in open_]
    newest = max(open_, key=lambda t: (t[1], t[3].charge_id))
    key, cls = rw._lender(newest[3])
    v = vol.get(key) or {}

    def months(a, b):
        tot = 0
        for k in range(a, b):
            y, m = on.year, on.month - 1 - k
            while m <= 0:
                m += 12; y -= 1
            tot += v.get((y, m), 0)
        return tot
    before = months(6, 18) / 2
    rates = [rw.repay_rate(vol, *rw._lender(t[3]), (on - t[1]).days, on) for t in open_]
    rn = rw.repay_rate(vol, key, cls, (on - newest[1]).days, on)
    # past switches: a repayment followed within 90 days by a charge from a lender not on the repaid loan
    past = 0
    for f, s, sd, c in cs:
        if sd and sd <= on and c.lender_keys:
            past += any(t[0] and sd - timedelta(days=30) <= t[0] <= sd + timedelta(days=90) and t[3].lender_keys
                        and not (t[3].lender_keys & c.lender_keys) for t in cs)
    created = rw._d(kc.created)
    return {
        "n_known": len(cs), "n_open": len(open_), "n_sat": len(sat),
        "newest_age": (on - newest[1]).days, "oldest_age": max(ages), "mean_age": sum(ages) / len(ages),
        "recent_3m": sum((on - t[0]).days <= 91 for t in cs), "recent_6m": sum((on - t[0]).days <= 183 for t in cs),
        "recent_12m": sum((on - t[0]).days <= 365 for t in cs), "sat_12m": sum((on - s).days <= 365 for s in sat),
        "sat_open_ratio": len(sat) / len(cs), "since_sat": (on - max(sat)).days if sat else math.nan,
        "since_filed": (on - cs[-1][0]).days, "n_lenders_open": len({rw._lender(t[3])[0] for t in open_}),
        "pullback_x": months(0, 6) / before if before >= 10 else math.nan, "lender_size": before,
        "rate_max": max((r for r in rates if r is not None), default=math.nan),
        "rate_newest": rn if rn is not None else math.nan,
        "co_age": (on - created).days if created else math.nan, "past_switch": past,
        "lender_cls": cls,
    }


def build(companies, dates, vol, window):
    import hashlib, pickle
    key = hashlib.sha1(repr((sorted(companies)[:50], len(companies), dates, window)).encode()).hexdigest()[:12]
    cache = OUT / "cache" / f"rows_{key}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    rows = []
    for co in companies.values():
        for on in dates:
            kc = ra.known_on(co, on)
            f = rw.features(kc, on, vol)
            if f:
                rows.append({"co": co.number, "on": on, "f": f, "x": numeric(kc, on, vol),
                             "y": ra.outcomes(co, on, window)})
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps([{**r, "f": {k: v for k, v in r["f"].items() if k != "_newest"}} for r in rows]))
    return rows


def frame(rows, use):
    import pandas as pd
    data = {}
    if "clues" in use:
        for c in CLUES:
            data[c] = [r["f"][c] for r in rows]
    if "numeric" in use:
        for c in NUMERIC:
            data[c] = [r["x"][c] for r in rows]
        for c in CATS:
            data[c] = [r["x"][c] for r in rows]
    if "segment" in use:
        data["serial"] = [int(bool(r["f"]["_serial"])) for r in rows]
    df = pd.DataFrame(data)
    return df


def _cats(df, ref=None):
    import pandas as pd
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            cats = ref[c].cat.categories if ref is not None else sorted(set(df[c]))
            df[c] = pd.Categorical(df[c], categories=cats)
    return df


PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, cat_smooth=20, min_data_per_group=100,
              verbose=-1, seed=7, deterministic=True, num_threads=4)


# RoadSense-style (ArmaanF-dot/Pothole-simulator-, roadsense/models/real_risk.py): clues that can only push a
# company up, so cohort quirks in the learning years can't flip them
MONOTONE = {"rate_max": 1, "rate_newest": 1, "sat_12m": 1, "past_switch": 1, "recent_6m": 1, "n_open": 1}


def seg_weights(rows, target):
    """Each segment (ordinary, serial) gets equal total weight on its positives, as RoadSense weights cities."""
    import numpy as np
    seg = np.array([bool(r["f"]["_serial"]) for r in rows])
    y = np.array([int(r["y"][target]) for r in rows])
    w = np.ones(len(rows))
    tot = y.sum()
    for s in (False, True):
        pos = y[seg == s].sum()
        if pos:
            w[seg == s] = (tot / 2) / pos
    return w


def fit(train, use, target, params=None):
    """Learn on the earlier 80% of the learning dates, stop early on the latest 20%, then refit on all."""
    import lightgbm as lgb
    p = {**PARAMS, **(params or {})}
    weigh = "weights" in use
    if "monotone" in use:
        cols = list(frame(train[:1], use).columns)
        p["monotone_constraints"] = [MONOTONE.get(c, 0) for c in cols]
        p["monotone_constraints_method"] = "advanced"
    dates = sorted({r["on"] for r in train})
    cut = dates[int(len(dates) * 0.8)]
    tr, va = [r for r in train if r["on"] < cut], [r for r in train if r["on"] >= cut]
    Xa = _cats(frame(train, use))
    Xtr, Xva = _cats(frame(tr, use), Xa), _cats(frame(va, use), Xa)
    ytr, yva = [int(r["y"][target]) for r in tr], [int(r["y"][target]) for r in va]
    wtr = seg_weights(tr, target) if weigh else None
    wva = seg_weights(va, target) if weigh else None
    b = lgb.train(p, lgb.Dataset(Xtr, ytr, weight=wtr), num_boost_round=1500, valid_sets=[lgb.Dataset(Xva, yva, weight=wva)],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    n = max(50, b.best_iteration)
    final = lgb.train(p, lgb.Dataset(Xa, [int(r["y"][target]) for r in train],
                                      weight=seg_weights(train, target) if weigh else None), num_boost_round=n)
    return final, Xa, n


def predict(model, ref, rows, use):
    return list(model.predict(_cats(frame(rows, use), ref)))


def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for k, i in enumerate(order):
        r[i] = k / max(1, len(xs) - 1)
    return r


def compare(rows, scores: dict, base="rw3 (live)", reps=1000, seed=7):
    """Per group and tier: switched-lender hit rate for each model, and the paired, company-resampled
    95% range for (model - rw3)."""
    import numpy as np
    out = []
    for label, keep in GROUPS:
        idx = [i for i, r in enumerate(rows) if keep(r["f"])]
        if not idx:
            continue
        ys = np.array([int(rows[i]["y"]["switched"]) for i in idx])
        cos = [rows[i]["co"] for i in idx]
        uniq = {c: k for k, c in enumerate(dict.fromkeys(cos))}
        cid = np.array([uniq[c] for c in cos])
        sel = {}
        for name, s in scores.items():
            tie = random.Random(seed)
            v = [(-s[i], tie.random()) for i in idx]
            order = sorted(range(len(idx)), key=lambda j: v[j])
            for t in TIERS:
                k = max(1, int(len(idx) * t))
                m = np.zeros(len(idx), bool); m[order[:k]] = True
                sel[(name, t)] = m
        gen = np.random.default_rng(seed)
        nco = len(uniq)
        # per company sums of y within each selection, for a fast cluster bootstrap
        per = {key: (np.bincount(cid, weights=m, minlength=nco), np.bincount(cid, weights=m * ys, minlength=nco))
               for key, m in sel.items()}
        draws = gen.integers(0, nco, size=(reps, nco))
        cnt = np.stack([np.bincount(d, minlength=nco) for d in draws])   # reps x nco multiplicities
        base_rate = ys.mean()
        for t in TIERS:
            b_n, b_h = per[(base, t)]
            b_rate = (cnt @ b_h) / np.maximum(cnt @ b_n, 1)
            for name in scores:
                n_, h_ = per[(name, t)]
                rate = h_.sum() / n_.sum()
                row = {"group": label, "tier": f"Top {int(t * 100)}%", "model": name, "rows": int(n_.sum()),
                       "hits": int(h_.sum()), "rate": round(float(rate), 4),
                       "lift": round(float(rate / base_rate), 2) if base_rate else None, "base": round(float(base_rate), 4)}
                if name != base:
                    d = (cnt @ h_) / np.maximum(cnt @ n_, 1) - b_rate
                    row["diff"] = round(float(rate - b_h.sum() / b_n.sum()), 4)
                    row["diff_ci"] = [round(float(np.quantile(d, 0.025)), 4), round(float(np.quantile(d, 0.975)), 4)]
                    row["ahead"] = round(float((d > 0).mean()), 3)
                out.append(row)
    return out


def avg_precision(scores, ys):
    ranked = sorted(zip(scores, ys), key=lambda t: -t[0])
    hits, tot, pos = 0, 0.0, sum(ys)
    for i, (_, y) in enumerate(ranked, 1):
        if y:
            hits += 1; tot += hits / i
    return tot / pos if pos else 0.0


def run(companies, window, stop, learn_on=None, log=print, variants=None):
    learn_on = learn_on or companies
    vol = rw.lender_volumes(learn_on)
    train = build(learn_on, ra.train_dates(window), vol, window)
    tests = ra.test_dates(companies, window, stop)
    test = build(companies, tests, vol, window)
    log(f"{window}d: learn {len(train):,} rows ({sum(r['y']['switched'] for r in train):,} switched), "
        f"test {len(test):,} rows on {len(tests)} dates")
    rw3 = rw.Model([(r["f"], r["y"]["any"]) for r in train], CLUES)
    scores = {"rw3 (live)": [rw3.score(r["f"]) for r in test]}
    info = {}
    todo = variants or [
        ("LightGBM, rw3's clues", ("clues",), "switched", "short"),
        ("LightGBM, rw3's clues + numeric charge clues", ("clues", "numeric"), "switched", "short"),
        ("LightGBM, same, trained on any refinance", ("clues", "numeric"), "any", "short"),
        ("LightGBM, clues + numeric, long history", ("clues", "numeric"), "switched", "long"),
        ("LightGBM RoadSense-style: monotone, segment input, balanced weights", ("clues", "numeric", "segment", "monotone", "weights"), "switched", "short"),
        ("LightGBM RoadSense-style, long history", ("clues", "numeric", "segment", "monotone", "weights"), "switched", "long"),
    ]
    long_train = None
    models = {}
    for name, use, target, hist in todo:
        tr = train
        if hist == "long":
            if long_train is None:
                long_train = build(learn_on, long_train_dates(window), vol, window)
                log(f"  long history: {len(long_train):,} rows from {long_train_dates(window)[0]}")
            tr = long_train
        m, ref, n = fit(tr, use, target)
        scores[name] = predict(m, ref, test, use)
        models[name] = (m, ref, use)
        info[name] = {"rounds": n, "learn_rows": len(tr)}
        log(f"  fitted {name} ({n} rounds)")
    best = "LightGBM, rw3's clues + numeric charge clues"
    scores["Blend: half rw3, half LightGBM (ranks)"] = [
        (a + b) / 2 for a, b in zip(_rank(scores["rw3 (live)"]), _rank(scores[best]))]
    ys = [int(r["y"]["switched"]) for r in test]
    ap = {}
    for label, keep in GROUPS:
        idx = [i for i, r in enumerate(test) if keep(r["f"])]
        ap[label] = {k: round(avg_precision([v[i] for i in idx], [ys[i] for i in idx]), 4) for k, v in scores.items()}
    return {"window_days": window, "learn_dates": [d.isoformat() for d in ra.train_dates(window)],
            "test_dates": [d.isoformat() for d in tests], "rows": len(test),
            "companies": len({r["co"] for r in test}), "models": info, "table": compare(test, scores),
            "avg_precision": ap, "importance": _importance(models.get(best))}, (models, test)


def _importance(m):
    if not m:
        return {}
    b = m[0]
    g = b.feature_importance("gain")
    tot = sum(g) or 1
    return dict(sorted(((n, round(float(v / tot), 3)) for n, v in zip(b.feature_name(), g)), key=lambda t: -t[1]))


def reasons(model, ref, rows, use, top=3):
    """SHAP contributions per row (LightGBM pred_contrib): the clues that pushed each company up."""
    X = _cats(frame(rows, use), ref)
    contrib = model.predict(X, pred_contrib=True)
    names = list(X.columns)
    out = []
    for r, c in zip(rows, contrib):
        parts = sorted(((float(v), n) for v, n in zip(c[:-1], names)), reverse=True)
        out.append([f"{n}={r['f'].get(n, r['x'].get(n))}" for v, n in parts[:top] if v > 0])
    return out


def _pct(x):
    return "–" if x is None else f"{x:.1%}"


def render_md(res) -> str:
    out = [f"## {res['window_days']} days: switched lender", "",
           f"Learned on {res['learn_dates'][0]} to {res['learn_dates'][-1]}; tested on {len(res['test_dates'])} monthly dates "
           f"{res['test_dates'][0]} to {res['test_dates'][-1]} ({res['rows']:,} company-dates, {res['companies']:,} companies).", "",
           "| Group | Tier | Model | Hit rate (hits) | Lift | vs rw3 | 95% range of the gap | Ahead of rw3 in |", "|---|---|---|---:|---:|---:|---|---:|"]
    for r in res["table"]:
        gap = f"{r['diff'] * 100:+.1f} pts" if "diff" in r else ""
        ci = f"{r['diff_ci'][0] * 100:+.1f} to {r['diff_ci'][1] * 100:+.1f}" if "diff_ci" in r else ""
        out.append(f"| {r['group']} | {r['tier']} | {r['model']} | {_pct(r['rate'])} ({r['hits']}) | {r['lift']}x | {gap} | {ci} | {f"{r['ahead']:.0%}" if 'ahead' in r else ''} |")
    out += ["", "Average precision (switched lender):", "", "| Group | " + " | ".join(next(iter(res["avg_precision"].values())).keys()) + " |",
            "|---|" + "---:|" * len(next(iter(res["avg_precision"].values())))]
    for g, d in res["avg_precision"].items():
        out.append(f"| {g} | " + " | ".join(f"{v:.3f}" for v in d.values()) + " |")
    return "\n".join(out) + "\n"


def main(argv=None):
    from .aliases import Resolver
    from .charge_history import load_companies
    from .db import connect
    from .lender_view import coverage_start
    p = argparse.ArgumentParser(prog="python -m radar.refi_gbm")
    p.add_argument("--window", type=int, action="append", help="days (default: 90 and 365)")
    p.add_argument("--population", choices=("live", "sample"), default="live")
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args(argv)
    conn = connect()
    if a.population == "sample":
        from . import backfill
        bconn = backfill.connect()
        companies = backfill.load_companies(conn, bconn, Resolver(conn))
        if not companies:
            raise SystemExit("No sample fetched yet (data/backfill.db): run `python -m radar.dbsync pull --backfill`.")
        stop, learn_on = backfill.first_fetched(bconn), None
    else:
        companies, stop, learn_on = load_companies(conn, Resolver(conn)), coverage_start(conn), None
    out = Path(a.out) / a.population
    out.mkdir(parents=True, exist_ok=True)
    md = [f"BACKTEST: LightGBM vs rw3, {a.population} population", ""]
    for w in a.window or (90, 365):
        res, _ = run(companies, w, stop, learn_on, log=lambda m: print(m, flush=True))
        (out / f"gbm_{w}d.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        md.append(render_md(res))
        print(render_md(res), flush=True)
    (out / "gbm.md").write_text("\n".join(md), encoding="utf-8")
    print("->", out / "gbm.md")


if __name__ == "__main__":
    main()

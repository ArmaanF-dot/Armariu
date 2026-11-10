"""Do Gazette winding-up petitions improve the UK risk score? (BACKTEST, plan task 13)

    python -m radar.petition_test [--codes 2450] [--out FILE]

Input: output/gazette/petitions.jsonl.gz from `python -m radar.gazette_fetch` (the "gazette" workflow). Adds two
features to radar.distress, "a petition notice for this company number published in the last 6 / 3 months", using
only notices published on or before the cutoff, and compares the same model with and without them on the same strict
time split (learn on 2022-23 cutoffs, test on 2024-25). Gazette data starts in Jan 2022, so only 6- and 3-month
lookbacks are used (the first cutoff is Jul 2022). Reports AUC for both, the gain with a company-clustered 95% range,
top-10% outcome rates, and the share of test insolvencies that had a petition first (how early the signal is).
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import config
from . import distress as D

GAZ = config.OUTPUT_DIR / "gazette" / "petitions.jsonl.gz"


def load_petitions(path=GAZ, codes=("2450",)):
    by = defaultdict(list)
    for line in gzip.open(path, "rt"):
        r = json.loads(line)
        if r.get("number") and str(r.get("code")) in codes and r.get("published"):
            by[r["number"]].append(date.fromisoformat(r["published"]))
    for v in by.values():
        v.sort()
    return by


def pet_feats(dates, C: date) -> dict:
    """Petitions published on or before C within 6 and 3 months (never after C)."""
    if not dates:
        return {"pet_6m": 0, "pet_3m": 0}
    hi = bisect.bisect_right(dates, C)
    n6 = hi - bisect.bisect_right(dates, C - timedelta(days=183))
    n3 = hi - bisect.bisect_right(dates, C - timedelta(days=91))
    return {"pet_6m": int(n6 > 0), "pet_3m": int(n3 > 0)}


def with_petitions(cos, pets, cats, cutoffs):
    rows, y, w, gid, cut, first_pet = [], [], [], [], [], []
    for i, co in enumerate(cos):
        key = co.n.zfill(8) if str(co.n).isdigit() else str(co.n).upper()
        for C in cutoffs:
            f = D.features(co, C, cats)
            if f is None:
                continue
            f.update(pet_feats(pets.get(key, []), C))
            rows.append(f); y.append(D.outcome(co, C)); w.append(co.w); gid.append(i); cut.append(C)
    return rows, np.array(y), np.array(w), np.array(gid), cut


def main(argv=None):
    from sklearn.ensemble import HistGradientBoostingClassifier
    p = argparse.ArgumentParser(prog="python -m radar.petition_test")
    p.add_argument("--codes", default="2450")
    p.add_argument("--out", default=str(D.OUT / "petition_test.json"))
    a = p.parse_args(argv)
    cats = D.load_lender_cats()
    cos, info = D.load()
    pets = load_petitions(codes=tuple(a.codes.split(",")))
    print("companies with a petition notice:", len(pets), "of", len(cos), "loaded")
    tr, ytr, wtr, gtr, ctr = with_petitions(cos, pets, cats, D.TRAIN)
    te, yte, wte, gte, cte = with_petitions(cos, pets, cats, D.TEST)
    allcols = sorted({k for r in tr + te for k in r})
    base_cols = D.cols_for((D.PUBLIC, D.ACTIVITY, D.CHARGES), [c for c in allcols if not c.startswith("pet_")])
    plus_cols = base_cols + ["pet_6m", "pet_3m"]
    scores = {}
    for name, cols in (("without_petitions", base_cols), ("with_petitions", plus_cols)):
        Xtr, Xte = D.matrix(tr, cols), D.matrix(te, cols)
        m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=80,
                                           l2_regularization=1.0, random_state=0)
        m.fit(Xtr, ytr, sample_weight=wtr / wtr.mean())
        scores[name] = m.predict_proba(Xte)[:, 1]
    res = {"test_rows": len(yte), "test_events": int(yte.sum()), "train_events": int(ytr.sum())}
    for name, s in scores.items():
        res[name] = D.metrics(s, yte, wte, cte)
    sa, sb = scores["with_petitions"], scores["without_petitions"]
    diff = D.bootstrap(lambda i: [D.auc(sa[i], yte[i], wte[i]) - D.auc(sb[i], yte[i], wte[i])], gte, n=150)
    res["auc_gain_ci"] = [float(diff[0, 0]), float(diff[1, 0])]
    res["auc_gain"] = res["with_petitions"]["auc"] - res["without_petitions"]["auc"]
    pet6 = D.matrix(te, ["pet_6m"])[:, 0] > 0
    res["petition_6m_rate"] = {"with": float((wte[pet6] * yte[pet6]).sum() / max(1e-9, wte[pet6].sum())),
                                "without": float((wte[~pet6] * yte[~pet6]).sum() / wte[~pet6].sum()),
                                "rows_with": int(pet6.sum()),
                                "share_of_events": float((wte[pet6] * yte[pet6]).sum() / max(1e-9, (wte * yte).sum()))}
    print(json.dumps(res, indent=1, default=float))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()

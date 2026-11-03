"""Refinance watch: which companies are most likely to refinance in the next 12 months.

Filings carry no loan end dates, so this is odds, not a forecast. Each company with a charge still
outstanding gets points for a handful of clues, learned from history:

  age_cls   age of its newest open charge, crossed with that lender's type (bridging counted apart)
  recent    how many charges it filed in the last six months (active borrowers move more)
  nopen     how many charges it has outstanding
  pullback  whether the newest open charge's lender has cut its new lending by half or more
  property  whether the company is in property

The outcome is a refinance: within 12 months an outstanding charge is marked satisfied and a new
charge is filed. Points are log-odds against the base rate, learned on cutoffs whose outcome window had
closed by the run date, so nothing peeks ahead. The odds shown next to a company are the hit rate of
its score band in that history. `backtest` learns on an earlier year and scores later dates it never
saw. Measured 3 Nov 2026 (12 months): top 1% refinanced 54%, against 11% for all companies; with a
90-day window the same score gave 22% against 3%. History covers only companies that borrowed after
20 Oct 2026, which flatters absolute rates; the lift is the steadier number.
Clues still to add when the data is reachable: loans due within a year (accounts), ownership changes.

    python -m radar.refi_watch [--as-of 2026-11-03] [--top 200]
    python -m radar.refi_watch backtest
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import config
from .charge_history import Company, load_companies

MODEL_VERSION = "rw1"
WINDOW = 365   # days; 90 and 180 were tested too (see the docstring)
CLUES = ("age_cls", "recent", "nopen", "pullback", "property")
LABELS = {
    "age_cls": "Newest open loan", "recent": "Recent borrowing", "nopen": "Loans outstanding",
    "pullback": "Lender's new lending", "property": "Property company",
}
CAVEAT = ("Odds, not a forecast: filings carry no loan end dates. The odds are how often companies with a "
          "similar score refinanced within 12 months in the past.")


def _d(s):
    return date.fromisoformat(s[:10]) if s else None


def _start(c):
    return _d(c.created or c.filed)


def _lender(c) -> tuple[str | None, str]:
    """(key, type) of a charge's main lender; bridging lenders get their own type."""
    if not c.lenders:
        return None, "other"
    name, key, cls = sorted(c.lenders, key=lambda t: (t[2] == "trustee_agent", t[0]))[0]
    return key, ("bridging" if "BRIDG" in (name or "").upper() else cls)


def _age_band(years: float) -> str:
    return "under 6 months" if years < 0.5 else "6 to 12 months" if years < 1 else "1 to 2 years" if years < 2 \
        else "2 to 5 years" if years < 5 else "over 5 years"


def lender_volumes(companies: dict[str, Company]) -> dict:
    vol: dict = defaultdict(Counter)
    for co in companies.values():
        for c in co.charges:
            f = _d(c.filed)
            if f:
                vol[_lender(c)[0]][(f.year, f.month)] += 1
    return vol


def _pullback(vol, key, on: date) -> str:
    v = vol.get(key)
    if not v:
        return "unknown"

    def months(a, b):
        tot = 0
        for k in range(a, b):
            y, m = on.year, on.month - 1 - k
            while m <= 0:
                m += 12; y -= 1
            tot += v[(y, m)]
        return tot

    recent, before = months(0, 6), months(6, 18) / 2
    if before < 10:
        return "small lender"
    r = recent / before
    return "falling by half or more" if r < 0.5 else "steady" if r < 1.5 else "growing"


def features(co: Company, on: date, vol) -> dict | None:
    """Clues for one company on one date, or None if it has nothing outstanding."""
    open_ = [c for c in co.charges if _start(c) and _start(c) < on and not (_d(c.satisfied) and _d(c.satisfied) <= on)]
    if not open_:
        return None
    newest = max(open_, key=lambda c: (_start(c), c.charge_id))
    key, cls = _lender(newest)
    recent = sum(1 for c in co.charges if _d(c.filed) and on - timedelta(days=183) <= _d(c.filed) < on)
    return {
        "age_cls": f"{_age_band((on - _start(newest)).days / 365.25)}, {cls.replace('_', ' ')}",
        "recent": "none" if recent == 0 else "one" if recent == 1 else "two or more",
        "nopen": "one" if len(open_) == 1 else "two or three" if len(open_) <= 3 else "four or more",
        "pullback": _pullback(vol, key, on),
        "property": "yes" if co.sector == "Property and real estate" else "no",
        "_newest": newest,
    }


def outcome(co: Company, on: date) -> bool:
    end = on + timedelta(days=WINDOW)
    sat = any(_d(c.satisfied) and on < _d(c.satisfied) <= end and _start(c) and _start(c) < on for c in co.charges)
    new = any(_d(c.filed) and on < _d(c.filed) <= end for c in co.charges)
    return sat and new


def examples(companies, cutoffs, vol):
    out = []
    for co in companies.values():
        for on in cutoffs:
            f = features(co, on, vol)
            if f:
                out.append((f, outcome(co, on)))
    return out


class Model:
    def __init__(self, rows):
        self.n = len(rows)
        self.base = (sum(y for _, y in rows) / self.n) if self.n else 0.0
        self.points: dict = {}
        for clue in CLUES:
            cnt = defaultdict(lambda: [0, 0])
            for x, y in rows:
                cnt[x[clue]][0] += 1; cnt[x[clue]][1] += y
            for v, (n, h) in cnt.items():   # smoothed towards the base rate; tiny groups score 0
                self.points[(clue, v)] = math.log(((h + 2 * self.base) / (n + 2)) / self.base) if n >= 30 and self.base else 0.0
        # calibration: hit rate by score band (20 equal bands)
        scored = sorted(((self.score(x), y) for x, y in rows), key=lambda t: t[0])
        self.bands = []
        k = max(1, len(scored) // 20)
        for i in range(0, len(scored), k):
            part = scored[i:i + k]
            self.bands.append((part[0][0], sum(y for _, y in part) / len(part)))

    def score(self, x) -> float:
        return sum(self.points.get((c, x[c]), 0.0) for c in CLUES)

    def odds(self, s: float) -> float:
        rate = self.bands[0][1] if self.bands else self.base
        for lo, r in self.bands:
            if s >= lo:
                rate = r
        return rate

    def reasons(self, x, top=3) -> list[str]:
        parts = sorted(((self.points.get((c, x[c]), 0.0), c) for c in CLUES), reverse=True)
        return [f"{LABELS[c]}: {x[c]}" for p, c in parts[:top] if p > 0.05]


def _cutoffs(end: date, years: float = 1.5, step: int = 61) -> list[date]:
    out, on = [], end
    while on > end - timedelta(days=int(365.25 * years)):
        out.append(on); on -= timedelta(days=step)
    return sorted(out)


def learn(companies, as_of: date, vol=None) -> Model:
    vol = vol or lender_volumes(companies)
    return Model(examples(companies, _cutoffs(as_of - timedelta(days=WINDOW + 1)), vol))


def watchlist(companies, as_of: date, top: int = 200, model: Model | None = None) -> tuple[list[dict], Model]:
    vol = lender_volumes(companies)
    model = model or learn(companies, as_of, vol)
    rows = []
    for co in companies.values():
        f = features(co, as_of, vol)
        if not f:
            continue
        s = model.score(f)
        newest = f["_newest"]
        rows.append({
            "company_number": co.number, "company_name": co.name, "sector": co.sector, "score": round(s, 3),
            "odds_12m": round(model.odds(s), 4), "base_rate": round(model.base, 4),
            "lift": round(model.odds(s) / model.base, 1) if model.base else None,
            "reasons": "; ".join(model.reasons(f)),
            "newest_lender": ", ".join(sorted({"A private individual" if cls == "individual" else n for n, _, cls in newest.lenders})),
            "newest_created": newest.created or newest.filed, "model_version": MODEL_VERSION,
            "filing_url": f"https://find-and-update.company-information.service.gov.uk/company/{co.number}/charges",
        })
    rows.sort(key=lambda r: (-r["score"], r["company_number"]))
    return rows[:top], model


def backtest(companies, train_end: date = date(2025, 1, 1), test_start: date = date(2025, 4, 1),
             test_end: date = date(2026, 10, 20)) -> dict:
    """Learn on the 12 months to train_end, score test cutoffs the model never saw."""
    vol = lender_volumes(companies)
    model = Model(examples(companies, _cutoffs(train_end, years=1.0), vol))
    tests, on = [], test_start
    while on <= test_end - timedelta(days=WINDOW):
        tests.append(on); on += timedelta(days=61)
    te = examples(companies, tests, vol)
    base = sum(y for _, y in te) / len(te) if te else 0.0
    ranked = sorted(((model.score(x), y) for x, y in te), key=lambda t: -t[0])
    tiers = []
    for q in (0.01, 0.05, 0.10, 0.25):
        k = max(1, int(len(ranked) * q))
        hit = sum(y for _, y in ranked[:k]) / k
        tiers.append({"top": f"{int(q * 100)}%", "companies": k, "hit_rate": round(hit, 4), "lift": round(hit / base, 1) if base else None})
    return {"label": "BACKTEST", "model_version": MODEL_VERSION, "train_end": train_end.isoformat(),
            "test_cutoffs": [t.isoformat() for t in tests], "rows": len(te), "base_rate": round(base, 4), "tiers": tiers}


def render_md(rows, bt, as_of: date) -> str:
    out = [f"# Refinance watch, {as_of.isoformat()}", "", f"_{CAVEAT}_", ""]
    if bt:
        out += [f"Backtest ({bt['model_version']}): learned to {bt['train_end']}, tested on {len(bt['test_cutoffs'])} later dates "
                f"({bt['rows']:,} company-dates). Base rate {bt['base_rate']:.1%}.", "",
                "| Top | Companies | Refinanced in 12 months | Lift |", "|---|---:|---:|---:|"]
        out += [f"| {t['top']} | {t['companies']:,} | {t['hit_rate']:.1%} | {t['lift']}x |" for t in bt["tiers"]]
        out.append("")
    out += ["| Company | Sector | Odds (12 months) | Why |", "|---|---|---:|---|"]
    out += [f"| {r['company_name']} ({r['company_number']}) | {r['sector']} | {r['odds_12m']:.0%} | {r['reasons']} |" for r in rows[:50]]
    return "\n".join(out) + "\n"


def write(rows, bt, out_dir, as_of: date) -> tuple[Path, Path]:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    csv_p, md_p = out / f"refi_watch_{as_of.isoformat()}.csv", out / f"refi_watch_{as_of.isoformat()}.md"
    with csv_p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["company_number"])
        w.writeheader(); w.writerows(rows)
    md_p.write_text(render_md(rows, bt, as_of), encoding="utf-8")
    return csv_p, md_p


def main(argv=None):
    from .aliases import Resolver
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.refi_watch")
    p.add_argument("cmd", nargs="?", default="list", choices=("list", "backtest"))
    p.add_argument("--as-of", default=date.today().isoformat())
    p.add_argument("--top", type=int, default=200)
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "refi_watch"))
    a = p.parse_args(argv)
    conn = connect()
    companies = load_companies(conn, Resolver(conn))
    bt = backtest(companies)
    if a.cmd == "backtest":
        print(render_md([], bt, date.fromisoformat(a.as_of)))
        return
    rows, model = watchlist(companies, date.fromisoformat(a.as_of), a.top)
    paths = write(rows, bt, a.out, date.fromisoformat(a.as_of))
    print(f"{len(rows)} companies, base rate {model.base:.1%} -> {paths[0]}")


if __name__ == "__main__":
    main()

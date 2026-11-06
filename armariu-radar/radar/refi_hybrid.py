"""Refinance watch, hybrid score: LightGBM for serial borrowers and property companies, rw3 for the rest.

Why. The 5 Nov 2026 tests (docs/refi_gbm.md) found LightGBM level with rw3 for ordinary companies at
12 months (the headline) but clearly better for serial borrowers (11+ charges) and property companies,
on walk-forward years and on a locked window. So only those two segments use it. Everyone else keeps rw3.

How it works.
  * Learn on monthly dates from 2019 whose 12-month outcome had closed by the run date (nothing peeks ahead).
    Target: switched lender. RoadSense-style: monotone constraints, segment input, balanced weights.
  * Rank: a company in the GBM segment gets the GBM's predicted chance. Its odds_12m is the hit rate of its
    score band for "refinanced" (the same outcome rw3's odds show), read from held-out learning dates.
  * Optional: needs `lightgbm`, `numpy` and `pandas` (requirements-gbm.txt). Without them, or with too little
    history, the list silently stays on rw3 and says so (`hybrid_status`).

`python -m radar.refi_hybrid backtest` runs the time-split test: learn on dates to Dec 2023, score every month of
2025 (outcomes closed before the radar began), rw3 against the hybrid, by group, with paired company-resampled
95% ranges. It writes output/refi_watch/hybrid/backtest.{md,json}. Absolute rates run about 3x high versus the
random sample (docs/refi_population.md); compare rows with each other and quote random-sample rates to lenders.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from . import config
from . import refi_accuracy as ra
from . import refi_gbm as g
from . import refi_watch as rw

MODEL_VERSION = "rw3+gbm1"
USE = ("clues", "numeric", "segment", "monotone", "weights")
WINDOW = 365
MIN_ROWS = 20000
OUT = config.OUTPUT_DIR / "refi_watch" / "hybrid"
RANDOM_SAMPLE_NOTE = (
    "Radar rates run high: the radar only holds companies active at Companies House after 20 Oct 2026. On the random "
    "sample of 1,501 companies (docs/refi_population.md), ordinary 12-month rates were 1.9% against 6.8%, and property "
    "top-10% 12-month was 23.3% against 40.9%. Compare rows with each other; quote random-sample rates to lenders.")


def in_segment(f: dict) -> bool:
    """Companies the GBM scores: serial borrowers and property companies."""
    return bool(f["_serial"]) or f["property"] == "yes"


def learn_dates(as_of: date) -> list[date]:
    last = as_of - timedelta(days=WINDOW + 1)
    return [d for d in g._monthly(date(2019, 1, 1), last)]


class Hybrid:
    def __init__(self, companies, as_of: date, vol=None, log=lambda m: None):
        self.vol, self.status, self.bands, self.model = vol or rw.lender_volumes(companies), "rw3 only", {}, None
        try:
            import lightgbm  # noqa: F401
            import pandas  # noqa: F401
        except ImportError:
            self.status = "rw3 only (lightgbm not installed)"
            return
        dates = learn_dates(as_of)
        rows = g.build(companies, dates, self.vol, WINDOW)
        seg = [r for r in rows if in_segment(r["f"])]
        log(f"hybrid: {len(rows):,} learning rows, {len(seg):,} in serial/property")
        if len(seg) < MIN_ROWS:
            self.status = f"rw3 only (only {len(seg):,} serial/property learning rows)"
            return
        self.model, self.ref, n = g.fit(rows, USE, "switched")
        # calibrate out of sample: refit on the earlier 80% of learning dates, score the latest 20%, and read each score
        # band's hit rate for "refinanced". (Bands read from the final model's own training rows ran to 100%.)
        ds = sorted({r["on"] for r in rows})
        cut = ds[int(len(ds) * 0.8)]
        early = [r for r in rows if r["on"] < cut]
        late = [r for r in seg if r["on"] >= cut]
        cal, cref, _ = g.fit(early, USE, "switched")
        self.cal_model = cal
        for name, part in (("serial", [r for r in late if r["f"]["_serial"]]),
                           ("property", [r for r in late if not r["f"]["_serial"]])):
            if len(part) >= 400:
                sc = g.predict(cal, cref, part, USE)
                self.bands[name] = rw.Model._bands([(x, int(r["y"]["any"])) for x, r in zip(sc, part)], n=10)
        self.cal_ref = cref
        self.status = f"LightGBM for serial and property, {n} rounds"

    def active(self) -> bool:
        return self.model is not None

    def score(self, co, on: date, f: dict) -> float | None:
        """(final model score, calibration model score, row) for a company in the segment, else None (use rw3).
        The final model ranks; the calibration model, learned on the earlier 80% only, reads the odds bands."""
        if not (self.active() and in_segment(f)):
            return None
        kc = ra.known_on(co, on)
        row = {"f": {k: v for k, v in f.items() if k != "_newest"}, "x": g.numeric(kc, on, self.vol)}
        return g.predict(self.model, self.ref, [row], USE)[0], g.predict(self.cal_model, self.cal_ref, [row], USE)[0], row

    def odds(self, s: float, serial: bool) -> float | None:
        bands = self.bands.get("serial" if serial else "property")
        if not bands:
            return None
        rate = bands[0][1]
        for lo, r in bands:
            if s >= lo:
                rate = r
        return rate


def rerank(rows: list[dict], companies, as_of: date, model: rw.Model, vol, filed=None, log=lambda m: None) -> tuple[list[dict], str]:
    """Replace odds (and the sort score) for serial and property rows with the GBM's; rw3 keeps the rest.

    The two scorers' raw scores are not on one scale, so a GBM row's sort score is log(odds / base rate), the
    same unit as rw3's points, with a tiny tiebreak from its place within the GBM ranking."""
    import math
    hy = Hybrid(companies, as_of, vol, log)
    for r in rows:
        r["scorer"] = "rw3"
    if not hy.active():
        return rows, hy.status
    scored = []
    for r in rows:
        co = companies[r["company_number"]]
        f = rw.features(co, as_of, vol, filed)
        out = hy.score(co, as_of, f) if f else None
        odds = hy.odds(out[1], bool(f["_serial"])) if out is not None else None
        if odds is not None:
            scored.append((r, out[0], odds, out[2]))
    order = sorted(s for _, s, _, _ in scored)
    why = g.reasons(hy.model, hy.ref, [x[3] for x in scored], USE, top=3) if scored else []
    for (r, s, odds, _), reasons in zip(scored, why):
        r["rw3_score"], r["rw3_odds_12m"] = r["score"], r["odds_12m"]
        r["score"] = round(math.log(max(odds, 1e-4) / model.base) + 1e-3 * _pct_rank(order, s), 4) if model.base else r["score"]
        r["odds_12m"] = round(odds, 4)
        r["lift"] = round(odds / model.base, 1) if model.base else None
        r["scorer"] = "lightgbm"
        r["model_version"] = MODEL_VERSION
        r["reasons"] = "; ".join(_label(x) for x in reasons) or r["reasons"]
    rows.sort(key=lambda r: (-r["score"], r["company_number"]))
    return rows, hy.status


FRIENDLY = {"n_known": "Loans filed", "n_open": "Loans outstanding", "n_sat": "Loans repaid", "newest_age": "Age of newest loan",
            "oldest_age": "Age of oldest loan", "mean_age": "Average loan age", "recent_3m": "Loans filed, last 3 months",
            "recent_6m": "Loans filed, last 6 months", "recent_12m": "Loans filed, last year", "sat_12m": "Loans repaid, last year",
            "sat_open_ratio": "Share of loans repaid", "since_sat": "Time since last repayment", "since_filed": "Time since last loan filed",
            "n_lenders_open": "Lenders on open loans", "pullback_x": "Lender's new lending trend", "lender_size": "Lender size",
            "rate_max": "Lender's repayment rate", "rate_newest": "Newest lender's repayment rate", "co_age": "Company age",
            "past_switch": "Past lender switches", "lender_cls": "Lender type", "serial": "Serial borrower",
            **rw.LABELS}


def _label(s: str) -> str:
    k, _, v = s.partition("=")
    try:
        v = f"{float(v):.0f}" if abs(float(v)) >= 10 else f"{float(v):.2g}"
    except ValueError:
        pass
    return f"{FRIENDLY.get(k, k)}: {v}"


def _pct_rank(sorted_vals, v):
    import bisect
    return bisect.bisect_left(sorted_vals, v) / max(1, len(sorted_vals) - 1)


# ------------------------------------------------------------------ time-split backtest
BT_GROUPS = [("Serial borrowers (11+ loans)", lambda f: bool(f["_serial"])),
             ("Property", lambda f: f["property"] == "yes"),
             ("Serial or property (GBM segment)", in_segment),
             ("Serial or property, top tiers pooled with ordinary on rw3", lambda f: True)]


def backtest(companies, window: int, stop: date | None, log=print) -> dict:
    """Learn on monthly dates from 2019 to the last whose outcome closed before 2025, score every month of 2025 on."""
    vol = rw.lender_volumes(companies)
    train = g.build(companies, g.long_train_dates(window), vol, window)
    tests = ra.test_dates(companies, window, stop)
    test = g.build(companies, tests, vol, window)
    log(f"{window}d: learn {len(train):,} rows, test {len(test):,} rows on {len(tests)} dates")
    rw3 = rw.Model([(r["f"], r["y"]["any"]) for r in train], rw.CLUES_RW3)
    m, ref, n = g.fit(train, USE, "switched")
    scores = {"rw3 (live)": [rw3.score(r["f"]) for r in test], "LightGBM": g.predict(m, ref, test, USE)}
    old = g.GROUPS
    g.GROUPS = BT_GROUPS[:3]
    try:
        table = g.compare(test, scores)
    finally:
        g.GROUPS = old
    return {"window_days": window, "learn_dates": [d.isoformat() for d in g.long_train_dates(window)],
            "test_dates": [d.isoformat() for d in tests], "rows": len(test), "rounds": n, "table": table,
            "companies": len({r["co"] for r in test})}


def render_md(res: dict) -> str:
    out = [f"## {res['window_days']} days: switched lender, time-split", "",
           f"Learned on {res['learn_dates'][0]} to {res['learn_dates'][-1]}; tested on {len(res['test_dates'])} monthly dates "
           f"{res['test_dates'][0]} to {res['test_dates'][-1]} ({res['rows']:,} company-dates, {res['companies']:,} companies). "
           "Ordinary non-property companies stay on rw3, so their rows are unchanged.", "",
           "| Group | Tier | Model | Hit rate (hits) | Lift | vs rw3 | 95% range of the gap |", "|---|---|---|---:|---:|---:|---|"]
    for r in res["table"]:
        gap = f"{r['diff'] * 100:+.1f} pts" if "diff" in r else ""
        ci = f"{r['diff_ci'][0] * 100:+.1f} to {r['diff_ci'][1] * 100:+.1f}" if "diff_ci" in r else ""
        out.append(f"| {r['group']} | {r['tier']} | {r['model']} | {r['rate']:.1%} ({r['hits']}) | {r['lift']}x | {gap} | {ci} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    from .aliases import Resolver
    from .charge_history import load_companies
    from .db import connect
    from .lender_view import coverage_start
    p = argparse.ArgumentParser(prog="python -m radar.refi_hybrid")
    p.add_argument("cmd", choices=("backtest",))
    p.add_argument("--window", type=int, action="append")
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args(argv)
    conn = connect()
    companies, stop = load_companies(conn, Resolver(conn)), coverage_start(conn)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    md = ["BACKTEST: hybrid Refinance watch (LightGBM for serial and property, rw3 for the rest)", "",
          f"_{RANDOM_SAMPLE_NOTE}_", ""]
    for w in a.window or (365, 90):
        res = backtest(companies, w, stop)
        (out / f"backtest_{w}d.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        md.append(render_md(res)); print(render_md(res), flush=True)
    (out / "backtest.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()

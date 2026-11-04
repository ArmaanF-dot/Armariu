"""Refinance watch accuracy at 90 days, row by row, on months the score never saw (BACKTEST).

The 12-month figures (radar.refi_watch backtest) mix everyone together, and serial property borrowers
who refinance all the time flatter the top rows. This table splits the 90-day result by the groups a
lender or broker cares about, with counts and 95% intervals on every row.

How it is tested
  learn   the rw1 points and odds, from monthly dates in 2024 (Jan to Oct, so every 90-day outcome
          closed before 2025 began)
  test    the first of every month from Jan 2025, while the 90 days closed before the radar began
          watching (20 Oct 2026): every company in the data had a charge filing after that day, so a window
          running past it would partly see the answer
  known   on each date, only charges already filed at Companies House count (no look-ahead)
  ranks   top 10%, 5% and 1% are taken within each group, pooled across the test dates
  ranges  95% intervals resample companies, not rows (a company appears on up to 20 dates),
          1,000 times

What counts as "refinanced" (within 90 days of the date)
  switched lender   a loan that was outstanding is marked repaid AND a new charge is filed for a
                    lender that held none of the repaid loans. This is the only kind that is a lead
                    for a competing lender, so it leads the table.
  any refinance     a loan is marked repaid AND any new charge is filed, same lender included
                    (what the live score has always been built on)
  repaid            a loan is marked repaid, whether or not anything replaced it

It is a lead list, not a forecast: filings carry no loan end dates, and nothing here has been tested
with brokers or lenders yet.

    python -m radar.refi_accuracy            # print the table, write output/refi_watch/accuracy_90d.{md,json}
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from . import config
from .charge_history import Company
from .refi_watch import CLUES, MODEL_VERSION, Model, _d, _start, features, lender_volumes

WINDOW = 90
TRAIN = [date(2024, m, 1) for m in range(1, 11)]          # 90-day outcomes all closed by 30 Dec 2024
TRAIN_180 = [date(2024, m, 1) for m in range(1, 8)]       # 180-day outcomes all closed by 28 Dec 2024
TRAIN_365 = [date(2023, m, 1) for m in range(1, 13)]      # 12-month outcomes all closed by 1 Dec 2024


def train_dates(window: int) -> list[date]:
    """Learning dates whose whole outcome window closed before 2025, so no test answer leaks in."""
    return TRAIN if window <= 90 else TRAIN_180 if window <= 180 else TRAIN_365
TEST_FROM = date(2025, 1, 1)
REPS = 1000
DEFINITION = ("Switched lender: within the window a loan that was outstanding is marked repaid and a new "
              "charge is filed for a lender that held none of the repaid loans. Any refinance: repaid and "
              "any new charge, same lender included. Repaid: a loan marked repaid, replaced or not.")
CAVEAT = ("A lead list, not a forecast: filings carry no loan end dates, and these are past hit rates on "
          "months the score never saw. Every company here had a charge filing (a new loan or a repayment) after "
          "20 Oct 2026, so firms that went quiet are missing and absolute rates run high; compare rows with each "
          "other. Not yet tested with brokers or lenders.")


def _filed(c):
    return _d(c.filed) or _start(c)


def known_on(co: Company, on: date) -> Company:
    """The company as Companies House showed it on `on`: charges filed before that day only."""
    return replace(co, charges=[c for c in co.charges if _filed(c) and _filed(c) < on])


def _agent_only(c) -> bool:
    return bool(c.lenders) and all(cls == "trustee_agent" for _, _, cls in c.lenders)


def outcomes(co: Company, on: date, window: int = WINDOW) -> dict:
    end = on + timedelta(days=window)
    repaid = [c for c in co.charges if _filed(c) and _filed(c) < on and _start(c) < on
              and _d(c.satisfied) and on < _d(c.satisfied) <= end]
    new = [c for c in co.charges if _d(c.filed) and on < _d(c.filed) <= end]
    old_keys = frozenset().union(*(c.lender_keys for c in repaid)) if repaid else frozenset()
    # a security trustee or agent replacing another, with no lender named on either side, is a change of
    # agent, not a new lender (4 Nov 2026 audit: about 1% of switches)
    old_agents = bool(repaid) and all(_agent_only(c) for c in repaid)
    switched = bool(repaid) and bool(old_keys) and any(
        c.lender_keys and not (c.lender_keys & old_keys) and not (old_agents and _agent_only(c)) for c in new)
    return {"switched": switched, "any": bool(repaid) and bool(new), "repaid": bool(repaid)}


def test_dates(companies, window: int = WINDOW, stop: date | None = None) -> list[date]:
    """Monthly from TEST_FROM, while the 90 days have passed and end before `stop` (when the radar began
    watching: every company in the data had a charge filing after that, so later windows would see it coming)."""
    last = max((_d(c.filed) for co in companies.values() for c in co.charges if _d(c.filed)), default=TEST_FROM)
    if stop:
        last = min(last, stop - timedelta(days=1))
    out, on = [], TEST_FROM
    while on + timedelta(days=window) <= last:
        out.append(on)
        on = date(on.year + (on.month == 12), on.month % 12 + 1, 1)
    return out


def build_rows(companies, dates, vol, window: int = WINDOW):
    out = []
    for co in companies.values():
        for on in dates:
            f = features(known_on(co, on), on, vol)
            if f:
                out.append((co.number, f, outcomes(co, on, window), on))
    return out


GROUPS = [
    ("Everyone", lambda f: True),
    ("Ordinary companies (10 loans or fewer)", lambda f: not f["_serial"]),
    ("Serial borrowers (11+ loans)", lambda f: f["_serial"]),
    ("Property", lambda f: f["property"] == "yes"),
    ("Not property", lambda f: f["property"] == "no"),
    ("Ordinary, not property", lambda f: not f["_serial"] and f["property"] == "no"),
]
TIERS = (None, 0.01, 0.05, 0.10, 0.25, "rest")   # "rest" = everyone outside the top 25%
OUTCOMES = ("switched", "any", "repaid")


def _interval(co_ids, ys, sel, rng, reps=REPS):
    """95% interval for the hit rate of the selected rows and its lift over the whole group,
    resampling companies with replacement."""
    import numpy as np
    per: dict = {}
    for c, y, s in zip(co_ids, ys, sel):
        a = per.setdefault(c, [0, 0, 0, 0])
        a[0] += 1; a[1] += y
        if s:
            a[2] += 1; a[3] += y
    agg = np.array(list(per.values()), dtype=np.int64)
    k = len(agg)
    gen = np.random.default_rng(rng.randrange(2 ** 32))
    sums, chunk = [], max(1, 2_000_000 // k)
    for i in range(0, reps, chunk):
        idx = gen.integers(0, k, size=(min(chunk, reps - i), k))
        sums.append(agg[idx].sum(axis=1))
    s = np.concatenate(sums)
    ok = s[:, 2] > 0
    rate = s[ok, 3] / s[ok, 2]
    lift = rate / np.where(s[ok, 1] > 0, s[ok, 1] / s[ok, 0], np.nan)
    q = lambda v: (float(np.nanquantile(v, 0.025)), float(np.nanquantile(v, 0.975))) if len(v) else (None, None)
    return q(rate), q(lift)


def monthly(rows, model, keep, tier: float = 0.10) -> dict:
    """Top `tier` ranked within each test date: how steady the switched-lender hit rate and lift are."""
    by: dict = {}
    for r in rows:
        if keep(r[1]):
            by.setdefault(r[3], []).append(r)
    months = []
    for on in sorted(by):
        part = sorted(by[on], key=lambda r: (-model.score(r[1]), r[0]))
        k = max(1, int(len(part) * tier))
        hits, base = sum(r[2]["switched"] for r in part[:k]), sum(r[2]["switched"] for r in part) / len(part)
        months.append({"date": on.isoformat(), "rows": len(part), "top": k, "hits": hits,
                       "rate": round(hits / k, 4), "lift": round(hits / k / base, 2) if base else None})
    lifts = sorted(m["lift"] for m in months if m["lift"] is not None)
    rates = sorted(m["rate"] for m in months)
    return {"months": months, "rate_min": rates[0] if rates else None, "rate_max": rates[-1] if rates else None,
            "lift_min": lifts[0] if lifts else None, "lift_median": lifts[len(lifts) // 2] if lifts else None,
            "lift_max": lifts[-1] if lifts else None, "months_below_1_5x": sum(l < 1.5 for l in lifts)}


def evaluate(companies, vol=None, window: int = WINDOW, reps: int = REPS, seed: int = 7,
             stop: date | None = None, learn_on=None) -> dict:
    """`learn_on`: companies to learn the points and odds from (default: the same companies). The random
    population sample (radar.population) learns on the radar's own data, as the live list does."""
    vol = vol or lender_volumes(learn_on or companies)
    train = [(f, o["any"]) for _, f, o, _ in build_rows(learn_on or companies, train_dates(window), vol, window)]
    model = Model(train, CLUES)
    dates = test_dates(companies, window, stop)
    rows = build_rows(companies, dates, vol, window)
    rng = random.Random(seed)
    everyone = {o: sum(r[2][o] for r in rows) / len(rows) for o in OUTCOMES} if rows else {}
    table = []
    for label, keep in GROUPS:
        part = [r for r in rows if keep(r[1])]
        if not part:
            continue
        tie = random.Random(seed)
        ranked = sorted(part, key=lambda r: (-model.score(r[1]), tie.random()))
        co_ids = [r[0] for r in ranked]
        for tier in TIERS:
            n = len(ranked)
            if tier is None:
                sel = [True] * n
            elif tier == "rest":
                k25 = max(1, int(n * 0.25))
                sel = [i >= k25 for i in range(n)]
            else:
                k = max(1, int(n * tier))
                sel = [i < k for i in range(n)]
            k = sum(sel)
            name = "All" if tier is None else "Rest (below top 25%)" if tier == "rest" else f"Top {int(tier * 100)}%"
            row = {"group": label, "tier": name,
                   "company_dates": k, "companies": len({c for c, s in zip(co_ids, sel) if s})}
            for o in OUTCOMES:
                ys = [int(r[2][o]) for r in ranked]
                hits = sum(y for y, s in zip(ys, sel) if s)
                base = sum(ys) / len(ys)
                rate = hits / k
                ci, lci = _interval(co_ids, ys, sel, rng, reps)
                row[o] = {"hits": hits, "rate": round(rate, 4), "ci": [round(x, 4) for x in ci],
                          "lift_group": round(rate / base, 2) if base else None,
                          "lift_group_ci": [round(x, 2) for x in lci] if tier is not None and lci[0] is not None else None,
                          "lift_everyone": round(rate / everyone[o], 2) if everyone.get(o) else None}
            table.append(row)
    stability = {label: monthly(rows, model, keep) for label, keep in GROUPS}
    return {"label": "BACKTEST", "model_version": MODEL_VERSION, "window_days": window, "monthly_top10": stability,
            "learned_on": [d.isoformat() for d in train_dates(window)], "test_dates": [d.isoformat() for d in dates],
            "rows": len(rows), "companies": len({r[0] for r in rows}), "definition": DEFINITION,
            "caveat": CAVEAT, "table": table}


def _pct(x):
    return "–" if x is None else f"{x:.1%}"


def _ci(c):
    return f"{c[0]:.1%} to {c[1]:.1%}" if c and c[0] is not None else "–"


def render_md(res: dict) -> str:
    out = [f"BACKTEST: Refinance watch ({res['model_version']}) accuracy at {res['window_days']} days", "",
           f"Learned on {len(res['learned_on'])} monthly dates {res['learned_on'][0]} to {res['learned_on'][-1]}; tested on {len(res['test_dates'])} monthly dates "
           f"{res['test_dates'][0]} to {res['test_dates'][-1]} ({res['rows']:,} company-dates, {res['companies']:,} companies).", "",
           f"_{res['definition']}_", "",
           "| Group | Rows | Company-dates | Companies | Switched lender | 95% range | Leads per switch | Lift in group | Lift range | Any refinance | 95% range | Repaid |",
           "|---|---|---:|---:|---:|---|---:|---:|---|---:|---|---:|"]
    for r in res["table"]:
        s, a, p = r["switched"], r["any"], r["repaid"]
        lr = s["lift_group_ci"]
        out.append(f"| {r['group']} | {r['tier']} | {r['company_dates']:,} | {r['companies']:,} | {_pct(s['rate'])} ({s['hits']:,}) | {_ci(s['ci'])} | {_per(s['rate'])} | "
                   f"{s['lift_group']}x | {f'{lr[0]}x to {lr[1]}x' if lr else '–'} | {_pct(a['rate'])} | {_ci(a['ci'])} | {_pct(p['rate'])} |")
    st = res.get("monthly_top10")
    if st:
        out += ["", "Month by month, top 10% ranked within each test date (switched lender):", "",
                "| Group | Hit rate, lowest to highest month | Lift, lowest / median / highest | Months under 1.5x |",
                "|---|---|---|---:|"]
        for g, m in st.items():
            if m["lift_min"] is not None:
                out.append(f"| {g} | {_pct(m['rate_min'])} to {_pct(m['rate_max'])} | {m['lift_min']}x / {m['lift_median']}x / "
                           f"{m['lift_max']}x | {m['months_below_1_5x']} of {len(m['months'])} |")
    out += ["", "_Leads per switch: how many firms from that level a lender would work through for one that "
            "switched lender within the window (1 / hit rate)._", "", f"_{res['caveat']}_"]
    return "\n".join(out) + "\n"


def _per(rate):
    return f"{1 / rate:.0f}" if rate else "–"


def write(res: dict, out_dir) -> tuple[Path, Path]:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    tag = f"{res.get('window_days', WINDOW)}d"
    j, m = out / f"accuracy_{tag}.json", out / f"accuracy_{tag}.md"
    j.write_text(json.dumps(res, indent=1), encoding="utf-8")
    m.write_text(render_md(res), encoding="utf-8")
    return j, m


def load(out_dir=None) -> dict | None:
    p = Path(out_dir or config.OUTPUT_DIR / "refi_watch") / "accuracy_90d.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    from .aliases import Resolver
    from .charge_history import load_companies
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.refi_accuracy")
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "refi_watch"))
    p.add_argument("--reps", type=int, default=REPS)
    p.add_argument("--window", type=int, default=WINDOW, help="days: 90 (default), 180 or 365")
    a = p.parse_args(argv)
    conn = connect()
    from .lender_view import coverage_start
    res = evaluate(load_companies(conn, Resolver(conn)), window=a.window, reps=a.reps,
                   stop=coverage_start(conn))
    print(render_md(res))
    print("->", *write(res, a.out))


if __name__ == "__main__":
    main()

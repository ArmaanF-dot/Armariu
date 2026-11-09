"""Do the odds shown beside each lead match what happened? (BACKTEST, plan task 3)

    python -m radar.refi_calibration [--window 365] [--out DIR]

On the random sample (radar/backfill.py), score every company on the test dates, split each group
(ordinary, serial) into ten equal bins of score, and compare the odds the model shows ("any refinance
within the window") with the share that did refinance. Two versions of the model are compared:

  radar    points and odds learned from the radar's own data (how the live list works today). That data
           is every company with a charge filing after 20 Oct 2026, so its odds read about 3x too high.
  sample   points and odds learned from the random sample's earlier months (before the test dates).

Learning always ends before the test months start. The error is the mean gap between shown odds and
actual rate across bins, weighted by bin size, and the same for the top bin on its own.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config
from .refi_accuracy import CLUES, Model, build_rows, lender_volumes, test_dates, train_dates


def _bins(rows, model, serial: bool, n: int = 10):
    part = [(model.score(f), model.odds(model.score(f), serial), int(o["any"])) for _, f, o, _ in rows
            if bool(f["_serial"]) == serial]
    part.sort(key=lambda t: t[0])
    k = max(1, len(part) // n)
    out = []
    for i in range(n):
        chunk = part[i * k:(i + 1) * k] if i < n - 1 else part[i * k:]
        if chunk:
            out.append({"bin": i + 1, "rows": len(chunk),
                        "shown": sum(c[1] for c in chunk) / len(chunk),
                        "actual": sum(c[2] for c in chunk) / len(chunk)})
    return out


def _err(bins):
    tot = sum(b["rows"] for b in bins)
    return {"mean_gap": sum(abs(b["shown"] - b["actual"]) * b["rows"] for b in bins) / tot,
            "top_bin_shown": bins[-1]["shown"], "top_bin_actual": bins[-1]["actual"],
            "top_bin_gap": bins[-1]["shown"] - bins[-1]["actual"]}


def run(companies, live_companies, window: int, stop=None) -> dict:
    dates = test_dates(companies, window, stop)
    vol_s = lender_volumes(companies)
    train_s = [(f, o["any"]) for _, f, o, _ in build_rows(companies, train_dates(window), vol_s, window)]
    versions = {"sample": (Model(train_s, CLUES), vol_s)}
    if live_companies:
        vol_r = lender_volumes(live_companies)
        train_r = [(f, o["any"]) for _, f, o, _ in build_rows(live_companies, train_dates(window), vol_r, window)]
        versions["radar"] = (Model(train_r, CLUES), vol_r)
    out = {"window_days": window, "test_dates": [d.isoformat() for d in dates], "versions": {}}
    for name, (model, vol) in versions.items():
        rows = build_rows(companies, dates, vol, window)
        res = {}
        for label, serial in (("ordinary", False), ("serial", True)):
            b = _bins(rows, model, serial)
            res[label] = {"bins": b, **_err(b)}
        out["versions"][name] = res
    return out


def render_md(res: dict) -> str:
    pct = lambda x: f"{x * 100:.1f}%"  # noqa: E731
    lines = [f"BACKTEST: shown odds against actual, any refinance within {res['window_days']} days, random sample", "",
             "| Model | Group | Mean gap, all bins | Top bin: shown | Top bin: actual |", "|---|---|---:|---:|---:|"]
    for name, r in res["versions"].items():
        for g in ("ordinary", "serial"):
            e = r[g]
            lines.append(f"| {name} | {g} | {pct(e['mean_gap'])} | {pct(e['top_bin_shown'])} | {pct(e['top_bin_actual'])} |")
    return "\n".join(lines)


def main(argv=None):
    from . import backfill
    from .aliases import Resolver
    from .charge_history import load_companies
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.refi_calibration")
    p.add_argument("--window", type=int, default=365)
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "refi_watch" / "calibration"))
    a = p.parse_args(argv)
    conn, bconn = connect(), backfill.connect()
    companies = backfill.load_companies(conn, bconn, Resolver(conn))
    if not companies:
        raise SystemExit("No sample fetched yet: run `python -m radar.dbsync pull --backfill`.")
    res = run(companies, load_companies(conn, Resolver(conn)), a.window, backfill.first_fetched(bconn))
    print(render_md(res))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / f"calibration_{a.window}d.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()

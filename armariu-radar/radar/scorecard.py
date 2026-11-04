"""Module D: hit-rate scorecard, starting with a BACKTEST.

    python -m radar.scorecard backtest [--months 24] [--window 90] [--out DIR]

Design guards (from the plan):
  * One outcome per signal type: a further charge, by any lender, filed within WINDOW days AFTER the
    signal date ("continued borrowing activity"). Insolvency and strike-off outcomes are not in the
    database yet, so they are not scored here.
  * Baseline: the same outcome for comparable charge events WITHOUT that signal, same sector and
    company age band. Lift = hit rate / baseline rate, where the baseline is the average of the
    matching cohort rates weighted by how many signals fall in each cohort.
  * No look-ahead: signals use filing dates only; an event counts only if its outcome window has
    closed before the cut-off (the day the radar started watching); the rule version is frozen.
  * Every rate carries a 95% interval that resamples companies, not charges (one company can file
    many charges, so a per-charge Wilson interval is too narrow). Small samples are shown, not hidden.
  * Backtest and live results are never mixed: this file labels everything BACKTEST. The live log
    (signals_published.csv) only starts after the validation gate.
Output is deterministic: the same database gives byte-identical files (only the run date is printed).

Known bias, printed in the output: history exists only for borrowers that filed a charge on the live
stream from 20 Oct 2026, so the sample is companies still lending-active. Signals and baseline share
that bias, which is why lift is more trustworthy than the raw hit rate.
"""
from __future__ import annotations

import argparse
import csv
import math
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import config
from .charge_history import Company, load_companies
from .charge_signals import RULE_VERSION, SIGNAL_TYPES, all_signals

BIAS_NOTE = ("Sample bias: history was fetched only for borrowers that filed a charge on the live stream from "
             "20 Oct 2026, so these are companies still active in lending. The signal group and its baseline share "
             "that bias; the raw hit rate overstates the market, the lift is the number to read. This is a backtest, "
             "not a live record.")
MIN_BASELINE_N = 30


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def clustered(per_company: dict, reps: int = 1000, seed: int = 7) -> tuple[float, float]:
    """95% interval for a hit rate, resampling companies with replacement. `per_company`: company ->
    [signals, hits]. Seeded, so a second run gives identical numbers. Falls back to Wilson when there are
    under 2 companies."""
    if len(per_company) < 2:
        n = sum(v[0] for v in per_company.values()); k = sum(v[1] for v in per_company.values())
        return wilson(k, n)
    import random   # plain Python: the pipeline does not install numpy
    a = [per_company[c] for c in sorted(per_company)]
    rng, k, rates = random.Random(seed), len(a), []
    for _ in range(reps):
        n = h = 0
        for i in rng.choices(range(k), k=k):
            n += a[i][0]; h += a[i][1]
        rates.append(h / n if n else 0.0)
    rates.sort()
    return rates[int(0.025 * (reps - 1))], rates[int(0.975 * (reps - 1))]


def _events(companies: dict[str, Company], start: date, cutoff: date, window: int):
    """Every charge filed in [start, cutoff - window]: (company, charge, hit?). Outcome windows are closed."""
    last = cutoff - timedelta(days=window)
    for co in companies.values():
        if not co.fetched:
            continue
        filed = [c.filed for c in co.charges if c.filed]
        for c in co.charges:
            if not c.filed or not (start.isoformat() <= c.filed <= last.isoformat()):
                continue
            end = (date.fromisoformat(c.filed) + timedelta(days=window)).isoformat()
            hi = bisect_right(filed, end)
            lo = bisect_right(filed, c.filed)
            # filings after this one's date, up to the window end (a charge filed the same day does not count)
            hit = hi > lo
            yield co, c, hit


def backtest(companies: dict[str, Company], cutoff: date, months: int = 24, window: int = 90,
             signals: list[dict] | None = None) -> list[dict]:
    start = cutoff - timedelta(days=int(months * 30.4375))
    signals = signals if signals is not None else all_signals(companies)
    sig_by_charge: dict[tuple, set] = defaultdict(set)
    for s in signals:
        if s["signal_date"] <= (cutoff - timedelta(days=window)).isoformat():
            sig_by_charge[(s["company_number"], s["charge_id"])].add(s["signal_type"])
    strata: dict[tuple, dict] = defaultdict(lambda: {"all": [0, 0]})
    per_sig: dict[str, list] = {t: [] for t in SIGNAL_TYPES}
    per_co: dict[str, dict] = {t: defaultdict(lambda: [0, 0]) for t in SIGNAL_TYPES}
    for co, c, hit in _events(companies, start, cutoff, window):
        stratum = (co.sector, co.age_band(c.filed))
        kinds = sig_by_charge.get((co.number, c.charge_id), set())
        st = strata[stratum]
        st["all"][0] += 1; st["all"][1] += int(hit)
        for t in SIGNAL_TYPES:
            st.setdefault(t, [0, 0])
            if t in kinds:
                st[t][0] += 1; st[t][1] += int(hit)
                per_sig[t].append(stratum)
                per_co[t][co.number][0] += 1; per_co[t][co.number][1] += int(hit)
    rows = []
    for t in SIGNAL_TYPES:
        n = len(per_sig[t])
        hits = 0
        by_stratum: dict[tuple, int] = defaultdict(int)
        for stratum in per_sig[t]:
            by_stratum[stratum] += 1
        exp = 0.0
        usable = 0
        for stratum, cnt in by_stratum.items():
            st = strata[stratum]
            hits += st[t][1]
            base_n = st["all"][0] - st[t][0]
            base_k = st["all"][1] - st[t][1]
            if base_n >= MIN_BASELINE_N:
                exp += cnt * base_k / base_n
                usable += cnt
        rate = hits / n if n else 0.0
        lo, hi = clustered(dict(per_co[t])) if n else (0.0, 1.0)
        base = (exp / usable) if usable else None
        rows.append({"label": "BACKTEST", "signal_type": t, "rule_version": RULE_VERSION,
                     "outcome": f"further charge filed within {window} days", "window_start": start.isoformat(),
                     "window_end": (cutoff - timedelta(days=window)).isoformat(), "signals": n, "outcomes_observed": hits,
                     "hit_rate": f"{rate:.4f}", "ci_low": f"{lo:.4f}", "ci_high": f"{hi:.4f}",
                     "ci_method": "companies resampled", "companies": len(per_co[t]),
                     "baseline_rate": f"{base:.4f}" if base is not None else "",
                     "baseline_coverage": f"{usable}/{n}",
                     "lift": f"{rate / base:.2f}" if base else "",
                     "small_sample": "yes" if n < 100 else ""})
    return rows


FIELDS = ("label", "signal_type", "rule_version", "outcome", "window_start", "window_end", "signals",
          "outcomes_observed", "hit_rate", "ci_low", "ci_high", "ci_method", "companies", "baseline_rate", "baseline_coverage", "lift",
          "small_sample")


def render_md(rows: list[dict], run_date: date) -> str:
    L = ["# Scorecard (BACKTEST)", "",
         f"_Run date {run_date.isoformat()}. Rule version {RULE_VERSION}. These are backtest results, not live results._", "",
         f"> {BIAS_NOTE}", "",
         "| Signal | Signals | Hits | Hit rate (95% CI) | Baseline | Lift | Note |", "|---|---:|---:|---|---:|---:|---|"]
    for r in rows:
        ci = f"{float(r['hit_rate']):.1%} ({float(r['ci_low']):.1%} to {float(r['ci_high']):.1%})"
        base = f"{float(r['baseline_rate']):.1%}" if r["baseline_rate"] else "n/a"
        L.append(f"| {r['signal_type']} | {r['signals']} | {r['outcomes_observed']} | {ci} | {base} | "
                 f"{r['lift'] or 'n/a'} | {'small sample' if r['small_sample'] else ''} |")
    L += ["", f"Outcome: a further charge, by any lender, filed within the window after the signal date. "
              f"Window of signals: {rows[0]['window_start']} to {rows[0]['window_end']}.", ""]
    return "\n".join(L)


def write(rows: list[dict], out_dir, run_date: date) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    c, m = out / "scorecard_backtest.csv", out / "scorecard_backtest.md"
    with open(c, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    m.write_text(render_md(rows, run_date), encoding="utf-8")
    return c, m


def scorecard_line(rows: list[dict] | None) -> str:
    """One line for the weekly pack. Always says BACKTEST until the live log exists."""
    if not rows:
        return "Scorecard: no backtest has been run yet."
    parts = []
    for r in rows:
        if r["lift"]:
            parts.append(f"{r['signal_type']}: {float(r['hit_rate']):.0%} vs {float(r['baseline_rate']):.0%} baseline "
                         f"(lift {r['lift']}, n={r['signals']})")
    return f"Scorecard (BACKTEST, rules {RULE_VERSION}): " + "; ".join(parts) + "."


def main(argv=None):
    from .db import connect
    from .lender_view import coverage_start
    p = argparse.ArgumentParser(prog="python -m radar.scorecard")
    p.add_argument("cmd", choices=("backtest",))
    p.add_argument("--months", type=int, default=24); p.add_argument("--window", type=int, default=90)
    p.add_argument("--cutoff", help="default: the day the radar started watching")
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "scorecard"))
    a = p.parse_args(argv)
    conn = connect()
    cut = date.fromisoformat(a.cutoff) if a.cutoff else (coverage_start(conn) or date.today())
    rows = backtest(load_companies(conn), cut, a.months, a.window)
    c, m = write(rows, a.out, date.today())
    for r in rows:
        print(r["signal_type"], r["signals"], r["hit_rate"], r["baseline_rate"], r["lift"])
    print("->", c)


if __name__ == "__main__":
    main()

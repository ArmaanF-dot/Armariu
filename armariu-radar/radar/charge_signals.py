"""Module B: interpret each new charge as a signal. Rules only, no AI.

    python -m radar.charge_signals [--start D --end D] [--out DIR]

Every row names its rule and RULE_VERSION so the scorecard stays honest when rules change. The
explanation states only facts from the filings (dates, lender names, statuses). It never claims an
amount, price or purpose. Dates are filing dates (`delivered_on`), so a signal is built only from
what was public on its signal date.

A "deal" is the set of charges created on the same day for the same lenders (a debenture plus share
charges, or one charge per property). Signals attach to the first charge of a deal, and a deal's own
charges never count as each other's "earlier charge".

v1 rules (the first three of the plan; club, all-assets and cluster wait until after the gate):
  first_facility  the company's first registered charge, or none in the prior 3 years
  refinancing     a charge satisfied for lender X within 90 days (either side) of a new charge for lender Y
  add_on          a new charge by a lender that already holds an outstanding charge on the company
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from pathlib import Path

from . import config
from .charge_history import Charge, Company, load_companies

RULE_VERSION = "v1"
FIRST_FACILITY_LOOKBACK_DAYS = 3 * 365
REFI_WINDOW_DAYS = 90
SIGNAL_TYPES = ("first_facility", "refinancing", "add_on")
FIELDS = ("signal_id", "signal_date", "signal_type", "rule_version", "company_number", "company_name",
          "sic_code", "sector", "charge_id", "charge_filed_on", "charge_created_on", "lender", "lender_names",
          "other_lender", "other_charge_id", "explanation", "filing_url")


def _d(s):
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _fmt(s) -> str:
    d = _d(s)
    return f"{d.day} {d.strftime('%b %Y')}" if d else "an unknown date"


def _who(names: list[str]) -> str:
    return " and ".join(names) if names else "an unnamed lender"


def _row(kind, co: Company, c: Charge, when: str, text: str, other: Charge | None = None) -> dict:
    sid = hashlib.sha1(f"{co.number}|{c.charge_id}|{kind}|{RULE_VERSION}".encode()).hexdigest()[:12]
    return {"signal_id": sid, "signal_date": when, "signal_type": kind, "rule_version": RULE_VERSION,
            "company_number": co.number, "company_name": co.name, "sic_code": co.sic_main, "sector": co.sector,
            "charge_id": c.charge_id, "charge_filed_on": c.filed or "", "charge_created_on": c.created or "",
            "lender": c.lender_names[0] if c.lender_names else "", "lender_names": "; ".join(c.lender_names),
            "other_lender": _who(other.lender_names) if other else "", "other_charge_id": other.charge_id if other else "",
            "explanation": text, "filing_url": c.url}


def for_company(co: Company) -> list[dict]:
    """Every v1 signal for one borrower, over its whole known history. Deterministic."""
    out = []
    cs = [c for c in co.charges if c.filed]
    sat_sorted = sorted(((o.satisfied[:10], o) for o in cs if _d(o.satisfied)), key=lambda t: (t[0], t[1].charge_id))
    sat_dates = [t[0] for t in sat_sorted]
    deals_seen: set = set()
    for i, c in enumerate(cs):
        d = _d(c.filed)
        if not d or not c.lender_keys:
            continue
        # Charges created the same day for the same lenders are one deal (a portfolio of properties,
        # a debenture plus share charges): only the first one filed gets signals.
        deal = (c.created or c.filed, c.lender_keys)
        if deal in deals_seen:
            continue
        deals_seen.add(deal)
        same_deal = lambda o: (o.created or o.filed) == deal[0] and bool(o.lender_keys & c.lender_keys)  # noqa: E731
        earlier = [o for o in cs[:i] if _d(o.filed) and _d(o.filed) < d and not same_deal(o)]
        who = _who(c.lender_names)

        # first facility: needs the full history, otherwise 'no earlier charge' proves nothing
        if co.fetched:
            recent = [o for o in earlier if (d - _d(o.filed)).days <= FIRST_FACILITY_LOOKBACK_DAYS]
            same_day_other = [o for o in cs if o.filed == c.filed and o.charge_id != c.charge_id and not same_deal(o)]
            if not recent and not same_day_other:
                prior = (f"The company's last earlier charge was filed {_fmt(earlier[-1].filed)}."
                         if earlier else "No earlier charge is on record.")
                out.append(_row("first_facility", co, c, c.filed,
                    f"{who} registered a charge, filed {_fmt(c.filed)}. {prior} "
                    "The company shows no registered charge in the three years before it.", None))

        # refinancing: a charge for a different lender was satisfied within 90 days of this one.
        # One signal per new charge, citing the satisfaction closest in time (ties: earlier filing).
        lo = bisect_left(sat_dates, (d - timedelta(days=REFI_WINDOW_DAYS)).isoformat())
        hi = bisect_right(sat_dates, (d + timedelta(days=REFI_WINDOW_DAYS)).isoformat())
        best = None
        for _, o in sat_sorted[lo:hi]:
            if o.charge_id == c.charge_id or o.lender_keys & c.lender_keys or not o.lender_keys or same_deal(o):
                continue
            gap = abs((_d(o.satisfied) - d).days)
            if best is None or (gap, o.filed or "", o.charge_id) < best[0]:
                best = ((gap, o.filed or "", o.charge_id), o)
        if best:
            o = best[1]
            s = _d(o.satisfied)
            out.append(_row("refinancing", co, c, max(d, s).isoformat(),   # public only once both facts are
                f"{_who(o.lender_names)}'s charge (filed {_fmt(o.filed)}) was marked satisfied on {_fmt(o.satisfied)}, "
                f"and {who} registered a charge filed {_fmt(c.filed)}, {best[0][0]} days "
                f"{'after' if d >= s else 'before'} it. Filings alone do not prove the new charge replaced the old one.", o))

        # add-on: same lender, earlier charge still outstanding when this one was filed
        for o in reversed(earlier):   # strictly earlier filing, and not part of the same deal
            if not (o.lender_keys & c.lender_keys):
                continue
            s = _d(o.satisfied)
            if o.status == "outstanding" or not s or s > d:
                out.append(_row("add_on", co, c, c.filed,
                    f"{who} registered a charge filed {_fmt(c.filed)} while its earlier charge (filed {_fmt(o.filed)}) "
                    "was still outstanding on the register.", o))
                break   # one add-on per charge, citing the most recent earlier charge
    out.sort(key=lambda r: (r["signal_date"], r["company_number"], r["charge_id"], r["signal_type"]))
    return out


def all_signals(companies: dict[str, Company]) -> list[dict]:
    out = []
    for num in sorted(companies):
        out += for_company(companies[num])
    out.sort(key=lambda r: (r["signal_date"], r["company_number"], r["charge_id"], r["signal_type"]))
    return out


def in_window(signals: list[dict], start: date, end: date) -> list[dict]:
    return [s for s in signals if start.isoformat() <= s["signal_date"] <= end.isoformat()]


def write_csv(rows: list[dict], path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.charge_signals")
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "signals"))
    p.add_argument("--sample", type=int, default=0, help="also write a random-free sample of N signals for hand checking")
    a = p.parse_args(argv)
    end = date.fromisoformat(a.end) if a.end else date.today()
    start = date.fromisoformat(a.start) if a.start else end - timedelta(days=6)
    conn = connect()
    sigs = in_window(all_signals(load_companies(conn)), start, end)
    path = write_csv(sigs, Path(a.out) / f"signals_{end.isoformat()}.csv")
    by = {t: sum(1 for s in sigs if s["signal_type"] == t) for t in SIGNAL_TYPES}
    print(f"{len(sigs)} signals {start} to {end} ({RULE_VERSION}): {by} -> {path}")
    if a.sample:
        step = max(1, len(sigs) // a.sample)
        pick = sigs[::step][:a.sample]
        write_csv(pick, Path(a.out) / f"handcheck_{end.isoformat()}.csv")
        print(f"hand-check sample of {len(pick)} written")


if __name__ == "__main__":
    main()

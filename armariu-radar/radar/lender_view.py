"""Module C: a lender-side weekly view. Who is lending, to whom, and where.

    python -m radar.lender_view [--week-start YYYY-MM-DD] [--out DIR]

Counts are charges REGISTERED (filing date), not deals. A charge can be registered after it was
created, so recent weeks can be revised upward. Every table prints its date window and this caveat.
A charge naming several lenders counts once for each lender; the raw charge total is printed beside
the league so the two can be reconciled.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import config
from .charge_history import Company, load_companies
from .charge_signals import all_signals

LOW_N = 5
CAVEAT = ("Counts are charges registered with Companies House, not deals. A charge can be registered after it "
          "was created, so recent weeks can be revised upward. Check the current filing deadline before quoting one.")
SAMPLE_CAVEAT = ("Borrower sample: the radar follows the live charge stream from 20 Oct 2026 and fetches each of those "
                 "borrowers' history. Weeks before that cover only borrowers that later filed a charge, not the whole market.")


@dataclass
class View:
    start: date
    end: date
    complete: bool            # radar was watching for the whole week
    raw_charges: int
    league: list[dict]
    sectors: list[dict]
    new_entrants: list[dict]
    flows: list[dict]
    syndication: list[dict]
    reconciliation: dict


def coverage_start(conn) -> date | None:
    """When the radar started watching: the stored marker, else the first charge it saw."""
    from .db import get_meta
    began = get_meta(conn, "coverage_start") or conn.execute("SELECT MIN(first_seen_at) FROM charges").fetchone()[0]
    try:
        return date.fromisoformat(str(began)[:10]) if began else None
    except ValueError:
        return None


def display_name(name: str, cls: str) -> str:
    """A bank and its security-trustee arm are different rows; say which one this is."""
    return f"{name} (security trustee)" if cls == "trustee_agent" else name


def row_key(key: str, cls: str) -> str:
    return key + "|AGENT" if cls == "trustee_agent" else key


def week_of(day: date) -> tuple[date, date]:
    mon = day - timedelta(days=day.weekday())
    return mon, mon + timedelta(days=6)


def _count(companies: dict[str, Company], start: date, end: date):
    """lender_key -> {name, cls, charges:set(...)}, plus raw charge count, for filings in the window."""
    s, e = start.isoformat(), end.isoformat()
    per: dict[str, dict] = {}
    raw = set()
    for co in companies.values():
        for c in co.charges:
            if not c.filed or not (s <= c.filed <= e):
                continue
            raw.add((c.company_number, c.charge_id))
            for name, key, cls in c.lenders:
                k = row_key(key, cls)
                d = per.setdefault(k, {"lender": display_name(name, cls), "cls": cls, "n": set(),
                                       "sectors": Counter(), "classes": Counter()})
                d["n"].add((c.company_number, c.charge_id))
                d["sectors"][co.sector] += 1
                d["classes"][cls] += 1
    for d in per.values():   # one lender, one type: the most common one (ties alphabetical)
        d["cls"] = sorted(d["classes"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return per, raw


def build(companies: dict[str, Company], week_start: date, coverage_start: date | None = None,
          signals: list[dict] | None = None, include_property: bool = True) -> View:
    ws, we = week_start, week_start + timedelta(days=6)
    w4s, w13s = we - timedelta(days=27), we - timedelta(days=90)
    now, raw = _count(companies, ws, we)
    t4, _ = _count(companies, w4s, we)
    t13, _ = _count(companies, w13s, we)
    cls_filter = (lambda d: True) if include_property else (lambda d: d["cls"] != "property")
    league = []
    for key, d in now.items():
        if not cls_filter(d):
            continue
        n = len(d["n"])
        league.append({"lender": d["lender"], "class": d["cls"], "this_week": n,
                       "trailing_4w": len(t4.get(key, {}).get("n", ())),
                       "trailing_13w": len(t13.get(key, {}).get("n", ())),
                       "low_observations": "yes" if len(t13.get(key, {}).get("n", ())) < LOW_N else ""})
    league.sort(key=lambda r: (-r["this_week"], -r["trailing_4w"], r["lender"]))

    sec = Counter()
    for key, d in now.items():
        if cls_filter(d):
            for s_, n in d["sectors"].items():
                sec[s_] += n
    sector_rows = [{"sector": k, "lender_charges": v} for k, v in sorted(sec.items(), key=lambda kv: (-kv[1], kv[0]))]

    # New entrants: a lender seen this week and in none of the 12 months before it (among observed borrowers).
    prior_start = ws - timedelta(days=365)
    prior, _ = _count(companies, prior_start, ws - timedelta(days=1))
    entrants = [{"lender": d["lender"], "class": d["cls"], "this_week": len(d["n"])}
                for k, d in now.items() if k not in prior and cls_filter(d) and d["cls"] != "trustee_agent"]
    entrants.sort(key=lambda r: (-r["this_week"], r["lender"]))

    # Refinancing flows X -> Y from Module B, trailing 13 weeks (a single week is too thin).
    signals = signals if signals is not None else all_signals(companies)
    flows = Counter()
    for sg in signals:
        if sg["signal_type"] == "refinancing" and w13s.isoformat() <= sg["signal_date"] <= we.isoformat():
            if sg["other_lender"] and sg["lender"] and sg["other_lender"] != sg["lender"]:
                flows[(sg["other_lender"], sg["lender"])] += 1
    flow_rows = [{"from_lender": a, "to_lender": b, "signals_13w": n}
                 for (a, b), n in sorted(flows.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]

    # Syndication proxy: trustee and agent charges per week, last 13 weeks.
    synd = []
    for i in range(12, -1, -1):
        a = ws - timedelta(weeks=i)
        per, raw_w = _count(companies, a, a + timedelta(days=6))
        agent = {p for d in per.values() if d["cls"] == "trustee_agent" for p in d["n"]}
        multi = {(c.company_number, c.charge_id) for co in companies.values() for c in co.charges
                 if c.filed and a.isoformat() <= c.filed <= (a + timedelta(days=6)).isoformat()
                 and len({k for _, k, cl in c.lenders if cl != "trustee_agent"}) > 1}
        synd.append({"week_start": a.isoformat(), "agent_charges": len(agent), "multi_lender_charges": len(multi),
                     "all_charges": len(raw_w)})

    # Reconciliation: every raw charge is counted once per lender it names, none are lost.
    named = {p for d in now.values() for p in d["n"]}
    credits = sum(len(d["n"]) for d in now.values())
    rec = {"raw_charges": len(raw), "charges_with_a_lender": len(named), "charges_without_lender": len(raw) - len(named),
           "lender_credits": credits, "ok": len(named) <= len(raw) and credits >= len(named)}
    complete = coverage_start is not None and coverage_start <= ws
    return View(ws, we, complete, len(raw), league, sector_rows, entrants, flow_rows, synd, rec)


def render_md(v: View, top: int = 15) -> str:
    L = [f"## Lender view: {v.start.isoformat()} to {v.end.isoformat()}", ""]
    if not v.complete:
        L += [f"> **Partial window.** {SAMPLE_CAVEAT}", ""]
    L += [f"### League table: new charges per lender", f"_Window: {v.start} to {v.end}; trailing counts end {v.end}. "
          f"Raw charges in window: {v.raw_charges}. {CAVEAT}_", "",
          "| Lender | Type | This week | Trailing 4w | Trailing 13w | Note |", "|---|---|---:|---:|---:|---|"]
    for r in v.league[:top]:
        L.append(f"| {r['lender']} | {r['class']} | {r['this_week']} | {r['trailing_4w']} | {r['trailing_13w']} | "
                 f"{'few observations' if r['low_observations'] else ''} |")
    L += ["", "### Sector cut", f"_Window: {v.start} to {v.end}. Lender-charge credits by borrower sector. {CAVEAT}_", "",
          "| Sector | Lender charges |", "|---|---:|"]
    L += [f"| {r['sector']} | {r['lender_charges']} |" for r in v.sectors[:10]]
    L += ["", "### New entrants", f"_Lenders seen this week and in none of the prior 12 months among observed borrowers. "
          f"Window: {v.start} to {v.end}. {CAVEAT}_", ""]
    L += [f"- {r['lender']} ({r['class']}): {r['this_week']} this week" for r in v.new_entrants[:10]] or ["- None."]
    L += ["", "### Refinancing flows", f"_Top lender X to lender Y pairs from Module B refinancing signals, 13 weeks to {v.end}. "
          "A satisfaction near a new charge is a pattern, not proof of a refinancing._", ""]
    L += [f"- {r['from_lender']} to {r['to_lender']}: {r['signals_13w']}" for r in v.flows] or ["- None."]
    L += ["", "### Syndication proxy", f"_Charges naming a security trustee or agent, and charges with several lenders, per week. {CAVEAT}_", "",
          "| Week starting | Agent / trustee charges | Multi-lender charges | All charges |", "|---|---:|---:|---:|"]
    L += [f"| {r['week_start']} | {r['agent_charges']} | {r['multi_lender_charges']} | {r['all_charges']} |" for r in v.syndication]
    rc = v.reconciliation
    L += ["", f"_Reconciliation: {rc['raw_charges']} raw charges; {rc['charges_with_a_lender']} name a lender, "
              f"{rc['charges_without_lender']} do not; {rc['lender_credits']} lender credits in the league._", ""]
    return "\n".join(L)


def write_csv(v: View, out_dir) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"lender_view_{v.start.isoformat()}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=("window_start", "window_end", "lender", "class", "this_week",
                                           "trailing_4w", "trailing_13w", "low_observations"))
        w.writeheader()
        for r in v.league:
            w.writerow({"window_start": v.start, "window_end": v.end, **r})
    return path


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.lender_view")
    p.add_argument("--week-start"); p.add_argument("--out", default=str(config.OUTPUT_DIR / "lender_view"))
    a = p.parse_args(argv)
    conn = connect()
    today = date.today()
    ws = date.fromisoformat(a.week_start) if a.week_start else week_of(today - timedelta(days=7))[0]
    v = build(load_companies(conn), ws, coverage_start(conn))
    path = write_csv(v, a.out)
    (Path(a.out) / f"lender_view_{ws.isoformat()}.md").write_text(render_md(v), encoding="utf-8")
    print(f"League of {len(v.league)} lenders for {v.start} to {v.end} -> {path}")


if __name__ == "__main__":
    main()

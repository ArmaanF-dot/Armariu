"""One JSON snapshot of the additions (Modules A to D) for the review app and the customer portal.

    python -m radar.snapshot            # writes output/snapshot/snapshot.json

Building it takes about a minute on the full database, so both apps read the cached file and the
review app rebuilds it on request. The `public` part never contains an individual's name: individual
chargees are masked when charges are loaded (radar/charge_history.py).
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import config
from .aliases import Resolver, coverage, review_rows
from .charge_history import load_companies
from .charge_signals import RULE_VERSION, all_signals, in_window
from .lender_view import CAVEAT, SAMPLE_CAVEAT, build, display_name, row_key, week_of
from .scorecard import BIAS_NOTE, backtest
from . import refi_accuracy, refi_watch
from .insights import build_insights
from .weekly_pack import NOT_ADVICE

PATH = config.OUTPUT_DIR / "snapshot" / "snapshot.json"
CLASS_LABELS = {"bank": "Bank", "direct_lender": "Private credit", "abl_invoice": "ABL / invoice",
                "asset_finance": "Asset finance", "trustee_agent": "Trustee / agent",
                "government_backed": "Government-backed", "property": "Property lender",
                "individual": "Private individual", "other": "Other"}
SIGNAL_LABELS = {"first_facility": "First facility", "refinancing": "Refinancing", "add_on": "Add-on"}


def build_snapshot(conn, today: date | None = None, weeks: int = 13) -> dict:
    from .lender_view import coverage_start
    today = today or date.today()
    resolver = Resolver(conn)
    companies = load_companies(conn, resolver)
    sigs = all_signals(companies)
    cov_d = coverage_start(conn) or today
    last_week = week_of(today - timedelta(days=7))[0]
    views = []
    for i in range(min(weeks, 4) - 1, -1, -1):
        ws = last_week - timedelta(weeks=i)
        if ws + timedelta(days=6) < cov_d:
            continue
        v = build(companies, ws, cov_d, sigs)
        views.append({"start": v.start.isoformat(), "end": v.end.isoformat(), "complete": v.complete,
                      "raw_charges": v.raw_charges, "league": v.league[:60], "sectors": v.sectors,
                      "new_entrants": v.new_entrants[:20], "flows": v.flows, "syndication": v.syndication,
                      "reconciliation": v.reconciliation})
    # Weekly series by lender class (13 weeks), for the trend chart.
    series = []
    for i in range(weeks - 1, -1, -1):
        ws = last_week - timedelta(weeks=i)
        cnt = Counter()
        s_, e_ = ws.isoformat(), (ws + timedelta(days=6)).isoformat()
        for co in companies.values():
            for c in co.charges:
                if c.filed and s_ <= c.filed <= e_:
                    cls = sorted({cl for _, _, cl in c.lenders}, key=lambda x: (x == "trustee_agent", x))
                    cnt[cls[0] if cls else "other"] += 1
        series.append({"week_start": s_, "observed_only": ws < cov_d, **cnt})
    # Lender profiles for the portal (top 120 by 13-week activity).
    since = (last_week - timedelta(weeks=12)).isoformat()
    prof: dict[str, dict] = {}
    for co in companies.values():
        for c in co.charges:
            if not c.filed or c.filed < since:
                continue
            for name, key, cls in c.lenders:
                if cls == "individual":
                    continue
                k = row_key(key, cls)
                p = prof.setdefault(k, {"lender": display_name(name, cls), "class": cls, "charges": 0, "sectors": Counter(),
                                        "weeks": Counter(), "recent": [], "classes": Counter()})
                p["classes"][cls] += 1
                p["charges"] += 1
                p["sectors"][co.sector] += 1
                p["weeks"][str(week_of(date.fromisoformat(c.filed))[0])] += 1
                p["recent"].append((c.filed, co.name, co.number, co.sector, c.url))
    lenders = []
    for k, p in sorted(prof.items(), key=lambda kv: (-kv[1]["charges"], kv[0]))[:150]:
        p["class"] = sorted(p["classes"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        p["recent"].sort(reverse=True)
        lenders.append({"id": k, "lender": p["lender"], "class": p["class"], "charges_13w": p["charges"],
                        "sectors": p["sectors"].most_common(6),
                        "weeks": [p["weeks"].get((last_week - timedelta(weeks=i)).isoformat(), 0) for i in range(weeks - 1, -1, -1)],
                        "recent": [{"date": d, "company": n, "number": num, "sector": s, "url": u} for d, n, num, s, u in p["recent"][:12]]})
    recent_sigs = [s for s in in_window(sigs, last_week - timedelta(weeks=3), today)
                   if "private individual" not in (s["lender_names"] + s["other_lender"]).lower()]
    recent_sigs.sort(key=lambda s: (s["signal_date"], s["company_number"]), reverse=True)
    bt = backtest(companies, cov_d, 24, 90, sigs)
    cv = coverage(conn, resolver, (today - timedelta(days=365)).isoformat())
    refi_rows, refi_model = refi_watch.watchlist(companies, today, top=100000)
    return {
        "built": today.isoformat(), "rule_version": RULE_VERSION, "coverage_start": cov_d.isoformat(),
        "caveat": CAVEAT, "sample_caveat": SAMPLE_CAVEAT, "bias_note": BIAS_NOTE, "not_advice": NOT_ADVICE,
        "class_labels": CLASS_LABELS, "signal_labels": SIGNAL_LABELS,
        "totals": {"companies": len(companies), "charges": sum(len(c.charges) for c in companies.values()),
                   "signals_recent": len(recent_sigs)},
        "weeks": views, "series": series, "lenders": lenders,
        "signals": [{k: s[k] for k in ("signal_id", "signal_date", "signal_type", "company_number", "company_name",
                                        "sector", "lender_names", "other_lender", "explanation", "filing_url")}
                    for s in recent_sigs[:1500]],
        "scorecard": bt,
        "refi_watch": _refi(companies, today, refi_rows, refi_model, cov_d),
        "insights": build_insights(companies, sigs, refi_rows, today, cov_d, weeks),
        "aliases": {"coverage": cv, "review": review_rows(conn, resolver, (today - timedelta(days=365)).isoformat(), 20)},
    }


def _refi(companies, today: date, rows=None, model=None, cov_d: date | None = None) -> dict:
    if rows is None:
        rows, model = refi_watch.watchlist(companies, today, top=100000)
    prop = "Property and real estate"   # keep a full list for each view, since property dominates the top
    rows = [r for r in rows if r["sector"] == prop][:300] + [r for r in rows if r["sector"] != prop][:300]
    rows.sort(key=lambda r: (-r["score"], r["company_number"]))
    return {"caveat": refi_watch.CAVEAT, "model_version": refi_watch.MODEL_VERSION, "window_days": refi_watch.WINDOW,
            "base_rate": round(model.base, 4), "backtest": refi_watch.backtest(companies), "rows": rows,
            # 90 days, by group, with counts and 95% ranges (radar.refi_accuracy; docs/refi_accuracy_90d.md)
            "accuracy_90d": refi_accuracy.evaluate(companies, reps=500, stop=cov_d)}


def write(snap: dict, path=None) -> Path:
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, default=str, separators=(",", ":")), encoding="utf-8")
    return path


def load(path=None) -> dict | None:
    path = Path(path or PATH)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.snapshot")
    p.add_argument("--today", help="pretend today is this date (for reproducible output)")
    a = p.parse_args(argv)
    snap = build_snapshot(connect(), date.fromisoformat(a.today) if a.today else None)
    print("Wrote", write(snap))


if __name__ == "__main__":
    main()

"""Weekly digest: categorised charges for a date window, written as CSV + Markdown.

    python -m radar.export                     # last complete Mon-Sun week + current week-to-date
    python -m radar.export --start 2026-10-12 --end 2026-10-18

The window is on delivered_on: the date the filing reached Companies House.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

from . import config
from .db import connect

TIERS = (
    ("A", "Direct lender / private credit"),
    ("B", "Behind a security agent or trustee (lender may be hidden)"),
    ("C", "Specialist and alternative lenders (ABL, asset finance, public sector)"),
    ("D", "Unclassified or needs review"),
    ("E", "Banks and individuals (counted, not listed)"),
)
_STATUS_RANK = {"needs_review": 0, "unclassified": 0, "auto_ok": 1, "ok": 2}

CSV_FIELDS = (
    "tier", "delivered_on", "created_on", "company_number", "company_name", "chargees",
    "categories", "parent_firms", "classified_by", "min_confidence", "review_status",
    "needs_human_check",
    "floating_covers_all", "negative_pledge", "charge_type", "secured", "status", "filing_url",
)


def week_bounds(today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    return (last_monday, this_monday - timedelta(days=1)), (this_monday, today)


def _primary(categories: list[str]) -> str:
    for cat in config.CATEGORY_PRIORITY:
        if cat in categories:
            return cat
    return "unknown"


def _tier(primary: str, worst_status: str, categories: list[str]) -> str:
    if worst_status in ("needs_review", "unclassified") and primary not in ("bank", "individual"):
        return "D"
    if primary == "direct_lender":
        return "A"
    if primary == "security_agent":
        return "B"
    if primary in ("abl_specialist", "asset_finance", "public_sector", "other"):
        return "C"
    if primary in ("bank", "individual"):
        return "E"
    return "D"


def collect(conn, start: date, end: date, date_field: str = "delivered_on") -> list[dict]:
    assert date_field in ("delivered_on", "satisfied_on")
    charges = conn.execute(
        f"SELECT c.*, co.name AS company_name FROM charges c "
        f"LEFT JOIN companies co ON co.company_number = c.company_number "
        f"WHERE c.deleted = 0 AND c.{date_field} BETWEEN ? AND ? "
        f"ORDER BY c.{date_field}, c.company_number",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    rows = []
    for c in charges:
        persons = conn.execute(
            "SELECT p.name_raw, p.name_norm, lc.category, lc.parent_firm, lc.confidence, "
            "lc.review_status, lc.source FROM charge_persons p "
            "LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm "
            "WHERE p.company_number = ? AND p.charge_id = ? ORDER BY p.name_raw",
            (c["company_number"], c["charge_id"]),
        ).fetchall()
        cats = [p["category"] or "unknown" for p in persons] or ["unknown"]
        statuses = [p["review_status"] or "unclassified" for p in persons] or ["unclassified"]
        worst = min(statuses, key=lambda s: _STATUS_RANK.get(s, 0))
        primary = _primary(cats)
        confs = [p["confidence"] for p in persons if p["confidence"] is not None]
        rows.append({
            "tier": _tier(primary, worst, cats),
            "delivered_on": c["delivered_on"],
            "created_on": c["created_on"],
            "satisfied_on": c["satisfied_on"],
            "company_number": c["company_number"],
            "company_name": c["company_name"] or "(name not fetched yet)",
            "chargees": "; ".join(p["name_raw"] for p in persons),
            "categories": "; ".join(sorted(set(cats))),
            "primary_category": primary,
            "parent_firms": "; ".join(sorted({p["parent_firm"] for p in persons if p["parent_firm"]})),
            "classified_by": "; ".join(sorted({p["source"] or "none" for p in persons})) or "none",
            "min_confidence": f"{min(confs):.2f}" if confs else "",
            "review_status": worst,
            "needs_human_check": "yes" if worst != "ok" else "no",
            "floating_covers_all": {1: "yes", 0: "no"}.get(c["floating_covers_all"], ""),
            "negative_pledge": {1: "yes", 0: "no"}.get(c["contains_negative_pledge"], ""),
            "charge_type": (c["classification_text"] or "")[:120],
            "secured": (c["secured_text"] or "")[:120],
            "status": c["status"],
            "filing_url": config.FILING_URL.format(company=c["company_number"],
                                                   charge_id=c["charge_id"]),
        })
    return rows


def _md_table(rows: list[dict], cols: list[tuple[str, str]], limit: int = 50) -> str:
    if not rows:
        return "_None this period._\n"
    out = ["| " + " | ".join(h for h, _ in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:limit]:
        cells = []
        for _, key in cols:
            val = str(r.get(key, "") or "").replace("|", "/").replace("\n", " ")
            cells.append(val)
        out.append("| " + " | ".join(cells) + " |")
    if len(rows) > limit:
        out.append(f"\n_...and {len(rows) - limit} more in the CSV._")
    return "\n".join(out) + "\n"


def write_digest(conn, start: date, end: date, out_dir=None, partial: bool = False) -> Path:
    out_dir = Path(out_dir or (config.OUTPUT_DIR / "weekly"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{start.isoformat()}_{end.isoformat()}" + ("_partial" if partial else "")
    rows = collect(conn, start, end)
    sats = [r for r in collect(conn, start, end, "satisfied_on")
            if r["primary_category"] in ("direct_lender", "security_agent")]

    with open(out_dir / f"{stem}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["tier"], r["delivered_on"] or "")):
            w.writerow(r)

    by_tier = {t: [r for r in rows if r["tier"] == t] for t, _ in TIERS}
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["primary_category"]] = by_cat.get(r["primary_category"], 0) + 1
    cols = [("Delivered", "delivered_on"), ("Borrower", "company_name"), ("No.", "company_number"),
            ("Chargee(s)", "chargees"), ("Parent", "parent_firms"), ("All-assets", "floating_covers_all"),
            ("Check?", "needs_human_check"), ("Filing", "filing_url")]
    md = [
        f"# Deal radar digest: {start.isoformat()} to {end.isoformat()}"
        + (" (week to date)" if partial else ""),
        "",
        "_From Companies House charge filings delivered in this window. "
        "A charge shows that security was granted; it does not show the amount, pricing or terms. "
        "Rows marked Check? = yes have not been confirmed by a person._",
        "",
        "## Summary",
        "",
        f"- Charges delivered: **{len(rows)}**",
        *(f"- Tier {t} ({label}): **{len(by_tier[t])}**" for t, label in TIERS),
        "- By primary lender category: "
        + (", ".join(f"{k} {v}" for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])) or "none"),
        f"- Satisfactions of direct-lender / agent charges (possible repayment or refinancing): "
        f"**{len(sats)}**",
        "",
    ]
    for t, label in TIERS[:3]:
        md += [f"## Tier {t}: {label}", "", _md_table(by_tier[t], cols)]
    md += ["## Satisfied charges (direct lender or agent)", "",
           _md_table(sats, [("Satisfied", "satisfied_on"), ("Borrower", "company_name"),
                            ("No.", "company_number"), ("Chargee(s)", "chargees"),
                            ("Filing", "filing_url")]),
           "## Tier D: needs review", "",
           f"{len(by_tier['D'])} charges have a chargee that is unclassified or flagged. "
           "Run `python -m radar.review list` to work through them.", ""]
    (out_dir / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(json.dumps({
        "start": start.isoformat(), "end": end.isoformat(), "partial": partial,
        "counts": {t: len(by_tier[t]) for t, _ in TIERS},
        "total": len(rows), "satisfactions": len(sats),
    }, indent=2), encoding="utf-8")
    return out_dir / f"{stem}.md"


def write_standard(conn, today: date | None = None, out_dir=None) -> list[Path]:
    """Last complete week (final) + this week so far (partial, overwritten each run)."""
    today = today or date.today()
    (ls, le), (ts, te) = week_bounds(today)
    out_dir = Path(out_dir or (config.OUTPUT_DIR / "weekly"))
    for old in out_dir.glob(f"{ts.isoformat()}_*_partial.*"):
        old.unlink()
    paths = [write_digest(conn, ls, le, out_dir)]
    if te >= ts:
        paths.append(write_digest(conn, ts, te, out_dir, partial=True))
    return paths


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.export")
    p.add_argument("--start"); p.add_argument("--end")
    args = p.parse_args(argv)
    conn = connect()
    if args.start and args.end:
        print(write_digest(conn, date.fromisoformat(args.start), date.fromisoformat(args.end)))
    else:
        for path in write_standard(conn):
            print(path)


if __name__ == "__main__":
    main()

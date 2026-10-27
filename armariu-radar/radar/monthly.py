"""Monthly extract: every new charge (MR01) and satisfaction (MR04) in a window, with lenders
normalised and grouped, as a clean CSV plus a short data-quality note.

    python -m radar.monthly                       # last complete calendar month
    python -m radar.monthly --month 2026-09
    python -m radar.monthly --start 2026-10-19 --end 2026-10-26 --label interim

Writes output/monthly/<label>/:
    filings.csv         one row per filing (MR01 = charge registered, MR04 = charge satisfied)
    lenders_long.csv    one row per filing x lender (base for league tables)
    data_quality.md     what to trust and what not to
    meta.json           the same counts, machine-readable

Rules only (data/lenders.csv and data/lender_groups.csv). No AI is called. The window is on the
filing date: delivered_on for MR01, satisfied_on for MR04.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, sectors
from .db import connect, get_meta
from .export import coverage
from .lender_groups import GROUP_LABELS, GROUPS, group_lender, load_group_rules
from .rules import find_rule  # noqa: F401  (re-exported for callers that want rule lookups)

FILING_FIELDS = (
    "filing_type", "filing_date", "created_on", "company_number", "company_name", "sector",
    "lender", "lender_group", "lender_is_agent", "lender_detail_category", "group_basis",
    "lender_raw", "all_chargees", "n_chargees", "classification_source", "needs_check",
    "charge_type", "secured_assets", "asset_tags", "all_assets_floating", "has_fixed",
    "has_floating", "negative_pledge", "charge_delivered_on", "charge_age_years",
    "charge_status", "filing_url",
)
LONG_FIELDS = (
    "filing_type", "filing_date", "company_number", "company_name", "lender", "lender_group",
    "lender_detail_category", "lender_is_agent", "is_primary", "lender_raw",
)
_TRUSTED = ("ok", "auto_ok")
_SECURED_MAX = 300

# What the particulars text says is secured. Plain keyword rules, in display order.
_ASSET_RULES = (
    ("all_assets", re.compile(r"\ball (the )?(assets|property|undertaking)|\ball and every\b|"
                              r"\bfixed and floating charge\b|\bfloating charge over all\b", re.I)),
    ("property", re.compile(r"\btitle (number|no)\b|\bfreehold\b|\bleasehold\b|\bland (at|on|and|known)\b|"
                            r"\b[A-Z]{1,2}\d{1,2}[A-Z]? ?\d[A-Z]{2}\b|(?<!intellectual )\bproperty\b", re.I)),
    ("receivables", re.compile(r"\bbook debts?\b|\breceivables?\b|\binvoices?\b|\bdebts\b", re.I)),
    ("shares", re.compile(r"\bshares?\b|\bequity interests?\b|\bmembership interests?\b", re.I)),
    ("equipment", re.compile(r"\bplant\b|\bmachinery\b|\bequipment\b|\bvehicles?\b|\bstock\b|"
                             r"\binventory\b", re.I)),
    ("ip", re.compile(r"\bintellectual property\b|\bpatents?\b|\btrade ?marks?\b|\bcopyrights?\b", re.I)),
    ("cash_accounts", re.compile(r"\bbank accounts?\b|\bcash\b|\bdeposit\b", re.I)),
    ("sub_charge", re.compile(r"\bsub-?charge\b|\bassignment of\b|\bsecurity assignment\b", re.I)),
)


def month_bounds(month: str | None, today: date | None = None) -> tuple[date, date]:
    """'2026-09' -> (2026-09-01, 2026-09-30); default is the last complete calendar month."""
    if month:
        y, m = (int(x) for x in month.split("-"))
        first = date(y, m, 1)
    else:
        today = today or date.today()
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def asset_tags(particulars: str | None, floating_covers_all) -> str:
    text = particulars or ""
    tags = [name for name, rx in _ASSET_RULES if rx.search(text)]
    if floating_covers_all == 1 and "all_assets" not in tags:
        tags.insert(0, "all_assets")
    return ";".join(tags)


def _sector(sic_json) -> str:
    try:
        codes = json.loads(sic_json) if sic_json else []
    except (TypeError, ValueError):
        codes = []
    return sectors.label(sectors.sectors_of(codes)[0]) if codes else "Sector not known yet"


def _age_years(charged: str | None, filed: str | None):
    try:
        return round((date.fromisoformat(filed) - date.fromisoformat(charged)).days / 365.25, 1)
    except (TypeError, ValueError):
        return ""


def _yn(value) -> str:
    return {1: "yes", 0: "no"}.get(value, "")


def _pick_primary(chargees: list[dict]) -> dict:
    """The lender to credit when a charge names several: a real lender before an agent or
    trustee, then the category priority, then name. Deterministic."""
    prio = {c: i for i, c in enumerate(config.CATEGORY_PRIORITY)}
    return min(chargees, key=lambda c: (c["is_agent"], prio.get(c["category"], 99), c["name_norm"]))


def build_rows(conn, start: date, end: date, group_rules=None) -> tuple[list[dict], list[dict]]:
    """(filings, lenders_long) for MR01 and MR04 filings dated inside the window."""
    group_rules = group_rules or load_group_rules()
    display: dict[str, str] = {}
    filings: list[dict] = []
    longs: list[dict] = []
    for form, field in (("MR01", "delivered_on"), ("MR04", "satisfied_on")):
        charges = conn.execute(
            f"SELECT c.*, co.name AS company_name, co.sic_codes FROM charges c "
            f"LEFT JOIN companies co ON co.company_number = c.company_number "
            f"WHERE c.deleted = 0 AND c.{field} BETWEEN ? AND ? "
            f"ORDER BY c.{field}, c.company_number, c.charge_id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        for c in charges:
            persons = conn.execute(
                "SELECT p.name_raw, p.name_norm, lc.category, lc.parent_firm, lc.source, "
                "lc.review_status FROM charge_persons p "
                "LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm "
                "WHERE p.company_number = ? AND p.charge_id = ? ORDER BY p.name_raw",
                (c["company_number"], c["charge_id"]),
            ).fetchall()
            chargees = []
            for p in persons:
                cat = p["category"] or "unknown"
                g = group_lender(p["name_norm"], cat, p["parent_firm"], group_rules)
                prev = display.get(g.key)
                if prev is None or (len(g.canonical), g.canonical) < (len(prev), prev):
                    display[g.key] = g.canonical
                chargees.append({
                    "raw": p["name_raw"], "name_norm": p["name_norm"], "category": cat, "g": g,
                    "is_agent": cat == "security_agent",
                    "source": p["source"] or "none",
                    "status": p["review_status"] or "unclassified",
                })
            filing = {
                "filing_type": form,
                "filing_date": c[field],
                "created_on": c["created_on"],
                "company_number": c["company_number"],
                "company_name": c["company_name"] or "(name not fetched yet)",
                "sector": _sector(c["sic_codes"]),
                "n_chargees": len(chargees),
                "all_chargees": "; ".join(x["raw"] for x in chargees),
                "charge_type": (c["classification_text"] or "")[:120],
                "secured_assets": re.sub(r"\s+", " ", (c["particulars_text"] or "")).strip()[:_SECURED_MAX],
                "asset_tags": asset_tags(c["particulars_text"], c["floating_covers_all"]),
                "all_assets_floating": _yn(c["floating_covers_all"]),
                "has_fixed": _yn(c["contains_fixed"]),
                "has_floating": _yn(c["contains_floating"]),
                "negative_pledge": _yn(c["contains_negative_pledge"]),
                "charge_delivered_on": c["delivered_on"] if form == "MR04" else "",
                "charge_age_years": _age_years(c["delivered_on"], c["satisfied_on"]) if form == "MR04" else "",
                "charge_status": c["status"],
                "filing_url": config.FILING_URL.format(company=c["company_number"], charge_id=c["charge_id"]),
                "_chargees": chargees,
                "_more_than_four": bool(c["more_than_four_persons"]),
            }
            if chargees:
                prim = _pick_primary(chargees)
                filing.update({
                    "_prim": prim, "lender_group": prim["g"].group,
                    "lender_is_agent": "yes" if prim["is_agent"] else "no",
                    "lender_detail_category": prim["category"], "group_basis": prim["g"].basis,
                    "lender_raw": prim["raw"], "classification_source": prim["source"],
                    "needs_check": "no" if prim["status"] in _TRUSTED else "yes",
                })
            else:
                filing.update({"_prim": None, "lender_group": "other", "lender_is_agent": "",
                               "lender_detail_category": "unknown", "group_basis": "none",
                               "lender_raw": "", "classification_source": "none", "needs_check": "yes"})
            filings.append(filing)
    # Canonical display names are decided once per key so a lender is spelled one way everywhere.
    for f in filings:
        prim = f["_prim"]
        f["lender"] = display[prim["g"].key] if prim else "(no chargee recorded)"
        seen = set()
        for x in f["_chargees"]:
            if x["g"].key in seen:
                continue
            seen.add(x["g"].key)
            longs.append({
                "filing_type": f["filing_type"], "filing_date": f["filing_date"],
                "company_number": f["company_number"], "company_name": f["company_name"],
                "lender": display[x["g"].key], "lender_group": x["g"].group,
                "lender_detail_category": x["category"],
                "lender_is_agent": "yes" if x["is_agent"] else "no",
                "is_primary": "yes" if prim and x["g"].key == prim["g"].key else "no",
                "lender_raw": x["raw"],
            })
    return filings, longs


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"


def _lag_days(conn, start: date, end: date) -> list[int]:
    rows = conn.execute(
        "SELECT delivered_on, first_seen_at FROM charges WHERE deleted = 0 "
        "AND delivered_on BETWEEN ? AND ? AND first_seen_at IS NOT NULL",
        (start.isoformat(), end.isoformat())).fetchall()
    out = []
    for r in rows:
        try:
            out.append((date.fromisoformat(str(r["first_seen_at"])[:10])
                        - date.fromisoformat(r["delivered_on"])).days)
        except (TypeError, ValueError):
            pass
    return out


def data_quality_note(conn, start: date, end: date, filings: list[dict], label: str,
                      today: date | None = None) -> tuple[str, dict]:
    today = today or date.today()
    mr01 = [f for f in filings if f["filing_type"] == "MR01"]
    mr04 = [f for f in filings if f["filing_type"] == "MR04"]
    covered, cov_note = coverage(conn, start, end)
    began = get_meta(conn, "coverage_start") or conn.execute(
        "SELECT MIN(first_seen_at) FROM charges").fetchone()[0]
    began_day = date.fromisoformat(str(began)[:10]) if began else None
    last_seen = conn.execute("SELECT MAX(first_seen_at) FROM charges").fetchone()[0]
    last_day = date.fromisoformat(str(last_seen)[:10]) if last_seen else None
    window_days = (end - start).days + 1
    first_ok = max(start, began_day) if began_day else None
    covered_days = max(0, (min(end, last_day or end) - first_ok).days + 1) if first_ok else 0

    lags = _lag_days(conn, start, end)
    per_day = Counter(f["filing_date"] for f in mr01)
    weekdays = [start + timedelta(days=i) for i in range(window_days)
                if (start + timedelta(days=i)).weekday() < 5]
    day_counts = [per_day.get(d.isoformat(), 0) for d in weekdays]
    median_day = statistics.median(day_counts) if day_counts else 0
    thin_days = [d.isoformat() for d, n in zip(weekdays, day_counts) if median_day and n < 0.25 * median_day]

    n = len(filings)
    def have(rows, key): return sum(1 for f in rows if f.get(key) not in (None, "", "(name not fetched yet)",
                                                                          "Sector not known yet"))
    chargee_total = sum(len(f["_chargees"]) for f in filings)
    src = Counter(x["source"] for f in filings for x in f["_chargees"])
    status = Counter(x["status"] for f in filings for x in f["_chargees"])
    group_counts = Counter(f["lender_group"] for f in filings)
    basis = Counter(f["group_basis"] for f in filings)
    agents = sum(1 for f in filings if f["lender_is_agent"] == "yes")
    multi = sum(1 for f in filings if f["n_chargees"] > 1)
    over4 = sum(1 for f in filings if f["_more_than_four"])
    need = Counter()
    for f in filings:
        if f["needs_check"] == "yes" and f["_prim"]:
            need[f["_prim"]["raw"]] += 1
    lenders = {f["lender"] for f in filings}

    issues = []
    if not covered:
        issues.append(cov_note)
    if last_day and end > last_day - timedelta(days=5):
        issues.append(f"The latest filing was first seen on {last_day.isoformat()}. Filings reach the radar "
                      "up to several days after their delivery date, so the last few days of this window "
                      "are probably under-counted. Re-run at least a week after the window ends.")
    if thin_days:
        issues.append("Weekdays with under a quarter of the median daily MR01 count (likely incomplete): "
                      + ", ".join(thin_days) + ".")
    if day_counts and median_day and max(day_counts) > 3 * median_day:
        issues.append(f"Daily MR01 counts are very uneven (busiest weekday {max(day_counts)} vs median "
                      f"{median_day:g}). Possible causes: a backlog delivered when the stream first connected, or "
                      "days not yet fully delivered. Do not quote daily or weekly volumes from this window.")
    if basis.get("category", 0) + basis.get("name", 0) + basis.get("none", 0) > 0.5 * n and n:
        issues.append("Over half the filings take their lender group from the category fallback, "
                      "not the lookup table. Most are property lenders, which the five groups put in 'other'.")

    md = [
        f"# Data-quality note: {label} ({start.isoformat()} to {end.isoformat()})",
        "",
        f"_Generated {today.isoformat()} from Companies House charge filings. Rules only: "
        "`data/lenders.csv` and `data/lender_groups.csv`, no AI._",
        "",
        "## Verdict",
        "",
        ("**Full coverage.** Counts can be read as monthly totals, subject to the limits below."
         if not issues else
         "**Not a clean month.** Read the issues below before using any count as a monthly total."),
        "",
        *(f"- {i}" for i in issues),
        *([""] if issues else []),
        "## Coverage",
        "",
        f"- Window: {window_days} days. Radar first collected filings on "
        f"{began_day.isoformat() if began_day else 'n/a'}; latest filing first seen "
        f"{last_day.isoformat() if last_day else 'n/a'}. Days of the window covered: "
        f"**{covered_days} of {window_days}**.",
        "- Filings reach the radar after the date they were delivered. Median delay "
        + (f"**{statistics.median(lags):g} days**, longest **{max(lags)} days**" if lags else "n/a")
        + f" (MR01 in this window, {len(lags)} measured).",
        f"- Daily MR01 count on weekdays: median {median_day:g}, min {min(day_counts) if day_counts else 0}, "
        f"max {max(day_counts) if day_counts else 0}.",
        "- Companies House gives no list of every charge filed in a period, so this extract only contains "
        "what the live charges stream delivered after the radar connected. It cannot be backfilled.",
        "",
        "## Counts",
        "",
        f"- MR01 (charge registered): **{len(mr01)}** across {len({f['company_number'] for f in mr01})} companies.",
        f"- MR04 (charge satisfied): **{len(mr04)}** across {len({f['company_number'] for f in mr04})} companies.",
        f"- Distinct canonical lenders: **{len(lenders)}**. Filings naming more than one chargee: "
        f"{multi} ({_pct(multi, n)}). Filings where Companies House says more than four chargees "
        f"(list truncated): {over4}.",
        "",
        "## Field completeness",
        "",
        "| Field | Filled | Share |", "|---|---|---|",
        *(f"| {lab} | {have(filings, key)} | {_pct(have(filings, key), n)} |" for lab, key in (
            ("Company name", "company_name"), ("Sector (from SIC code)", "sector"),
            ("Charge type", "charge_type"), ("Secured assets text", "secured_assets"),
            ("Date created", "created_on"), ("Asset tags (rule-derived)", "asset_tags"))),
        "",
        "`secured_assets` is the free text the filer entered. Some filers write 'None' or leave it blank; "
        "`asset_tags` is a keyword reading of it and is blank when nothing matched.",
        "",
        "## Lender matching",
        "",
        f"- Chargee names in the extract: {chargee_total}. How each was classified "
        f"(lenders.csv): " + ", ".join(f"{k} {v}" for k, v in src.most_common()) + ".",
        "- Review status: " + ", ".join(f"{k} {v}" for k, v in status.most_common())
        + ". Only `ok` (human-confirmed) and `auto_ok` count as trusted; the rest show `needs_check = yes`.",
        "- Group assigned by: " + ", ".join(f"{k} {v}" for k, v in basis.most_common())
        + " (table = `lender_groups.csv`; category = lenders.csv fallback; name = no parent known).",
        f"- Filings where the primary chargee is a security agent or trustee (real lender may be hidden): "
        f"**{agents}** ({_pct(agents, n)}).",
        f"- Filings flagged `needs_check = yes`: **{sum(1 for f in filings if f['needs_check'] == 'yes')}** "
        f"({_pct(sum(1 for f in filings if f['needs_check'] == 'yes'), n)}).",
        "",
        "| Lender group | Filings | Share |", "|---|---|---|",
        *(f"| {GROUP_LABELS[g]} | {group_counts.get(g, 0)} | {_pct(group_counts.get(g, 0), n)} |" for g in GROUPS),
        "",
        "### Names to check first (most filings, not yet trusted)",
        "",
        *([f"- {name}: {cnt}" for name, cnt in need.most_common(10)] or ["- None."]),
        "",
        "Fix these with `python -m radar.review set \"NAME\" CATEGORY --parent \"Firm\"` or by adding a row "
        "to `data/lender_groups.csv`, then re-run this command.",
        "",
        "## What this extract does not tell you",
        "",
        "- A charge is evidence that security was granted. It does not show the amount, pricing, tenor or purpose.",
        "- MR01 must be filed within 21 days of creation, so `filing_date` can trail the real deal date "
        "(see `created_on`).",
        "- A lender named as security agent or trustee may sit in front of a syndicate; the individual lenders are not shown.",
        "- MR04 is the filing that marks a charge satisfied. Satisfied does not mean refinanced: it may be a plain "
        "repayment, a sale, or housekeeping on an old charge (see `charge_age_years`).",
        "- Lender groups come from the name alone. The clearing/challenger split is a judgement call, listed row by "
        "row in `data/lender_groups.csv`.",
        "- Individuals' names can appear as chargees. Keep them out of anything published; use companies and lenders only.",
        "",
    ]
    meta = {
        "label": label, "start": start.isoformat(), "end": end.isoformat(), "generated": today.isoformat(),
        "coverage": "full" if not issues else "incomplete", "issues": issues,
        "days_covered": covered_days, "window_days": window_days,
        "mr01": len(mr01), "mr04": len(mr04), "distinct_lenders": len(lenders),
        "group_counts": {g: group_counts.get(g, 0) for g in GROUPS},
        "needs_check": sum(1 for f in filings if f["needs_check"] == "yes"),
    }
    return "\n".join(md), meta


def write_monthly(conn, start: date, end: date, label: str | None = None, out_dir=None,
                  today: date | None = None) -> Path:
    label = label or (start.strftime("%Y-%m") if start.day == 1 and (end + timedelta(days=1)).day == 1
                      else f"{start.isoformat()}_{end.isoformat()}")
    out = Path(out_dir or (config.OUTPUT_DIR / "monthly")) / label
    out.mkdir(parents=True, exist_ok=True)
    filings, longs = build_rows(conn, start, end)
    with open(out / "filings.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FILING_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(filings)
    with open(out / "lenders_long.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LONG_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(longs)
    note, meta = data_quality_note(conn, start, end, filings, label, today)
    (out / "data_quality.md").write_text(note, encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.monthly")
    p.add_argument("--month", help="YYYY-MM (default: last complete calendar month)")
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--label", help="output folder name (default: the month, or the dates)")
    args = p.parse_args(argv)
    if args.start or args.end:
        if not (args.start and args.end):
            p.error("--start and --end go together")
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    else:
        start, end = month_bounds(args.month)
    out = write_monthly(connect(), start, end, args.label)
    print(out)
    print((out / "data_quality.md").read_text(encoding="utf-8").split("## Coverage")[0])


if __name__ == "__main__":
    main()

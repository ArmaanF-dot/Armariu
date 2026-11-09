"""Loan size for a lead, from the latest filed accounts (plan task 6).

Filings carry no loan amounts, but accounts show bank loans and borrowings (loans_1y + loans_after)
and total assets less current liabilities. A lead gets those figures only when accounts were public
before the list date and are not older than two years; otherwise the size is left blank, never
guessed. Small firms often file thin accounts, so cover is partial and is reported.

    python -m radar.lead_size coverage LIST.csv     # share of leads with a size
"""
from __future__ import annotations

import csv
import sys
from datetime import date

MAX_AGE_DAYS = 730
COLUMNS = ("borrowings_gbp", "total_assets_less_cl_gbp", "accounts_date")


def _d(s):
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def size_for(number: str, on: date, accounts: dict) -> dict:
    """Latest accounts known before `on`. Blank fields when none, or when the filing breaks nothing down."""
    blank = {c: "" for c in COLUMNS}
    best = None
    for a in accounts.get(number, []):
        known, bs = _d(a.get("known_from")), _d(a.get("balance_sheet_date"))
        if known and bs and known < on and (on - bs).days <= MAX_AGE_DAYS:
            best = a
    if not best:
        return blank
    l1, la = best.get("loans_1y"), best.get("loans_after")
    borrow = None if l1 is None and la is None else (l1 or 0.0) + (la or 0.0)
    tal = best.get("total_assets_less_cl")
    if borrow is None and tal is None:
        return blank
    return {"borrowings_gbp": "" if borrow is None else round(borrow), 
            "total_assets_less_cl_gbp": "" if tal is None else round(tal),
            "accounts_date": best["balance_sheet_date"]}


def add_sizes(rows: list[dict], on: date, accounts: dict) -> float:
    """Add the size columns to watchlist rows in place. Returns the share of rows with a borrowings figure."""
    n = 0
    for r in rows:
        s = size_for(r["company_number"], on, accounts)
        r.update(s)
        n += s["borrowings_gbp"] != ""
    return n / len(rows) if rows else 0.0


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 2 or a[0] != "coverage":
        raise SystemExit(__doc__)
    rows = list(csv.DictReader(open(a[1], encoding="utf-8")))
    have = sum(1 for r in rows if r.get("borrowings_gbp"))
    print(f"{have} of {len(rows)} leads have a borrowings figure ({have / len(rows):.0%})" if rows else "empty list")


if __name__ == "__main__":
    main()

"""Refinance watch hit rates measured on a random sample of companies (BACKTEST).

These are the only absolute rates the client portal quotes. They come from docs/refi_population.md
(1,501 England and Wales companies drawn at random from those with a charge register, scored on 21,474
company-dates at 90 days and 11,130 at 12 months, 5 Nov 2026). The radar's own data holds only companies
active at Companies House after 20 Oct 2026, so rates measured on it run about 3 times high and are not
shown to clients. Update this file, and the doc, together whenever the sample is rerun.

Switched lender, top 10% of the row's group, 95% ranges. `rate`: share that switched lender in the window;
`lift`: that rate over the group's own average. The top 1% and 5% rows rest on a few dozen firms and are
never shown.
"""
from __future__ import annotations

AS_OF = "2026-11-05"
COMPANIES = 1501
SCORED = {"90d": {"company_dates": 21474, "companies": 1198}, "12m": {"company_dates": 11130, "companies": 1159}}

# group key -> window -> {all: average rate, top10: rate, lift}; every pair is (low, high) 95% range in %
GROUPS = {
    "ordinary": {
        "label": "Ordinary companies", "note": "10 loans or fewer",
        "12m": {"all": 1.9, "all_ci": (1.2, 2.6), "top10": 7.1, "top10_ci": (3.9, 11.4), "lift": 3.8, "lift_ci": (2.3, 5.6)},
        "90d": {"all": 0.4, "all_ci": (0.2, 0.5), "top10": 1.7, "top10_ci": (0.8, 2.8), "lift": 4.7, "lift_ci": (2.7, 6.6)},
    },
    "property": {
        "label": "Property companies", "note": "all property borrowers",
        "12m": {"top10": 23.3, "top10_ci": (12.4, 35.6), "lift": 4.9, "lift_ci": (3.1, 7.2)},
    },
    "ordinary_np": {
        "label": "Ordinary, not property", "note": "10 loans or fewer; the ranking is likely to help, not proven",
        "12m": {"top10": 4.7, "top10_ci": (1.5, 8.9), "lift": 3.0, "lift_ci": (1.2, 5.1)},
        "90d": {"top10": 1.3, "top10_ci": (0.4, 2.3), "lift": 4.6, "lift_ci": (1.7, 7.5)},
    },
}

CAVEATS = [
    "Backtest on a random sample of England and Wales companies that have a charge register; lead list, not a forecast.",
    "Ranges are 95% and resample companies. Several are wide and the lower ends of two sit close to 1x.",
    "Filings carry no loan end dates, so the list says who tends to switch lender, not when a loan ends.",
    "Not covered: Scottish and Northern Irish companies, and companies removed from the register.",
    "The top 1% and top 5% are not quoted: they rest on a few dozen firms.",
]


def public() -> dict:
    return {"as_of": AS_OF, "companies": COMPANIES, "scored": SCORED, "groups": GROUPS, "caveats": CAVEATS}


def band_group(row: dict) -> str:
    """Which random-sample row a company is ranked in."""
    if row.get("sector") == "Property and real estate":
        return "property"
    return "serial" if row.get("serial_borrower") else "ordinary_np"


def add_bands(rows: list[dict]) -> None:
    """Tag each row with its rank band inside its group. Only the top 10% carries a measured rate."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(band_group(r), []).append(r)
    for g, rs in by.items():
        rs.sort(key=lambda r: (-r["score"], r["company_number"]))
        n = len(rs)
        for i, r in enumerate(rs):
            r["group"] = g
            r["band"] = None if g == "serial" else "top10" if i < max(1, n // 10) else "top25" if i < max(1, n // 4) else "rest"

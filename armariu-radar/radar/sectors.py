"""Borrower sectors from UK SIC 2007 codes, grouped for filtering.

Companies House gives each company up to four SIC codes. The first one decides the sector shown;
filters match a company if any of its codes falls in the sector. Holding companies get their own
group because private credit deals so often lend to a group's holding company rather than the
trading business, so "Holding company" is a hint, not an industry.
"""
from __future__ import annotations

# key: label. The order here is the order filters list them in.
SECTORS = {
    "holding": "Holding company",
    "real_estate": "Property and real estate",
    "construction": "Construction",
    "manufacturing": "Manufacturing",
    "tech_media": "Technology and media",
    "healthcare": "Healthcare and care",
    "business_services": "Business and professional services",
    "financial": "Financial services",
    "retail": "Retail and wholesale",
    "hospitality": "Hospitality and leisure",
    "transport": "Transport and logistics",
    "energy": "Energy, utilities and resources",
    "education": "Education",
    "agriculture": "Agriculture",
    "public_other": "Public sector and other",
    "dormant": "Dormant or not trading",
    "unknown": "Sector not known yet",
}

# Exact codes first (they override the division they sit in).
_CODES = {
    "64201": "holding", "64202": "holding", "64203": "holding", "64204": "holding", "64205": "holding",
    "64209": "holding", "70100": "holding", "64306": "real_estate",  # REITs
    "41100": "real_estate",  # development of building projects: property developers
    "99999": "dormant", "74990": "dormant",  # dormant company; non-trading company
}
# SIC 2007 divisions (first two digits) -> sector.
_DIVISIONS = (
    ((1, 3), "agriculture"), ((5, 9), "energy"), ((10, 33), "manufacturing"), ((35, 39), "energy"),
    ((41, 43), "construction"), ((45, 47), "retail"), ((49, 53), "transport"), ((55, 56), "hospitality"),
    ((58, 63), "tech_media"), ((64, 66), "financial"), ((68, 68), "real_estate"),
    ((69, 75), "business_services"), ((77, 82), "business_services"), ((84, 84), "public_other"),
    ((85, 85), "education"), ((86, 88), "healthcare"), ((90, 93), "hospitality"), ((94, 99), "public_other"),
)


def sector_of_code(code) -> str:
    code = str(code or "").strip()
    if code in _CODES:
        return _CODES[code]
    if len(code) < 2 or not code[:2].isdigit():
        return "unknown"
    div = int(code[:2])
    for (lo, hi), key in _DIVISIONS:
        if lo <= div <= hi:
            return key
    return "unknown"


def sectors_of(codes) -> list[str]:
    """Every sector a company's codes fall in, first code's sector first, no repeats."""
    out = []
    for code in codes or []:
        key = sector_of_code(code)
        if key not in out:
            out.append(key)
    return out or ["unknown"]


def label(key: str) -> str:
    return SECTORS.get(key, key)

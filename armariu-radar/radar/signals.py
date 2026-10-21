"""What the filings suggest: plain-English signals for each borrower on the register.

Signals are read only from charge dates, statuses and lender types. They never invent amounts,
terms or deal purposes, and every one is worded as what the pattern usually means, with the
filings it rests on attached so a person can check it.

Each borrower's charges come from two places, merged by charge id:
- `charges`: the register (what the stream delivered)
- `borrower_history`: their full charge list from the REST API, older loans included, which
  is what lets the radar tell a refinancing from a first loan.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import config, sectors
from .normalise import normalise_name
from .rules import find_rule, load_rules

RECENT_DAYS = 120          # a charge or repayment this recent counts as news
REFI_BEFORE_DAYS = 180     # new charge up to this long before the old one was marked repaid...
REFI_AFTER_DAYS = 90       # ...or this long after
MATURITY_MIN_YEARS = 3.0   # private credit loans are often refinanced from about year three
MATURITY_MAX_YEARS = 7.5   # past this, an outstanding charge is more likely just never cleared
MATURITY_CATEGORIES = ("direct_lender", "security_agent", "abl_specialist")

TYPES = {
    # key: (label, short explanation shown as the filter's help)
    "refinanced": ("Refinanced", "An old loan was repaid and a new lender took security around the same time"),
    "maturity": ("Refinancing may come up", "A private credit loan has been in place long enough that it is often refinanced"),
    "alongside": ("New lender alongside existing debt", "A new lender took security while another lender's charge is still outstanding"),
    "top_up": ("Existing lender added security", "A lender that already had a charge took another one"),
    "first_loan": ("First secured loan", "No earlier charges on record"),
    "returning": ("New loan after earlier ones were repaid", "All earlier secured loans had been repaid"),
    "repaid": ("Repaid, no new lender yet", "A loan was marked repaid and no new lender has taken security since"),
}
# Signals grouped by what they mean for someone watching the market.
THEMES = {
    "new": ("New lending", "Money just went in: refinancings, new lenders, first loans"),
    "ahead": ("Refinancing ahead", "Loans old enough that a refinancing is often near"),
    "repaid": ("Repayments", "Loans paid off with no new lender yet"),
}
THEME_OF = {"refinanced": "new", "alongside": "new", "top_up": "new", "first_loan": "new", "returning": "new",
            "maturity": "ahead", "repaid": "repaid"}
CATEGORY_WORDS = {
    "direct_lender": "a private credit lender", "bank": "a bank", "security_agent": "a security agent",
    "abl_specialist": "an asset-based lender", "asset_finance": "an asset finance lender", "property_lender": "a property lender",
    "public_sector": "a public-sector lender", "individual": "a private individual", "other": "another kind of lender",
    "unknown": "a lender not yet classified",
}


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _years(a: date, b: date) -> float:
    return (b - a).days / 365.25


def _fmt(d: date | None) -> str:
    return f"{d.day} {d.strftime('%b %Y')}" if d else "an unknown date"


def _primary(cats) -> str:
    for cat in config.CATEGORY_PRIORITY:
        if cat in cats:
            return cat
    return "unknown"


class Namer:
    """Lender type for any name: your decisions and the agent's answers first, then the rules."""

    def __init__(self, conn, rules=None):
        self.cache = {r["name_norm"]: r["category"] for r in conn.execute(
            "SELECT name_norm, category FROM lender_classifications")}
        try:
            self.rules = rules if rules is not None else load_rules()
        except (OSError, ValueError):
            self.rules = []

    def category(self, raw: str) -> tuple[str, str]:
        norm = normalise_name(raw)
        if norm in self.cache:
            return norm, self.cache[norm]
        rule = find_rule(norm, self.rules) if norm else None
        cat = rule.category if rule else "unknown"
        self.cache[norm] = cat
        return norm, cat


def company_charges(conn, number: str, namer: Namer) -> tuple[list[dict], bool]:
    """Every charge we know of for one borrower, oldest first, and whether its full history was fetched."""
    number = number.upper()
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM borrower_history WHERE company_number = ?", (number,)):
        out[r["charge_id"]] = {
            "charge_id": r["charge_id"], "status": r["status"], "created_on": r["created_on"],
            "delivered_on": r["delivered_on"], "satisfied_on": r["satisfied_on"],
            "charge_type": r["classification"], "names": json.loads(r["persons"] or "[]"), "on_register": False,
        }
    for r in conn.execute("SELECT * FROM charges WHERE company_number = ? AND deleted = 0", (number,)):
        names = [p[0] for p in conn.execute(
            "SELECT name_raw FROM charge_persons WHERE company_number = ? AND charge_id = ? ORDER BY name_raw",
            (number, r["charge_id"]))]
        out[r["charge_id"]] = {  # the register copy is the freshest
            "charge_id": r["charge_id"], "status": r["status"], "created_on": r["created_on"],
            "delivered_on": r["delivered_on"], "satisfied_on": r["satisfied_on"],
            "charge_type": r["classification_text"], "names": names or out.get(r["charge_id"], {}).get("names", []),
            "on_register": True,
        }
    rows = []
    for c in out.values():
        lenders = []
        for raw in c["names"]:
            norm, cat = namer.category(raw)
            lenders.append({"name": norm, "display": raw, "category": cat})
        c["lenders"] = lenders
        c["primary"] = _primary([x["category"] for x in lenders])
        c["deal_date"] = c["created_on"] or c["delivered_on"]
        c["filing_url"] = config.FILING_URL.format(company=number, charge_id=c["charge_id"])
        del c["names"]
        rows.append(c)
    rows.sort(key=lambda c: (c["deal_date"] or "", c["charge_id"]))
    fetched = conn.execute("SELECT 1 FROM borrower_history_fetched WHERE company_number = ?", (number,)).fetchone()
    return rows, bool(fetched)


def _who(charge: dict) -> str:
    names = [x["display"] for x in charge["lenders"]]
    if not names:
        return "an unnamed lender"
    return names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} other{'s' if len(names) > 2 else ''}"


def _names(charge: dict) -> set:
    return {x["name"] for x in charge["lenders"]}


def _ref(c: dict) -> dict:
    return {k: c[k] for k in ("charge_id", "status", "created_on", "delivered_on", "satisfied_on",
                              "charge_type", "lenders", "primary", "filing_url", "on_register")}


def _signal(kind, company, when, text, charges, extra=""):
    pc = any(x["category"] in ("direct_lender", "security_agent") for c in charges for x in c["lenders"])
    secs = company.get("sectors") or ["unknown"]
    return {"type": kind, "label": TYPES[kind][0], "theme": THEME_OF[kind], "company_number": company["number"],
            "company_name": company["name"], "date": when.isoformat() if when else None,
            "sector": secs[0], "sector_label": sectors.label(secs[0]), "sectors": secs,
            "lender_types": sorted({x["category"] for c in charges for x in c["lenders"]}),
            "meaning": text + (" " + extra if extra else ""), "private_credit": pc,
            "charges": [_ref(c) for c in charges]}


def for_company(number: str, name: str | None, charges: list[dict], fetched: bool, today: date,
                sic_codes=None) -> list[dict]:
    company = {"number": number, "name": name or number, "sectors": sectors.sectors_of(sic_codes)}
    recent = today - timedelta(days=RECENT_DAYS)
    out, used = [], set()

    # New charges: group charges made on the same day for the same lenders into one deal.
    deals: dict[tuple, list[dict]] = {}
    for c in charges:
        d = _d(c["deal_date"])
        if d and d >= recent:
            deals.setdefault((c["deal_date"][:10], frozenset(_names(c))), []).append(c)
    for (_, names), group in sorted(deals.items(), key=lambda kv: kv[0][0], reverse=True):
        new = group[0]
        d = _d(new["deal_date"])
        ids = {c["charge_id"] for c in group}
        older = [c for c in charges if c["charge_id"] not in ids and (_d(c["deal_date"]) or date.min) < d]
        what = CATEGORY_WORDS.get(new["primary"], "a lender")
        many = f" ({len(group)} charges registered together)" if len(group) > 1 else ""
        repaid = [c for c in older if c["status"] != "outstanding" and _d(c["satisfied_on"])
                  and -REFI_AFTER_DAYS <= (_d(c["satisfied_on"]) - d).days <= REFI_BEFORE_DAYS
                  and not (_names(c) & names)]
        if repaid:
            old = repaid[-1]
            extra = ""
            if old["primary"] == "bank" and new["primary"] in ("direct_lender", "security_agent"):
                extra = "It moved from a bank to private credit."
            elif old["primary"] in ("direct_lender", "security_agent") and new["primary"] == "bank":
                extra = "It moved from private credit back to a bank."
            elif old["primary"] in ("direct_lender", "security_agent") and new["primary"] in ("direct_lender", "security_agent"):
                extra = "One private credit lender replaced another."
            out.append(_signal("refinanced", company, d,
                f"{_who(old)} ({CATEGORY_WORDS.get(old['primary'], 'a lender')}, charge from {_d(old['deal_date']).year if _d(old['deal_date']) else 'an unknown year'}) "
                f"was marked repaid on {_fmt(_d(old['satisfied_on']))}, and {_who(new)} ({what}) took security on {_fmt(d)}{many}. "
                "That pattern usually means the old loan was refinanced.", group + [old], extra))
            used.update(c["charge_id"] for c in group + [old])
            continue
        live = [c for c in older if c["status"] == "outstanding"]
        same = [c for c in live if _names(c) & names]
        other = [c for c in live if not (_names(c) & names)]
        if other:
            o = other[-1]
            out.append(_signal("alongside", company, d,
                f"{_who(new)} ({what}) took security on {_fmt(d)}{many} while {_who(o)} still holds a charge from "
                f"{_d(o['deal_date']).year if _d(o['deal_date']) else 'an unknown year'}. That points to extra borrowing: "
                "a second loan, a separate facility, or lenders sharing the same security.", group + [o]))
        elif same:
            o = same[-1]
            out.append(_signal("top_up", company, d,
                f"{_who(new)} took another charge on {_fmt(d)}{many}; it already held one from "
                f"{_d(o['deal_date']).year if _d(o['deal_date']) else 'an unknown year'}. Often an amended, increased or extended loan.",
                group + [o]))
        elif not fetched:
            continue  # without the full history we can't tell a first loan from a refinancing
        elif not older:
            out.append(_signal("first_loan", company, d,
                f"{_who(new)} ({what}) took security on {_fmt(d)}{many}, and the company has no earlier charges on record. "
                "This looks like its first secured loan.", group))
        else:
            out.append(_signal("returning", company, d,
                f"{_who(new)} ({what}) took security on {_fmt(d)}{many}. The company's {len(older)} earlier "
                f"secured loan{'s were' if len(older) > 1 else ' was'} all repaid, so this is new secured borrowing.",
                group + older[-1:]))
        used.update(ids)

    # Repaid with nothing new since: one signal per repayment date.
    repaid_on: dict[date, list[dict]] = {}
    for c in charges:
        s = _d(c["satisfied_on"])
        if c["charge_id"] in used or c["status"] == "outstanding" or not s or s < recent or c["primary"] == "individual":
            continue
        if any(n is not c and (_d(n["deal_date"]) or date.min) >= s - timedelta(days=REFI_BEFORE_DAYS) for n in charges):
            continue
        repaid_on.setdefault(s, []).append(c)
    for s, group in sorted(repaid_on.items(), reverse=True):
        c = group[-1]
        years = sorted({_d(x["deal_date"]).year for x in group if _d(x["deal_date"])})
        when = f"from {years[0]}" if len(years) == 1 else f"from {years[0]} to {years[-1]}" if years else "of unknown date"
        lead = (f"{_who(c)}'s charge {when} was" if len(group) == 1
                else f"{len(group)} charges {when} ({', '.join(sorted({_who(x) for x in group}))}) were")
        caveat = ("" if fetched else " The radar hasn't fetched this company's older charges yet, so this may turn out "
                  "to be part of a refinancing.")
        out.append(_signal("repaid", company, s,
            f"{lead} marked repaid on {_fmt(s)}, and no new lender has taken security since. Possible reasons: the "
            "company was sold, a new loan's charge hasn't been filed yet (that can take up to 21 days), or the debt "
            "was simply paid off." + caveat, group))

    # Private credit lenders in place long enough that a refinancing is often near: one signal per lender.
    seen: set = set()
    for c in charges:
        d = _d(c["deal_date"])
        if c["status"] != "outstanding" or not d or c["primary"] not in MATURITY_CATEGORIES or c["charge_id"] in seen:
            continue
        same = [n for n in charges if n["status"] == "outstanding" and (n is c or _names(n) & _names(c))]
        seen.update(n["charge_id"] for n in same)
        age = _years(d, today)
        if not MATURITY_MIN_YEARS <= age <= MATURITY_MAX_YEARS:
            continue
        latest = max(same, key=lambda n: n["deal_date"] or "")
        later = (f" It has taken {len(same) - 1} more charge{'s' if len(same) > 2 else ''} since, the latest on "
                 f"{_fmt(_d(latest['deal_date']))}, which can mean the loan was already increased, amended or extended."
                 if len(same) > 1 else "")
        stage = ("is in the window where these loans are often refinanced" if age < 5
                 else "is near the end of a typical term")
        out.append(_signal("maturity", company, d,
            f"{_who(c)} ({CATEGORY_WORDS.get(c['primary'], 'a lender')}) has held security since {_fmt(d)}, "
            f"{age:.1f} years ago, which {stage}. As a rule of thumb, private credit loans run about five to seven "
            f"years and are often refinanced after three or four.{later} Charges sometimes stay on the register after "
            "a loan is repaid, so check the filings.", same))
    return out


def all_signals(conn, today: date | None = None, namer: Namer | None = None) -> list[dict]:
    today = today or date.today()
    namer = namer or Namer(conn)
    out = []
    for r in conn.execute(
            "SELECT DISTINCT c.company_number, co.name, co.sic_codes FROM charges c LEFT JOIN companies co "
            "ON co.company_number = c.company_number WHERE c.deleted = 0"):
        charges, fetched = company_charges(conn, r["company_number"], namer)
        sic = json.loads(r["sic_codes"]) if r["sic_codes"] else []
        out += for_company(r["company_number"], r["name"], charges, fetched, today, sic)
    out.sort(key=lambda s: (s["date"] or ""), reverse=True)
    out.sort(key=lambda s: not s["private_credit"])
    return out


def counts(signals: list[dict]) -> dict:
    c = {k: 0 for k in TYPES}
    for s in signals:
        c[s["type"]] += 1
    return c

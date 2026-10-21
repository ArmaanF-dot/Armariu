"""Who is behind a lender: its own Companies House record, owners and corporate officers.

Many unclear lender names are UK companies ("FORTYS CAPITAL 6 LIMITED", "ROMACO SPV 2 LIMITED").
Their filings say who owns them (persons with significant control) and who runs them, which
often names the fund manager or group behind them. No AI: plain Companies House lookups,
using the same REST key and rate limit as the rest of the pipeline.

    python -m radar.owners show "FORTYS CAPITAL 6"     # what the database holds for a lender

The result is evidence for the agents and for you, never a decision on its own.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

from .db import connect, now_iso
from .normalise import normalise_name

REFRESH_DAYS = 60        # re-check a matched lender's owners this often
RETRY_UNMATCHED_DAYS = 30
OWNER_HOPS = 2           # owner, and the owner's owner (often the fund manager)

# Common SIC codes for lenders, so the app can say what a lender's own filing calls it.
LENDER_SIC = {
    "64110": "Central banking", "64191": "Banks", "64192": "Building societies",
    "64201": "Holding company (agriculture)", "64209": "Holding company", "64205": "Holding company (financial services)",
    "64301": "Investment trusts", "64302": "Unit trusts", "64303": "Venture and development capital",
    "64304": "Open-ended investment companies", "64305": "Property unit trusts", "64306": "Real estate investment trusts",
    "64910": "Financial leasing", "64921": "Credit granting by non-deposit-taking finance houses",
    "64922": "Mortgage finance", "64929": "Other credit granting", "64991": "Security dealing on own account",
    "64992": "Factoring", "64999": "Other financial services", "66110": "Administration of financial markets",
    "66190": "Other activities auxiliary to financial services", "66300": "Fund management", "70100": "Head office",
    "68100": "Buying and selling own real estate", "68209": "Letting own or leased real estate",
    "41100": "Development of building projects",
}
_CONTROL = {
    "ownership-of-shares-75-to-100-percent": "75-100% of shares", "ownership-of-shares-50-to-75-percent": "50-75% of shares",
    "ownership-of-shares-25-to-50-percent": "25-50% of shares", "voting-rights-75-to-100-percent": "75-100% of votes",
    "voting-rights-50-to-75-percent": "50-75% of votes", "voting-rights-25-to-50-percent": "25-50% of votes",
    "right-to-appoint-and-remove-directors": "appoints the directors", "significant-influence-or-control": "significant control",
    "right-to-share-surplus-assets-75-to-100-percent": "75-100% of surplus (LLP)",
    "right-to-appoint-and-remove-members": "appoints the members (LLP)",
}
_NUMBER_IN_NAME = re.compile(r"\b(?:COMPANY\s*(?:NO|NUMBER)|CRN|REG(?:ISTERED)?\s*(?:NO|NUMBER))\W*([A-Z]{0,2}\d{6,8})\b", re.I)
_SEARCH_TRIM = [
    re.compile(r"\(.*?\)"),
    re.compile(r"\b(?:COMPANY\s*(?:NO|NUMBER)|CRN)\b.*$", re.I),
    re.compile(r"\b(?:TRADING|T/A)\s+AS\b.*$", re.I),
    re.compile(r"\bAS\s+(?:LENDER|AGENT|SECURITY\s+(?:AGENT|TRUSTEE)|TRUSTEE|NOTES\s+COLLATERAL\s+AGENT)\b.*$", re.I),
    re.compile(r"\bAND\s+ITS\s+SUCCESSORS\b.*$", re.I),
]


def search_name(display: str) -> str:
    s = display or ""
    for rx in _SEARCH_TRIM:
        s = rx.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" ,.;:-")


def number_in_name(display: str) -> str | None:
    m = _NUMBER_IN_NAME.search(display or "")
    return m.group(1).upper().zfill(8) if m else None


def candidates(conn, limit: int) -> list[dict]:
    """Lender names worth looking up, most useful first: unsure ones, then headline types, then
    names the classifier hasn't seen. Names settled by your rules or the starter rules are skipped."""
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=REFRESH_DAYS)).isoformat(timespec="seconds")
    retry = (now - timedelta(days=RETRY_UNMATCHED_DAYS)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT p.name_norm, MIN(p.name_raw) AS display, COUNT(*) AS n, lc.review_status, lc.category, lc.source "
        "FROM charge_persons p LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm "
        "LEFT JOIN lender_profiles lp ON lp.name_norm = p.name_norm "
        "WHERE (lc.source IS NULL OR lc.source = 'agent') "
        "AND (lp.name_norm IS NULL OR (lp.company_number IS NOT NULL AND lp.fetched_at < ?) "
        "     OR (lp.company_number IS NULL AND lp.fetched_at < ?)) "
        "GROUP BY p.name_norm", (fresh, retry)).fetchall()

    def rank(r):
        if r["review_status"] == "needs_review":
            return 0
        if r["category"] in ("direct_lender", "security_agent"):
            return 1
        if r["category"] is None:
            return 2
        return 3
    out = sorted((dict(r) for r in rows if r["category"] != "individual"), key=lambda r: (rank(r), -r["n"]))
    return out[:limit]


def _owners(client, number: str) -> list[dict]:
    data = client.get(f"/company/{number}/persons-with-significant-control", {"items_per_page": 25}) or {}
    out = []
    for it in data.get("items") or []:
        if it.get("ceased_on") or it.get("ceased"):
            continue
        ident = it.get("identification") or {}
        out.append({
            "name": (it.get("name") or "").strip(),
            "kind": "company" if "corporate" in (it.get("kind") or "") or "legal-person" in (it.get("kind") or "")
                    else "person" if "individual" in (it.get("kind") or "") else "other",
            "control": [_CONTROL.get(c, c.replace("-", " ")) for c in it.get("natures_of_control") or []][:3],
            "registration_number": (ident.get("registration_number") or "").strip() or None,
            "country": (ident.get("country_registered") or ident.get("place_registered") or "").strip() or None,
        })
    return out


def _officers(client, number: str) -> list[dict]:
    data = client.get(f"/company/{number}/officers", {"items_per_page": 35}) or {}
    out = []
    for it in data.get("items") or []:
        if it.get("resigned_on"):
            continue
        role = it.get("officer_role") or ""
        # Corporate officers and LLP members name the firm that runs a vehicle; individual
        # directors are counted but not listed (they rarely say who the lender is).
        if role.startswith("corporate") or role.startswith("llp"):
            out.append({"name": (it.get("name") or "").strip(), "role": role.replace("-", " ")})
    return out[:8]


def _uk_number(owner: dict) -> str | None:
    num = (owner.get("registration_number") or "").replace(" ", "").upper()
    country = (owner.get("country") or "").lower()
    uk = not country or any(w in country for w in ("england", "wales", "scotland", "united kingdom", "uk", "northern ireland"))
    return num.zfill(8) if uk and re.fullmatch(r"[A-Z]{0,2}\d{5,8}", num) else None


def lookup(client, display: str, norm: str) -> dict:
    """Find a lender's company and who is behind it. Returns the row stored in lender_profiles."""
    number, matched_by = number_in_name(display), "number in name"
    if not number:
        q = search_name(display)
        matched_by = "exact name"
        res = client.get("/search/companies", {"q": q, "items_per_page": 10}) or {}
        target = normalise_name(q)
        hits = [it for it in res.get("items") or [] if normalise_name(it.get("title") or "") == target]
        hits.sort(key=lambda it: it.get("company_status") != "active")
        number = hits[0]["company_number"] if hits else None
    if not number:
        return {"name_norm": norm, "company_number": None, "matched_by": "no match"}
    prof = client.company(number) or {}
    if not prof:
        return {"name_norm": norm, "company_number": None, "matched_by": "no match"}
    owners = _owners(client, number)
    chain, seen = [], {number}
    hop = next((o for o in owners if o["kind"] == "company"), None)
    for _ in range(OWNER_HOPS - 1):  # follow the first corporate owner one more step, if it's a UK company
        nxt = _uk_number(hop) if hop else None
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        up = _owners(client, nxt)
        chain.append({"of": hop["name"], "owners": up})
        hop = next((o for o in up if o["kind"] == "company"), None)
    return {
        "name_norm": norm, "company_number": number, "company_name": prof.get("company_name"),
        "company_status": prof.get("company_status"), "company_type": prof.get("type"),
        "sic_codes": prof.get("sic_codes") or [], "date_of_creation": prof.get("date_of_creation"),
        "owners": owners, "owner_chain": chain, "officers": _officers(client, number), "matched_by": matched_by,
    }


def save(conn, row: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO lender_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (row["name_norm"], row.get("company_number"), row.get("company_name"), row.get("company_status"),
         row.get("company_type"), json.dumps(row.get("sic_codes") or []), row.get("date_of_creation"),
         json.dumps({"direct": row.get("owners") or [], "chain": row.get("owner_chain") or []}),
         json.dumps(row.get("officers") or []), row.get("matched_by"), now_iso()))


def fetch(conn, client, limit: int = 80) -> int:
    n = 0
    for r in candidates(conn, limit):
        if client.out_of_time():
            break
        save(conn, lookup(client, r["display"], r["name_norm"]))
        n += 1
        if n % 10 == 0:
            conn.commit()
    conn.commit()
    return n


def profile(conn, norm: str) -> dict | None:
    """The stored lookup for one lender, shaped for the app and the agents' queues."""
    r = conn.execute("SELECT * FROM lender_profiles WHERE name_norm = ?", (norm,)).fetchone()
    if not r:
        return None
    if not r["company_number"]:
        return {"matched": False, "checked": r["fetched_at"]}
    own = json.loads(r["owners"] or "{}")
    sic = json.loads(r["sic_codes"] or "[]")
    chain = own.get("chain") or []
    top = None  # the furthest corporate owner we reached: usually the group or fund manager
    for step in [{"owners": own.get("direct") or []}] + chain:
        corp = next((o for o in step["owners"] if o["kind"] == "company"), None)
        if corp:
            top = corp["name"]
    return {
        "matched": True, "matched_by": r["matched_by"], "checked": r["fetched_at"],
        "company_number": r["company_number"], "company_name": r["company_name"],
        "company_status": r["company_status"], "company_type": r["company_type"],
        "incorporated": r["date_of_creation"],
        "sic": [{"code": c, "label": LENDER_SIC.get(c, "")} for c in sic],
        "owners": own.get("direct") or [], "owner_chain": chain,
        "officers": json.loads(r["officers"] or "[]"),
        "group": top,
        "url": f"https://find-and-update.company-information.service.gov.uk/company/{r['company_number']}",
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "show":
        print(__doc__)
        return 1
    print(json.dumps(profile(connect(), normalise_name(argv[1])), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

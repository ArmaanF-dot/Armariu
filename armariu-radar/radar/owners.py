"""Who is behind a lender: its own Companies House record, who controls it, and its links to
its borrowers.

Many unclear lender names are UK companies ("FORTYS CAPITAL 6 LIMITED", "ROMACO SPV 2 LIMITED").
Their filings say who owns them (persons with significant control) and who runs them. No AI:
plain Companies House lookups, sharing the pipeline's rate limit and time budget.

- Control chain: majority owners are followed up to 4 levels while the controller is a UK company.
  It stops where ownership splits (no single owner over 50%): the last controller is the group.
- Related parties: if a lender and a borrower share an owner or director (corporate service
  providers aside), the charge is probably funding within a group or between related people.
- Every company looked up is cached (ch_companies), so shared parents are fetched once.

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
OWNER_HOPS = 4           # majority owners followed this many levels above the lender

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
        "     OR (lp.company_number IS NULL AND lp.fetched_at < ?) "
        "     OR (lp.company_number IS NOT NULL AND lp.owners NOT LIKE '%\"v\": 2%')) "
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
        kind = it.get("kind") or ""
        out.append({
            "name": (it.get("name") or "").strip(),
            "kind": "company" if "corporate" in kind or "legal-person" in kind
                    else "person" if "individual" in kind else "other",
            "control": [_CONTROL.get(c, c.replace("-", " ")) for c in it.get("natures_of_control") or []][:3],
            "registration_number": (ident.get("registration_number") or "").strip() or None,
            "country": (ident.get("country_registered") or ident.get("place_registered") or "").strip() or None,
        })
    return out


def _all_officers(client, number: str) -> list[dict]:
    data = client.get(f"/company/{number}/officers", {"items_per_page": 50}) or {}
    out = []
    for it in data.get("items") or []:
        if it.get("resigned_on"):
            continue
        role = (it.get("officer_role") or "").replace("-", " ")
        out.append({"name": (it.get("name") or "").strip(), "role": role,
                    "kind": "company" if role.startswith("corporate") else "person"})
    return out


def _corporate(officers: list[dict]) -> list[dict]:
    """Corporate directors and LLP members name the firm that runs a vehicle; individual
    directors are kept for the related-party check but not listed."""
    return [{"name": o["name"], "role": o["role"]} for o in officers
            if o["kind"] == "company" or o["role"].startswith("llp")][:8]


def _uk_number(owner: dict) -> str | None:
    num = (owner.get("registration_number") or "").replace(" ", "").upper()
    country = (owner.get("country") or "").lower()
    uk = not country or any(w in country for w in ("england", "wales", "scotland", "united kingdom", "northern ireland")) \
        or country.strip() in ("uk", "gb", "great britain")
    return num.zfill(8) if uk and re.fullmatch(r"[A-Z]{0,2}\d{5,8}", num) else None


# Firms that act as director, secretary or nominee for thousands of unrelated companies.
# Sharing one of them says nothing about who is related to whom.
_SERVICE_PROVIDER = re.compile(
    r"\b(SECRETARIES|SECRETARIAL|NOMINEES?|REGISTRARS?|CORPORATE SERVICES|COMPANY SERVICES|FORMATIONS?|"
    r"VISTRA|INTERTRUST|SANNE|OCORIAN|TMF|CITCO|ALTER DOMUS|APEX|LAW DEBENTURE|CSC|JTC|AZTEC|IQ EQ|"
    r"WILMINGTON|GLAS|CAPITA|EQUINITI|COMPUTERSHARE|LINK)\b")
def controls(owner: dict) -> bool:
    """A majority owner: more than half the shares or votes, or the right to appoint the board."""
    return any(c.startswith(("75-100%", "50-75%")) or c == "appoints the directors" for c in owner.get("control") or [])


# --------------------------------------------------------------------------- cached records

def record(client, conn, number: str, max_age_days: int = REFRESH_DAYS) -> dict:
    """Owners and officers of any company, cached in ch_companies so a parent shared by many
    lending vehicles, or a borrower seen twice, is only fetched once."""
    if conn is not None:
        row = conn.execute("SELECT * FROM ch_companies WHERE company_number = ?", (number,)).fetchone()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        if row and row["fetched_at"] >= cutoff:
            return {"number": number, "owners": json.loads(row["owners"]), "officers": json.loads(row["officers"])}
    rec = {"number": number, "owners": _owners(client, number), "officers": _all_officers(client, number)}
    if conn is not None:
        conn.execute("INSERT OR REPLACE INTO ch_companies VALUES (?,?,?,?)",
                     (number, json.dumps(rec["owners"]), json.dumps(rec["officers"]), now_iso()))
    return rec


def climb(client, conn, number: str, owners: list[dict], depth: int = OWNER_HOPS) -> tuple[list[dict], dict | None, str | None]:
    """Follow majority owners up from a company, up to `depth` levels above it, while the
    controller is a UK company. Stops where ownership splits (no single controller), at a person,
    or at a company registered abroad (Companies House has no record of it).
    Returns (levels above the company, the top controlling company, a controlling person)."""
    levels, seen = [], {number}
    group, person = None, None
    current = owners
    for _ in range(depth + 1):
        ctrl = next((o for o in current if controls(o)), None)
        if not ctrl:
            break
        if ctrl["kind"] == "person":
            person = ctrl["name"]
            break
        if _SERVICE_PROVIDER.search(ctrl["name"].upper()):
            # A corporate services firm holding the shares for someone else (typical of fund and
            # securitisation vehicles): not a lending group, and it would wrongly merge its clients.
            person = None
            group = {"name": ctrl["name"], "number": None, "nominee": True}
            break
        nxt = _uk_number(ctrl)
        group = {"name": ctrl["name"], "number": nxt}
        if not nxt or nxt in seen or len(levels) >= depth:
            break
        seen.add(nxt)
        up = record(client, conn, nxt)["owners"]
        levels.append({"of": ctrl["name"], "number": nxt, "owners": up,
                       "split": bool(up) and not any(controls(o) for o in up)})
        current = up
    return levels, group, person


def lookup(client, display: str, norm: str, conn=None) -> dict:
    """Find a lender's company and who controls it. Returns the row stored in lender_profiles."""
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
    rec = record(client, conn, number)
    levels, group, person = climb(client, conn, number, rec["owners"])
    return {
        "name_norm": norm, "company_number": number, "company_name": prof.get("company_name"),
        "company_status": prof.get("company_status"), "company_type": prof.get("type"),
        "sic_codes": prof.get("sic_codes") or [], "date_of_creation": prof.get("date_of_creation"),
        "owners": rec["owners"], "owner_chain": levels, "group": group, "controller_person": person,
        "officers": _corporate(rec["officers"]), "matched_by": matched_by,
    }


def save(conn, row: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO lender_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (row["name_norm"], row.get("company_number"), row.get("company_name"), row.get("company_status"),
         row.get("company_type"), json.dumps(row.get("sic_codes") or []), row.get("date_of_creation"),
         json.dumps({"v": 2, "direct": row.get("owners") or [], "chain": row.get("owner_chain") or [],
                     "group": row.get("group"), "person": row.get("controller_person")}),
         json.dumps(row.get("officers") or []), row.get("matched_by"), now_iso()))


# --------------------------------------------------------------------------- related parties

_TITLES = {"MR", "MRS", "MS", "MISS", "DR", "SIR", "DAME", "LORD", "LADY", "PROF", "PROFESSOR", "REV"}


def person_key(name: str) -> str | None:
    """'Mr Benny Perlberger' and 'PERLBERGER, Benny Moshe' -> 'BENNY PERLBERGER'."""
    s = re.sub(r"[^A-Z, ]", " ", (name or "").upper())
    if "," in s:
        surname, fore = s.split(",", 1)
        fore = [t for t in fore.split() if t not in _TITLES]
        last = surname.split()[-1] if surname.split() else ""
        first = fore[0] if fore else ""
    else:
        toks = [t for t in s.split() if t not in _TITLES]
        first, last = (toks[0], toks[-1]) if len(toks) >= 2 else ("", "")
    return f"{first} {last}" if first and last else None


def _keys(entities: list[dict]) -> dict[str, str]:
    """Matchable keys -> display name, for owners and officers. Service providers are left out."""
    out = {}
    for e in entities:
        name = e.get("name") or ""
        if e.get("kind") == "person":
            k = person_key(name)
            if k:
                out[f"P:{k}"] = name
        elif not _SERVICE_PROVIDER.search(name.upper()):
            n = normalise_name(name)
            if n:
                out[f"C:{n}"] = name
            num = _uk_number(e) if e.get("registration_number") else None
            if num:
                out[f"N:{num}"] = name
    return out


def related(client, conn, norm: str, max_borrowers: int = 10) -> dict:
    """Do this lender and its borrowers share an owner or a director? If so the 'loan' is likely
    funding inside a group or between related people, not market lending."""
    lp = conn.execute("SELECT * FROM lender_profiles WHERE name_norm = ?", (norm,)).fetchone()
    if not lp or not lp["company_number"]:
        return {"checked": 0, "linked": []}
    own = json.loads(lp["owners"] or "{}")
    lender = record(client, conn, lp["company_number"])
    side = list(lender["owners"]) + list(lender["officers"])
    for lvl in own.get("chain") or []:
        side += lvl.get("owners") or []
    lkeys = _keys(side)
    lkeys[f"C:{normalise_name(lp['company_name'] or '')}"] = lp["company_name"]
    lkeys[f"N:{lp['company_number']}"] = lp["company_name"]
    if own.get("group") and own["group"].get("number"):
        lkeys[f"N:{own['group']['number']}"] = own["group"]["name"]
    borrowers = conn.execute(
        "SELECT DISTINCT p.company_number, co.name FROM charge_persons p LEFT JOIN companies co "
        "ON co.company_number = p.company_number WHERE p.name_norm = ? LIMIT ?", (norm, max_borrowers)).fetchall()
    linked, checked = [], 0
    for b in borrowers:
        if client.out_of_time():
            break
        if b["company_number"] == lp["company_number"]:
            continue
        brec = record(client, conn, b["company_number"])
        checked += 1
        bkeys = _keys(list(brec["owners"]) + list(brec["officers"]))
        shared = sorted({lkeys[k] for k in set(lkeys) & set(bkeys)})
        if shared:
            linked.append({"borrower": b["name"] or b["company_number"], "number": b["company_number"], "shared": shared[:4]})
    out = {"checked": checked, "linked": linked}
    conn.execute("INSERT OR REPLACE INTO lender_links VALUES (?,?,?,?)", (norm, checked, json.dumps(linked), now_iso()))
    return out


# --------------------------------------------------------------------------- pipeline step

def fetch(conn, client, limit: int = 80, links_limit: int = 30, reserve_seconds: float = 150) -> dict:
    """Look up unclear lenders, then check the looked-up ones against their borrowers. Stops early
    to leave `reserve_seconds` of the run's Companies House budget for the steps after it."""
    def low():
        left = client.time_left() if hasattr(client, "time_left") else None
        return client.out_of_time() or (left is not None and left < reserve_seconds)
    n = 0
    for r in candidates(conn, limit):
        if low():
            break
        save(conn, lookup(client, r["display"], r["name_norm"], conn))
        n += 1
        if n % 10 == 0:
            conn.commit()
    conn.commit()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat(timespec="seconds")
    todo = [r["name_norm"] for r in conn.execute(
        "SELECT lp.name_norm FROM lender_profiles lp LEFT JOIN lender_links ll ON ll.name_norm = lp.name_norm "
        "LEFT JOIN lender_classifications lc ON lc.name_norm = lp.name_norm "
        "WHERE lp.company_number IS NOT NULL AND (ll.name_norm IS NULL OR ll.fetched_at < ?) "
        "AND (lc.source IS NULL OR lc.source = 'agent') "
        "ORDER BY (lc.review_status = 'needs_review') DESC LIMIT ?", (cutoff, links_limit))]
    m = 0
    for name in todo:
        if low():
            break
        related(client, conn, name)
        m += 1
        conn.commit()
    return {"looked_up": n, "related_checked": m}


# --------------------------------------------------------------------------- for the app and agents

def groups(conn) -> dict[str, dict]:
    """name_norm -> its controlling group {name, number}, for every looked-up lender."""
    out = {}
    for r in conn.execute("SELECT name_norm, owners FROM lender_profiles WHERE company_number IS NOT NULL"):
        own = json.loads(r["owners"] or "{}") or {}
        g = own.get("group")
        if own.get("v") != 2:
            g = None
            for owners_at in [own.get("direct") or []] + [lvl.get("owners") or [] for lvl in own.get("chain") or []]:
                ctrl = next((o for o in owners_at if controls(o)), None)
                if not ctrl or ctrl["kind"] != "company":
                    break
                if _SERVICE_PROVIDER.search(ctrl["name"].upper()):
                    g = {"name": ctrl["name"], "nominee": True}
                    break
                g = {"name": ctrl["name"], "number": _uk_number(ctrl)}
        if g and g.get("name") and not g.get("nominee"):
            out[r["name_norm"]] = g
    return out


def profile(conn, norm: str, group_map: dict | None = None) -> dict | None:
    """The stored lookup for one lender, shaped for the app and the agents' queues.
    Pass `group_map` (from groups()) when calling this for many lenders, to read the table once."""
    r = conn.execute("SELECT * FROM lender_profiles WHERE name_norm = ?", (norm,)).fetchone()
    if not r:
        return None
    if not r["company_number"]:
        return {"matched": False, "checked": r["fetched_at"]}
    own = json.loads(r["owners"] or "{}")
    sic = json.loads(r["sic_codes"] or "[]")
    group = own.get("group")
    if own.get("v") != 2:  # stored before majority-only climbing: re-derive the group from what's stored
        group, person = None, None
        for owners_at in [own.get("direct") or []] + [lvl.get("owners") or [] for lvl in own.get("chain") or []]:
            ctrl = next((o for o in owners_at if controls(o)), None)
            if not ctrl:
                break
            if ctrl["kind"] == "person":
                person = ctrl["name"]
                break
            if _SERVICE_PROVIDER.search(ctrl["name"].upper()):
                group = {"name": ctrl["name"], "number": None, "nominee": True}
                break
            group = {"name": ctrl["name"], "number": _uk_number(ctrl)}
        own["person"] = person
    links = conn.execute("SELECT * FROM lender_links WHERE name_norm = ?", (norm,)).fetchone()
    siblings = []
    if group and group.get("name") and not group.get("nominee"):
        siblings = [n for n, g in (group_map if group_map is not None else groups(conn)).items() if n != norm and
                    ((g.get("number") and g.get("number") == group.get("number")) or g.get("name") == group.get("name"))]
    return {
        "matched": True, "matched_by": r["matched_by"], "checked": r["fetched_at"],
        "company_number": r["company_number"], "company_name": r["company_name"],
        "company_status": r["company_status"], "company_type": r["company_type"],
        "incorporated": r["date_of_creation"],
        "sic": [{"code": c, "label": LENDER_SIC.get(c, "")} for c in sic],
        "owners": own.get("direct") or [], "owner_chain": own.get("chain") or [],
        "officers": json.loads(r["officers"] or "[]"),
        "group": group["name"] if group and not group.get("nominee") else None,
        "group_number": group.get("number") if group and not group.get("nominee") else None,
        "nominee": group["name"] if group and group.get("nominee") else None,
        "controller_person": own.get("person"),
        "same_group": sorted(siblings)[:12],
        "related": ({"checked": links["checked"], "linked": json.loads(links["links"] or "[]")} if links else None),
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

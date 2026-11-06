"""Public-sector supplier tag and the working-capital lead list (a "who", never a "when").

    python -m radar.public_suppliers build AWARDS.csv.gz COMPANIES.csv.gz   # refresh data/public_suppliers.csv.gz
    python -m radar.public_suppliers leads [--out DIR]                       # working-capital list as CSV + Markdown
    python -m radar.public_suppliers status                                  # counts in the tag file

What the 5 Nov 2026 test found (research/contract-wins-test-2026-11-05.md, held-out months 2024 to Oct 2025):
  * Firms that win public contracts of £100k or more took a new charge within 12 months 6.4% of the time,
    against 4.1% for similar random firms (1.6x, range 1.3 to 1.9).
  * Firms with no charge before took a first charge 2.7% of the time against 0.65% (about 4x, range 3.1 to
    5.5): roughly 1 in 37.
  * The win itself does not time the borrowing: firms that first win a year or more later borrow just as
    much (0.88x at 12 months, range 0.74 to 1.09).
So the tag says what kind of firm this is. It is NOT part of the Refinance watch score (switching lender
on an existing loan barely moves with it), and nothing here says "won a contract, call now".

Data: Contracts Finder and Find a Tender award notices published 2022 to 2025 (Open Government Licence
v3.0), matched to Companies House Basic Company Data (1 Nov 2026 snapshot): by the company number in the
notice, otherwise by a name that matches exactly one live company. About 1 in 10 name matches may be
wrong, so every row says how it was matched. The cloud workspace cannot reach those sites; the pull runs
as a GitHub Actions job (branch claude/project-thread-8r1ade), and `build` turns its two files into the
committed tag file.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from . import config
from .contracts import is_prime, load_primes
from .normalise import normalise_name
from .sectors import label, sector_of_code

PATH = config.ROOT / "data" / "public_suppliers.csv.gz"
VERSION = "ps1"
MIN_VALUE = 100_000
TAG = "Public-sector supplier"
TAG_HELP = ("Won at least one public contract notice worth £100k or more (Contracts Finder or Find a Tender, "
            "2022 to 2025). A description of the firm, not a timing signal: it is not part of the refinance score.")
FINDINGS = {
    "all_12m": {"suppliers": 0.064, "similar": 0.041, "ratio": 1.6, "range": [1.3, 1.9]},
    "first_charge_12m": {"suppliers": 0.027, "similar": 0.0065, "ratio": 4.1, "range": [3.1, 5.5], "contacts_per_hit": 37},
    "vs_later_winners_12m": {"ratio": 0.88, "range": [0.74, 1.09]},
    "held_out": "Jan 2024 to Oct 2025",
}
CAVEAT = ("Public-sector suppliers with no charge on record took a first charge within 12 months 2.7% of the time, "
          "against 0.65% for similar firms (about 4x, held-out months Jan 2024 to Oct 2025). That is roughly 1 in 37. "
          "Winning a contract does not tell you when: firms that win a year later borrow just as much. "
          "This list says who may need new lending, not when, and its order is not a tested ranking. "
          "Micro and small companies only: the lift did not hold for medium and larger firms.")
SOURCE_NOTE = ("Contains public sector information licensed under the Open Government Licence v3.0 "
               "(Contracts Finder, Find a Tender, Companies House).")

FIELDS = ("company_number", "name", "match", "notices_100k", "notices", "value_gbp", "largest_gbp",
          "first_notice", "last_notice", "buyers", "top_buyer", "last_buyer", "last_title", "last_url",
          "sic1", "accounts_category", "incorporated", "postcode_district", "category",
          "charges_total", "charges_outstanding")
INT_FIELDS = ("notices_100k", "notices", "value_gbp", "largest_gbp", "buyers", "charges_total", "charges_outstanding")
# Company types that are lendable trading businesses. Charities, guarantee companies, societies and
# overseas entities stay tagged but are left off the working-capital list.
LEAD_CATEGORIES = {"Private Limited Company", "Public Limited Company", "Limited Liability Partnership",
                   "Community Interest Company", "Private Unlimited Company", "Private Unlimited"}
# The test's lift held for micro and small accounts (1.9x) but not medium, full or group accounts (1.1x,
# range 0.7 to 1.6), so only those two bands go on the working-capital list.
LEAD_SIZES = ("micro", "small")
SIZE_LABELS = {"micro": "Micro", "small": "Small", "medium": "Medium", "large": "Large or group", "other": "Other or not filed"}


def size_band(cat: str) -> str:
    c = (cat or "").upper()
    if "MICRO" in c:
        return "micro"
    if "SMALL" in c or "TOTAL EXEMPTION" in c or "UNAUDITED" in c or "AUDIT EXEMPTION" in c:
        return "small"
    if "MEDIUM" in c:
        return "medium"
    if c in ("FULL", "GROUP"):
        return "large"
    return "other"


def _district(pc: str) -> str:
    pc = (pc or "").upper().replace(" ", "")
    return pc[:-3] if len(pc) > 4 else pc


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ build (the same match as the test)

def build(awards_rows, company_rows) -> list[dict]:
    """Aggregate award notices per matched company. Same matching rules as the 5 Nov test."""
    cands = list(company_rows)
    by_num = {r["company_number"]: r for r in cands}
    by_name = defaultdict(list)
    for r in cands:
        by_name[r["name_norm"]].append(r)
    seen = set()
    per: dict[str, dict] = {}
    for a in awards_rows:
        k = (a["ocid"], normalise_name(a["supplier_name"]), a["value"])
        if k in seen:
            continue
        seen.add(k)
        num, how = None, ""
        coh = [i[7:].strip().upper().zfill(8) for i in (a.get("supplier_ids") or "").split("|") if i.startswith("GB-COH:")]
        for n in coh:
            if n in by_num:
                num, how = n, "number"
        if not num and not coh:
            c = by_name.get(normalise_name(a["supplier_name"])) or []
            if len(c) == 1:
                num, how = c[0]["company_number"], "name"
            elif len(c) > 1:
                same = [x for x in c if a.get("postcode") and x["postcode"] == a["postcode"]]
                if len(same) == 1:
                    num, how = same[0]["company_number"], "name_postcode"
        if not num:
            continue
        p = per.setdefault(num, {"names": Counter(), "how": Counter(), "notices": set(), "big": set(), "value": 0.0,
                                 "largest": 0.0, "dates": [], "buyers": Counter(), "last": None})
        p["names"][a["supplier_name"].strip()] += 1
        p["how"][how] += 1
        v = _num(a["value"])
        p["notices"].add(a["ocid"])
        if v >= MIN_VALUE:
            p["big"].add(a["ocid"])
        p["value"] += v
        p["largest"] = max(p["largest"], v)
        d = (a.get("published") or "")[:10]
        if d:
            p["dates"].append(d)
            if p["last"] is None or d >= p["last"][0]:
                p["last"] = (d, a.get("buyer") or "", a.get("title") or "", a.get("notice_url") or "")
        if a.get("buyer"):
            p["buyers"][a["buyer"].strip()] += 1
    out = []
    for num, p in per.items():
        if not p["big"]:
            continue   # the tag needs a notice of £100k or more, as in the test
        c = by_num[num]
        how = "number" if p["how"]["number"] else ("name_postcode" if p["how"]["name_postcode"] else "name")
        last = p["last"] or ("", "", "", "")
        out.append({
            "company_number": num, "name": p["names"].most_common(1)[0][0], "match": how,
            "notices_100k": len(p["big"]), "notices": len(p["notices"]), "value_gbp": round(p["value"]),
            "largest_gbp": round(p["largest"]), "first_notice": min(p["dates"], default=""), "last_notice": last[0],
            "buyers": len(p["buyers"]), "top_buyer": p["buyers"].most_common(1)[0][0] if p["buyers"] else "",
            "last_buyer": last[1], "last_title": last[2][:160], "last_url": last[3],
            "sic1": c.get("sic1", ""), "accounts_category": c.get("accounts_category", ""),
            "incorporated": c.get("incorporated", ""), "postcode_district": _district(c.get("postcode", "")),
            "category": c.get("category", ""), "charges_total": int(_num(c.get("charges_total"))),
            "charges_outstanding": int(_num(c.get("charges_outstanding"))),
        })
    out.sort(key=lambda r: r["company_number"])
    return out


def write(rows, path=None) -> Path:
    path = Path(path or PATH)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_bytes(gzip.compress(buf.getvalue().encode("utf-8"), mtime=0))
    return path


_CACHE: dict = {}


def load(path=None) -> dict[str, dict]:
    """company_number -> row. Empty when the file is missing (the tag then simply never shows)."""
    path = Path(path or PATH)
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return {}
    if _CACHE.get("key") != key:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            rows = {}
            for r in csv.DictReader(fh):
                for f in INT_FIELDS:
                    r[f] = int(_num(r.get(f)))
                rows[r["company_number"]] = r
        _CACHE.update(key=key, rows=rows)
    return _CACHE["rows"]


def tag(number: str, suppliers: dict | None = None) -> dict | None:
    s = (load() if suppliers is None else suppliers).get((number or "").strip().upper())
    if not s:
        return None
    return {"label": TAG, "help": TAG_HELP, "notices_100k": s["notices_100k"], "last_notice": s["last_notice"],
            "top_buyer": s["top_buyer"], "match": s["match"]}


# ------------------------------------------------------------------ working-capital lead list

def leads(suppliers: dict, companies: dict | None = None, today: date | None = None, primes=None) -> list[dict]:
    """Suppliers with no charge on record: candidates for invoice finance and working-capital lenders.

    "No charge" means none in the Companies House company data (1 Nov 2026) and none in the radar's
    own register since. Micro and small accounts only (see LEAD_SIZES). Ordered by most recent notice, then number of £100k+ notices; that order is a
    convenience, not a tested ranking.
    """
    today = today or date.today()
    primes = load_primes() if primes is None else primes
    companies = companies or {}
    out = []
    for s in suppliers.values():
        if s["charges_total"] or s["category"] not in LEAD_CATEGORIES or is_prime(s["name"], primes):
            continue
        band = size_band(s["accounts_category"])
        if band not in LEAD_SIZES:
            continue
        co = companies.get(s["company_number"])
        if co is not None and co.charges:
            continue   # the radar has seen a charge since the company data snapshot
        try:
            age = round((today - date.fromisoformat(s["incorporated"])).days / 365.25, 1)
        except ValueError:
            age = None
        out.append({
            "company_number": s["company_number"], "company_name": s["name"], "sector": label(sector_of_code(s["sic1"])),
            "size": SIZE_LABELS[band], "size_band": band, "age_years": age, "postcode_district": s["postcode_district"],
            "notices_100k": s["notices_100k"], "notices": s["notices"], "value_gbp": s["value_gbp"],
            "largest_gbp": s["largest_gbp"], "first_notice": s["first_notice"], "last_notice": s["last_notice"],
            "buyers": s["buyers"], "top_buyer": s["top_buyer"], "last_buyer": s["last_buyer"], "last_title": s["last_title"],
            "notice_url": s["last_url"], "match": s["match"],
            "match_note": "Company number in the notice" if s["match"] == "number" else "Matched by name: check before contacting",
            "why": _why(s, band),
            "filing_url": f"https://find-and-update.company-information.service.gov.uk/company/{s['company_number']}",
        })
    out.sort(key=lambda r: (r["last_notice"], r["notices_100k"], r["company_number"]), reverse=True)
    return out


def _why(s: dict, band: str) -> str:
    n = s["notices_100k"]
    bits = [f"{n} public notice{'s' if n != 1 else ''} of £100k or more"
            + (f", latest {s['last_notice']} from {s['last_buyer']}" if s["last_buyer"] else f", latest {s['last_notice']}")]
    if s["buyers"] > 1:
        bits.append(f"{s['buyers']} different buyers")
    bits.append("no charge on record")
    return "; ".join(bits) + "."


def summary(rows: list[dict], suppliers: dict) -> dict:
    by_size = Counter(r["size_band"] for r in rows)
    by_sector = Counter(r["sector"] for r in rows)
    return {"version": VERSION, "tag": TAG, "tag_help": TAG_HELP, "caveat": CAVEAT, "source": SOURCE_NOTE,
            "findings": FINDINGS, "tagged": len(suppliers), "leads": len(rows),
            "by_number": sum(1 for r in rows if r["match"] == "number"),
            "sizes": [{"key": k, "label": SIZE_LABELS[k], "n": by_size[k]} for k in SIZE_LABELS if by_size[k]],
            "sectors": by_sector.most_common(12)}


def render_md(rows: list[dict], info: dict, today: date, top: int = 200) -> str:
    L = ["# Working-capital leads: public-sector suppliers with no charge on record", "",
         f"_Built {today}. {info['leads']:,} companies ({info['by_number']:,} matched by company number). "
         f"Tag {VERSION}._", "", info["caveat"], "",
         "| Company | Sector | Size | £100k+ notices | Latest notice | Buyer | Match |", "|---|---|---|---|---|---|---|"]
    for r in rows[:top]:
        L.append(f"| {r['company_name']} ({r['company_number']}) | {r['sector']} | {r['size']} | {r['notices_100k']} | "
                 f"{r['last_notice']} | {r['last_buyer']} | {'number' if r['match'] == 'number' else 'name, check'} |")
    L += ["", info["source"], ""]
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.public_suppliers")
    p.add_argument("cmd", choices=("build", "leads", "status"))
    p.add_argument("awards", nargs="?", help="build: awards.csv.gz from the contracts pull")
    p.add_argument("companies", nargs="?", help="build: bcd_candidates.csv.gz from the contracts pull")
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "working_capital"))
    a = p.parse_args(argv)
    if a.cmd == "build":
        if not (a.awards and a.companies):
            p.error("build needs AWARDS and COMPANIES files")
        with gzip.open(a.awards, "rt", encoding="utf-8") as fa, gzip.open(a.companies, "rt", encoding="utf-8") as fc:
            rows = build(csv.DictReader(fa), csv.DictReader(fc))
        print(f"{len(rows):,} tagged companies -> {write(rows)}")
        return
    sup = load()
    if a.cmd == "status":
        print(f"{len(sup):,} public-sector suppliers tagged ({sum(1 for s in sup.values() if s['match'] == 'number'):,} by number)")
        return
    companies = {}
    try:
        from .charge_history import load_companies
        from .db import connect
        companies = load_companies(connect())
    except Exception as e:  # no database on this PC yet: the company-data snapshot alone still works
        print("Radar database not read:", e)
    today = date.today()
    rows = leads(sup, companies, today)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cols = [k for k in rows[0]] if rows else []
    with open(out / "leads.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    (out / "leads.md").write_text(render_md(rows, summary(rows, sup), today), encoding="utf-8")
    print(f"{len(rows):,} working-capital leads -> {out}/leads.csv")


if __name__ == "__main__":
    main()

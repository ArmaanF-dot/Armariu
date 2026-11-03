"""Module F: contract-win trigger, done by hand first.

    python -m radar.contracts leads awards.csv [--out DIR]

Input is a CSV you export by hand from Contracts Finder or Find a Tender award notices (the plan says
to verify the current endpoints and fields first; nothing here calls them). Columns used:
    notice_id, notice_url, supplier_name, supplier_company_number (optional), value_gbp (optional),
    award_date (optional), buyer (optional), title (optional)

Steps: match winners to Companies House records in the radar database (number first, otherwise strict
name match flagged for hand confirmation); drop large primes and old/large groups; enrich with
outstanding charges, existing lender and recent charge activity; write leads.csv and leads.md with a
one-line, fact-only rationale. A lead is published only when the match is by number or you have set
confirmed=yes in leads.csv. Nothing is automated, and no amounts are invented.
Kill signal (from the plan): lenders say they source through sponsors and brokers, not contract notices.
"""
from __future__ import annotations

import argparse
import csv
import io
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from . import config
from .aliases import Resolver
from .charge_history import load_companies
from .normalise import normalise_name

PRIMES_CSV = config.ROOT / "data" / "large_primes.csv"
NAME_THRESHOLD = 0.93
MAX_AGE_YEARS = 20
FIELDS = ("notice_id", "notice_url", "supplier_name", "match_method", "needs_confirmation", "confirmed",
          "company_number", "company_name", "company_age_years", "sector", "outstanding_charges", "existing_lenders",
          "last_charge_filed", "charges_last_12m", "value_gbp", "award_date", "buyer", "rationale")


def load_primes(path=None) -> list[str]:
    path = Path(path or PRIMES_CSV)
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return [normalise_name(r["pattern"]) for r in csv.DictReader(io.StringIO("\n".join(lines)))]


def is_prime(name: str, primes: list[str]) -> bool:
    padded = f" {normalise_name(name)} "
    return any(f" {p} " in padded for p in primes)


def match(supplier: str, number: str, companies: dict, by_name: dict) -> tuple[object, str, bool]:
    """(company or None, method, needs_confirmation). Number first; names are never trusted unconfirmed."""
    number = (number or "").strip().upper().zfill(8) if (number or "").strip().isdigit() else (number or "").strip().upper()
    if number and number in companies:
        return companies[number], "company_number", False
    key = normalise_name(supplier)
    if key in by_name and len(by_name[key]) == 1:
        return by_name[key][0], "name_exact", True
    best = None
    for k in sorted(by_name):
        if len(by_name[k]) != 1:
            continue
        score = SequenceMatcher(None, key, k).ratio()
        if score >= NAME_THRESHOLD and (best is None or score > best[0]):
            best = (score, by_name[k][0])
    if best:
        return best[1], f"name_fuzzy_{best[0]:.2f}", True
    return None, "unmatched", True


def build_leads(rows: list[dict], companies: dict, today: date, primes: list[str], resolver_lenders=None) -> list[dict]:
    by_name: dict[str, list] = {}
    for co in companies.values():
        by_name.setdefault(normalise_name(co.name), []).append(co)
    out = []
    for r in rows:
        name = (r.get("supplier_name") or "").strip()
        if not name or is_prime(name, primes):
            continue
        co, method, needs = match(name, r.get("supplier_company_number", ""), companies, by_name)
        if co is None:
            out.append(_row(r, None, method, needs, today))
            continue
        age = _age(co, today)
        if age is not None and age > MAX_AGE_YEARS:
            continue   # old company: more likely a large group than a small supplier
        out.append(_row(r, co, method, needs, today, age))
    return out


def _age(co, today):
    try:
        return round((today - date.fromisoformat(co.created)).days / 365.25, 1)
    except (TypeError, ValueError):
        return None


def _row(r, co, method, needs, today, age=None) -> dict:
    base = {"notice_id": r.get("notice_id", ""), "notice_url": r.get("notice_url", ""), "supplier_name": r.get("supplier_name", ""),
            "match_method": method, "needs_confirmation": "yes" if needs else "no", "confirmed": "",
            "value_gbp": r.get("value_gbp", ""), "award_date": r.get("award_date", ""), "buyer": r.get("buyer", "")}
    if co is None:
        return {**{f: "" for f in FIELDS}, **base, "rationale": "No Companies House match in the radar database. Match by hand before using."}
    outstanding = [c for c in co.charges if c.status == "outstanding"]
    lenders = sorted({n for c in outstanding for n in c.lender_names})
    cutoff = today.replace(year=today.year - 1).isoformat()
    recent = [c for c in co.charges if c.filed and c.filed >= cutoff]
    last = max((c.filed for c in co.charges if c.filed), default="")
    facts = [f"{co.name} won a notice from {r.get('buyer') or 'a public buyer'}"
             + (f" valued at £{r['value_gbp']}" if r.get("value_gbp") else "") + ".",
             f"It has {len(outstanding)} outstanding charge{'s' if len(outstanding) != 1 else ''}"
             + (f" (lenders: {', '.join(lenders)})" if lenders else "") + f" and {len(recent)} filed in the last 12 months."]
    if not co.charges:
        facts[1] = "It has no charge on record in the radar database."
    return {**base, "company_number": co.number, "company_name": co.name, "company_age_years": "" if age is None else age,
            "sector": co.sector, "outstanding_charges": len(outstanding), "existing_lenders": "; ".join(lenders),
            "last_charge_filed": last, "charges_last_12m": len(recent), "rationale": " ".join(facts)}


def publishable(lead: dict) -> bool:
    return lead["match_method"] == "company_number" or lead.get("confirmed", "").lower() == "yes"


def render_md(leads: list[dict], run_date: date) -> str:
    ok = [l for l in leads if publishable(l) and l["company_number"]]
    L = ["# Contract-win leads (manual sample)", "",
         f"_Run date {run_date}. {len(ok)} publishable of {len(leads)} candidates. Matches by name stay out until you set confirmed=yes._", ""]
    for l in ok:
        L.append(f"- **{l['company_name']}** ({l['company_number']}), {l['sector']}. {l['rationale']} [notice]({l['notice_url']})")
    L += ["", "Kill signal: if lenders say they source through sponsors and brokers, not contract notices, or that tickets are too small, drop Module F.", ""]
    return "\n".join(L)


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.contracts")
    p.add_argument("cmd", choices=("leads",)); p.add_argument("awards")
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "contracts"))
    a = p.parse_args(argv)
    rows = list(csv.DictReader(open(a.awards, encoding="utf-8-sig")))
    today = date.today()
    leads = build_leads(rows, load_companies(connect()), today, load_primes())
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    with open(out / "leads.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(leads)
    (out / "leads.md").write_text(render_md(leads, today), encoding="utf-8")
    print(f"{len(leads)} candidate leads -> {out}/leads.csv (set confirmed=yes on name matches you checked)")


if __name__ == "__main__":
    main()

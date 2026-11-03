"""One merged view of every charge we know of, with lenders resolved to canonical entities.

Shared by Modules B to E. Sources are merged by (company, charge id): `borrower_history` (full REST
history, older charges included) first, then `charges` (what the stream delivered), which wins.
Dates used are FILING dates (`delivered_on`): the day the charge became public, never the day it was
created, so nothing here looks ahead.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from . import config, sectors
from .aliases import Resolved, Resolver

PRIVATE_INDIVIDUAL = "A private individual"


@dataclass
class Charge:
    company_number: str
    charge_id: str
    filed: str | None            # delivered_on
    created: str | None
    satisfied: str | None
    status: str | None
    lenders: list[tuple[str, str, str]] = field(default_factory=list)   # (canonical, key, class)
    floating_all: bool = False
    negative_pledge: bool = False
    on_register: bool = False

    @property
    def lender_keys(self) -> frozenset:
        """Who counts as the lender: real lenders if named, else the agents."""
        real = {k for _, k, c in self.lenders if c != "trustee_agent"}
        return frozenset(real or {k for _, k, _ in self.lenders})

    @property
    def lender_names(self) -> list[str]:
        real = sorted({n for n, _, c in self.lenders if c != "trustee_agent"})
        return real or sorted({n for n, _, _ in self.lenders})

    @property
    def has_agent(self) -> bool:
        return any(c == "trustee_agent" for _, _, c in self.lenders)

    @property
    def url(self) -> str:
        return config.FILING_URL.format(company=self.company_number, charge_id=self.charge_id)


@dataclass
class Company:
    number: str
    name: str
    sic_codes: list[str]
    created: str | None
    status: str | None
    fetched: bool                 # full history fetched, so "no earlier charge" can be trusted
    charges: list[Charge]

    @property
    def sector(self) -> str:
        return sectors.label(sectors.sectors_of(self.sic_codes)[0]) if self.sic_codes else "Sector not known yet"

    @property
    def sic_main(self) -> str:
        return self.sic_codes[0] if self.sic_codes else ""

    def age_band(self, on: str) -> str:
        try:
            years = (date.fromisoformat(on) - date.fromisoformat(self.created)).days / 365.25
        except (TypeError, ValueError):
            return "unknown"
        return "<2y" if years < 2 else "2-5y" if years < 5 else "5-10y" if years < 10 else "10y+"


def _json_list(s):
    try:
        v = json.loads(s) if s else []
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def load_companies(conn, resolver: Resolver | None = None) -> dict[str, Company]:
    resolver = resolver or Resolver(conn)
    meta = {}
    for r in conn.execute("SELECT company_number, name, sic_codes, date_of_creation, company_status FROM companies"):
        meta[r["company_number"]] = (r["name"] or r["company_number"], _json_list(r["sic_codes"]),
                                     r["date_of_creation"], r["company_status"])
    fetched = {r[0] for r in conn.execute("SELECT company_number FROM borrower_history_fetched")}
    charges: dict[tuple, Charge] = {}

    def lenders_of(names):
        out, seen = [], set()
        for n in names:
            r = resolver.resolve(n)
            if r.cls == "individual":   # never carry an individual's name into any output
                r = Resolved(PRIVATE_INDIVIDUAL, "PRIVATE INDIVIDUAL", "individual", r.how, r.category)
            if (r.key, r.cls) not in seen:
                seen.add((r.key, r.cls))
                out.append((r.canonical, r.key, r.cls))
        return sorted(out)

    for r in conn.execute("SELECT * FROM borrower_history"):
        charges[(r["company_number"], r["charge_id"])] = Charge(
            r["company_number"], r["charge_id"], (r["delivered_on"] or None), (r["created_on"] or None),
            (r["satisfied_on"] or None), r["status"], lenders_of(_json_list(r["persons"])))
    persons: dict[tuple, list[str]] = {}
    for r in conn.execute("SELECT company_number, charge_id, name_raw FROM charge_persons ORDER BY name_raw"):
        persons.setdefault((r["company_number"], r["charge_id"]), []).append(r["name_raw"])
    for r in conn.execute("SELECT * FROM charges WHERE deleted = 0"):
        key = (r["company_number"], r["charge_id"])
        names = persons.get(key)
        prev = charges.get(key)
        lenders = lenders_of(names) if names else (prev.lenders if prev else [])
        charges[key] = Charge(r["company_number"], r["charge_id"], r["delivered_on"] or None,
                              r["created_on"] or None, r["satisfied_on"] or None, r["status"], lenders,
                              bool(r["floating_covers_all"]), bool(r["contains_negative_pledge"]), True)
    by_co: dict[str, list[Charge]] = {}
    for c in charges.values():
        by_co.setdefault(c.company_number, []).append(c)
    out = {}
    for num, cs in by_co.items():
        cs.sort(key=lambda c: (c.filed or "", c.charge_id))
        name, sic, created, status = meta.get(num, (num, [], None, None))
        out[num] = Company(num, name, sic, created, status, num in fetched, cs)
    return out

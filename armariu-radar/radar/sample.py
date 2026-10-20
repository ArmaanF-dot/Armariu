"""Synthetic Companies House stream events for tests and the offline demo.

All borrowers, company numbers and most lender names here are INVENTED. A few real bank and
security-agent names are used only so the seed rules in lenders.csv have something to match.
This is not real filing data.
"""
from __future__ import annotations

import json
from datetime import date, timedelta


def _charge(num, cid, persons, delivered, created=None, status="outstanding", satisfied=None,
            floating_all=True, kind="A registered charge"):
    return {
        "charge_number": 1,
        "charge_code": f"{num}{cid[-4:]}",
        "status": status,
        "created_on": (created or delivered),
        "delivered_on": delivered,
        "satisfied_on": satisfied,
        "classification": {"type": "charge-description", "description": kind},
        "persons_entitled": [{"name": p} for p in persons],
        "particulars": {"type": "brief-description", "description": "All assets of the company.",
                        "contains_fixed_charge": True, "contains_floating_charge": True,
                        "floating_charge_covers_all": floating_all, "contains_negative_pledge": True},
        "secured_details": {"type": "amount-secured", "description": "All monies due or to become due"},
        "links": {"self": f"/company/{num}/charges/{cid}"},
    }


def _event(num, cid, data, tp, etype="changed"):
    return {
        "resource_kind": "company-charges",
        "resource_uri": f"/company/{num}/charges/{cid}",
        "resource_id": cid,
        "data": data,
        "event": {"timepoint": tp, "type": etype, "published_at": "2026-10-01T09:00:00"},
    }


def sample_events(week_start: date) -> list[dict]:
    """Events whose filings fall inside the 7 days from week_start, plus one the week after."""
    d = lambda n: (week_start + timedelta(days=n)).isoformat()  # noqa: E731
    ev = [
        _event("99000001", "chg0000000001", _charge("99000001", "chg0000000001",
               ["HSBC UK Bank PLC"], d(0)), 1001),
        _event("99000002", "chg0000000002", _charge("99000002", "chg0000000002",
               ["Northgate Credit Partners II SCSp", "GLAS Trust Corporation Limited"], d(1)), 1002),
        _event("99000003", "chg0000000003", _charge("99000003", "chg0000000003",
               ["GLAS Trust Corporation Limited"], d(1)), 1003),
        _event("99000004", "chg0000000004", _charge("99000004", "chg0000000004",
               ["Fernhill Private Debt Fund LP"], d(2)), 1004),
        _event("99000005", "chg0000000005", _charge("99000005", "chg0000000005",
               ["Kestrel Invoice Finance Limited"], d(3), floating_all=False), 1005),
        _event("99000006", "chg0000000006", _charge("99000006", "chg0000000006",
               ["John Example"], d(3)), 1006),
        _event("99000007", "chg0000000007", _charge("99000007", "chg0000000007",
               ["Brightwater Capital Holdings Limited"], d(4)), 1007),
        # A direct-lender charge from years ago, satisfied this week (possible refinancing).
        _event("99000008", "chg0000000008", _charge("99000008", "chg0000000008",
               ["Fernhill Private Debt Fund LP"], "2023-03-01", status="fully-satisfied",
               satisfied=d(5)), 1008),
        # Filed then removed (a deletion event).
        _event("99000009", "chg0000000009", _charge("99000009", "chg0000000009",
               ["Northgate Credit Partners II SCSp"], d(5)), 1009),
        _event("99000009", "chg0000000009", {}, 1010, etype="deleted"),
        # Next week: should land in the following digest, not this one.
        _event("99000010", "chg0000000010", _charge("99000010", "chg0000000010",
               ["Northgate Credit Partners II SCSp"], d(8)), 1011),
    ]
    return ev


def sample_agent_results() -> list[str]:
    """What a classifier agent run might write, including one invalid line."""
    rows = [
        {"name_norm": "NORTHGATE CREDIT PARTNERS II SCSP", "category": "direct_lender",
         "parent_firm": "Northgate Capital", "confidence": 0.93,
         "reason": "Luxembourg fund vehicle named as a credit fund; appears with a security agent"},
        {"name_norm": "JOHN EXAMPLE", "category": "individual", "parent_firm": None,
         "confidence": 0.95, "reason": "A personal name"},
        {"name_norm": "BRIGHTWATER CAPITAL HOLDINGS", "category": "other", "parent_firm": None,
         "confidence": 0.55, "reason": "Holding company; could be intra-group or a family office"},
        {"name_norm": "MADE UP NAME", "category": "direct_lender", "confidence": 0.9,
         "reason": "should be rejected: not in the data"},
        {"name_norm": "JOHN EXAMPLE", "category": "hedge_fund", "confidence": 0.9,
         "reason": "should be rejected: bad category"},
    ]
    return [json.dumps(r) for r in rows]

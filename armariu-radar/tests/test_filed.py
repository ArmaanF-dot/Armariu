"""Tests for the accounts and owner-register clues (radar.accounts, radar.ownership, refi_watch rw2).
Fixtures are synthetic."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import accounts, refi_watch as rw  # noqa: E402
from radar.charge_history import Charge, Company  # noqa: E402

IXBRL = """<html><body>
<xbrli:context id="c1"><xbrli:entity><xbrli:identifier>1</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="c1y"><xbrli:entity><xbrli:identifier>1</xbrli:identifier><xbrli:segment>
  <xbrldi:explicitMember dimension="core:MaturitiesOrExpirationPeriodsDimension">core:WithinOneYear</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="cafter"><xbrli:entity><xbrli:identifier>1</xbrli:identifier><xbrli:segment>
  <xbrldi:explicitMember dimension="core:MaturitiesOrExpirationPeriodsDimension">core:AfterOneYear</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="p1y"><xbrli:entity><xbrli:identifier>1</xbrli:identifier><xbrli:segment>
  <xbrldi:explicitMember dimension="core:MaturitiesOrExpirationPeriodsDimension">core:WithinOneYear</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="pafter"><xbrli:entity><xbrli:identifier>1</xbrli:identifier><xbrli:segment>
  <xbrldi:explicitMember dimension="core:MaturitiesOrExpirationPeriodsDimension">core:AfterOneYear</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>
<ix:nonFraction name="core:Creditors" contextRef="c1y" unitRef="GBP" scale="3" decimals="-3">1,250</ix:nonFraction>
<ix:nonFraction name="core:Creditors" contextRef="cafter" unitRef="GBP" decimals="0">-</ix:nonFraction>
<ix:nonFraction name="core:BankBorrowingsOverdrafts" contextRef="c1y" unitRef="GBP" decimals="0">900,000</ix:nonFraction>
<ix:nonFraction name="core:NetCurrentAssetsLiabilities" contextRef="c1" unitRef="GBP" sign="-" decimals="0">(400,000)</ix:nonFraction>
<ix:nonFraction name="core:TotalAssetsLessCurrentLiabilities" contextRef="c1" unitRef="GBP" decimals="0">2,000,000</ix:nonFraction>
<ix:nonFraction name="core:Creditors" contextRef="p1y" unitRef="GBP" decimals="0">300,000</ix:nonFraction>
<ix:nonFraction name="core:Creditors" contextRef="pafter" unitRef="GBP" decimals="0">850,000</ix:nonFraction>
<ix:nonNumeric name="bus:NameEntityOfficer" contextRef="c1">Jane Example</ix:nonNumeric>
</body></html>"""


def co(number="1", created="2015-01-01"):
    return Company(number, "Test Co", ["68100"], created, "active", True,
                   [Charge(number, "a", "2024-01-01", "2024-01-01", None, "outstanding", [("Lender B", "LENDER B", "bank")])])


class AccountsParseTests(unittest.TestCase):
    def test_reads_maturity_split_scale_sign_and_comparatives(self):
        facts, officers = accounts.parse(IXBRL)
        s = accounts.summarise(facts, officers, "2025-12-31")
        self.assertEqual(s["creditors_1y"], 1_250_000)
        self.assertEqual(s["creditors_after"], 0)
        self.assertEqual(s["loans_1y"], 900_000)
        self.assertEqual(s["net_current"], -400_000)
        self.assertEqual(s["prev_creditors_after"], 850_000)
        self.assertEqual(s["officers"], 1)
        self.assertNotIn("JANE", s["officers_fp"])   # only a fingerprint, never a name

    def test_long_term_debt_falling_due_is_spotted(self):
        facts, officers = accounts.parse(IXBRL)
        self.assertEqual(rw._debt_shift(accounts.summarise(facts, officers, "2025-12-31")),
                         "long-term debt now due or repaid")


class FiledFeatureTests(unittest.TestCase):
    def filing(self, known, bs, **kw):
        base = {k: None for k in accounts.FIELDS[4:-1]}
        base.update({"company_number": "1", "known_from": known, "balance_sheet_date": bs, "officers_fp": ""}, **kw)
        return base

    def test_accounts_are_not_used_before_they_were_public(self):
        f = rw.Filed({"1": [self.filing("2026-03-31", "2025-12-31", loans_1y=500_000.0)]}, {})
        self.assertEqual(rw.filed_features(co(), date(2026, 3, 1), f)["due_1y"], "no recent accounts")
        self.assertEqual(rw.filed_features(co(), date(2026, 4, 1), f)["due_1y"], "all due within a year")

    def test_owner_change_lag_register_start_and_founders(self):
        owners = {"1": [{"notified_on": "2016-04-06", "ceased_on": ""},            # register began: not a change
                        {"notified_on": "2015-01-20", "ceased_on": ""},            # founder
                        {"notified_on": "2026-02-01", "ceased_on": ""}]}           # a real change
        f = rw.Filed({}, owners)
        self.assertEqual(rw.filed_features(co(), date(2026, 2, 15), f)["owner_change"], "no change on record")
        self.assertEqual(rw.filed_features(co(), date(2026, 4, 1), f)["owner_change"], "changed in the last 6 months")

    def test_without_fetched_data_every_clue_is_not_checked(self):
        self.assertEqual(set(rw.filed_features(co(), date(2026, 1, 1), None).values()), {"not checked"})

    def test_live_score_is_rw3(self):
        self.assertEqual(rw.CLUES, rw.CLUES_RW3)
        self.assertEqual(rw.MODEL_VERSION, "rw3")
        self.assertFalse(set(rw.CLUES) & set(rw.CLUES_FILED))


def _ch(cid, filed, sat=None, lender="BANK"):
    return Charge("9", cid, filed, filed, sat, "satisfied" if sat else "outstanding", [(lender, lender, "bank")])


class HistoryClueTests(unittest.TestCase):
    def test_charges_filed_after_the_date_are_ignored(self):
        c = Company("9", "X", [], "2010-01-01", "active", True, [_ch("a", "2026-03-01")])
        self.assertIsNone(rw.features(c, date(2026, 2, 1), rw.lender_volumes({"9": c})))

    def test_last_repaid(self):
        c = Company("9", "X", [], "2010-01-01", "active", True,
                    [_ch("a", "2020-01-01", "2025-12-01"), _ch("b", "2024-01-01")])
        f = rw.features(c, date(2026, 2, 1), rw.lender_volumes({"9": c}))
        self.assertEqual(f["since_sat"], "in the last 6 months")
        self.assertEqual(rw.features(c, date(2025, 11, 1), rw.lender_volumes({"9": c}))["since_sat"], "never")

    def test_lender_rate_uses_only_repayments_known_by_the_date(self):
        # 30 loans of one lender, all 1-2 years old a year before the date; 12 repaid in that year, 6 after it
        cos = {}
        for i in range(30):
            sat = "2025-08-01" if i < 12 else "2026-06-01" if i < 18 else None
            cos[str(i)] = Company(str(i), "X", [], "2010-01-01", "active", True, [_ch(f"c{i}", "2023-06-01", sat, "L")])
        cos["new"] = Company("new", "Y", [], "2010-01-01", "active", True, [_ch("n", "2024-06-01", None, "L")])
        vol = rw.lender_volumes(cos)
        self.assertAlmostEqual(rw.repay_rate(vol, "L", "bank", 580, date(2026, 1, 1)), 12 / 30)
        self.assertEqual(rw.features(cos["new"], date(2026, 1, 1), vol)["lender_rate"], "over 35%")


if __name__ == "__main__":
    unittest.main()


class _FakeCH:
    def __init__(self, pages):
        self.pages = pages

    def get(self, path, params=None):
        return self.pages.get(path)

    def out_of_time(self):
        return False


class DirectorTests(unittest.TestCase):
    def test_pipeline_step_keeps_dates_and_roles_but_no_names(self):
        from radar import ingest
        from radar.db import connect
        conn = connect(":memory:")
        conn.execute("INSERT INTO charges (company_number, charge_id, delivered_on, deleted, first_seen_at) VALUES ('1', 'a', '2026-01-01', 0, 'x')")
        ch = _FakeCH({"/company/1/officers": {"total_results": 2, "items": [
            {"name": "EXAMPLE, Jane", "officer_role": "director", "appointed_on": "2015-01-02",
             "links": {"self": "/company/1/appointments/x"}},
            {"name": "SAMPLE, John", "officer_role": "director", "appointed_on": "2025-11-01",
             "resigned_on": "2026-02-01", "links": {"self": "/company/1/appointments/y"}}]}})
        self.assertEqual(ingest.officers(conn, ch), 1)
        rows = [tuple(r) for r in conn.execute("SELECT * FROM borrower_officers ORDER BY appointed_on")]
        self.assertEqual([(r[2], r[3], r[4]) for r in rows], [("director", "2015-01-02", None), ("director", "2025-11-01", "2026-02-01")])
        self.assertNotIn("Jane", repr(rows))
        d = rw.load_directors(conn)
        f = rw.Filed({}, {}, d)
        self.assertEqual(rw.filed_features(co(), date(2026, 2, 15), f)["director_change"], "changed in the last 6 months")
        self.assertEqual(rw.filed_features(co(), date(2025, 11, 15), f)["director_change"], "no change on record")
        self.assertEqual(rw.filed_features(co("2"), date(2026, 2, 15), f)["director_change"], "not checked")

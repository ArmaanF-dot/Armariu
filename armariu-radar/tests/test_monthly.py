"""Tests for the monthly extract and the lender-group lookup."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar import classify, ingest, monthly  # noqa: E402
from radar.lender_groups import GROUPS, group_lender, load_group_rules  # noqa: E402
from radar.normalise import normalise_name  # noqa: E402
from radar.rules import find_rule, load_rules  # noqa: E402
from radar.sample import sample_events  # noqa: E402
from test_radar import WEEK, WEEK_END, mem_db  # noqa: E402


def grouped(name, category=None, parent=None):
    return group_lender(normalise_name(name), category, parent, load_group_rules())


class LenderGroupTests(unittest.TestCase):
    def test_table_loads_with_valid_groups(self):
        rules = load_group_rules()
        self.assertTrue(rules)
        self.assertTrue(all(r.lender_group in GROUPS for r in rules))

    def test_hsbc_variants_become_one_lender(self):
        a = grouped("HSBC UK Bank PLC", "bank", "HSBC")
        b = grouped("HSBC Bank plc", "bank", "HSBC")
        self.assertEqual((a.canonical, a.group), ("HSBC", "clearing_bank"))
        self.assertEqual((a.canonical, a.key, a.group), (b.canonical, b.key, b.group))

    def test_specific_row_beats_general_row(self):
        g = grouped("HSBC Invoice Finance (UK) Limited", "abl_specialist")
        self.assertEqual((g.canonical, g.group), ("HSBC", "abl_invoice"))
        g = grouped("Lloyds Bank Commercial Finance Limited", "bank")
        self.assertEqual(g.group, "abl_invoice")

    def test_old_names_and_security_agents_map_to_the_bank(self):
        self.assertEqual(grouped("The Royal Bank of Scotland plc", "bank").canonical, "NatWest")
        self.assertEqual(grouped("Lloyds TSB Bank plc", "bank", "TSB").canonical, "Lloyds")
        self.assertEqual(grouped("Barclays Security Trustee Limited", "security_agent").canonical, "Barclays")
        self.assertEqual(grouped("TSB Bank plc", "bank").group, "challenger_bank")

    def test_challengers(self):
        for name in ("Aldermore Bank PLC", "Shawbrook Bank Limited", "OneSavings Bank plc", "Metro Bank PLC"):
            self.assertEqual(grouped(name, "bank").group, "challenger_bank", name)

    def test_category_fallback(self):
        self.assertEqual(grouped("Fernhill Private Debt Fund LP", "direct_lender", "Fernhill").group,
                         "private_credit_direct")
        self.assertEqual(grouped("Kestrel Invoice Finance Limited", "abl_specialist").group, "abl_invoice")
        g = grouped("Some Mortgage Co Limited", "property_lender")
        self.assertEqual((g.group, g.basis), ("other", "name"))
        self.assertEqual(grouped("Mystery Lender", None).group, "other")

    def test_parent_spellings_share_a_key(self):
        a = grouped("Fortys Capital 5", "property_lender", "Fortys Capital Group Limited")
        b = grouped("Fortys Capital 6", "property_lender", "Fortys Capital Group")
        self.assertEqual(a.key, b.key)

    def test_names_not_matched_by_substring(self):
        self.assertEqual(grouped("Hsbcx Holdings", "other").basis, "name")

    def test_lloyds_instrument_rule_no_longer_swallows_collateral_agents(self):
        rules = load_rules()
        self.assertIsNone(find_rule(normalise_name(
            "Sound Point Agency LLC in its capacity as the Collateral Agent for the Lenders "
            "as defined in the instrument"), rules))
        self.assertIsNotNone(find_rule(normalise_name("A trust under the Lloyd's Act 1871"), rules))


class AssetTagTests(unittest.TestCase):
    def test_tags(self):
        self.assertEqual(monthly.asset_tags("Freehold property 12 high street under title number AB123.", None), "property")
        self.assertIn("all_assets", monthly.asset_tags("A fixed and floating charge over all assets.", None))
        self.assertEqual(monthly.asset_tags("All the intellectual property of the company", None), "ip")
        self.assertEqual(monthly.asset_tags("None.", None), "")
        self.assertEqual(monthly.asset_tags("", 1), "all_assets")


class MonthBoundsTests(unittest.TestCase):
    def test_explicit_and_default(self):
        self.assertEqual(monthly.month_bounds("2026-02"), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(monthly.month_bounds("2026-12"), (date(2026, 12, 1), date(2026, 12, 31)))
        self.assertEqual(monthly.month_bounds(None, today=date(2026, 1, 15)),
                         (date(2025, 12, 1), date(2025, 12, 31)))


def loaded():
    conn = mem_db()
    for ev in sample_events(WEEK):
        ingest.process_event(conn, ev)
    conn.commit()
    classify.refresh_rule_matches(conn, load_rules())
    return conn


class ExtractTests(unittest.TestCase):
    def test_rows_cover_mr01_and_mr04(self):
        conn = loaded()
        filings, longs = monthly.build_rows(conn, WEEK, WEEK_END)
        kinds = [f["filing_type"] for f in filings]
        self.assertEqual(kinds.count("MR04"), 1)           # the Fernhill charge satisfied this week
        self.assertEqual(kinds.count("MR01"), 7)           # charges 1-7; #8 is old (MR04 only), #9 deleted, #10 next week
        sat = next(f for f in filings if f["filing_type"] == "MR04")
        self.assertEqual(sat["lender_group"], "private_credit_direct")
        self.assertGreater(sat["charge_age_years"], 3)     # charge from 2023-03-01
        self.assertEqual(sat["charge_delivered_on"], "2023-03-01")
        self.assertNotIn("99000009", {f["company_number"] for f in filings})  # deleted charge excluded
        self.assertNotIn("99000010", {f["company_number"] for f in filings})  # outside the window
        hsbc = next(f for f in filings if f["company_number"] == "99000001")
        self.assertEqual((hsbc["lender"], hsbc["lender_group"]), ("HSBC", "clearing_bank"))
        self.assertTrue(longs)

    def test_agent_is_not_the_primary_lender_when_a_real_lender_is_named(self):
        conn = loaded()
        filings, longs = monthly.build_rows(conn, WEEK, WEEK_END)
        f = next(f for f in filings if f["company_number"] == "99000002")
        self.assertEqual(f["n_chargees"], 2)
        self.assertEqual(f["lender_raw"], "Northgate Credit Partners II SCSp")
        self.assertEqual(f["lender_is_agent"], "no")
        rows = [r for r in longs if r["company_number"] == "99000002"]
        self.assertEqual(sorted(r["is_primary"] for r in rows), ["no", "yes"])
        self.assertEqual(next(f for f in filings if f["company_number"] == "99000003")["lender_is_agent"], "yes")

    def test_write_outputs_and_note(self):
        conn = loaded()
        with tempfile.TemporaryDirectory() as tmp:
            out = monthly.write_monthly(conn, WEEK, WEEK_END, label="test", out_dir=tmp,
                                        today=date(2026, 11, 2))
            for name in ("filings.csv", "lenders_long.csv", "data_quality.md", "meta.json"):
                self.assertTrue((out / name).exists(), name)
            with open(out / "filings.csv", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(list(rows[0].keys()), list(monthly.FILING_FIELDS))
            self.assertEqual(len(rows), 8)
            note = (out / "data_quality.md").read_text(encoding="utf-8")
            for heading in ("## Verdict", "## Coverage", "## Counts", "## Lender matching",
                            "## What this extract does not tell you"):
                self.assertIn(heading, note)
            self.assertIn("MR01 (charge registered): **7**", note)
            self.assertIn("MR04 (charge satisfied): **1**", note)
            self.assertIn("Generated 2026-11-02", note)


if __name__ == "__main__":
    unittest.main()

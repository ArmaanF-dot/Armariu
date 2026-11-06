import csv, gzip, io, tempfile, unittest
from datetime import date
from pathlib import Path

from radar import public_suppliers as ps

CANDS = [
    {"company_number": "00000001", "name_norm": "ALPHA BUILD", "postcode": "AB12CD", "category": "Private Limited Company",
     "incorporated": "2019-05-01", "sic1": "41200", "accounts_category": "MICRO ENTITY", "charges_total": "0", "charges_outstanding": "0"},
    {"company_number": "00000002", "name_norm": "BETA CARE", "postcode": "ZZ11AA", "category": "Private Limited Company",
     "incorporated": "2015-01-01", "sic1": "87100", "accounts_category": "SMALL", "charges_total": "2", "charges_outstanding": "1"},
    {"company_number": "00000003", "name_norm": "GAMMA", "postcode": "", "category": "Private Limited Company",
     "incorporated": "1990-01-01", "sic1": "62020", "accounts_category": "FULL", "charges_total": "0", "charges_outstanding": "0"},
    {"company_number": "00000004", "name_norm": "SMALL BIT", "postcode": "", "category": "Private Limited Company",
     "incorporated": "2020-01-01", "sic1": "62020", "accounts_category": "MICRO ENTITY", "charges_total": "0", "charges_outstanding": "0"},
]


def aw(ocid, name, value, ids="", published="2024-03-01"):
    return {"ocid": ocid, "supplier_name": name, "value": str(value), "supplier_ids": ids, "postcode": "",
            "published": published, "buyer": "Some Council", "title": "T", "notice_url": "http://x"}


AWARDS = [
    aw("o1", "Alpha Build Ltd", 250000, "GB-COH:1"),          # number match (zero-padded)
    aw("o1", "Alpha Build Ltd", 250000, "GB-COH:1"),          # duplicate row
    aw("o2", "Beta Care Limited", 500000),                    # name match, has charges
    aw("o3", "Gamma PLC", 900000),                            # large accounts
    aw("o4", "Small Bit Ltd", 50000),                         # under £100k: no tag
    aw("o5", "Nobody Ltd", 900000),                           # no match
]


class PublicSuppliers(unittest.TestCase):
    def setUp(self):
        self.rows = ps.build(AWARDS, CANDS)
        self.sup = {r["company_number"]: {**r, **{f: int(r[f]) for f in ps.INT_FIELDS}} for r in self.rows}

    def test_tag_needs_100k_notice_and_a_match(self):
        self.assertEqual(sorted(self.sup), ["00000001", "00000002", "00000003"])
        self.assertEqual(self.sup["00000001"]["match"], "number")
        self.assertEqual(self.sup["00000001"]["notices"], 1)   # duplicate row removed
        self.assertEqual(self.sup["00000002"]["match"], "name")

    def test_leads_are_micro_small_with_no_charge(self):
        leads = ps.leads(self.sup, {}, date(2026, 11, 6), primes=[])
        self.assertEqual([l["company_number"] for l in leads], ["00000001"])   # 2 has charges, 3 is large
        self.assertEqual(leads[0]["size"], "Micro")

    def test_radar_charge_since_snapshot_drops_lead(self):
        class Co: charges = [object()]
        self.assertEqual(ps.leads(self.sup, {"00000001": Co()}, date(2026, 11, 6), primes=[]), [])

    def test_roundtrip_and_tag(self):
        with tempfile.TemporaryDirectory() as d:
            p = ps.write(self.rows, Path(d) / "x.csv.gz")
            got = ps.load(p)
        self.assertEqual(set(got), set(self.sup))
        self.assertEqual(ps.tag("00000001", got)["label"], ps.TAG)
        self.assertIsNone(ps.tag("99999999", got))
        self.assertEqual(ps.load(Path("/nonexistent.csv.gz")), {})

    def test_wording_is_honest(self):
        self.assertIn("2.7%", ps.CAVEAT)
        self.assertIn("0.65%", ps.CAVEAT)
        self.assertIn("not when", ps.CAVEAT)
        self.assertIn("not part of the refinance score", ps.TAG_HELP)


if __name__ == "__main__":
    unittest.main()

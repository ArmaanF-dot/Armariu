"""Tests for the older-data backfill (radar/backfill.py). Synthetic company numbers only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import backfill  # noqa: E402
from radar.db import connect  # noqa: E402
from radar.ingest import RestClient  # noqa: E402


def rows(n, with_charge=lambda i: i % 2 == 0):
    for i in range(n):
        yield {"CompanyNumber": f"T{i:07d}", "Mortgages.NumMortCharges": "3" if with_charge(i) else "0",
               "Mortgages.NumMortOutstanding": "1" if i % 4 == 0 else "0", "Mortgages.NumMortPartSatisfied": "0",
               "Mortgages.NumMortSatisfied": "2", "CompanyStatus": "Active", "CompanyCategory": "Private Limited Company",
               "IncorporationDate": "01/02/2010", "SICCode.SicText_1": "68100 - Buying and selling of own real estate",
               "SICCode.SicText_2": "", "SICCode.SicText_3": "", "SICCode.SicText_4": ""}


class FakeResp:
    def __init__(self, data, code=200):
        self.data, self.status_code = data, code

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class FakeSession:
    """Answers /company/<n>/charges with two charges (one repaid, one open)."""

    def __init__(self):
        self.paths = []

    def get(self, url, params=None, auth=None, timeout=None):
        self.paths.append(url)
        num = url.split("/company/")[1].split("/")[0]
        items = [
            {"id": f"{num}a", "status": "fully-satisfied", "created_on": "2023-01-05", "delivered_on": "2023-01-10",
             "satisfied_on": "2025-03-01", "persons_entitled": [{"name": "Old Bank Plc"}],
             "classification": {"description": "A registered charge"}},
            {"id": f"{num}b", "status": "outstanding", "created_on": "2025-02-20", "delivered_on": "2025-02-25",
             "persons_entitled": [{"name": "New Lender Limited"}]},
        ]
        return FakeResp({"items": items, "total_count": 2})


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.conn = backfill.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_frame_is_random_fixed_and_counts_everyone_with_a_charge(self):
        kept, stats = backfill.frame_from_rows(rows(400), sample_max=50)
        self.assertEqual(stats["companies_read"], 400)
        self.assertEqual(stats["frame_size"], 200)          # every company with a charge, open or not
        self.assertEqual(stats["frame_outstanding"], 100)
        self.assertEqual(len(kept), 50)
        keys = [backfill.rank_key(r["company_number"]) for r in kept]
        self.assertEqual(keys, sorted(keys))                 # rank order
        everyone = sorted(backfill.rank_key(f"T{i:07d}") for i in range(0, 400, 2))
        self.assertEqual(keys, everyone[:50])                # exactly the 50 lowest ranks of the frame
        again, _ = backfill.frame_from_rows(rows(400), sample_max=50)
        self.assertEqual([r["company_number"] for r in again], [r["company_number"] for r in kept])
        self.assertEqual(kept[0]["sic_codes"], '["68100"]')
        self.assertEqual(kept[0]["incorporated"], "2010-02-01")

    def test_fetch_resumes_in_rank_order_and_respects_limit(self):
        backfill.build_frame(self.conn, rows(100), snapshot="2026-11-01", sample_max=20)
        self.assertEqual(backfill.build_frame(self.conn, rows(100))["frame"], "already built")
        client = RestClient("k", session=FakeSession())
        first = backfill.fetch(self.conn, client, limit=7)
        self.assertEqual((first["companies"], first["calls"], first["charges"]), (7, 7, 14))
        self.assertEqual(first["stopped"], "limit reached")
        second = backfill.fetch(self.conn, RestClient("k", session=FakeSession()))
        self.assertEqual(second["companies"], 13)
        self.assertEqual(second["stopped"], "finished the sample")
        order = [r[0] for r in self.conn.execute(
            "SELECT f.rank FROM fetched d JOIN frame f USING (company_number) ORDER BY d.rowid")]
        self.assertEqual(order, list(range(1, 21)))
        st = backfill.status(self.conn)
        self.assertEqual((st["sample_kept"], st["fetched"], st["charges_stored"]), (20, 20, 40))

    def test_spare_never_runs_when_live_used_the_budget(self):
        backfill.build_frame(self.conn, rows(40), sample_max=10)
        session = FakeSession()
        client = RestClient("k", session=session, budget_seconds=0.0001)
        while not client.out_of_time():
            pass
        built = []
        out = backfill.spare(client, extra_minutes=30, path=":memory:", build=lambda c: built.append(1))
        self.assertIn("skipped", out)
        self.assertEqual(session.paths, [])                  # no calls, extra minutes not used
        self.assertEqual(built, [])

    def test_spare_uses_extra_minutes_only_for_backfill(self):
        client = RestClient("k", session=FakeSession(), budget_seconds=60)
        before = client.time_left()
        backfill.spare(client, extra_minutes=1, path=":memory:",
                       build=lambda c: backfill.build_frame(c, rows(10), sample_max=3))
        self.assertGreater(client.time_left(), before + 50)
        self.assertEqual(client.calls, 3)

    def test_loaded_sample_feeds_the_backtest_and_masks_nothing_new(self):
        backfill.build_frame(self.conn, rows(20), sample_max=5)
        backfill.fetch(self.conn, RestClient("k", session=FakeSession()))
        live = connect(":memory:")
        cos = backfill.load_companies(live, self.conn)
        self.assertEqual(len(cos), 5)
        co = next(iter(cos.values()))
        self.assertTrue(co.fetched)
        self.assertEqual(co.sic_codes, ["68100"])
        self.assertEqual([c.filed for c in co.charges], ["2023-01-10", "2025-02-25"])
        self.assertTrue(co.charges[0].lender_keys and co.charges[1].lender_keys)
        self.assertNotEqual(co.charges[0].lender_keys, co.charges[1].lender_keys)
        self.assertIsNotNone(backfill.first_fetched(self.conn))
        live.close()


if __name__ == "__main__":
    unittest.main()

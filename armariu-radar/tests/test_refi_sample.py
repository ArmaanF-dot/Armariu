"""Client portal shows only random-sample hit rates; the radar-data odds never leave the build."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import refi_sample  # noqa: E402
from radar.portal import server  # noqa: E402


def row(n, sector, score, serial=False):
    return {"company_number": str(n), "company_name": f"C{n}", "sector": sector, "score": score, "serial_borrower": serial,
            "odds_12m": 0.7, "lift": 5.0, "base_rate": 0.12, "newest_lender": "X"}


class RandomSampleTests(unittest.TestCase):
    def test_bands_within_group(self):
        rows = [row(i, "Property and real estate", i) for i in range(40)] + [row(100 + i, "Retail", i) for i in range(40)] \
            + [row(200, "Retail", 99, serial=True)]
        refi_sample.add_bands(rows)
        prop = [r for r in rows if r["group"] == "property"]
        self.assertEqual(sum(r["band"] == "top10" for r in prop), 4)
        self.assertEqual(sum(r["band"] == "top25" for r in prop), 6)
        self.assertIsNone(rows[-1]["band"])      # serial borrowers have no measured band
        self.assertEqual(rows[-1]["group"], "serial")

    def test_public_export_has_no_radar_data_odds(self):
        rows = [row(i, "Retail", i) for i in range(10)]
        refi_sample.add_bands(rows)
        out = server.public_data({"refi_watch": {"rows": rows, "base_rate": 0.12, "backtest": {"tiers": [{"top": "1%"}]},
                                                 "accuracy_90d": {"table": []}, "caveat": "x"}, "signals": []})
        text = json.dumps(out["refi_watch"])
        for banned in ("odds_12m", "base_rate", "backtest", "accuracy_90d", "top 1%", "top 5%"):
            self.assertNotIn(banned, text.replace("The top 1% and top 5% are not quoted", ""), banned)
        self.assertEqual(out["refi_watch"]["sample"]["groups"]["ordinary"]["12m"]["top10"], 7.1)

    def test_no_top1_top5_rows(self):
        keys = {k for g in refi_sample.GROUPS.values() for w in ("12m", "90d") for k in g.get(w, {})}
        self.assertEqual(keys, {"all", "all_ci", "top10", "top10_ci", "lift", "lift_ci"})


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import date

from radar import distress as ds

CHARGE = {"created_on": "2024-01-10", "delivered_on": "2024-01-20", "satisfied_on": None, "persons": ["Some Bank Plc"]}


def co(filings=(), charges=(CHARGE,), created=date(2015, 1, 1), ceased=None):
    return ds.Co("00000001", 1.0, created, ceased, ["62012"], [list(f) for f in filings], list(charges), "test")


class DistressTests(unittest.TestCase):
    def test_needs_an_outstanding_charge_filed_by_the_cutoff(self):
        c = date(2025, 1, 1)
        self.assertIsNotNone(ds.features(co(), c, {}))
        self.assertIsNone(ds.features(co(charges=[]), c, {}))
        late = dict(CHARGE, delivered_on="2025-02-01")
        self.assertIsNone(ds.features(co(charges=[late]), c, {}))
        repaid = dict(CHARGE, satisfied_on="2024-06-01")
        self.assertIsNone(ds.features(co(charges=[repaid]), c, {}))

    def test_already_insolvent_firms_are_left_out_and_later_insolvency_is_the_outcome(self):
        c = date(2025, 1, 1)
        # (type, category, subcategory, filed, action date, description, made up date, new date)
        liq = ("COCOMP", "insolvency", "", "2025-06-01", None, "winding-up-order", None, None)
        firm = co(filings=[liq])
        self.assertIsNotNone(ds.features(firm, c, {}))
        self.assertEqual(ds.outcome(firm, c), 1)
        self.assertEqual(ds.outcome(firm, date(2023, 1, 1)), 0)      # more than 12 months before
        self.assertIsNone(ds.features(firm, date(2025, 7, 1), {}))   # already in the process

    def test_solvent_wind_up_is_not_an_insolvency(self):
        mvl = ("LIQ01", "insolvency", "", "2025-06-01", None, "declaration-of-solvency", None, None)
        self.assertEqual(ds.outcome(co(filings=[mvl]), date(2025, 1, 1)), 0)

    def test_overdue_accounts_clue_and_reasons(self):
        acc = ("AA", "accounts", "", "2022-09-30", None, "accounts-with-accounts-type-small", "2021-12-31", None)
        f = ds.features(co(filings=[acc]), date(2025, 1, 1), {})
        self.assertEqual(f["acc_overdue"], 1)
        self.assertIn("accounts overdue", ds.reasons(f))

    def test_decile_rates_run_from_riskiest_down(self):
        self.assertEqual(len(ds.DECILE_RATES), 10)
        self.assertGreater(ds.DECILE_RATES[0], ds.DECILE_RATES[-2])


if __name__ == "__main__":
    unittest.main()


class FacilityTargetTests(unittest.TestCase):
    def test_target_window_and_history(self):
        from datetime import date
        from radar import facility_backtest as F
        co = ds.Co("1", 1.0, date(2015, 1, 1), None, [], [], [
            {"created_on": "2024-03-01", "delivered_on": "2024-03-05", "persons": ["Some Invoice Ltd"]}], "live")
        cats = {ds.normalise_name("Some Invoice Ltd"): "abl_specialist"}
        self.assertTrue(F.had_target(co, date(2024, 6, 1), cats))
        self.assertFalse(F.had_target(co, date(2024, 1, 1), cats))
        self.assertEqual(F.target_in_window(co, date(2024, 1, 1), cats), 1)
        self.assertEqual(F.target_in_window(co, date(2025, 1, 1), cats), 0)

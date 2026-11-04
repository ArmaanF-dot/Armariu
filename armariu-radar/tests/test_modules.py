"""Tests for the additions: Modules A to F. Fixtures are synthetic; names of real banks are used
only so the lookup tables have something to match."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar import aliases, charge_signals as bs, contracts, lender_view, scorecard, weekly_pack  # noqa: E402
from radar.charge_history import Charge, Company, load_companies  # noqa: E402
from radar.db import connect  # noqa: E402

A = ("Lender A", "LENDER A", "direct_lender")
B = ("Lender B", "LENDER B", "bank")
AGENT = ("Lender A", "LENDER A", "trustee_agent")


def ch(cid, filed, lenders, status="outstanding", sat=None, co="1"):
    return Charge(co, cid, filed, filed, sat, status, list(lenders))


def company(charges, fetched=True, number="1", created="2015-01-01"):
    return Company(number, "Test Co", ["62012"], created, "active", fetched, charges)


def mem_with(rows):
    """In-memory db with borrower_history rows [(co, id, delivered, satisfied, status, persons)]."""
    conn = connect(":memory:")
    for co, cid, dl, sat, st, persons in rows:
        conn.execute("INSERT INTO borrower_history (company_number, charge_id, status, created_on, delivered_on, "
                     "satisfied_on, classification, persons) VALUES (?,?,?,?,?,?,?,?)",
                     (co, cid, st, dl, dl, sat, "x", json.dumps(persons)))
        conn.execute("INSERT OR IGNORE INTO borrower_history_fetched VALUES (?, '2026-11-01', 1)", (co,))
        conn.execute("INSERT OR IGNORE INTO companies (company_number, name, sic_codes, date_of_creation) VALUES (?,?,?,?)",
                     (co, f"Co {co}", '["62012"]', "2015-01-01"))
    return conn


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.r = aliases.Resolver(None)

    def test_spellings_become_one_lender(self):
        a, b = self.r.resolve("HSBC UK Bank PLC"), self.r.resolve("Hsbc Bank plc.")
        self.assertEqual((a.canonical, a.key), (b.canonical, b.key))

    def test_alias_table_beats_other_rules(self):
        self.assertEqual(self.r.resolve("Together Commercial Finance Limited").how, "alias")

    def test_agents_stay_their_own_class(self):
        res = self.r.resolve("Barclays Security Trustee Limited")
        self.assertEqual(res.cls, "trustee_agent")
        self.assertNotEqual(res.cls, self.r.resolve("Barclays Bank PLC").cls)

    def test_fuzzy_needs_high_threshold_and_same_first_token(self):
        r = aliases.Resolver(None, aliases={"ALDERMORE": ("Aldermore", "bank", "")}, group_rules=[], rules=[])
        self.assertEqual(r.resolve("Aldermoore").how, "fuzzy")
        self.assertEqual(r.resolve("Allmore").how, "unmapped")       # different opening letters never merge
        self.assertEqual(r.resolve("Aldermore Zebra Capital").how, "unmapped")

    def test_unmapped_goes_to_review_never_merged(self):
        conn = mem_with([("1", "c1", "2026-01-05", None, "outstanding", ["Zed Funding Ltd"])])
        conn.execute("INSERT INTO charges (company_number, charge_id, delivered_on, first_seen_at) VALUES ('1','c1','2026-01-05','x')")
        conn.execute("INSERT INTO charge_persons VALUES ('1','c1','Zed Funding Ltd','ZED FUNDING')")
        rows = aliases.review_rows(conn, aliases.Resolver(conn, aliases={}, group_rules=[], rules=[]))
        self.assertEqual(rows[0]["alias_key"], "ZED FUNDING")

    def test_role_phrases_are_dropped_from_agent_names(self):
        res = self.r.resolve("Glas Trust Corporation Limited as Security Trustee for the Secured Parties")
        self.assertEqual((res.canonical, res.cls), ("Glas Trust Corporation", "trustee_agent"))
        self.assertEqual(aliases.strip_role("PALMER AGENCY SERVICES UK LIMITED FOR ITSELF AND AS TRUSTEE"),
                         "PALMER AGENCY SERVICES")

    def test_group_table_decides_the_class(self):
        self.assertEqual(self.r.resolve("HSBC Invoice Finance (UK) Limited").cls, "abl_invoice")
        self.assertEqual(self.r.resolve("HSBC UK Bank PLC").cls, "bank")

    def test_deterministic(self):
        names = ["HSBC UK Bank PLC", "Lloyds Bank PLC", "Zed Funding Ltd"]
        self.assertEqual([aliases.Resolver(None).resolve(n) for n in names], [aliases.Resolver(None).resolve(n) for n in names])

    def test_bare_personal_names_are_individuals(self):
        r = aliases.Resolver(None)
        self.assertEqual(r.resolve("John William Neaverson").cls, "individual")
        self.assertNotEqual(r.resolve("Derwent London Plc").cls, "individual")
        self.assertNotEqual(r.resolve("Green Borough Holdings").cls, "individual")

    def test_alias_conflict_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.csv"
            p.write_text("canonical,alias,class,notes\nX,FOO,bank,\nY,FOO,bank,\n")
            with self.assertRaises(ValueError):
                aliases.load_aliases(p)


class SignalTests(unittest.TestCase):
    def kinds(self, co):
        return [(s["signal_type"], s["charge_id"]) for s in bs.for_company(co)]

    def test_first_facility_when_no_charge_in_three_years(self):
        co = company([ch("old", "2019-01-01", [B], "fully-satisfied", "2020-01-01"), ch("new", "2026-05-01", [A])])
        self.assertIn(("first_facility", "new"), self.kinds(co))

    def test_not_first_facility_with_recent_charge(self):
        co = company([ch("old", "2025-01-01", [B], "fully-satisfied", "2025-02-01"), ch("new", "2026-05-01", [A])])
        self.assertNotIn(("first_facility", "new"), self.kinds(co))

    def test_not_first_facility_with_older_outstanding_charge(self):
        # found in the 3 Nov 2026 hand-check (Jacinthe Rayner, Pickaname): an old charge still open
        co = company([ch("old", "2022-03-07", [B]), ch("new", "2026-10-26", [A])])
        self.assertNotIn(("first_facility", "new"), self.kinds(co))

    def test_first_facility_needs_full_history(self):
        self.assertEqual(self.kinds(company([ch("new", "2026-05-01", [A])], fetched=False)), [])

    def test_refinancing_other_lender_within_90_days(self):
        co = company([ch("x", "2020-01-01", [B], "fully-satisfied", "2026-04-01"), ch("y", "2026-05-01", [A])])
        sig = [s for s in bs.for_company(co) if s["signal_type"] == "refinancing"][0]
        self.assertEqual((sig["charge_id"], sig["other_charge_id"]), ("y", "x"))
        self.assertEqual(sig["rule_version"], bs.RULE_VERSION)

    def test_refinancing_not_for_same_lender_or_outside_window(self):
        same = company([ch("x", "2020-01-01", [A], "fully-satisfied", "2026-04-01"), ch("y", "2026-05-01", [A])])
        far = company([ch("x", "2020-01-01", [B], "fully-satisfied", "2025-01-01"), ch("y", "2026-05-01", [A])])
        for co in (same, far):
            self.assertNotIn("refinancing", [k for k, _ in self.kinds(co)])

    def test_refinancing_dated_when_both_facts_are_public(self):
        co = company([ch("x", "2020-01-01", [B], "fully-satisfied", "2026-06-01"), ch("y", "2026-05-01", [A])])
        sig = [s for s in bs.for_company(co) if s["signal_type"] == "refinancing"][0]
        self.assertEqual(sig["signal_date"], "2026-06-01")   # the satisfaction, not the earlier charge

    def test_add_on_while_earlier_charge_outstanding(self):
        co = company([ch("x", "2024-01-01", [A]), ch("y", "2026-05-01", [A])])
        self.assertIn(("add_on", "y"), self.kinds(co))

    def test_add_on_not_when_earlier_charge_already_satisfied(self):
        co = company([ch("x", "2024-01-01", [A], "fully-satisfied", "2025-01-01"), ch("y", "2026-05-01", [A])])
        self.assertNotIn("add_on", [k for k, _ in self.kinds(co)])

    def test_one_deal_registered_as_several_charges_is_one_signal(self):
        a = Charge("1", "a", "2026-05-01", "2026-04-28", None, "outstanding", [A])
        b = Charge("1", "b", "2026-05-01", "2026-04-28", None, "outstanding", [A])
        c = Charge("1", "c", "2026-05-02", "2026-04-28", None, "outstanding", [A])
        kinds = self.kinds(company([a, b, c]))
        self.assertEqual(kinds, [("first_facility", "a")])   # no add-ons between charges of the same deal

    def test_deterministic_and_unique_ids(self):
        co = company([ch("x", "2024-01-01", [A]), ch("y", "2026-05-01", [A])])
        a, b = bs.for_company(co), bs.for_company(co)
        self.assertEqual(a, b)
        self.assertEqual(len({s["signal_id"] for s in a}), len(a))

    def test_explanation_has_no_invented_amounts(self):
        co = company([ch("x", "2024-01-01", [A]), ch("y", "2026-05-01", [A])])
        for s in bs.for_company(co):
            self.assertNotIn("£", s["explanation"])


class ViewAndScorecardTests(unittest.TestCase):
    def cos(self):
        return {"1": company([ch("x", "2026-10-27", [A]), ch("y", "2026-10-28", [A, B])]),
                "2": Company("2", "Two", ["62012"], "2010-01-01", "active", True, [ch("z", "2026-10-29", [AGENT], co="2")])}

    def test_league_reconciles_with_raw_counts(self):
        v = lender_view.build(self.cos(), date(2026, 10, 26), date(2026, 10, 20))
        self.assertEqual(v.raw_charges, 3)
        self.assertTrue(v.reconciliation["ok"])
        self.assertEqual(v.reconciliation["charges_without_lender"], 0)
        a = [r for r in v.league if r["lender"] == "Lender A"][0]
        self.assertEqual(a["this_week"], 2)
        self.assertEqual(a["low_observations"], "yes")

    def test_agent_is_a_separate_row(self):
        v = lender_view.build(self.cos(), date(2026, 10, 26), date(2026, 10, 20))
        self.assertIn("Lender A (security trustee)", [r["lender"] for r in v.league])

    def test_tables_state_window_and_caveat(self):
        md = lender_view.render_md(lender_view.build(self.cos(), date(2026, 10, 26), None))
        self.assertIn("Window: 2026-10-26 to 2026-11-01", md)
        self.assertIn("not deals", md)
        self.assertIn("Partial window", md)

    def test_insights_counts_and_no_individuals(self):
        from radar import insights
        person = ("A private individual", "PRIVATE INDIVIDUAL", "individual")
        cos = self.cos()
        cos["3"] = Company("3", "Three", ["68100"], "2024-06-01", "active", True,
                           [ch("p", "2026-10-28", [person], co="3")])
        for c in cos["1"].charges:
            c.on_register, c.floating_all = True, True
        out = insights.build_insights(cos, [], [{"odds_12m": 0.45, "sector": "S", "base_rate": 0.12}],
                                      date(2026, 11, 3), date(2026, 10, 20))
        wk = out["flow_weeks"][-1]
        self.assertEqual((wk["new"], wk["register"], wk["all_assets"], wk["private_credit"]), (4, 2, 2, 3))
        self.assertFalse(wk["observed_only"])
        self.assertTrue(out["flow_weeks"][0]["observed_only"])
        self.assertNotIn("individual", [m["class"] for m in out["league_moves"]])
        self.assertEqual(out["watch"]["high"], 1)
        self.assertNotIn("private individual", json.dumps(out).lower())

    def test_wilson_interval(self):
        lo, hi = scorecard.wilson(50, 100)
        self.assertAlmostEqual(lo, 0.404, places=2)
        self.assertAlmostEqual(hi, 0.596, places=2)
        self.assertEqual(scorecard.wilson(0, 0), (0.0, 1.0))

    def test_backtest_is_reproducible_and_has_baseline_and_interval(self):
        cos = {}
        for i in range(200):
            n = str(1000 + i)
            cs = [ch("a", "2025-01-10", [A], co=n)]
            if i % 2 == 0:
                cs.append(ch("b", "2025-02-10", [A], co=n))   # a further charge: a hit
            cos[n] = company(cs, number=n)
        r1 = scorecard.backtest(cos, date(2026, 1, 1), 24, 90)
        r2 = scorecard.backtest(cos, date(2026, 1, 1), 24, 90)
        self.assertEqual(r1, r2)
        self.assertTrue(all(r["label"] == "BACKTEST" for r in r1))
        self.assertTrue(all(r["ci_low"] != "" and r["ci_high"] != "" for r in r1))

    def test_backtest_ignores_events_whose_window_is_still_open(self):
        cos = {"1": company([ch("a", "2025-12-30", [A])])}
        rows = scorecard.backtest(cos, date(2026, 1, 1), 24, 90)
        self.assertEqual(sum(int(r["signals"]) for r in rows), 0)


class PackAndContractTests(unittest.TestCase):
    def test_pack_has_all_sections_and_caveats(self):
        cos = {"1": company([ch("x", "2026-10-27", [A])])}
        md = weekly_pack.build_pack(cos, date(2026, 10, 26), date(2026, 10, 20), None, date(2026, 11, 3))
        for h in ("The read", "Lender view", "Signals worth a look", "One exhibit", "Scorecard line", "Footer"):
            self.assertIn(h, md)
        self.assertIn("not investment", md)
        self.assertIn("BACKTEST", scorecard.scorecard_line([{"signal_type": "add_on", "hit_rate": "0.5", "baseline_rate": "0.4", "lift": "1.25", "signals": "10"}]))

    def test_individuals_are_masked(self):
        conn = mem_with([("1", "c1", "2026-01-05", None, "outstanding", ["John Example"])])
        conn.execute("INSERT INTO lender_classifications (name_norm, category, source, review_status) VALUES ('JOHN EXAMPLE','individual','human','ok')")
        cos = load_companies(conn, aliases.Resolver(conn, aliases={}, group_rules=[], rules=[]))
        self.assertNotIn("John", " ".join(cos["1"].charges[0].lender_names))

    def test_pack_failure_is_loud(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(":memory:")
            with self.assertRaises(RuntimeError):
                weekly_pack.run(date(2026, 10, 26), d, conn=conn)
            weekly_pack.notify_failure("boom", Path(d))
            self.assertTrue((Path(d) / "WEEKLY_PACK_FAILED.txt").exists())

    def test_contract_match_by_number_vs_name(self):
        cos = {"01234567": Company("01234567", "Acme Widgets Ltd", ["62012"], "2018-01-01", "active", True,
                                   [ch("a", "2026-06-01", [A], co="01234567")])}
        rows = [{"notice_id": "n1", "notice_url": "u", "supplier_name": "Acme Widgets", "supplier_company_number": "1234567", "value_gbp": "50000", "buyer": "NHS"},
                {"notice_id": "n2", "notice_url": "u", "supplier_name": "Acme Widgets Limited", "supplier_company_number": ""},
                {"notice_id": "n3", "notice_url": "u", "supplier_name": "Capita Business Services", "supplier_company_number": ""},
                {"notice_id": "n4", "notice_url": "u", "supplier_name": "Nobody At All", "supplier_company_number": ""}]
        leads = contracts.build_leads(rows, cos, date(2026, 11, 3), contracts.load_primes())
        by = {l["notice_id"]: l for l in leads}
        self.assertEqual(by["n1"]["match_method"], "company_number")
        self.assertTrue(contracts.publishable(by["n1"]))
        self.assertEqual(by["n2"]["needs_confirmation"], "yes")
        self.assertFalse(contracts.publishable(by["n2"]))
        self.assertNotIn("n3", by)                       # large prime dropped
        self.assertEqual(by["n4"]["match_method"], "unmatched")
        self.assertIn("outstanding charge", by["n1"]["rationale"])


class RealFixtureTests(unittest.TestCase):
    """Twenty real stream records (Phase 0). Live responses override anything assumed in the plan."""

    def setUp(self):
        from radar.ingest import process_event
        data = json.loads((Path(__file__).parent / "fixtures" / "ch_charges_sample.json").read_text(encoding="utf-8"))
        self.events = data["events"]
        self.conn = connect(":memory:")
        for ev in self.events:
            process_event(self.conn, ev)

    def test_fields_the_modules_rely_on_are_present(self):
        for ev in self.events:
            d = ev["data"]
            for k in ("status", "delivered_on", "persons_entitled", "classification"):
                self.assertIn(k, d)
        self.assertEqual(len(self.events), 20)

    def test_load_and_signal_real_records(self):
        cos = load_companies(self.conn, aliases.Resolver(self.conn))
        self.assertEqual(sum(len(c.charges) for c in cos.values()), 20)
        self.assertTrue(all(ch.lenders for c in cos.values() for ch in c.charges))
        a = bs.all_signals(cos)
        self.assertEqual(a, bs.all_signals(load_companies(self.conn, aliases.Resolver(self.conn))))


if __name__ == "__main__":
    unittest.main()


class RefiWatchTests(unittest.TestCase):
    def setUp(self):
        from radar import refi_watch
        self.rw = refi_watch

    def test_features_need_an_open_charge(self):
        co = company([ch("old", "2020-01-01", [B], "fully-satisfied", "2021-01-01")])
        self.assertIsNone(self.rw.features(co, date(2026, 1, 1), {}))

    def test_outcome_is_a_payoff_plus_a_new_charge(self):
        co = company([ch("old", "2024-01-01", [B], "fully-satisfied", "2025-03-01"), ch("new", "2025-03-05", [A])])
        self.assertTrue(self.rw.outcome(co, date(2025, 1, 1)))
        only_new = company([ch("old", "2024-01-01", [B]), ch("new", "2025-03-05", [A])])
        self.assertFalse(self.rw.outcome(only_new, date(2025, 1, 1)))

    def test_no_peeking_and_deterministic(self):
        cos = {str(i): company([ch(f"a{i}", "2023-01-01", [B], "fully-satisfied", "2024-06-01"), ch(f"b{i}", "2024-06-03", [A])], number=f"{i:08d}")
               for i in range(40)}
        rows1, m1 = self.rw.watchlist(cos, date(2025, 1, 1), top=5)
        rows2, _ = self.rw.watchlist(cos, date(2025, 1, 1), top=5)
        self.assertEqual(rows1, rows2)
        # learned only on cutoffs whose window had closed before the run date
        self.assertTrue(all(c <= date(2025, 1, 1) - timedelta(days=self.rw.WINDOW) for c in self.rw._cutoffs(date(2025, 1, 1) - timedelta(days=self.rw.WINDOW + 1))))


class RefiOddsBySegmentTests(unittest.TestCase):
    def test_serial_and_ordinary_get_their_own_odds(self):
        from radar.refi_watch import Model
        x = lambda serial: {"age_cls": "a", "recent": "none", "nopen": "one", "pullback": "steady",
                            "property": "no", "since_sat": "never", "lender_rate": "unknown", "_serial": serial}
        rows = [(x(False), i % 10 == 0) for i in range(1000)] + [(x(True), i % 2 == 0) for i in range(1000)]
        m = Model(rows)
        s = m.score(x(False))
        self.assertAlmostEqual(m.odds(s, False), 0.1, places=2)
        self.assertAlmostEqual(m.odds(s, True), 0.5, places=2)
        self.assertAlmostEqual(m.odds(s), 0.3, delta=0.21)   # shared table still there for callers without a segment


class RefiAccuracy90Tests(unittest.TestCase):
    def test_switch_needs_a_different_lender(self):
        from radar.refi_accuracy import outcomes
        on = date(2025, 1, 1)
        switch = company([ch("old", "2024-01-01", [B], "fully-satisfied", "2025-02-01"), ch("new", "2025-02-03", [A])])
        same = company([ch("old", "2024-01-01", [B], "fully-satisfied", "2025-02-01"), ch("new", "2025-02-03", [B])])
        repaid = company([ch("old", "2024-01-01", [B], "fully-satisfied", "2025-02-01")])
        late = company([ch("old", "2024-01-01", [B], "fully-satisfied", "2025-02-01"), ch("new", "2025-05-01", [A])])
        self.assertEqual(outcomes(switch, on), {"switched": True, "any": True, "repaid": True})
        self.assertEqual(outcomes(same, on), {"switched": False, "any": True, "repaid": True})
        self.assertEqual(outcomes(repaid, on), {"switched": False, "any": False, "repaid": True})
        self.assertEqual(outcomes(late, on), {"switched": False, "any": False, "repaid": True})   # outside 90 days

    def test_only_charges_filed_before_the_date_count(self):
        from radar.refi_accuracy import known_on
        co = company([ch("a", "2024-01-01", [B]), ch("b", "2025-01-02", [A])])
        self.assertEqual([c.charge_id for c in known_on(co, date(2025, 1, 1)).charges], ["a"])

    def test_test_windows_close_before_coverage_starts(self):
        from radar.refi_accuracy import test_dates
        cos = {"1": company([ch("a", "2026-11-01", [B])])}
        ds = test_dates(cos, stop=date(2026, 10, 20))
        self.assertEqual(ds[0], date(2025, 1, 1))
        self.assertTrue(all(d + timedelta(days=90) < date(2026, 10, 20) for d in ds))
        self.assertEqual(ds[-1], date(2026, 7, 1))

"""Tests for the deal radar. Run: python -m unittest discover -s tests -v"""
from __future__ import annotations

import contextlib
import csv
import shutil
import subprocess
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import agentcheck, classify, config, dbsync, export, ingest, sectors, signals  # noqa: E402
from radar.db import connect, get_meta  # noqa: E402
from radar.normalise import normalise_name  # noqa: E402
from radar.review import add_human_rule  # noqa: E402
from radar.rules import find_rule, load_rules  # noqa: E402
from radar.sample import sample_agent_results, sample_events  # noqa: E402

WEEK = date(2026, 10, 12)  # a Monday

_CONNS = []


def mem_db():
    """In-memory database that is closed when the test module finishes."""
    conn = connect(":memory:")
    _CONNS.append(conn)
    return conn


def tearDownModule():
    for conn in _CONNS:
        conn.close()
WEEK_END = date(2026, 10, 18)


def loaded_db():
    conn = mem_db()
    for ev in sample_events(WEEK):
        ingest.process_event(conn, ev)
    conn.commit()
    return conn


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(config, "LOGS_DIR", self.tmp / "logs")
        patcher.start(); self.addCleanup(patcher.stop)


class NormaliseTests(unittest.TestCase):
    def test_suffixes_and_punctuation(self):
        self.assertEqual(normalise_name("Barclays Bank PLC"), "BARCLAYS BANK")
        self.assertEqual(normalise_name("BARCLAYS BANK Plc."), "BARCLAYS BANK")
        self.assertEqual(normalise_name("Smith & Co. Limited"), "SMITH AND CO")
        self.assertEqual(normalise_name("Société Générale"), "SOCIETE GENERALE")

    def test_only_trailing_suffix_removed(self):
        self.assertEqual(normalise_name("LTD Holdings Limited"), "LTD HOLDINGS")
        self.assertEqual(normalise_name("Limited"), "LIMITED")
        self.assertEqual(normalise_name(""), "")


class RulesTests(TempDirTest):
    def test_seed_rules_load_and_match(self):
        rules = load_rules()
        self.assertEqual(find_rule("HSBC UK BANK", rules).category, "bank")
        self.assertEqual(find_rule("GLAS TRUST CORPORATION", rules).category, "security_agent")
        self.assertEqual(find_rule("FERNHILL PRIVATE DEBT FUND LP", rules).category, "direct_lender")
        self.assertIsNone(find_rule("NORTHGATE CREDIT PARTNERS II SCSP", rules))

    def test_contains_is_whole_word(self):
        rules = load_rules()
        self.assertIsNone(find_rule("CHARES HOLDINGS", rules))       # not ARES
        self.assertIsNone(find_rule("MICGROUP TRADING", rules))      # not ICG

    def test_human_rule_beats_seed(self):
        csv_path = self.tmp / "lenders.csv"
        csv_path.write_text(config.LENDERS_CSV.read_text(encoding="utf-8"), encoding="utf-8")
        add_human_rule("HSBC UK Bank PLC", "other", "HSBC", "test", path=csv_path)
        rules = load_rules(csv_path)
        rule = find_rule("HSBC UK BANK", rules)
        self.assertEqual((rule.category, rule.source), ("other", "human"))

    def test_bad_category_rejected(self):
        bad = self.tmp / "bad.csv"
        bad.write_text("match_type,pattern,category,parent_firm,confidence,source,note\n"
                       "exact,FOO,hedge_fund,,1,human,\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_rules(bad)


class IngestTests(unittest.TestCase):
    def test_events_stored(self):
        conn = loaded_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0], 10)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM charges WHERE deleted=1").fetchone()[0], 1)
        row = conn.execute("SELECT * FROM charges WHERE charge_id='chg0000000002'").fetchone()
        self.assertEqual(row["floating_covers_all"], 1)
        self.assertEqual(row["classification_text"], "A registered charge")
        persons = {r[0] for r in conn.execute(
            "SELECT name_norm FROM charge_persons WHERE charge_id='chg0000000002'")}
        self.assertEqual(persons, {"NORTHGATE CREDIT PARTNERS II SCSP", "GLAS TRUST CORPORATION"})
        self.assertEqual(get_meta(conn, "stream_timepoint"), "1011")

    def test_update_logs_status_change(self):
        conn = loaded_db()
        ev = sample_events(WEEK)[0]
        ev["data"]["status"] = "fully-satisfied"
        ev["data"]["satisfied_on"] = "2026-10-20"
        ev["event"]["timepoint"] = 2000
        ingest.process_event(conn, ev)
        row = conn.execute("SELECT status, first_seen_at FROM charges WHERE charge_id='chg0000000001'").fetchone()
        self.assertEqual(row["status"], "fully-satisfied")
        last = conn.execute("SELECT status_before, status_after FROM charge_events "
                            "WHERE charge_id='chg0000000001' ORDER BY id DESC").fetchone()
        self.assertEqual((last[0], last[1]), ("outstanding", "fully-satisfied"))

    def test_list_shaped_fields_also_work(self):
        conn = mem_db()
        data = {"status": "outstanding", "delivered_on": "2026-10-13",
                "classification": [{"type": "nature-of-charge", "description": "Debenture"}],
                "particulars": [{"type": "brief-description", "description": "x",
                                 "floating_charge_covers_all": False}],
                "persons_entitled": [{"name": "A Lender Ltd"}, {"name": ""}]}
        ingest.upsert_charge(conn, "99000099", "abc", data)
        row = conn.execute("SELECT * FROM charges").fetchone()
        self.assertEqual(row["classification_text"], "Debenture")
        self.assertEqual(row["floating_covers_all"], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM charge_persons").fetchone()[0], 1)

    def test_bad_uri_skipped(self):
        conn = mem_db()
        self.assertFalse(ingest.process_event(conn, {"resource_uri": "/company/1/officers/x",
                                                     "event": {"timepoint": 5}}))
        self.assertEqual(get_meta(conn, "stream_timepoint"), "5")


class _FakeResp:
    def __init__(self, status, lines=(), payload=None):
        self.status_code, self._lines, self._payload = status, list(lines), payload

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_lines(self): yield from self._lines
    def json(self): return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.responses.pop(0)


class StreamAndRestTests(unittest.TestCase):
    def test_stream_resumes_from_timepoint(self):
        conn = mem_db()
        lines = [b"", json.dumps(sample_events(WEEK)[0]).encode(), b"", b"not json"]
        ingest.run_stream(conn, 1, "key", session=_FakeSession([_FakeResp(200, lines)]))
        self.assertEqual(get_meta(conn, "stream_timepoint"), "1001")
        sess = _FakeSession([_FakeResp(200, [])])
        ingest.run_stream(conn, 1, "key", session=sess)
        self.assertEqual(sess.calls[0][1]["params"], {"timepoint": "1001"})
        self.assertEqual(sess.calls[0][1]["auth"], ("key", ""))

    def test_stream_gap_resets_timepoint(self):
        conn = mem_db()
        conn.execute("INSERT INTO meta VALUES ('stream_timepoint', '5')")
        with self.assertRaises(ingest.StreamGap):
            ingest.run_stream(conn, 1, "key", session=_FakeSession([_FakeResp(416)]))
        self.assertIsNone(get_meta(conn, "stream_timepoint"))
        self.assertIsNotNone(get_meta(conn, "stream_gap_at"))

    def test_rest_backfill_paginates(self):
        conn = mem_db()
        item = lambda i: {"id": f"c{i}", "status": "outstanding", "delivered_on": "2026-10-13",  # noqa: E731
                          "persons_entitled": [{"name": "X Bank PLC"}]}
        pages = [_FakeResp(200, payload={"total_count": 3, "items": [item(1), item(2)]}),
                 _FakeResp(200, payload={"total_count": 3, "items": [item(3)]})]
        client = ingest.RestClient("key", session=_FakeSession(pages))
        self.assertEqual(ingest.backfill(conn, client, ["99000001"]), 3)

    def test_rest_404_is_none(self):
        client = ingest.RestClient("key", session=_FakeSession([_FakeResp(404)]))
        self.assertIsNone(client.company("00000000"))


def _charge(cid, lender, cat, created, status="outstanding", satisfied=None):
    return {"charge_id": cid, "status": status, "created_on": created, "delivered_on": created,
            "satisfied_on": satisfied, "charge_type": None, "deal_date": created, "on_register": True,
            "lenders": [{"name": normalise_name(lender), "display": lender, "category": cat}],
            "primary": cat, "filing_url": "https://example/" + cid}


class SignalsTests(unittest.TestCase):
    TODAY = date(2026, 10, 20)

    def kinds(self, charges, fetched=True):
        return [s["type"] for s in signals.for_company("01", "Acme Ltd", charges, fetched, self.TODAY)]

    def test_refinanced_from_bank_to_private_credit(self):
        found = signals.for_company("01", "Acme Ltd", [
            _charge("a", "Big Bank PLC", "bank", "2019-03-01", "fully-satisfied", "2026-10-01"),
            _charge("b", "Northgate Credit Partners", "direct_lender", "2026-09-25"),
        ], True, self.TODAY)
        self.assertEqual([s["type"] for s in found], ["refinanced"])
        self.assertIn("moved from a bank to private credit", found[0]["meaning"])
        self.assertTrue(found[0]["private_credit"])
        self.assertEqual(len(found[0]["charges"]), 2)

    def test_new_lender_alongside_and_top_up(self):
        base = _charge("a", "Big Bank PLC", "bank", "2021-01-01")
        self.assertEqual(self.kinds([base, _charge("b", "Fund LP", "direct_lender", "2026-09-01")]), ["alongside"])
        self.assertEqual(self.kinds([base, _charge("b", "Big Bank PLC", "bank", "2026-09-01")]), ["top_up"])

    def test_first_loan_needs_the_full_history(self):
        new = [_charge("b", "Fund LP", "direct_lender", "2026-09-01")]
        self.assertEqual(self.kinds(new), ["first_loan"])
        self.assertEqual(self.kinds(new, fetched=False), [])
        old = _charge("a", "Big Bank PLC", "bank", "2010-01-01", "fully-satisfied", "2015-01-01")
        self.assertEqual(self.kinds([old] + new), ["returning"])

    def test_charges_made_together_are_one_deal(self):
        found = self.kinds([_charge(c, "Fund LP", "direct_lender", "2026-09-01") for c in "xyz"])
        self.assertEqual(found, ["first_loan"])

    def test_repaid_with_nothing_new(self):
        found = signals.for_company("01", "Acme Ltd", [
            _charge("a", "Big Bank PLC", "bank", "2018-01-01", "fully-satisfied", "2026-10-10"),
            _charge("b", "Big Bank PLC", "bank", "2018-01-01", "fully-satisfied", "2026-10-10"),
        ], True, self.TODAY)
        self.assertEqual([s["type"] for s in found], ["repaid"])
        self.assertIn("2 charges", found[0]["meaning"])

    def test_maturity_window_for_private_credit_only(self):
        self.assertEqual(self.kinds([_charge("a", "Fund LP", "direct_lender", "2022-06-01")]), ["maturity"])
        self.assertEqual(self.kinds([_charge("a", "Fund LP", "direct_lender", "2025-06-01")]), [])  # too young
        self.assertEqual(self.kinds([_charge("a", "Fund LP", "direct_lender", "2012-06-01")]), [])  # likely stale
        self.assertEqual(self.kinds([_charge("a", "Big Bank PLC", "bank", "2022-06-01")]), [])
        two = self.kinds([_charge("a", "Fund LP", "direct_lender", "2022-06-01"),
                          _charge("b", "Fund LP", "direct_lender", "2024-06-01")])
        self.assertEqual(two, ["maturity"])

    def test_history_is_fetched_and_merged(self):
        conn = loaded_db()
        old = {"id": "old1", "status": "fully-satisfied", "created_on": "2015-01-01", "delivered_on": "2015-01-05",
               "satisfied_on": "2020-01-01", "persons_entitled": [{"name": "Lloyds Bank PLC"}]}
        sess = _FakeSession([_FakeResp(200, payload={"total_count": 1, "items": [old]})] * 50)
        n = ingest.history(conn, ingest.RestClient("key", session=sess), limit=1)
        self.assertEqual(n, 1)
        number = conn.execute("SELECT company_number FROM borrower_history_fetched").fetchone()[0]
        rows, fetched = signals.company_charges(conn, number, signals.Namer(conn))
        self.assertTrue(fetched)
        self.assertIn("old1", [r["charge_id"] for r in rows])
        self.assertEqual(next(r for r in rows if r["charge_id"] == "old1")["primary"], "bank")  # from the rules
        self.assertIsNone(conn.execute("SELECT 1 FROM charges WHERE charge_id = 'old1'").fetchone())  # register untouched
        self.assertEqual(ingest.history(conn, ingest.RestClient("key", session=sess), limit=1), 1)  # next borrower


class SectorTests(unittest.TestCase):
    def test_sic_codes_map_to_sectors(self):
        self.assertEqual(sectors.sectors_of(["68209"]), ["real_estate"])
        self.assertEqual(sectors.sectors_of(["70100", "62012"]), ["holding", "tech_media"])
        self.assertEqual(sectors.sectors_of(["86101"]), ["healthcare"])
        self.assertEqual(sectors.sectors_of(["99999"]), ["dormant"])
        self.assertEqual(sectors.sectors_of([]), ["unknown"])
        self.assertEqual(sectors.sectors_of(["bad"]), ["unknown"])


class TriageTests(unittest.TestCase):
    def test_family_keys_merge_variants_only(self):
        from radar.ui.api import family_key
        self.assertEqual(family_key("FORTYS CAPITAL 6"), "FORTYS CAPITAL")
        self.assertEqual(family_key("FORTYS CAPITAL"), "FORTYS CAPITAL")
        self.assertEqual(family_key("MS LENDING SPV1"), "MS LENDING")
        self.assertEqual(family_key("ROMACO SPV 5 LIMITED CRN 11112580"), "ROMACO")
        self.assertEqual(family_key("TOGETHER COMMERCIAL FINANCE LIMITED COMPANY NUMBER 02058813"),
                         "TOGETHER COMMERCIAL FINANCE")
        self.assertEqual(family_key("VIDA BANK LIMITED TRADING AS VIDA HOMELOANS"), "VIDA BANK")
        self.assertEqual(family_key("SBF FIVE"), "SBF FIVE")
        self.assertEqual(family_key("PP08 FINANCE"), "PP08 FINANCE")
        self.assertEqual(family_key("ABC 2"), "ABC 2")  # too short a stem to trust

    def test_low_stakes_types_need_less_confidence(self):
        self.assertEqual(classify._status_for("bank", 0.65), "auto_ok")
        self.assertEqual(classify._status_for("property_lender", 0.6), "auto_ok")
        self.assertEqual(classify._status_for("other", 0.5), "needs_review")
        self.assertEqual(classify._status_for("direct_lender", 0.85), "needs_review")
        self.assertEqual(classify._status_for("security_agent", 0.8), "needs_review")
        self.assertEqual(classify._status_for("unknown", 1.0), "needs_review")

    def test_restatus_keeps_reviewer_flags(self):
        conn = mem_db()
        conn.execute("INSERT INTO lender_classifications VALUES ('A BANK','A Bank','bank',NULL,0.7,'agent','needs_review','x',NULL)")
        conn.execute("INSERT INTO lender_classifications VALUES ('B BANK','B Bank','bank',NULL,0.7,'agent','needs_review','x | reviewer: odd',NULL)")
        self.assertEqual(classify.restatus(conn), 1)
        st = dict(conn.execute("SELECT name_norm, review_status FROM lender_classifications").fetchall())
        self.assertEqual(st, {"A BANK": "auto_ok", "B BANK": "needs_review"})

    def test_property_names_match_starter_rules(self):
        rules = load_rules()
        for name in ("KENSINGTON MORTGAGE COMPANY", "ASPEN BRIDGING", "VIDA BANK TRADING AS VIDA HOMELOANS",
                     "TOGETHER COMMERCIAL FINANCE LIMITED COMPANY NUMBER 02058813"):
            self.assertEqual(find_rule(name, rules).category, "property_lender", name)
        self.assertEqual(export._tier("property_lender", "auto_ok", ["property_lender"]), "C")


class ClassifyTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.conn = loaded_db()
        classify.refresh_rule_matches(self.conn, load_rules())
        self.results = self.tmp / "results"; self.results.mkdir()

    def status(self, name):
        return self.conn.execute(
            "SELECT category, source, review_status FROM lender_classifications WHERE name_norm=?",
            (name,)).fetchone()

    def test_rules_classify_known_names(self):
        self.assertEqual(tuple(self.status("HSBC UK BANK")), ("bank", "seed", "auto_ok"))
        self.assertEqual(self.status("GLAS TRUST CORPORATION")["category"], "security_agent")

    def test_queue_has_only_unknown_names(self):
        q = self.tmp / "queue.jsonl"
        n = classify.build_queue(self.conn, q)
        names = [json.loads(l)["name_norm"] for l in q.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(n, 3)
        self.assertEqual(names[0], "NORTHGATE CREDIT PARTNERS II SCSP")  # most charges first
        self.assertIn("filing_url", json.loads(q.read_text(encoding="utf-8").splitlines()[0])["examples"][0])

    def test_apply_results_validates(self):
        (self.results / "r1.jsonl").write_text("\n".join(sample_agent_results()) + "\nnot json\n",
                                              encoding="utf-8")
        totals = classify.apply_results(self.conn, self.results)
        self.assertEqual((totals["ok"], totals["rejected"]), (3, 3))
        self.assertEqual(tuple(self.status("NORTHGATE CREDIT PARTNERS II SCSP")),
                         ("direct_lender", "agent", "auto_ok"))
        self.assertEqual(self.status("BRIGHTWATER CAPITAL HOLDINGS")["review_status"], "needs_review")
        # Applying the same file again does nothing.
        self.assertEqual(classify.apply_results(self.conn, self.results)["files"], 0)
        self.assertEqual(classify.build_queue(self.conn, self.tmp / "q.jsonl"), 0)

    def test_agent_cannot_overwrite_rule(self):
        (self.results / "r.jsonl").write_text(json.dumps(
            {"name_norm": "HSBC UK BANK", "category": "direct_lender", "confidence": 0.99,
             "reason": "x"}) + "\n", encoding="utf-8")
        self.assertEqual(classify.apply_results(self.conn, self.results)["rejected"], 1)
        self.assertEqual(self.status("HSBC UK BANK")["category"], "bank")

    def test_direct_lender_needs_higher_confidence(self):
        (self.results / "r.jsonl").write_text(json.dumps(
            {"name_norm": "NORTHGATE CREDIT PARTNERS II SCSP", "category": "direct_lender",
             "confidence": 0.87, "reason": "x"}) + "\n", encoding="utf-8")
        classify.apply_results(self.conn, self.results)
        self.assertEqual(self.status("NORTHGATE CREDIT PARTNERS II SCSP")["review_status"], "needs_review")

    def test_reviewer_flags(self):
        (self.results / "r.jsonl").write_text("\n".join(sample_agent_results()), encoding="utf-8")
        classify.apply_results(self.conn, self.results)
        reviews = self.tmp / "reviews"; reviews.mkdir()
        (reviews / "w-flags.jsonl").write_text("\n".join([
            json.dumps({"name_norm": "Northgate Credit Partners II SCSp", "issue": "is a bank",
                        "suggested_category": "bank"}),
            json.dumps({"name_norm": "HSBC UK BANK", "issue": "seed rule disputed"}),
        ]), encoding="utf-8")
        totals = classify.apply_flags(self.conn, reviews)
        self.assertEqual((totals["flagged"], totals["ignored"]), (1, 1))
        self.assertEqual(self.status("NORTHGATE CREDIT PARTNERS II SCSP")["review_status"], "needs_review")
        self.assertEqual(self.status("HSBC UK BANK")["review_status"], "auto_ok")

    def test_removed_rule_releases_name(self):
        rules = [r for r in load_rules() if r.pattern != "PRIVATE DEBT"]
        classify.refresh_rule_matches(self.conn, rules)
        self.assertIsNone(self.status("FERNHILL PRIVATE DEBT FUND LP"))


class ExportTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.conn = loaded_db()
        classify.refresh_rule_matches(self.conn, load_rules())
        res = self.tmp / "res"; res.mkdir()
        (res / "r.jsonl").write_text("\n".join(sample_agent_results()), encoding="utf-8")
        classify.apply_results(self.conn, res)

    def test_tiers(self):
        rows = {r["company_number"]: r for r in export.collect(self.conn, WEEK, WEEK_END)}
        self.assertNotIn("99000009", rows)          # deleted
        self.assertNotIn("99000010", rows)          # next week
        self.assertEqual(rows["99000002"]["tier"], "A")   # direct lender beats agent
        self.assertEqual(rows["99000004"]["tier"], "A")
        self.assertEqual(rows["99000003"]["tier"], "B")
        self.assertEqual(rows["99000005"]["tier"], "C")
        self.assertEqual(rows["99000001"]["tier"], "E")
        self.assertEqual(rows["99000006"]["tier"], "E")
        self.assertEqual(rows["99000007"]["tier"], "D")
        self.assertEqual(rows["99000002"]["needs_human_check"], "yes")

    def test_files_written(self):
        md = export.write_digest(self.conn, WEEK, WEEK_END, self.tmp / "weekly")
        text = md.read_text(encoding="utf-8")
        self.assertIn("Tier A", text)
        self.assertIn("NORTHGATE", text.upper())
        with open(md.with_suffix(".csv"), encoding="utf-8") as fh:
            self.assertEqual(len(list(csv.DictReader(fh))), 7)
        meta = json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual((meta["counts"]["A"], meta["satisfactions"]), (2, 1))

    def test_coverage(self):
        # Sample data was first seen "now", i.e. after the sample week: coverage is incomplete.
        covered, note = export.coverage(self.conn, WEEK, WEEK_END)
        self.assertFalse(covered)
        self.assertIn("started watching", note)
        md = export.write_digest(self.conn, WEEK, WEEK_END, self.tmp / "weekly")
        self.assertIn("Incomplete coverage", md.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))["coverage"],
                         "incomplete")
        # Once the radar was watching from before the window, it counts as covered...
        self.conn.execute("INSERT INTO meta VALUES ('coverage_start', '2026-10-01')")
        self.assertEqual(export.coverage(self.conn, WEEK, WEEK_END), (True, ""))
        # ...unless the stream position expired during the window.
        self.conn.execute("INSERT INTO meta VALUES ('stream_gap_at', '2026-10-14T09:00:00')")
        self.assertFalse(export.coverage(self.conn, WEEK, WEEK_END)[0])
        self.assertTrue(export.coverage(self.conn, date(2026, 10, 19), date(2026, 10, 25))[0])
        self.assertFalse(export.coverage(mem_db(), WEEK, WEEK_END)[0])

    def test_week_bounds(self):
        (ls, le), (ts, te) = export.week_bounds(date(2026, 10, 21))  # a Wednesday
        self.assertEqual((ls, le, ts, te), (date(2026, 10, 12), date(2026, 10, 18),
                                            date(2026, 10, 19), date(2026, 10, 21)))


class AgentCheckTests(TempDirTest):
    def setUp(self):
        super().setUp()
        root = self.tmp / "repo"
        for d in ("classify/results", "output/weekly", "reviews", "drafts", "inbox/classifier"):
            (root / d).mkdir(parents=True)
        for name, value in {"ROOT": root, "RESULTS_DIR": root / "classify/results",
                            "QUEUE_PATH": root / "classify/queue.jsonl",
                            "OUTPUT_DIR": root / "output"}.items():
            p = mock.patch.object(config, name, value); p.start(); self.addCleanup(p.stop)
        self.root = root

    def run_quiet(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return agentcheck.main(list(args))

    def test_classifier_no_work_then_work(self):
        self.assertEqual(self.run_quiet("prepare", "classifier"), agentcheck.NO_WORK)
        (self.root / "classify/queue.jsonl").write_text(
            json.dumps({"name_norm": "A"}) + "\n" + json.dumps({"name_norm": "B"}) + "\n", encoding="utf-8")
        (self.root / "classify/results/old.jsonl").write_text(json.dumps({"name_norm": "A"}) + "\n",
                                                              encoding="utf-8")
        self.assertEqual(self.run_quiet("prepare", "classifier"), 0)
        pending = (self.root / "classify/pending.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["name_norm"] for l in pending], ["B"])
        self.assertIn("classify/results/", agentcheck.task_path("classifier").read_text(encoding="utf-8"))

    def test_incomplete_weeks_are_skipped(self):
        weekly = self.root / "output/weekly"
        (weekly / "2026-10-12_2026-10-18.md").write_text("# digest", encoding="utf-8")
        (weekly / "2026-10-12_2026-10-18.json").write_text('{"coverage": "incomplete"}', encoding="utf-8")
        self.assertEqual(self.run_quiet("prepare", "reviewer"), agentcheck.NO_WORK)
        self.assertEqual(self.run_quiet("prepare", "newsletter"), agentcheck.NO_WORK)
        (weekly / "2026-10-12_2026-10-18.json").write_text('{"coverage": "full"}', encoding="utf-8")
        self.assertEqual(self.run_quiet("prepare", "reviewer"), 0)

    def test_inbox_archived_on_finish(self):
        msg = self.root / "inbox/classifier/20261020-from-maintainer.md"
        msg.write_text("Please re-check GLAS names.", encoding="utf-8")
        self.assertEqual(self.run_quiet("prepare", "classifier"), 0)
        self.assertIn("re-check GLAS", agentcheck.task_path("classifier").read_text(encoding="utf-8"))
        self.run_quiet("finish", "classifier")
        self.assertFalse(msg.exists())
        self.assertTrue((self.root / "inbox/classifier/done" / msg.name).exists())

    def test_reviewer_then_newsletter(self):
        weekly = self.root / "output/weekly"
        (weekly / "2026-10-12_2026-10-18.md").write_text("# digest", encoding="utf-8")
        (weekly / "2026-10-19_2026-10-21_partial.md").write_text("# partial", encoding="utf-8")
        self.assertEqual(self.run_quiet("prepare", "reviewer"), 0)
        task = agentcheck.task_path("reviewer").read_text(encoding="utf-8")
        self.assertIn("reviews/2026-10-12_2026-10-18-review.md", task)
        self.assertNotIn("partial", task)
        (self.root / "reviews/2026-10-12_2026-10-18-review.md").write_text("ok", encoding="utf-8")
        self.assertEqual(self.run_quiet("prepare", "reviewer"), agentcheck.NO_WORK)
        self.assertEqual(self.run_quiet("prepare", "newsletter"), 0)
        (self.root / "drafts/2026-10-12_2026-10-18-draft.md").write_text("d", encoding="utf-8")
        self.assertEqual(self.run_quiet("prepare", "newsletter"), agentcheck.NO_WORK)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class DbSyncTests(TempDirTest):
    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def setUp(self):
        super().setUp()
        self.remote = self.tmp / "remote.git"
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.git("config", "user.email", "t@t"); self.git("config", "user.name", "t")
        self.git("remote", "add", "origin", str(self.remote))
        p = mock.patch.object(config, "ROOT", self.repo); p.start(); self.addCleanup(p.stop)
        self.db = self.repo / "data" / "radar.db"
        conn = connect(self.db)
        for ev in sample_events(WEEK):
            ingest.process_event(conn, ev)
        conn.commit(); conn.close()

    def run_quiet(self, fn, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args)

    def test_pull_without_branch(self):
        self.assertEqual(self.run_quiet(dbsync.pull, self.db), 1)

    def test_round_trip_and_single_commit(self):
        original = self.db.read_bytes()
        self.assertEqual(self.run_quiet(dbsync.push, self.db), 0)
        self.assertEqual(self.run_quiet(dbsync.push, self.db), 0)   # force-push replaces
        self.assertEqual(self.git("rev-list", "--count", "radar-data", cwd=self.remote), "1")
        self.db.unlink()
        self.assertEqual(self.run_quiet(dbsync.pull, self.db), 0)
        self.assertEqual(self.db.read_bytes(), original)
        conn = connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0], 10)
        conn.close()
        # The working folder and current branch are untouched.
        self.assertEqual(self.git("status", "--porcelain"), "?? data/")


class ReviewAppTests(TempDirTest):
    """The review app's actions, with git turned off (folder isn't a repo)."""

    def setUp(self):
        super().setUp()
        from radar.ui import api
        self.api = api
        root = self.tmp / "proj"
        (root / "data").mkdir(parents=True)
        shutil.copy(config.LENDERS_CSV, root / "data" / "lenders.csv")
        for d in ("inbox/maintainer", "drafts", "reviews", "output/weekly"):
            (root / d).mkdir(parents=True)
        for name, value in {"ROOT": root, "LENDERS_CSV": root / "data" / "lenders.csv",
                            "DB_PATH": root / "data" / "radar.db", "OUTPUT_DIR": root / "output"}.items():
            p = mock.patch.object(config, name, value); p.start(); self.addCleanup(p.stop)
        self.root = root
        self.conn = connect(config.DB_PATH)
        self.addCleanup(self.conn.close)
        for ev in sample_events(WEEK):
            ingest.process_event(self.conn, ev)
        self.conn.commit()
        classify.refresh_rule_matches(self.conn, load_rules())
        res = self.tmp / "res"; res.mkdir()
        (res / "r.jsonl").write_text("\n".join(sample_agent_results()), encoding="utf-8")
        classify.apply_results(self.conn, res)
        self.git = api.Git()
        self.git.in_repo = self.git.has_remote = False

    def test_decisions_lists_what_needs_a_person(self):
        d = self.api.decisions(self.conn)
        names = lambda g: [x["name"] for x in d["lenders"][g]]  # noqa: E731
        # An unsure "other" can't change the headline, so it goes to quick triage, not the main queue.
        self.assertEqual(names("review"), [])
        self.assertEqual(names("low"), ["BRIGHTWATER CAPITAL HOLDINGS"])
        self.assertIn("NORTHGATE CREDIT PARTNERS II SCSP", names("confirm"))
        self.assertTrue(d["lenders"]["low"][0]["examples"][0]["filing_url"].startswith("https://"))
        self.assertNotIn("unknown", [c["key"] for c in d["choices"]])

    def test_decide_and_approve_write_human_rules(self):
        before = self.api.version()
        self.api.decide(self.conn, self.git, "Brightwater Capital Holdings Limited", "individual", "Smith family")
        self.api.approve(self.conn, self.git, ["NORTHGATE CREDIT PARTNERS II SCSP"])
        rows = {r["name"]: r for r in self.api.lenders(self.conn)["rows"]}
        self.assertEqual((rows["BRIGHTWATER CAPITAL HOLDINGS"]["source"],
                          rows["BRIGHTWATER CAPITAL HOLDINGS"]["category"]), ("human", "individual"))
        self.assertEqual(rows["NORTHGATE CREDIT PARTNERS II SCSP"]["status"], "ok")
        self.assertEqual(self.api.decisions(self.conn)["lenders"]["low"], [])
        self.assertIn("BRIGHTWATER CAPITAL HOLDINGS,individual,Smith family",
                      config.LENDERS_CSV.read_text(encoding="utf-8"))
        self.assertNotEqual(before, self.api.version())

    def test_decide_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            self.api.decide(self.conn, self.git, "JOHN EXAMPLE", "unknown")
        with self.assertRaises(ValueError):
            self.api.decide(self.conn, self.git, "JOHN EXAMPLE", "hedge_fund")
        with self.assertRaises(KeyError):
            self.api.decide(self.conn, self.git, "Not In The Data Ltd", "bank")

    def test_messages_and_drafts(self):
        msg = self.root / "inbox/maintainer/20261020T1200Z-from-reviewer.md"
        msg.write_text("Check this week", encoding="utf-8")
        draft = self.root / "drafts/2026-10-12_2026-10-18-draft.md"
        draft.write_text("Draft", encoding="utf-8")
        self.assertEqual(len(self.api.decisions(self.conn)["messages"]), 1)
        self.api.message_done(self.git, "inbox/maintainer/20261020T1200Z-from-reviewer.md")
        self.assertTrue((self.root / "inbox/maintainer/done" / msg.name).exists())
        self.assertEqual(self.api.decisions(self.conn)["messages"], [])
        self.api.save_draft(self.git, "drafts/2026-10-12_2026-10-18-draft.md", "Edited\r\ntext")
        self.assertEqual(draft.read_text(encoding="utf-8"), "Edited\ntext")
        self.api.set_draft_status(self.git, "drafts/2026-10-12_2026-10-18-draft.md", "published")
        self.assertEqual(self.api.decisions(self.conn)["drafts"], [])
        self.assertEqual(self.api.documents()["drafts"][0]["status"], "published")

    def test_paths_cannot_escape_their_folder(self):
        for bad in ("../data/lenders.csv", "drafts/../../x.md", "data/lenders.csv", "drafts/x.txt"):
            with self.assertRaises(ValueError):
                self.api.save_draft(self.git, bad, "x")
        with self.assertRaises(ValueError):
            self.api.message_done(self.git, "drafts/whatever.md")

    def test_charges_filters(self):
        all_rows = self.api.charges(self.conn)
        self.assertEqual(all_rows["total"], 9)
        self.assertEqual({r["tier"] for r in self.api.charges(self.conn, tier="A")["rows"]}, {"A"})
        hits = self.api.charges(self.conn, q="glas")["rows"]
        self.assertEqual({r["company_number"] for r in hits}, {"99000002", "99000003"})


    def test_directory_signals_company_and_notes(self):
        d = self.api.directory(self.conn)
        self.assertEqual(sum(c["count"] for c in d["categories"]), d["total"])
        self.assertTrue(all("borrowers" in r for r in d["rows"]))
        self.assertTrue(d["watch"])  # rules nothing has matched yet
        sig = self.api.signals(self.conn)
        self.assertEqual(sig["total"], sum(sig["counts"].values()))
        number = self.conn.execute("SELECT company_number FROM charges LIMIT 1").fetchone()[0]
        c = self.api.company(self.conn, number)
        self.assertEqual(c["number"], number)
        self.assertFalse(c["history_fetched"])
        self.assertTrue(c["charges"][0]["lenders"][0]["known"])
        with self.assertRaises(KeyError):
            self.api.company(self.conn, "NOPE")
        name = d["rows"][0]["name"]
        self.api.save_note(self.conn, self.git, name, "  Mid-market lender  ")
        self.assertEqual(self.api.lender(self.conn, name)["note"], "Mid-market lender")
        self.api.save_note(self.conn, self.git, name, "")
        self.assertEqual(self.api.lender(self.conn, name)["note"], "")
        with self.assertRaises(KeyError):
            self.api.save_note(self.conn, self.git, "Not In The Data Ltd", "x")
        self.assertIn("signals", self.api.overview(self.conn, self.git))
        # A family decision covers every variant and can add a rule for future names.
        lender = self.conn.execute("SELECT name_norm FROM charge_persons LIMIT 1").fetchone()[0]
        res = self.api.decide(self.conn, self.git, lender, "bank", "", "", [], "")
        self.assertEqual(res["names"], [lender])
        with self.assertRaises(ValueError):
            self.api.decide(self.conn, self.git, lender, "bank", future="NOT IN THE NAME")
        filed = self.api.file_as(self.conn, self.git, [lender, "Not In The Data Ltd"], "property_lender")
        self.assertEqual(filed["filed"], [lender])
        self.assertEqual(self.api.lender(self.conn, lender)["category"], "property_lender")
        every = self.api.charges(self.conn)
        no_prop = self.api.charges(self.conn, hide_property=True)
        self.assertLessEqual(no_prop["total"], every["total"])
        self.assertTrue(all(r["sector"] != "real_estate" for r in no_prop["rows"]))
        first = every["rows"][0]["sector"]
        self.assertTrue(all(first in r["sectors"] for r in self.api.charges(self.conn, sector=first)["rows"]))

class ReviewAppHttpTests(ReviewAppTests):
    """The real HTTP server: host check, token check, and a decision over HTTP."""

    def setUp(self):
        super().setUp()
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from radar.ui import server
        self.urllib = urllib.request
        app = server.App(sync_enabled=False)
        app.git = self.git
        self.app = app
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app, 0))
        port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = server.make_handler(app, port)
        self.base = f"http://127.0.0.1:{port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def req(self, path, body=None, token=True, host=None):
        data = json.dumps(body).encode() if body is not None else None
        r = self.urllib.Request(self.base + path, data=data, method="POST" if data else "GET")
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("X-Radar-Token", self.app.token)
        if host:
            r.add_header("Host", host)
        try:
            with self.urllib.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read()
        except self.urllib.HTTPError as e:
            return e.code, e.read()

    def test_http(self):
        status, page = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), page)
        self.assertEqual(self.req("/api/summary", host="evil.example")[0], 403)
        self.assertEqual(self.req("/api/decide", {"name": "JOHN EXAMPLE", "category": "bank"}, token=False)[0], 403)
        self.assertEqual(self.req("/api/decide", {"name": "JOHN EXAMPLE", "category": "nope"})[0], 400)
        status, body = self.req("/api/decide", {"name": "JOHN EXAMPLE", "category": "individual"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(self.req("/api/lender?name=JOHN%20EXAMPLE")[1])["source"], "human")
        self.assertEqual(self.req("/static/../../schema.sql")[0], 404)
        self.assertEqual(self.req("/api/lender?name=nobody")[0], 404)
        self.assertEqual(json.loads(self.req("/api/summary")[1])["charges"], 9)


if __name__ == "__main__":
    unittest.main()

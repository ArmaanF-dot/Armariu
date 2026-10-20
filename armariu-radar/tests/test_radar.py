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

from radar import agentcheck, classify, config, dbsync, export, ingest  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()

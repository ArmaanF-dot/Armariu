"""Dated database backups (radar/dbbackup.py): retention rule, save, restore.
GitHub releases are replaced by an in-memory store; git uses a local bare repo as the remote."""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import config, dbbackup, dbsync  # noqa: E402
from radar.db import connect  # noqa: E402


def names_every(hours: int, count: int, end: datetime) -> list[str]:
    return [dbbackup.backup_name("radar", end - timedelta(hours=hours * i)) for i in range(count)]


class RetentionTests(unittest.TestCase):
    END = datetime(2026, 11, 9, 18, 23, tzinfo=timezone.utc)

    def test_few_backups_are_all_kept(self):
        self.assertEqual(dbbackup.to_delete(names_every(3, 14, self.END), "radar"), [])

    def test_a_year_of_three_hourly_runs(self):
        names = names_every(3, 8 * 365, self.END)
        drop = set(dbbackup.to_delete(names, "radar"))
        kept = [n for n in names if n not in drop]
        self.assertLessEqual(len(kept), 14 + 14 + 27)
        self.assertGreaterEqual(len(kept), 26 + 13)
        self.assertEqual(kept[:14], names[:14])                     # last 14 runs
        days = {dbbackup.parse_name(n, "radar").date() for n in kept}
        for d in range(14):                                         # one per day for 14 days
            self.assertIn((self.END - timedelta(days=d)).date(), days)
        oldest = min(dbbackup.parse_name(n, "radar") for n in kept)
        self.assertGreater(self.END - oldest, timedelta(weeks=25))  # weekly copies reach ~6 months
        self.assertLess(self.END - oldest, timedelta(weeks=27))

    def test_newest_backup_anchors_the_windows(self):
        # A pipeline that stopped months ago keeps its history: no wall clock involved.
        names = names_every(24, 60, datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertLess(len(dbbackup.to_delete(names, "radar")), 60 - 14)

    def test_other_files_are_never_touched(self):
        names = names_every(3, 40, self.END) + ["notes.txt", "backfill-20260101T000000Z.db.gz"]
        drop = dbbackup.to_delete(names, "radar")
        self.assertNotIn("notes.txt", drop)
        self.assertNotIn("backfill-20260101T000000Z.db.gz", drop)


class FakeReleases:
    def __init__(self, root: Path):
        self.root = root
        self.store: dict[str, dict[str, bytes]] = {}

    def list_assets(self, tag):
        if tag not in self.store:
            return None
        return [{"name": n, "size": len(b)} for n, b in self.store[tag].items()]

    def ensure_release(self, tag, prefix):
        self.store.setdefault(tag, {})

    def upload(self, tag, path):
        self.store[tag][Path(path).name] = Path(path).read_bytes()

    def delete_asset(self, tag, name):
        del self.store[tag][name]

    def download(self, tag, name, dest_dir):
        out = Path(dest_dir) / name
        out.write_bytes(self.store[tag][name])
        return out


@unittest.skipUnless(shutil.which("git"), "git not installed")
class SaveRestoreTests(unittest.TestCase):
    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.remote = self.tmp / "remote.git"
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.git("config", "user.email", "t@t"); self.git("config", "user.name", "t")
        self.git("remote", "add", "origin", str(self.remote))
        self.db = self.repo / "data" / "radar.db"
        for name, value in {"ROOT": self.repo, "DB_PATH": self.db}.items():
            p = mock.patch.object(config, name, value); p.start(); self.addCleanup(p.stop)
        self.fake = FakeReleases(self.tmp)
        for fn in ("list_assets", "ensure_release", "upload", "delete_asset", "download"):
            p = mock.patch.object(dbbackup, fn, getattr(self.fake, fn)); p.start(); self.addCleanup(p.stop)
        self.write_rows(5)

    def write_rows(self, n):
        conn = connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        conn.execute("DELETE FROM t")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(n)])
        conn.commit(); conn.close()

    def rows(self, path=None):
        conn = connect(path or self.db)
        try:
            return conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        finally:
            conn.close()

    def quiet(self, fn, *args, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args, **kw)

    def test_save_without_branch_does_nothing(self):
        self.assertEqual(self.quiet(dbbackup.save), 0)
        self.assertEqual(self.fake.store, {})

    def test_save_is_idempotent_and_restore_puts_it_back(self):
        self.assertEqual(self.quiet(dbsync.push, self.db), 0)
        self.assertEqual(self.quiet(dbbackup.save), 0)
        self.assertEqual(self.quiet(dbbackup.save), 0)               # same snapshot: no duplicate
        backups = self.fake.store["db-backups-radar"]
        self.assertEqual(len(backups), 1)
        first = next(iter(backups))
        self.assertIsNotNone(dbbackup.parse_name(first, "radar"))

        # A later run saves something different (allowed: more rows).
        self.write_rows(50)
        self.assertEqual(self.quiet(dbsync.push, self.db), 0)
        self.assertEqual(self.rows(), 50)

        # Restore the first backup: local copy and branch both go back to 5 rows.
        self.assertEqual(self.quiet(dbbackup.restore, first), 0)
        self.assertEqual(self.rows(), 5)
        self.assertTrue(self.db.with_name("radar.db.before-restore").exists())
        self.db.unlink()
        self.assertEqual(self.quiet(dbsync.pull, self.db), 0)
        self.assertEqual(self.rows(), 5)

    def test_restore_from_downloaded_file_local_only(self):
        self.assertEqual(self.quiet(dbsync.push, self.db), 0)
        self.assertEqual(self.quiet(dbbackup.save), 0)
        name, data = next(iter(self.fake.store["db-backups-radar"].items()))
        downloaded = self.tmp / name; downloaded.write_bytes(data)
        self.write_rows(50)
        branch_before = self.git("rev-parse", "radar-data", cwd=self.remote)
        self.assertEqual(self.quiet(dbbackup.restore, str(downloaded), local_only=True), 0)
        self.assertEqual(self.rows(), 5)
        self.assertEqual(self.git("rev-parse", "radar-data", cwd=self.remote), branch_before)

    def test_restore_refuses_a_broken_backup(self):
        bad = self.tmp / "radar-20260101T000000Z.db.gz"
        import gzip
        bad.write_bytes(gzip.compress(b"not a database" * 100))
        self.assertEqual(self.quiet(dbbackup.restore, str(bad)), 3)
        self.assertEqual(self.rows(), 5)                              # local copy untouched

    def test_save_prunes_old_backups(self):
        end = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.fake.store["db-backups-radar"] = {n: b"x" for n in names_every(3, 100, end)}
        self.assertEqual(self.quiet(dbsync.push, self.db), 0)
        self.assertEqual(self.quiet(dbbackup.save), 0)
        left = self.fake.store["db-backups-radar"]
        self.assertLess(len(left), 60)
        newest = max(left, key=lambda n: dbbackup.parse_name(n, "radar"))
        self.assertEqual(self.rows_in_backup(left[newest]), 5)         # the new one is kept

    def rows_in_backup(self, data):
        import gzip
        p = self.tmp / "b.db"; p.write_bytes(gzip.decompress(data))
        return self.rows(p)


if __name__ == "__main__":
    unittest.main()

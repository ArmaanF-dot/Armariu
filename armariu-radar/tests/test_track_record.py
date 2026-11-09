import json
import tempfile
import unittest
from pathlib import Path

from radar import track_record as T


class TrackRecordTests(unittest.TestCase):
    def setUp(self):
        self.p = Path(tempfile.mkdtemp()) / "log.jsonl"
        self.rows = [{"company_number": "1", "score": "2.0"}, {"company_number": "2", "score": "1.0"}]

    def test_chain_verifies_and_is_idempotent(self):
        T.append(self.rows, "2026-12", "rw3", self.p, now="t")
        T.append(self.rows, "2026-12", "rw3", self.p, now="t")
        self.assertEqual(len(T.read_log(self.p)), 2)
        self.assertTrue(T.verify(self.p)[0])

    def test_edit_or_delete_breaks_chain(self):
        T.append(self.rows, "2026-12", "rw3", self.p, now="t")
        lines = self.p.read_text().splitlines()
        r = json.loads(lines[0]); r["score"] = "9.9"; lines[0] = json.dumps(r, sort_keys=True)
        self.p.write_text("\n".join(lines) + "\n")
        self.assertFalse(T.verify(self.p)[0])
        T.append  # appending to a broken log must refuse
        with self.assertRaises(SystemExit):
            T.append(self.rows, "2027-01", "rw3", self.p)

    def test_deleting_first_row_breaks(self):
        T.append(self.rows, "2026-12", "rw3", self.p, now="t")
        self.p.write_text("\n".join(self.p.read_text().splitlines()[1:]) + "\n")
        self.assertFalse(T.verify(self.p)[0])

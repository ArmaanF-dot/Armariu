"""Sealed, dated track record of predictions (plan task 12). SWITCHED OFF until the owner says yes.

    python -m radar.track_record append --scores FILE.csv [--month YYYY-MM] [--model rw3]
    python -m radar.track_record verify

How it works: each month's top predictions are appended to data/track_record/log.jsonl, one row per
company. Every row carries `prev` (the hash of the row before) and `hash` (of its own contents plus
`prev`), so editing or deleting any earlier row breaks every later hash. The head hash is also printed so
the owner can note it somewhere outside the repo (an email to themselves) to make the log dated by
something other than the repo. Nothing is published or hosted. Outcomes are never written into the log rows; marking
what happened (a later step, not built yet) goes to a separate file so predictions stay as made.

Off switch: the tool refuses to append unless the file data/track_record/ENABLED exists. Only the owner
creates it (`python -m radar.track_record enable --confirm`).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timezone

from . import config

DIR = config.ROOT / "data" / "track_record"
LOG = DIR / "log.jsonl"
ENABLED = DIR / "ENABLED"
GENESIS = "0" * 64
FIELDS = ("company_number", "company_name", "score", "odds_12m", "risk_percentile", "model_probability", "sector")


def row_hash(row: dict, prev: str) -> str:
    body = json.dumps({k: v for k, v in row.items() if k != "hash"}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode()).hexdigest()


def read_log(path=LOG) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def verify(path=LOG) -> tuple[bool, str]:
    prev = GENESIS
    for i, r in enumerate(read_log(path), 1):
        if r.get("prev") != prev or r.get("hash") != row_hash(r, prev):
            return False, f"row {i} does not match the row before it"
        prev = r["hash"]
    return True, prev


def append(scores: list[dict], month: str, model: str, path=LOG, now=None) -> str:
    ok, head = verify(path)
    if not ok:
        raise SystemExit(f"log is broken, not appending: {head}")
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {(r["month"], r["model"], r["company_number"]) for r in read_log(path)}
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "a") as f:
        for s in scores:
            if (month, model, s["company_number"]) in existing:
                continue
            row = {"month": month, "model": model, "written_at": now,
                   **{k: s.get(k) for k in FIELDS if s.get(k) not in (None, "")}, "prev": head}
            row["hash"] = row_hash(row, head)
            head = row["hash"]
            f.write(json.dumps(row, sort_keys=True) + "\n")
            n += 1
    return head if n else head


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.track_record")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enable"); e.add_argument("--confirm", action="store_true")
    a_ = sub.add_parser("append"); a_.add_argument("--scores", required=True)
    a_.add_argument("--month", default=date.today().strftime("%Y-%m")); a_.add_argument("--model", default="rw3")
    a_.add_argument("--top", type=int, default=5000)
    sub.add_parser("verify")
    a = p.parse_args(argv)
    if a.cmd == "enable":
        if not a.confirm:
            raise SystemExit("Add --confirm. Only the owner does this.")
        DIR.mkdir(parents=True, exist_ok=True); ENABLED.write_text(date.today().isoformat() + "\n")
        print("Track record switched on."); return
    if a.cmd == "verify":
        ok, info = verify(); print("OK, head " + info if ok else "BROKEN: " + info); raise SystemExit(0 if ok else 1)
    if not ENABLED.exists():
        raise SystemExit("Track record is switched off (no data/track_record/ENABLED). The owner decides when to start.")
    rows = list(csv.DictReader(open(a.scores)))[: a.top]
    print("head hash", append(rows, a.month, a.model), f"({len(rows)} rows offered)")


if __name__ == "__main__":
    main()

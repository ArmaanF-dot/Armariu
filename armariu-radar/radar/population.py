"""A random sample of ALL companies, to test the Refinance watch on the real population (BACKTEST).

The radar's own database only holds companies that had a charge filing on the stream from 20 Oct 2026
(a new loan or a repayment), plus their full history. That is a set of firms still active at Companies
House today, so a backtest on it leaves out firms that went quiet and overstates hit rates. This module
draws company numbers uniformly at random, keeps every company whose record links to a charge register,
fetches that register, and scores it on past dates exactly as radar.refi_accuracy does.

    python -m radar.population sample --target 2500     # resumable; ~90 requests a minute
    python -m radar.population status
    python -m radar.population evaluate                  # 90-day and 12-month tables on the sample

Sampling frame: England and Wales company numbers 00000001 to 16600000 (16.6M is about where numbers
stood in July 2026, the last test date; later companies cannot have a charge on a test date). Numbers
that return 404 were never issued or were removed (dissolved long ago); they are counted and skipped.
Dissolved companies still on the register are kept, so firms that stopped borrowing are in the sample.
Not covered: Scottish (SC) and Northern Irish (NI) companies, and companies removed from the register.

The sample lives in output/population/sample.db (git-ignored, schema.sql). The score itself (points,
odds, lender statistics) still comes from the main database, the same as the live list.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config

DIR = config.OUTPUT_DIR / "population"
DB = DIR / "sample.db"
MAX_NUMBER = 16_600_000
SEED = 20261104
LIMIT, WINDOW = 400, 300.0     # requests per 5 minutes: leaves room under CH's 600 for the pipeline and backfill
PIPELINE_MINUTE = 23           # radar.yml runs at :23 every 3 hours (UTC); pause then so it keeps the rate limit

DDL = """
CREATE TABLE IF NOT EXISTS draws (
    seq            INTEGER PRIMARY KEY,   -- position in the seeded random order
    company_number TEXT NOT NULL,
    http           INTEGER,               -- 200 or 404
    company_status TEXT,
    has_register   INTEGER,               -- the company record links to a charge register
    fetched_at     TEXT
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=DB) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript((config.ROOT / "schema.sql").read_text(encoding="utf-8"))
    conn.executescript(DDL)
    return conn


def numbers(seed: int = SEED):
    """The same random order of company numbers every run, so sampling resumes where it stopped."""
    rng, seen = random.Random(seed), set()
    while True:
        n = rng.randint(1, MAX_NUMBER)
        if n not in seen:
            seen.add(n)
            yield f"{n:08d}"


def _pipeline_running(now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    return now.hour % 3 == 0 and PIPELINE_MINUTE - 3 <= now.minute < PIPELINE_MINUTE + 32


def _fetch(client, num):
    for wait in (300, 600, 900, 1200):   # another job is using the shared quota: back off, don't crash
        try:
            return _fetch_once(client, num)
        except RuntimeError as e:
            if "429" not in str(e):
                raise
            time.sleep(wait)
    return _fetch_once(client, num)


def _fetch_once(client, num):
    prof = client.company(num)
    reg = bool(prof and (prof.get("links") or {}).get("charges"))
    return num, prof, reg, (list(client.charges(num)) if reg else [])


class _Shared:
    """A RestClient whose rate-limit guard can be shared by a few threads."""

    def __init__(self, client):
        import threading
        self.client, self.lock = client, threading.Lock()
        throttle = client._throttle

        def guarded():
            with self.lock:
                throttle()
        client._throttle = guarded


def sample(conn, client, target: int, max_minutes: float | None = None, threads: int = 3) -> int:
    """Draw numbers until `target` companies with a charge register have been fetched."""
    from concurrent.futures import ThreadPoolExecutor
    from .ingest import _as_list, _join_descriptions, parse_resource_uri
    _Shared(client)
    done = conn.execute("SELECT COALESCE(MAX(seq), -1) FROM draws").fetchone()[0]
    have = conn.execute("SELECT COUNT(*) FROM draws WHERE has_register = 1").fetchone()[0]
    stop = time.monotonic() + max_minutes * 60 if max_minutes else None
    gen = ((seq, num) for seq, num in enumerate(numbers()) if seq > done)
    with ThreadPoolExecutor(threads) as pool:
        while have < target and not (stop and time.monotonic() > stop):
            while _pipeline_running():
                time.sleep(60)
            batch = [next(gen) for _ in range(30)]
            for (seq, _), (num, prof, reg, items) in zip(batch, pool.map(lambda b: _fetch(client, b[1]), batch)):
                if prof:
                    conn.execute("INSERT OR REPLACE INTO companies (company_number, name, sic_codes, date_of_creation, "
                                 "company_status, enriched_at) VALUES (?,?,?,?,?,?)",
                                 (num, prof.get("company_name"), json.dumps(prof.get("sic_codes") or []),
                                  prof.get("date_of_creation"), prof.get("company_status"), _now()))
                if reg:
                    conn.execute("DELETE FROM borrower_history WHERE company_number = ?", (num,))
                    for item in items:
                        cid = item.get("id") or parse_resource_uri((item.get("links") or {}).get("self", ""))[1]
                        if not cid:
                            continue
                        persons = [(p.get("name") or "").strip() for p in _as_list(item.get("persons_entitled"))]
                        conn.execute("INSERT OR REPLACE INTO borrower_history VALUES (?,?,?,?,?,?,?,?)",
                                     (num, cid, item.get("status"), item.get("created_on"), item.get("delivered_on"),
                                      item.get("satisfied_on"), _join_descriptions(item.get("classification")),
                                      json.dumps([p for p in persons if p])))
                    conn.execute("INSERT OR REPLACE INTO borrower_history_fetched VALUES (?,?,?)", (num, _now(), len(items)))
                    have += 1
                conn.execute("INSERT INTO draws VALUES (?,?,?,?,?,?)",
                             (seq, num, 200 if prof else 404, (prof or {}).get("company_status"), int(reg), _now()))
            conn.commit()
            print(f"draw {batch[-1][0]:,}: {have:,} companies with a charge register", flush=True)
    return have


def status(conn) -> dict:
    r = conn.execute("SELECT COUNT(*) AS draws, SUM(http = 200) AS found, SUM(has_register) AS registers "
                     "FROM draws").fetchone()
    return {"draws": r["draws"], "found": r["found"] or 0, "with_charge_register": r["registers"] or 0,
            "charges": conn.execute("SELECT COUNT(*) FROM borrower_history").fetchone()[0]}


def evaluate(windows=(90, 365), reps: int = 1000) -> dict:
    """refi_accuracy's tables on the random sample: points, odds and lender statistics learned from the
    main database (as the live list is), outcomes and ranks on the sample."""
    from . import refi_accuracy as ra
    from .aliases import Resolver
    from .charge_history import load_companies
    from .db import connect as main_connect
    from .lender_view import coverage_start
    from .refi_watch import lender_volumes
    main = main_connect()
    resolver = Resolver(main)
    known = load_companies(main, resolver)
    vol = lender_volumes(known)
    sample_cos = load_companies(connect(), resolver)
    stop = coverage_start(main)
    out = {}
    for w in windows:
        res = ra.evaluate(sample_cos, vol, window=w, reps=reps, stop=stop, learn_on=known)
        res["population"] = ("Random sample of England and Wales company numbers with a charge register "
                             f"({len(sample_cos):,} companies); score learned on the radar's own data")
        out[w] = res
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.population")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--target", type=int, default=2500)
    s.add_argument("--minutes", type=float, default=None)
    sub.add_parser("status")
    e = sub.add_parser("evaluate")
    e.add_argument("--reps", type=int, default=1000)
    a = p.parse_args(argv)
    if a.cmd == "sample":
        from .ingest import RestClient
        # the key can be a dummy where a proxy adds the real one
        client = RestClient(os.environ.get("CH_REST_KEY") or "proxy", limit=LIMIT, window=WINDOW)
        conn = connect()
        sample(conn, client, a.target, a.minutes)
        print(status(conn))
    elif a.cmd == "status":
        print(status(connect()))
    else:
        from . import refi_accuracy as ra
        out_dir = DIR
        for w, res in evaluate(reps=a.reps).items():
            md = ra.render_md(res)
            (out_dir / f"accuracy_{w}d.md").write_text(md, encoding="utf-8")
            (out_dir / f"accuracy_{w}d.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
            print(md)


if __name__ == "__main__":
    main()

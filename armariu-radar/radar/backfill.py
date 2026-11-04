"""Older charge histories for a random sample of companies, fetched with spare Companies House quota.

Why: every company in the live database was active at Companies House after 20 Oct 2026, so the
Refinance watch backtest only sees firms that were busy recently (the 4 Nov audit's top finding).
This builds a fair population instead: a random sample of every live company that has ever
registered a charge, and fetches each one's full charge list, one REST call per company.

How the sample is drawn
  frame     Companies House's free Basic Company Data file (every live company, monthly) has charge
            counts per company. Every company with at least one charge is in the frame, whether or
            not a charge is outstanding today: a firm that repaid everything in 2025 had an open loan
            on a 2024 test date, so dropping it would select on the future again.
  order     each company gets a fixed pseudo-random rank (SHA-256 of a seed and its number). Fetching
            goes in rank order, so whatever has been fetched so far is always a uniform random sample,
            and stopping or resuming never bends it.
  kept      the first SAMPLE_MAX companies by rank, with their size and status fields from the file
            (counts of outstanding / satisfied charges, SIC codes, incorporation date): the sample
            can be cut to "has an outstanding charge today" later without new calls.
  caveat    dissolved companies are not in the file, so firms that have since closed are missing.

How it uses only spare quota
  In the scheduled pipeline it runs last, on the same REST client as every live step, so it shares
  their rate limit (500 calls / 5 minutes) and their time budget. If the live steps used the whole
  budget, it does nothing that run. It can be given extra minutes per run (`--backfill-minutes`),
  which only ever go to the backfill. It is off unless RADAR_BACKFILL=1 (set in the workflow).

Storage: data/backfill.db, its own SQLite file, synced to the `radar-backfill` branch by
`python -m radar.dbsync push|pull --backfill`. The live database is never touched.

    python -m radar.backfill frame              # build the sample frame (downloads ~450 MB, streamed)
    python -m radar.backfill run --minutes 10   # fetch the next companies in rank order
    python -m radar.backfill status             # progress and estimates

Rules only: no AI. No names of people are stored (lender names are kept as filed, and individuals
are masked when the histories are loaded, as everywhere else).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import io
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

from . import config
from .db import now_iso

log = logging.getLogger("radar.backfill")

SEED = "armariu-backfill-v1"   # never change: it fixes which companies are in the sample
SAMPLE_MAX = 250_000           # companies kept from the frame, in rank order
# Stop fetching before the file gets too big for its branch (GitHub rejects files over 100 MB
# compressed; measured on 108k real charges: ~140 bytes a charge, gzip about 3.3 times smaller,
# so 220 MB is about 1.5 million charges and a ~65 MB file).
MAX_DB_BYTES = int(os.environ.get("RADAR_BACKFILL_MAX_MB", "220")) * 1024 * 1024

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS frame (
    rank            INTEGER PRIMARY KEY,   -- 1 = first to fetch
    company_number  TEXT NOT NULL UNIQUE,
    charges_total   INTEGER,               -- counts as of the snapshot
    outstanding     INTEGER,
    part_satisfied  INTEGER,
    satisfied       INTEGER,
    company_status  TEXT,
    category        TEXT,
    incorporated    TEXT,                  -- yyyy-mm-dd
    sic_codes       TEXT                   -- JSON list of 5-digit codes
);
CREATE TABLE IF NOT EXISTS history (       -- same columns as borrower_history in the live database
    company_number TEXT NOT NULL,
    charge_id      TEXT NOT NULL,
    status         TEXT,
    created_on     TEXT,
    delivered_on   TEXT,
    satisfied_on   TEXT,
    classification TEXT,
    persons        TEXT,                   -- JSON list of names as filed
    PRIMARY KEY (company_number, charge_id)
) WITHOUT ROWID;                           -- about a third smaller: the key is not stored twice
CREATE TABLE IF NOT EXISTS fetched (
    company_number TEXT PRIMARY KEY,
    fetched_at     TEXT NOT NULL,
    total          INTEGER,                -- charges returned
    calls          INTEGER                 -- REST calls it took
);
"""


def connect(path=None) -> sqlite3.Connection:
    path = Path(path or config.BACKFILL_DB_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def _meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r[0] if r else default


def _set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, None if value is None else str(value)))


def rank_key(number: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{SEED}:{number}".encode()).digest()[:8], "big")


# --------------------------------------------------------------------------- frame

def _int(s) -> int:
    try:
        return int((s or "0").strip() or 0)
    except ValueError:
        return 0


def _sic(text: str) -> str:
    m = re.match(r"\s*(\d{4,5})\b", text or "")
    return m.group(1).zfill(5) if m else ""


def frame_from_rows(rows, sample_max: int = SAMPLE_MAX) -> tuple[list[dict], dict]:
    """Pick the sample from Basic Company Data rows (dicts keyed by the file's stripped headers).
    Returns (kept companies in rank order, counts over the whole frame)."""
    from .profile import _iso
    heap: list[tuple[int, str, dict]] = []   # max-heap on rank key via negation, size <= sample_max
    stats = {"companies_read": 0, "frame_size": 0, "frame_outstanding": 0}
    for rec in rows:
        stats["companies_read"] += 1
        total = _int(rec.get("Mortgages.NumMortCharges"))
        if total <= 0:
            continue
        num = (rec.get("CompanyNumber") or "").strip().upper()
        if not num:
            continue
        stats["frame_size"] += 1
        outstanding = _int(rec.get("Mortgages.NumMortOutstanding"))
        stats["frame_outstanding"] += outstanding > 0
        k = rank_key(num)
        if len(heap) >= sample_max and -heap[0][0] < k:
            continue
        row = {"company_number": num, "charges_total": total, "outstanding": outstanding,
               "part_satisfied": _int(rec.get("Mortgages.NumMortPartSatisfied")),
               "satisfied": _int(rec.get("Mortgages.NumMortSatisfied")),
               "company_status": (rec.get("CompanyStatus") or "").strip(),
               "category": (rec.get("CompanyCategory") or "").strip(),
               "incorporated": _iso(rec.get("IncorporationDate") or ""),
               "sic_codes": json.dumps([c for c in (_sic(rec.get(f"SICCode.SicText_{i}")) for i in range(1, 5)) if c])}
        item = (-k, num, row)
        if len(heap) < sample_max:
            heapq.heappush(heap, item)
        else:
            heapq.heapreplace(heap, item)
    kept = [r for _, _, r in sorted(heap, key=lambda t: -t[0])]
    return kept, stats


def _basic_company_rows(name: str, log_fn=log.info):
    from .ownership import _lines
    from .profile import BASE
    header, t0 = None, time.time()
    for i, line in enumerate(_lines(BASE + name)):
        line = line.rstrip(b"\r")
        if not line:
            continue
        rec = next(csv.reader(io.StringIO(line.decode("utf-8", "replace"))))
        if header is None:
            header = [k.strip() for k in rec]
            continue
        if len(rec) < len(header):
            continue
        if i % 1_000_000 == 0 and i:
            log_fn(f"  {i:,} rows read, {time.time() - t0:.0f}s")
        yield dict(zip(header, rec))


def build_frame(conn, rows=None, snapshot: str | None = None, sample_max: int = SAMPLE_MAX) -> dict:
    """Fill the frame table once. Never rebuilt automatically: a fixed frame keeps the sample fixed."""
    if conn.execute("SELECT 1 FROM frame LIMIT 1").fetchone():
        return {"frame": "already built", "snapshot": _meta(conn, "frame_snapshot")}
    if rows is None:
        from .profile import _latest
        name = _latest()
        snapshot = re.search(r"(\d{4}-\d{2}-\d{2})", name).group(1)
        rows = _basic_company_rows(name)
    kept, stats = frame_from_rows(rows, sample_max)
    conn.executemany(
        "INSERT INTO frame VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(i + 1, r["company_number"], r["charges_total"], r["outstanding"], r["part_satisfied"],
          r["satisfied"], r["company_status"], r["category"], r["incorporated"], r["sic_codes"])
         for i, r in enumerate(kept)])
    for k, v in {**stats, "frame_snapshot": snapshot, "seed": SEED, "sample_kept": len(kept),
                 "frame_built_at": now_iso()}.items():
        _set_meta(conn, k, v)
    conn.commit()
    log.info("Sample frame: %s companies with a charge, %s kept", f"{stats['frame_size']:,}", f"{len(kept):,}")
    return {**stats, "sample_kept": len(kept), "snapshot": snapshot}


# --------------------------------------------------------------------------- fetching

def _store(conn, num: str, items: list[dict], calls: int) -> None:
    from .ingest import _as_list, _join_descriptions, parse_resource_uri
    conn.execute("DELETE FROM history WHERE company_number = ?", (num,))
    for item in items:
        charge_id = item.get("id") or parse_resource_uri((item.get("links") or {}).get("self", ""))[1]
        if not charge_id:
            continue
        persons = [(p.get("name") or "").strip() for p in _as_list(item.get("persons_entitled"))]
        conn.execute("INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?,?,?)",
                     (num, charge_id, item.get("status"), item.get("created_on"), item.get("delivered_on"),
                      item.get("satisfied_on"), _join_descriptions(item.get("classification")),
                      json.dumps([p for p in persons if p])))
    conn.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?)", (num, now_iso(), len(items), calls))


def _too_big(conn) -> bool:
    page = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
    return page >= MAX_DB_BYTES


def fetch(conn, client, limit: int | None = None) -> dict:
    """Fetch the next companies in rank order until the client is out of time (or `limit`)."""
    done = calls = charges = 0
    stopped = "finished the sample"
    while limit is None or done < limit:
        batch = conn.execute(
            "SELECT f.company_number FROM frame f LEFT JOIN fetched d ON d.company_number = f.company_number "
            "WHERE d.company_number IS NULL ORDER BY f.rank LIMIT 50").fetchall()
        if not batch:
            break
        for r in batch:
            if client.out_of_time():
                stopped = "time budget used"
                break
            if _too_big(conn):
                stopped = f"database reached {MAX_DB_BYTES // 2**20} MB: stopped to stay under GitHub's file limit"
                break
            if limit is not None and done >= limit:
                stopped = "limit reached"
                break
            num, before = r["company_number"], client.calls
            try:
                items = list(client.charges(num))
            except RuntimeError as exc:   # repeated 429s or a rejected key: stop, don't hammer
                stopped = f"stopped: {exc}"
                break
            _store(conn, num, items, client.calls - before)
            done += 1
            calls += client.calls - before
            charges += len(items)
            if done % 25 == 0:
                conn.commit()
        else:
            continue
        break
    if limit is not None and done >= limit:
        stopped = "limit reached"
    conn.commit()
    return {"companies": done, "calls": calls, "charges": charges, "stopped": stopped,
            "sample_fetched": conn.execute("SELECT COUNT(*) FROM fetched").fetchone()[0]}


def spare(client, extra_minutes: float = 0, path=None, build=build_frame) -> dict:
    """The pipeline's last step: use what is left of the shared budget, plus `extra_minutes`.

    Live work always comes first. If it used the whole budget this run, nothing is fetched,
    and the extra minutes are not used either."""
    if client.out_of_time():
        return {"skipped": "live steps used the whole time budget"}
    if extra_minutes > 0:
        client.extend(extra_minutes * 60)
    conn = connect(path)
    try:
        if not conn.execute("SELECT 1 FROM frame LIMIT 1").fetchone():
            try:
                build(conn)
            except Exception as exc:  # no frame, no backfill this run; the live run is unaffected
                log.exception("Could not build the backfill sample frame")
                return {"frame_error": repr(exc)}
        return fetch(conn, client)
    finally:
        conn.close()


# --------------------------------------------------------------------------- loading for backtests

def load_companies(live_conn, conn=None, resolver=None):
    """The fetched sample as charge_history.Company objects (lenders resolved with the live rules).

    Only companies whose history was fetched are included. Names are not stored, so a company's
    name is its number."""
    from .aliases import Resolved, Resolver
    from .charge_history import PRIVATE_INDIVIDUAL, Charge, Company, _json_list
    conn = conn or connect()
    resolver = resolver or Resolver(live_conn)
    cache: dict[str, tuple] = {}

    def lenders_of(names):
        out, seen = [], set()
        for n in names:
            if n not in cache:
                r = resolver.resolve(n)
                if r.cls == "individual":
                    r = Resolved(PRIVATE_INDIVIDUAL, "PRIVATE INDIVIDUAL", "individual", r.how, r.category)
                cache[n] = (r.canonical, r.key, r.cls)
            t = cache[n]
            if (t[1], t[2]) not in seen:
                seen.add((t[1], t[2]))
                out.append(t)
        return sorted(out)

    meta = {r["company_number"]: r for r in conn.execute(
        "SELECT f.* FROM frame f JOIN fetched d ON d.company_number = f.company_number")}
    by_co: dict[str, list] = {n: [] for n in meta}
    for r in conn.execute("SELECT * FROM history"):
        if r["company_number"] in by_co:
            by_co[r["company_number"]].append(Charge(
                r["company_number"], r["charge_id"], r["delivered_on"] or None, r["created_on"] or None,
                r["satisfied_on"] or None, r["status"], lenders_of(_json_list(r["persons"]))))
    out = {}
    for num, cs in by_co.items():
        cs.sort(key=lambda c: (c.filed or "", c.charge_id))
        m = meta[num]
        out[num] = Company(num, num, _json_list(m["sic_codes"]), m["incorporated"] or None,
                           (m["company_status"] or "").lower() or None, True, cs)
    return out


def first_fetched(conn) -> date | None:
    v = conn.execute("SELECT MIN(fetched_at) FROM fetched").fetchone()[0]
    return date.fromisoformat(v[:10]) if v else None


# --------------------------------------------------------------------------- status

def status(conn) -> dict:
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    kept = one("SELECT COUNT(*) FROM frame")
    got = one("SELECT COUNT(*) FROM fetched")
    per_day = {r[0]: r[1] for r in conn.execute(
        "SELECT substr(fetched_at, 1, 10) d, COUNT(*) FROM fetched GROUP BY d ORDER BY d DESC LIMIT 7")}
    recent = sorted(per_day.values())[-3:] if per_day else []
    rate = sum(recent) / len(recent) if recent else 0
    return {
        "frame_snapshot": _meta(conn, "frame_snapshot"),
        "companies_with_a_charge": _meta(conn, "frame_size"),
        "with_one_outstanding": _meta(conn, "frame_outstanding"),
        "sample_kept": kept,
        "fetched": got,
        "fetched_with_outstanding_today": one(
            "SELECT COUNT(*) FROM fetched d JOIN frame f ON f.company_number = d.company_number WHERE f.outstanding > 0"),
        "charges_stored": one("SELECT COUNT(*) FROM history"),
        "calls_per_company": round(one("SELECT AVG(calls) FROM fetched") or 0, 3),
        "fetched_per_day": per_day,
        "days_to_finish_at_recent_rate": round((kept - got) / rate, 1) if rate else None,
        "database_mb": round(conn.execute("PRAGMA page_count").fetchone()[0]
                             * conn.execute("PRAGMA page_size").fetchone()[0] / 2**20, 1),
    }


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m radar.backfill")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("frame")
    r = sub.add_parser("run")
    r.add_argument("--minutes", type=float, default=10)
    r.add_argument("--limit", type=int)
    sub.add_parser("status")
    a = p.parse_args(argv)
    conn = connect()
    if a.cmd == "frame":
        print(json.dumps(build_frame(conn), indent=2))
    elif a.cmd == "run":
        from .ingest import RestClient, _need
        if not conn.execute("SELECT 1 FROM frame LIMIT 1").fetchone():
            build_frame(conn)
        client = RestClient(_need("CH_REST_KEY"), budget_seconds=a.minutes * 60)
        print(json.dumps(fetch(conn, client, a.limit), indent=2))
    else:
        print(json.dumps(status(conn), indent=2))


if __name__ == "__main__":
    main()

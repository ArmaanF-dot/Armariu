"""Pull charge data from Companies House into SQLite.

    python -m radar.ingest stream --minutes 10        # follow the charges stream, then exit
    python -m radar.ingest backfill --company 01234567 [--company-file companies.txt]
    python -m radar.ingest enrich --limit 100         # fetch borrower profiles (name, SIC codes)

Both APIs use HTTP basic auth with the key as the username and an empty password.
The REST key and the stream key are different keys (two applications on the developer hub).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import time
from collections import deque

import requests

from . import config
from .db import connect, get_meta, now_iso, set_meta
from .normalise import normalise_name

log = logging.getLogger("radar.ingest")

_URI_RE = re.compile(r"^/company/([^/]+)/charges/([^/?#]+)")


class StreamGap(Exception):
    """The saved timepoint is too old (HTTP 416): events were missed while offline."""


# --------------------------------------------------------------------------- parsing helpers

def parse_resource_uri(uri: str):
    m = _URI_RE.match(uri or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def _as_list(value):
    """The spec says arrays, but live responses sometimes return a single object."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _join_descriptions(value) -> str | None:
    parts = [str(i.get("description")).strip() for i in _as_list(value) if i.get("description")]
    return " | ".join(parts) or None


def _any_flag(items, key):
    vals = [i.get(key) for i in items if key in i]
    if not vals:
        return None
    return 1 if any(bool(v) for v in vals) else 0


# --------------------------------------------------------------------------- storage

def upsert_charge(
    conn: sqlite3.Connection,
    company_number: str,
    charge_id: str,
    data: dict,
    *,
    event_type: str = "backfill",
    timepoint=None,
    published_at=None,
    fields_changed=None,
) -> None:
    company_number = company_number.upper()
    prev = conn.execute(
        "SELECT status FROM charges WHERE company_number = ? AND charge_id = ?",
        (company_number, charge_id),
    ).fetchone()
    particulars = _as_list(data.get("particulars"))
    now = now_iso()
    row = {
        "company_number": company_number,
        "charge_id": charge_id,
        "charge_number": data.get("charge_number"),
        "charge_code": data.get("charge_code"),
        "status": data.get("status"),
        "created_on": data.get("created_on"),
        "delivered_on": data.get("delivered_on"),
        "satisfied_on": data.get("satisfied_on"),
        "classification_text": _join_descriptions(data.get("classification")),
        "secured_text": _join_descriptions(data.get("secured_details")),
        "particulars_text": _join_descriptions(particulars),
        "contains_fixed": _any_flag(particulars, "contains_fixed_charge"),
        "contains_floating": _any_flag(particulars, "contains_floating_charge"),
        "floating_covers_all": _any_flag(particulars, "floating_charge_covers_all"),
        "contains_negative_pledge": _any_flag(particulars, "contains_negative_pledge"),
        "more_than_four_persons": 1 if data.get("more_than_four_persons_entitled") else 0,
        "first_seen_at": now,
        "last_event_at": now,
        "last_timepoint": timepoint,
        # The full API response, for debugging. RADAR_STORE_RAW=0 skips it to keep the database
        # small (the GitHub Actions setup does this, as the database is uploaded every run).
        "raw_json": (json.dumps(data, separators=(",", ":"), sort_keys=True)
                     if os.environ.get("RADAR_STORE_RAW", "1") != "0" else None),
    }
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    updates = ", ".join(
        f"{k} = excluded.{k}" for k in row if k not in ("company_number", "charge_id", "first_seen_at")
    )
    conn.execute(
        f"INSERT INTO charges ({cols}) VALUES ({marks}) "
        f"ON CONFLICT(company_number, charge_id) DO UPDATE SET {updates}, deleted = 0",
        row,
    )
    conn.execute(
        "DELETE FROM charge_persons WHERE company_number = ? AND charge_id = ?",
        (company_number, charge_id),
    )
    for person in _as_list(data.get("persons_entitled")):
        raw = (person.get("name") or "").strip()
        norm = normalise_name(raw)
        if norm:
            conn.execute(
                "INSERT OR IGNORE INTO charge_persons VALUES (?, ?, ?, ?)",
                (company_number, charge_id, raw, norm),
            )
    conn.execute(
        "INSERT INTO charge_events (company_number, charge_id, event_type, timepoint, published_at,"
        " fields_changed, status_before, status_after, seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            company_number, charge_id, event_type, timepoint, published_at,
            json.dumps(fields_changed) if fields_changed else None,
            prev["status"] if prev else None, data.get("status"), now,
        ),
    )


def mark_deleted(conn, company_number, charge_id, *, timepoint=None, published_at=None) -> None:
    company_number = company_number.upper()
    conn.execute(
        "UPDATE charges SET deleted = 1, last_event_at = ?, last_timepoint = ? "
        "WHERE company_number = ? AND charge_id = ?",
        (now_iso(), timepoint, company_number, charge_id),
    )
    conn.execute(
        "INSERT INTO charge_events (company_number, charge_id, event_type, timepoint, published_at,"
        " seen_at) VALUES (?,?,?,?,?,?)",
        (company_number, charge_id, "deleted", timepoint, published_at, now_iso()),
    )


def process_event(conn: sqlite3.Connection, ev: dict) -> bool:
    """Apply one stream event. Returns True if it was a charge event we stored."""
    event = ev.get("event") or {}
    timepoint = event.get("timepoint")
    company, charge_id = parse_resource_uri(ev.get("resource_uri", ""))
    stored = False
    if company and charge_id:
        if (event.get("type") or "").lower() == "deleted":
            mark_deleted(conn, company, charge_id, timepoint=timepoint,
                         published_at=event.get("published_at"))
        else:
            upsert_charge(
                conn, company, charge_id, ev.get("data") or {},
                event_type=event.get("type") or "changed", timepoint=timepoint,
                published_at=event.get("published_at"), fields_changed=event.get("fields_changed"),
            )
        stored = True
    else:
        log.warning("Skipping event with unexpected resource_uri: %r", ev.get("resource_uri"))
    if timepoint is not None:
        set_meta(conn, "stream_timepoint", timepoint)
    return stored


# --------------------------------------------------------------------------- stream

def run_stream(conn, minutes: float, key: str, session=None) -> int:
    """Follow the charges stream for `minutes`, resuming from the saved timepoint."""
    session = session or requests.Session()
    timepoint = get_meta(conn, "stream_timepoint")
    params = {"timepoint": timepoint} if timepoint else {}
    deadline = time.monotonic() + minutes * 60
    count = 0
    log.info("Connecting to charges stream (timepoint=%s)", timepoint or "now")
    try:
        with session.get(config.STREAM_URL, params=params, auth=(key, ""), stream=True,
                         timeout=(15, 120)) as resp:
            if resp.status_code == 416:
                set_meta(conn, "stream_timepoint", None)
                set_meta(conn, "stream_gap_at", now_iso())
                conn.commit()
                raise StreamGap(
                    "Saved timepoint is too old: events were missed while this machine was off. "
                    "The next run starts from 'now'. Use `backfill` for companies you care about."
                )
            if resp.status_code == 429:
                log.warning("Stream rate-limited (429). Not retrying this run.")
                return 0
            if resp.status_code == 401:
                raise RuntimeError("Stream key rejected (401). Check CH_STREAM_KEY in .env.")
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:  # blank lines are heartbeats
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("Bad JSON line skipped")
                        continue
                    if process_event(conn, ev):
                        count += 1
                        if count % 100 == 0:
                            conn.commit()
                if time.monotonic() > deadline:
                    break
    except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError,
            requests.exceptions.ReadTimeout) as exc:
        log.warning("Stream connection ended early: %s", exc)
    conn.commit()
    log.info("Stored %d charge events", count)
    return count


# --------------------------------------------------------------------------- REST

class RestClient:
    """Small Companies House REST client that stays under the per-key rate limit."""

    def __init__(self, key: str, session=None, limit=config.REST_LIMIT, window=config.REST_WINDOW):
        self.key = key
        self.session = session or requests.Session()
        self.limit, self.window = limit, window
        self._calls: deque[float] = deque()

    def _throttle(self):
        now = time.monotonic()
        while self._calls and now - self._calls[0] > self.window:
            self._calls.popleft()
        if len(self._calls) >= self.limit:
            wait = self.window - (now - self._calls[0]) + 1
            log.info("Rate limit guard: sleeping %.0fs", wait)
            time.sleep(wait)
        self._calls.append(time.monotonic())

    def get(self, path: str, params=None):
        for attempt in range(3):
            self._throttle()
            resp = self.session.get(config.REST_BASE + path, params=params,
                                    auth=(self.key, ""), timeout=30)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                time.sleep(60 * (attempt + 1))
                continue
            if resp.status_code == 401:
                raise RuntimeError("REST key rejected (401). Check CH_REST_KEY in .env.")
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Gave up on {path} after repeated 429s")

    def charges(self, company: str):
        start = 0
        while True:
            page = self.get(f"/company/{company}/charges",
                            {"items_per_page": 100, "start_index": start})
            if not page:
                return
            items = page.get("items") or []
            yield from items
            start += len(items)
            if not items or start >= int(page.get("total_count") or 0):
                return

    def company(self, company: str):
        return self.get(f"/company/{company}")


def backfill(conn, client: RestClient, companies) -> int:
    n = 0
    for company in companies:
        company = company.strip().upper()
        if not company:
            continue
        for item in client.charges(company):
            charge_id = item.get("id")
            if not charge_id:
                _, charge_id = parse_resource_uri((item.get("links") or {}).get("self", ""))
            if charge_id:
                upsert_charge(conn, company, charge_id, item, event_type="backfill")
                n += 1
        conn.commit()
    return n


def enrich(conn, client: RestClient, limit: int = 100) -> int:
    rows = conn.execute(
        "SELECT DISTINCT c.company_number FROM charges c "
        "LEFT JOIN companies co ON co.company_number = c.company_number "
        "WHERE co.company_number IS NULL ORDER BY c.delivered_on DESC LIMIT ?",
        (limit,),
    ).fetchall()
    n = 0
    for r in rows:
        num = r["company_number"]
        prof = client.company(num) or {}
        conn.execute(
            "INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)",
            (
                num, prof.get("company_name"), prof.get("company_status") or "not-found",
                prof.get("type"), prof.get("date_of_creation"),
                json.dumps(prof.get("sic_codes") or []), now_iso(),
            ),
        )
        n += 1
        if n % 25 == 0:
            conn.commit()
    conn.commit()
    return n


# --------------------------------------------------------------------------- CLI

def _need(var: str) -> str:
    val = os.environ.get(var, "").strip()
    if not val:
        raise SystemExit(f"{var} is not set. Copy .env.example to .env and add your key.")
    return val


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m radar.ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stream"); s.add_argument("--minutes", type=float, default=10)
    b = sub.add_parser("backfill")
    b.add_argument("--company", action="append", default=[])
    b.add_argument("--company-file")
    e = sub.add_parser("enrich"); e.add_argument("--limit", type=int, default=100)
    args = p.parse_args(argv)
    conn = connect()
    if args.cmd == "stream":
        try:
            run_stream(conn, args.minutes, _need("CH_STREAM_KEY"))
        except StreamGap as gap:
            log.error("%s", gap)
            raise SystemExit(2)
    elif args.cmd == "backfill":
        companies = list(args.company)
        if args.company_file:
            with open(args.company_file, encoding="utf-8") as fh:
                companies += [ln.strip() for ln in fh if ln.strip()]
        print(f"Backfilled {backfill(conn, RestClient(_need('CH_REST_KEY')), companies)} charges")
    elif args.cmd == "enrich":
        print(f"Enriched {enrich(conn, RestClient(_need('CH_REST_KEY')), args.limit)} companies")


if __name__ == "__main__":
    main()

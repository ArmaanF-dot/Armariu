"""Data pull for the borrower risk score (radar/distress.py). Runs on GitHub Actions through the "distress"
workflow, because cloud sessions cannot reach Companies House. Moved from scripts/distress_test/fetch.py (PR 22)
and made resumable on its own data branch.

    python -m radar.distress_fetch [radar] [dissolved] [live]     (default: all three, in that order)

Writes to output/distress/ and pushes it to the `distress-data` branch every 30 minutes, so a rerun resumes.

Steps
  radar      filing history for the radar's own companies that have a charge (names and lenders known), the
             firms an adviser would be sent. One or two calls per company.
  dissolved  survivorship fix: companies dissolved since 1 Jul 2022 are missing from the live frame. Random
             calendar days are drawn; every company dissolved that day is listed, a seeded share is kept,
             its charge list fetched, and for those with a charge its filing history too. Counts per day are
             saved so the sample can be weighted.
  live       filing history for the backfill sample companies whose charge history is fetched.
It pauses whenever the scheduled radar job is running, so the radar keeps its quota.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config

OUT = config.OUTPUT_DIR / "distress"
DATA_BRANCH = "distress-data"
OUT.mkdir(parents=True, exist_ok=True)
B = "https://api.company-information.service.gov.uk"
CATS = ("gazette,insolvency,capital,resolution,accounts,address,change-of-name,officers,"
        "persons-with-significant-control,confirmation-statement,annual-return,incorporation,"
        "liquidation,restoration,miscellaneous,other,auditors,dissolution")
OLDEST = "2018-01-01"
DISS_FROM, DISS_TO = date(2022, 7, 1), date(2026, 10, 31)
KEEP_SHARE = float(os.environ.get("KEEP_SHARE", "0.2"))
DISS_CHECK_CAP = int(os.environ.get("DISS_CHECK_CAP", "30000"))
JOB_DEADLINE = time.time() + float(os.environ.get("JOB_MINUTES", "340")) * 60
KEY = os.environ.get("CH_REST_KEY", "")
sess, gh = requests.Session(), requests.Session()
calls: list[float] = []
state = {"last_check": 0.0, "paused": False, "last_commit": time.time(), "n_calls": 0}


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def rank(seed, s):
    return int.from_bytes(hashlib.sha256(f"{seed}:{s}".encode()).digest()[:8], "big") / 2**64


def commit(msg):
    """Save progress to the data branch (a commit on top of the checked-out main; force-pushed, one writer)."""
    subprocess.run(["git", "add", "-f", str(OUT)], check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", f"distress data: {msg}"], check=False)
    for _ in range(3):
        if subprocess.run(["git", "push", "-q", "--force", "origin", f"HEAD:refs/heads/{DATA_BRANCH}"]).returncode == 0:
            return
        time.sleep(5)


def radar_running():
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return False
    try:
        for st in ("in_progress", "queued"):
            r = gh.get(f"https://api.github.com/repos/{repo}/actions/workflows/radar.yml/runs",
                       params={"status": st, "per_page": 1}, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            if r.ok and r.json().get("total_count", 0) > 0:
                return True
    except requests.RequestException:
        pass
    return False


def wait_turn():
    while True:
        if time.time() - state["last_check"] > 120:
            state["last_check"] = time.time()
            was = state["paused"]
            state["paused"] = radar_running()
            if state["paused"] and not was:
                log("radar job running: pausing")
        if not state["paused"]:
            break
        time.sleep(60)
        state["last_check"] = 0
    now = time.time()
    while calls and now - calls[0] > 300:
        calls.pop(0)
    if len(calls) >= 440:
        time.sleep(300 - (now - calls[0]) + 1)
    calls.append(time.time())
    state["n_calls"] += 1


def get(path, params=None):
    """JSON, None for 404, or raises after repeated failure."""
    for attempt in range(6):
        wait_turn()
        try:
            r = sess.get(B + path, params=params, auth=(KEY, ""), timeout=40)
        except requests.RequestException:
            time.sleep(10); continue
        if r.status_code == 429:
            log("429: sleeping 90s"); time.sleep(90); continue
        if r.status_code == 404:
            return None
        if r.status_code >= 500:
            time.sleep(15); continue
        if r.status_code != 200:
            log("status", r.status_code, path, r.text[:200]); return None
        return r.json()
    raise RuntimeError(f"failed {path}")


def filings(num):
    items, start = [], 0
    while True:
        d = get(f"/company/{num}/filing-history", {"items_per_page": 100, "start_index": start, "category": CATS})
        if not d:
            break
        got = d.get("items") or []
        for it in got:
            dv = it.get("description_values") or {}
            items.append([it.get("type"), it.get("category"), it.get("subcategory"), it.get("date"),
                          it.get("action_date"), it.get("description"), dv.get("made_up_date"),
                          dv.get("new_date") or dv.get("change_date")])
        start += len(got)
        if not got or start >= (d.get("total_count") or 0) or (got[-1].get("date") or "9") < OLDEST:
            break
    return items


def charges(num):
    items, start = [], 0
    while True:
        d = get(f"/company/{num}/charges", {"items_per_page": 100, "start_index": start})
        if not d:
            break
        got = d.get("items") or []
        items += [{"id": it.get("id") or "", "status": it.get("status"), "created_on": it.get("created_on"),
                   "delivered_on": it.get("delivered_on"), "satisfied_on": it.get("satisfied_on"),
                   "classification": (it.get("classification") or {}).get("description"),
                   "persons": [p.get("name") for p in it.get("persons_entitled") or []]} for it in got]
        start += len(got)
        if not got or start >= (d.get("total_count") or 0):
            break
    return items


def read_done(p, key="n"):
    done = set()
    if p.exists():
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)[key])
                except Exception:
                    pass
    return done


def appender(p):
    """Append to a .jsonl.gz by rewriting via a plain work file (gzip append is fine too)."""
    return gzip.open(p, "at", encoding="utf-8")


def maybe_commit(fhs, msg):
    if time.time() - state["last_commit"] > 30 * 60:
        for f in fhs:
            f.flush()
        commit(msg)
        state["last_commit"] = time.time()


def step_radar():
    con = sqlite3.connect(config.DB_PATH)
    nums = [r[0] for r in con.execute(
        "SELECT c.company_number FROM companies c WHERE c.company_number IN (SELECT company_number FROM charges) "
        "OR c.company_number IN (SELECT company_number FROM borrower_history) ORDER BY c.company_number")]
    p = OUT / "radar_filings.jsonl.gz"
    done = read_done(p)
    log(f"radar: {len(nums):,} companies, {len(done):,} done")
    f = appender(p)
    n = 0
    for num in nums:
        if num in done:
            continue
        if time.time() > JOB_DEADLINE:
            log("radar: time used"); break
        f.write(json.dumps({"n": num, "items": filings(num)}) + "\n")
        n += 1
        if n % 500 == 0:
            log(f"radar: {n:,} fetched, {state['n_calls']:,} calls")
        maybe_commit([f], f"radar filings, {n} this run")
    f.close()
    commit(f"radar filings done this run ({n})")


def step_live():
    con = sqlite3.connect(config.BACKFILL_DB_PATH)
    nums = [r[0] for r in con.execute("SELECT f.company_number FROM frame f JOIN fetched x USING(company_number) ORDER BY f.rank")]
    p = OUT / "live_filings.jsonl.gz"
    done = read_done(p)
    log(f"live: {len(nums):,} companies, {len(done):,} done")
    f = appender(p)
    n = 0
    for num in nums:
        if num in done:
            continue
        if time.time() > JOB_DEADLINE:
            log("live: time used"); break
        f.write(json.dumps({"n": num, "items": filings(num)}) + "\n")
        n += 1
        if n % 500 == 0:
            log(f"live: {n:,} fetched, {state['n_calls']:,} calls")
        maybe_commit([f], f"live filings, {n} this run")
    f.close()
    commit(f"live filings done this run ({n})")


def step_dissolved():
    days = [DISS_FROM + timedelta(d) for d in range((DISS_TO - DISS_FROM).days + 1)]
    random.Random("armariu-distress-days").shuffle(days)
    pd = OUT / "diss_days.jsonl.gz"
    pc = OUT / "diss_companies.jsonl.gz"
    done_days = read_done(pd, "day")
    checked = 0
    if pc.exists():
        with gzip.open(pc, "rt", encoding="utf-8") as fin:
            checked = sum(1 for _ in fin)
    log(f"dissolved: {len(done_days)} days done, {checked:,} companies checked")
    fd, fc = appender(pd), appender(pc)
    for day in days:
        if checked >= DISS_CHECK_CAP or time.time() > JOB_DEADLINE:
            break
        ds = day.isoformat()
        if ds in done_days:
            continue
        found, start, hits, ok = [], 0, None, True
        while True:
            d = get("/advanced-search/companies", {"company_status": "dissolved", "dissolved_from": ds,
                                                   "dissolved_to": ds, "size": 5000, "start_index": start})
            if d is None:
                if hits is None:
                    hits = 0
                break
            hits = d.get("hits", hits)
            got = d.get("items") or []
            found += got
            start += len(got)
            if not got or start >= (hits or 0) or start >= 10000:
                break
        types = {}
        kept = []
        for it in found:
            t = it.get("company_type") or ""
            types[t] = types.get(t, 0) + 1
            num = it.get("company_number")
            if t in ("ltd", "llp", "plc", "private-unlimited") and num and rank("diss", num) < KEEP_SHARE:
                kept.append(it)
        n_ch = 0
        for it in kept:
            num = it["company_number"]
            ch = charges(num)
            rec = {"n": num, "day": ds, "type": it.get("company_type"), "created": it.get("date_of_creation"),
                   "ceased": it.get("date_of_cessation"), "sic": it.get("sic_codes"), "charges": ch}
            if ch:
                n_ch += 1
                rec["items"] = filings(num)
            fc.write(json.dumps(rec) + "\n")
            checked += 1
            maybe_commit([fd, fc], f"dissolved sample, {checked} checked")
        fd.write(json.dumps({"day": ds, "hits": hits, "listed": len(found), "types": types,
                             "kept": len(kept), "with_charges": n_ch, "complete": len(found) >= (hits or 0)}) + "\n")
        fd.flush(); fc.flush()
        log(f"day {ds}: {hits} dissolved, {len(kept)} kept, {n_ch} with charges; total checked {checked:,}")
    fd.close(); fc.close()
    commit(f"dissolved sample ({checked} checked)")


def main():
    if not KEY:
        log("no CH_REST_KEY"); return
    steps = sys.argv[1:] or ["radar", "dissolved", "live"]
    for name, fn in (("radar", step_radar), ("dissolved", step_dissolved), ("live", step_live)):
        if name in steps:
            fn()
    (OUT / "meta.json").write_text(json.dumps({"finished_at": datetime.now(timezone.utc).isoformat(),
                                               "calls_last_run": state["n_calls"]}, indent=1))
    commit("meta")
    log("done", state["n_calls"], "calls")


if __name__ == "__main__":
    main()

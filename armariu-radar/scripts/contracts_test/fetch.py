"""Research-only data pull for the contract-wins test. Runs on GitHub Actions (the cloud workspace
can't reach these hosts). Not part of the radar. Writes everything to contracts_test_out/.

Steps (each skipped if its output already exists, so a rerun resumes):
  awards   Contracts Finder + Find a Tender OCDS award releases, 2022-01 to 2026-10 (OGL v3.0)
  bcd      Companies House Basic Company Data: rows that could be suppliers (by number or name),
           plus a seeded random pool of all live companies for controls
  hist     charge histories (REST, CH_REST_KEY) for charged winners and a random set of charged
           controls, interleaved, pausing while the scheduled radar job runs so it keeps its quota
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from radar.normalise import normalise_name  # noqa: E402

OUT = Path("contracts_test_out")
OUT.mkdir(exist_ok=True)
START, END = date(2022, 1, 1), date(2025, 12, 31)
CF = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
FTS = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
POOL_FRACTION = 0.06          # share of all live companies kept as the control pool
HIST_CAP_WINNERS = int(os.environ.get("HIST_CAP_WINNERS", "9000"))
HIST_CAP_CONTROLS = int(os.environ.get("HIST_CAP_CONTROLS", "9000"))
JOB_DEADLINE = time.time() + float(os.environ.get("JOB_MINUTES", "340")) * 60
AWARD_FIELDS = ("source", "release_id", "ocid", "published", "award_date", "value", "value_kind",
                "supplier_name", "supplier_ids", "postcode", "scale", "buyer", "cpv", "title", "notice_url")

log_lock = threading.Lock()


def log(*a):
    with log_lock:
        print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def rank(seed: str, s: str) -> float:
    return int.from_bytes(hashlib.sha256(f"{seed}:{s}".encode()).digest()[:8], "big") / 2**64


def commit(msg: str):
    """Save progress to the branch now, in case the job is cut off."""
    subprocess.run(["git", "add", "-f", str(OUT)], check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", f"contracts-test: {msg}"], check=False)
    for _ in range(3):
        if subprocess.run(["git", "pull", "--rebase", "-q"]).returncode == 0 and \
                subprocess.run(["git", "push", "-q"]).returncode == 0:
            return
        time.sleep(5)


# ------------------------------------------------------------------ awards

def _get_json(sess, url, params=None):
    for attempt in range(8):
        try:
            r = sess.get(url, params=params, timeout=90, headers={"Accept": "application/json"})
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(120, 5 * 2 ** attempt))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 7:
                log("giving up", url, params, exc)
                return None
            time.sleep(min(120, 5 * 2 ** attempt))
    return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_release(source: str, rel: dict) -> list[dict]:
    parties = {p.get("id"): p for p in rel.get("parties") or []}
    tender = rel.get("tender") or {}
    cpv = ((tender.get("classification") or {}).get("id") or "")
    buyer = (rel.get("buyer") or {}).get("name") or ""
    contracts = rel.get("contracts") or []
    rows = []
    for aw in rel.get("awards") or []:
        if (aw.get("status") or "active") not in ("active", "pending"):
            continue
        val, kind = _num((aw.get("value") or {}).get("amount")), "award"
        cdate = ""
        mine = [c for c in contracts if c.get("awardID") == aw.get("id")]
        if val is None and mine:
            vs = [_num((c.get("value") or {}).get("amount")) for c in mine]
            vs = [v for v in vs if v is not None]
            if vs:
                val, kind = sum(vs), "contract"
        if mine:
            cdate = (mine[0].get("dateSigned") or "")[:10]
        if val is None:
            val, kind = _num((tender.get("value") or {}).get("amount")), "tender"
        sups = aw.get("suppliers") or []
        for s in sups:
            p = parties.get(s.get("id")) or {}
            ids = set()
            for ident in [p.get("identifier") or {}] + list(p.get("additionalIdentifiers") or []):
                if ident.get("scheme") and ident.get("id"):
                    ids.add(f"{ident['scheme']}:{ident['id']}")
            sid = s.get("id") or ""
            if sid.startswith("GB-COH-"):
                ids.add("GB-COH:" + sid[7:])
            rows.append({
                "source": source, "release_id": rel.get("id", ""), "ocid": rel.get("ocid", ""),
                "published": (rel.get("date") or "")[:10],
                "award_date": (aw.get("date") or cdate or "")[:10],
                "value": "" if val is None else round(val, 2), "value_kind": kind if val is not None else "",
                "supplier_name": (s.get("name") or p.get("name") or "").strip(),
                "supplier_ids": "|".join(sorted(ids)),
                "postcode": ((p.get("address") or {}).get("postalCode") or "").upper().replace(" ", ""),
                "scale": (p.get("details") or {}).get("scale") or "",
                "buyer": buyer, "cpv": cpv, "title": (tender.get("title") or "")[:160],
                "notice_url": next((d.get("url") for d in aw.get("documents") or [] if d.get("url")), ""),
            })
    return rows


def fetch_day(source: str, day: date) -> list[dict]:
    sess = requests.Session()
    a, b = f"{day.isoformat()}T00:00:00", f"{(day + timedelta(days=1)).isoformat()}T00:00:00"
    if source == "cf":
        url, params = CF, {"publishedFrom": a, "publishedTo": b, "stages": "award", "limit": 100}
    else:
        url, params = FTS, {"updatedFrom": a, "updatedTo": b, "stages": "award", "limit": 100}
    rows, pages, seen = [], 0, set()
    while url:
        j = _get_json(sess, url, params)
        if j is None:
            break
        pages += 1
        for rel in j.get("releases") or []:
            if rel.get("id") in seen:
                continue
            seen.add(rel.get("id"))
            tags = rel.get("tag") or []
            if "award" not in tags and not rel.get("awards"):
                continue
            rows.extend(parse_release(source, rel))
        nxt = (j.get("links") or {}).get("next")
        if not nxt or pages > 200:
            break
        url, params = nxt, None
        time.sleep(0.3)
    return rows


def step_awards():
    """Month by month, one gzip file per source and month, committed every ~20 minutes (resumable)."""
    path = OUT / "awards.csv.gz"
    if path.exists():
        log("awards: exists, skipping")
        return
    chunks = OUT / "awards"
    chunks.mkdir(exist_ok=True)
    months = []
    d = START
    while d <= END:
        months.append(d)
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_commit = time.time()
    with ThreadPoolExecutor(12) as ex:
        for m in months:
            nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
            days = [m + timedelta(days=i) for i in range((min(nxt, END + timedelta(days=1)) - m).days)]
            for src in ("cf", "fts"):
                f = chunks / f"{src}-{m:%Y-%m}.csv.gz"
                if f.exists():
                    continue
                rows = [r for lst in ex.map(lambda dd: fetch_day(src, dd), days) for r in lst]
                with gzip.open(f, "wt", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=AWARD_FIELDS); w.writeheader(); w.writerows(rows)
                log(f"awards {src} {m:%Y-%m}: {len(rows):,} rows")
            if time.time() - last_commit > 20 * 60:
                commit(f"award notices to {m:%Y-%m}"); last_commit = time.time()
    counts = {}
    with gzip.open(path, "wt", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=AWARD_FIELDS); w.writeheader()
        for f in sorted(chunks.glob("*.csv.gz")):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            w.writerows(rows); src = f.name.split("-")[0]; counts[src] = counts.get(src, 0) + len(rows)
    (OUT / "awards_meta.json").write_text(json.dumps({"rows": counts, "from": str(START), "to": str(END)}))
    log("awards done", counts)
    commit("award notices")


# ------------------------------------------------------------------ Basic Company Data

def _sic(t):
    import re
    m = re.match(r"\s*(\d{4,5})\b", t or "")
    return m.group(1).zfill(5) if m else ""


def _iso(d):
    d = (d or "").strip()
    return f"{d[6:10]}-{d[3:5]}-{d[0:2]}" if len(d) == 10 else ""


def step_bcd():
    if (OUT / "bcd_candidates.csv.gz").exists():
        log("bcd: exists, skipping")
        return
    from radar.backfill import _basic_company_rows
    from radar.profile import _latest
    ids, names = set(), set()
    with gzip.open(OUT / "awards.csv.gz", "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            for i in r["supplier_ids"].split("|"):
                if i.startswith("GB-COH:"):
                    ids.add(i[7:].strip().upper().zfill(8))
            n = normalise_name(r["supplier_name"])
            if n:
                names.add(n)
    log(f"bcd: {len(ids):,} supplier numbers, {len(names):,} supplier names")
    name = _latest()
    cols = ("company_number", "name_norm", "postcode", "status", "category", "incorporated", "sic1",
            "sic_all", "accounts_category", "charges_total", "charges_outstanding")
    fc = gzip.open(OUT / "bcd_candidates.csv.gz", "wt", newline="", encoding="utf-8")
    fp = gzip.open(OUT / "bcd_pool.csv.gz", "wt", newline="", encoding="utf-8")
    wc, wp = csv.writer(fc), csv.writer(fp)
    wc.writerow(cols); wp.writerow(cols)
    totals = {"live_companies": 0, "charged": 0, "pool": 0, "pool_charged": 0, "candidates": 0}
    for rec in _basic_company_rows(name, log):
        num = (rec.get("CompanyNumber") or "").strip().upper()
        if not num:
            continue
        nn = normalise_name(rec.get("CompanyName") or "")
        sics = [c for c in (_sic(rec.get(f"SICCode.SicText_{i}")) for i in range(1, 5)) if c]
        try:
            ch = int((rec.get("Mortgages.NumMortCharges") or "0").strip() or 0)
            out = int((rec.get("Mortgages.NumMortOutstanding") or "0").strip() or 0)
        except ValueError:
            ch, out = 0, 0
        row = (num, nn, (rec.get("RegAddress.PostCode") or "").upper().replace(" ", ""),
               (rec.get("CompanyStatus") or "").strip(), (rec.get("CompanyCategory") or "").strip(),
               _iso(rec.get("IncorporationDate")), sics[0] if sics else "", ";".join(sics),
               (rec.get("Accounts.AccountCategory") or "").strip(), ch, out)
        totals["live_companies"] += 1
        totals["charged"] += ch > 0
        if num in ids or nn in names:
            wc.writerow(row); totals["candidates"] += 1
        if rank("pool-v1", num) < POOL_FRACTION:
            wp.writerow(row); totals["pool"] += 1; totals["pool_charged"] += ch > 0
    fc.close(); fp.close()
    totals["snapshot"] = name
    (OUT / "bcd_meta.json").write_text(json.dumps(totals, indent=1))
    log("bcd done", totals)
    commit("company data candidates and control pool")


# ------------------------------------------------------------------ matching (also redone offline)

def load_bcd(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def match_awards():
    cands = load_bcd(OUT / "bcd_candidates.csv.gz")
    by_num = {r["company_number"]: r for r in cands}
    by_name = {}
    for r in cands:
        by_name.setdefault(r["name_norm"], []).append(r)
    out = {}
    with gzip.open(OUT / "awards.csv.gz", "rt", encoding="utf-8") as fh:
        for a in csv.DictReader(fh):
            num = None
            for i in a["supplier_ids"].split("|"):
                if i.startswith("GB-COH:"):
                    n = i[7:].strip().upper().zfill(8)
                    if n in by_num:
                        num = n
            if not num:
                c = by_name.get(normalise_name(a["supplier_name"])) or []
                if len(c) == 1:
                    num = c[0]["company_number"]
                elif len(c) > 1 and a["postcode"]:
                    same = [x for x in c if x["postcode"] == a["postcode"]]
                    if len(same) == 1:
                        num = same[0]["company_number"]
            if num:
                out.setdefault(num, by_num[num])
    return out


# ------------------------------------------------------------------ histories

def radar_running(sess) -> bool:
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return False
    try:
        for st in ("in_progress", "queued"):
            r = sess.get(f"https://api.github.com/repos/{repo}/actions/workflows/radar.yml/runs",
                         params={"status": st, "per_page": 1},
                         headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            if r.ok and r.json().get("total_count", 0) > 0:
                return True
    except requests.RequestException:
        pass
    return False


def step_hist():
    key = os.environ.get("CH_REST_KEY")
    if not key:
        log("hist: no CH_REST_KEY, skipping")
        return
    winners = match_awards()
    pool = load_bcd(OUT / "bcd_pool.csv.gz")
    w_ch = sorted((n for n, r in winners.items() if int(r["charges_total"]) > 0), key=lambda n: rank("w", n))
    c_ch = sorted((r["company_number"] for r in pool if int(r["charges_total"]) > 0 and r["company_number"] not in winners),
                  key=lambda n: rank("c", n))
    w_ch, c_ch = w_ch[:HIST_CAP_WINNERS], c_ch[:HIST_CAP_CONTROLS]
    plan = {"winners_matched": len(winners), "winners_charged_planned": len(w_ch), "controls_charged_planned": len(c_ch)}
    log("hist plan", plan)
    order = []
    for i in range(max(len(w_ch), len(c_ch))):
        if i < len(w_ch): order.append(("w", w_ch[i]))
        if i < len(c_ch): order.append(("c", c_ch[i]))
    hpath = Path("hist_work.jsonl")
    if not hpath.exists() and (OUT / "histories.jsonl.gz").exists():
        with gzip.open(OUT / "histories.jsonl.gz", "rb") as a, open(hpath, "wb") as b:
            b.write(a.read())
    done = set()
    if hpath.exists():
        for line in open(hpath, encoding="utf-8"):
            try:
                done.add(json.loads(line)["n"])
            except Exception:
                pass
    # Reuse histories already in the radar database where present.
    radar = {}
    try:
        import sqlite3
        con = sqlite3.connect("data/radar.db")
        fetched = {r[0] for r in con.execute("SELECT company_number FROM borrower_history_fetched")}
        for r in con.execute("SELECT company_number, charge_id, status, created_on, delivered_on, satisfied_on, persons FROM borrower_history"):
            radar.setdefault(r[0], []).append(r[1:])
        for n in fetched:
            radar.setdefault(n, [])
        log(f"radar db: {len(radar):,} companies with stored histories")
    except Exception as exc:
        log("radar db not available", exc)
    sess, gh = requests.Session(), requests.Session()
    calls = []   # timestamps, keep <= 450 per 300 s
    fh = open(hpath, "a", encoding="utf-8")
    last_commit, last_check, paused = time.time(), 0, False
    n_done = n_calls = n_reused = 0
    for grp, num in order:
        if num in done:
            continue
        if time.time() > JOB_DEADLINE:
            log("hist: job time used, stopping")
            break
        if num in radar:
            fh.write(json.dumps({"n": num, "g": grp, "src": "radar", "items": [
                {"id": c[0], "status": c[1], "created_on": c[2], "delivered_on": c[3], "satisfied_on": c[4],
                 "persons": json.loads(c[5] or "[]") if (c[5] or "").startswith("[") else []} for c in radar[num]]}) + "\n")
            n_reused += 1
            continue
        while True:
            if time.time() - last_check > 120:
                last_check = time.time()
                paused = radar_running(gh)
                if paused:
                    log("radar job running: pausing")
            if not paused:
                break
            time.sleep(60)
            last_check = 0
        items, start, ok = [], 0, True
        while True:
            now = time.time()
            while calls and now - calls[0] > 300:
                calls.pop(0)
            if len(calls) >= 450:
                time.sleep(300 - (now - calls[0]) + 1)
            calls.append(time.time()); n_calls += 1
            try:
                r = sess.get(f"https://api.company-information.service.gov.uk/company/{num}/charges",
                             params={"items_per_page": 100, "start_index": start}, auth=(key, ""), timeout=30)
            except requests.RequestException:
                time.sleep(10); continue
            if r.status_code == 429:
                log("429: sleeping 90s"); time.sleep(90); continue
            if r.status_code == 404:
                break
            if r.status_code >= 500:
                time.sleep(15); continue
            if r.status_code != 200:
                log("status", r.status_code, num); ok = False; break
            page = r.json()
            got = page.get("items") or []
            items += [{"id": it.get("id") or "", "status": it.get("status"), "created_on": it.get("created_on"),
                       "delivered_on": it.get("delivered_on"), "satisfied_on": it.get("satisfied_on"),
                       "persons": [p.get("name") for p in it.get("persons_entitled") or []]} for it in got]
            start += len(got)
            if not got or start >= (page.get("total_count") or 0):
                break
        if not ok:
            continue
        fh.write(json.dumps({"n": num, "g": grp, "src": "api", "items": items}) + "\n")
        n_done += 1
        if n_done % 500 == 0:
            fh.flush()
            log(f"hist: {n_done:,} fetched, {n_reused:,} reused, {n_calls:,} calls")
        if time.time() - last_commit > 40 * 60:
            fh.flush()
            gz_hist()
            commit(f"histories, {n_done} so far")
            last_commit = time.time()
    fh.close()
    plan.update({"fetched": n_done, "reused": n_reused, "calls": n_calls})
    (OUT / "hist_meta.json").write_text(json.dumps(plan, indent=1))
    gz_hist()
    log("hist done", plan)


def gz_hist():
    src = Path("hist_work.jsonl")
    if src.exists():
        with open(src, "rb") as a, gzip.open(OUT / "histories.jsonl.gz", "wb") as b:
            b.write(a.read())


def main():
    steps = sys.argv[1:] or ["awards", "bcd", "hist"]
    if "awards" in steps: step_awards()
    if "bcd" in steps: step_bcd()
    if "hist" in steps: step_hist()


if __name__ == "__main__":
    main()

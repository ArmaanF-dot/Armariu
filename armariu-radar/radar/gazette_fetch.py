"""Pull Gazette insolvency notices that mention a petition, 2022 onward, for the UK risk score (plan task 13).

    python -m radar.gazette_fetch [--from 2022-01-01] [--out output/gazette]

Public Gazette data feed (no key). One request per page of up to 100 notices per week window, polite pause between
requests, resumable (a week already in petitions.jsonl.gz is skipped). Each saved row: notice id, notice code, title
(the company name), published date and company number when the notice text carries one. Code 2450 is the winding-up
petition notice; other codes that mention a petition are kept but labelled by code.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://www.thegazette.co.uk/insolvency/notice/data.json"
NUM = re.compile(r"Company\s+(?:Number|No\.?)\s*[:.]?\s*([A-Z]{0,2}\d{5,8})", re.I)
UA = {"User-Agent": "Mozilla/5.0 (research)"}


def parse(entry: dict) -> dict:
    content = re.sub(r"<[^>]+>", " ", entry.get("content") or "") if isinstance(entry.get("content"), str) else ""
    m = NUM.search(content)
    num = m.group(1).upper().zfill(8) if m else None
    return {"id": str(entry.get("id", "")).rsplit("/", 1)[-1], "code": entry.get("f:notice-code"),
            "title": entry.get("title"), "published": (entry.get("published") or "")[:10], "number": num}


def get(params: dict, tries: int = 4) -> dict:
    for i in range(tries):
        try:
            r = requests.get(BASE, params=params, headers=UA, timeout=90)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(5 * (i + 1))
    raise RuntimeError("failed after retries: " + json.dumps(params))


def week_rows(start: date, end: date) -> list[dict]:
    rows, page = [], 1
    while True:
        d = get({"text": "petition", "start-publish-date": start.isoformat(), "end-publish-date": end.isoformat(),
                 "results-page-size": 100, "results-page": page})
        ent = d.get("entry") or []
        if isinstance(ent, dict):
            ent = [ent]
        rows += [parse(e) for e in ent]
        total = int(d.get("f:total") or 0)
        if page * 100 >= total or not ent:
            return rows
        page += 1
        time.sleep(0.3)


_gate = {"lock": None, "last": 0.0}


def _pace(gap: float):
    import threading
    if _gate["lock"] is None:
        _gate["lock"] = threading.Lock()
    with _gate["lock"]:
        wait = _gate["last"] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _gate["last"] = time.time()


def notice_number(nid: str, gap: float = 0.8):
    """Company number from the notice's own linked-data record (the feed excerpt usually cuts it off).
    Returns the number, "" if the notice has none, None if the lookup failed (try again later)."""
    for i in range(2):
        _pace(gap)
        try:
            r = requests.get(f"https://www.thegazette.co.uk/notice/{nid}/data.json", params={"view": "linked-data"},
                             headers=UA, timeout=30)
            if r.status_code == 200:
                about = (r.json().get("result", {}).get("primaryTopic", {}) or {}).get("isAbout", {}) or {}
                comp = about.get("company") or {}
                if isinstance(comp, list):
                    comp = comp[0] if comp else {}
                n = comp.get("companyNumber")
                return str(n).upper().zfill(8) if n and str(n).isdigit() else (str(n).upper() if n else "")
            if r.status_code == 404:
                return ""
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2)
    return None


def fill_numbers(f: Path, codes=("2450",), workers: int = 2, limit_minutes: float = 90, gap: float = 0.8):
    """Second pass: look up company numbers for petition notices that lack one. Resumable ('' = looked up, none).
    The Gazette stopped answering after a few hundred fast requests, so this paces itself (one request per `gap`
    seconds in total) and pauses for three minutes whenever several lookups in a row fail."""
    from concurrent.futures import ThreadPoolExecutor
    rows = [json.loads(x) for x in gzip.open(f, "rt")]
    todo = [r for r in rows if str(r.get("code")) in codes and r.get("number") is None]
    print("notices to look up:", len(todo), flush=True)
    t0, done, ok, pauses = time.time(), 0, 0, 0
    for i in range(0, len(todo), 100):
        if time.time() - t0 > limit_minutes * 60:
            print("time limit, stopping; rerun to continue", flush=True)
            break
        batch = todo[i:i + 100]
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(lambda r: notice_number(r["id"], gap), batch))
        fails = 0
        for r, n in zip(batch, res):
            if n is None:
                fails += 1
            else:
                r["number"] = n; ok += 1
        done += len(batch)
        if i % 400 == 300 or fails:
            with gzip.open(f, "wt") as g:
                g.writelines(json.dumps(r) + "\n" for r in rows)
        print(f"tried {done} of {len(todo)}, answered {ok}, failed in this batch {fails}, "
              f"{(time.time() - t0) / 60:.0f} min", flush=True)
        if fails > 20:
            pauses += 1
            print("many failures: pausing 3 minutes", flush=True)
            time.sleep(180)
    with gzip.open(f, "wt") as g:
        g.writelines(json.dumps(r) + "\n" for r in rows)


def search_number(num: str, gap: float = 0.25):
    """Gazette insolvency notices that mention this company number since Oct 2021 (full-text search).
    Returns a list of {id, code, published}, or None if the lookup failed."""
    for i in range(2):
        _pace(gap)
        try:
            r = requests.get(BASE, params={"text": num, "start-publish-date": "2021-10-01", "results-page-size": 100},
                             headers=UA, timeout=45)
            if r.status_code == 200:
                d = r.json()
                ent = d.get("entry") or []
                if isinstance(ent, dict):
                    ent = [ent]
                return [{"id": str(e.get("id", "")).rsplit("/", 1)[-1], "code": e.get("f:notice-code"),
                         "published": (e.get("published") or "")[:10]} for e in ent]
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2)
    return None


def by_number(pop_path: Path, f: Path, workers: int = 4, limit_minutes: float = 320, gap: float = 0.25):
    """Search the Gazette for each company in the risk-score sample (random order, so any prefix is a random
    sample). Resumable; saves every 400. Output rows: {"n": number, "notices": [...]}."""
    import random
    from concurrent.futures import ThreadPoolExecutor
    nums = [x.strip() for x in gzip.open(pop_path, "rt") if x.strip()]
    random.Random(7).shuffle(nums)
    have = {}
    if f.exists():
        for line in gzip.open(f, "rt"):
            r = json.loads(line); have[r["n"]] = r["notices"]
    todo = [n for n in nums if n not in have]
    print(f"{len(have)} already done, {len(todo)} to search", flush=True)

    def save():
        with gzip.open(f, "wt") as g:
            g.writelines(json.dumps({"n": k, "notices": v}) + "\n" for k, v in have.items())

    t0 = time.time()
    for i in range(0, len(todo), 100):
        if time.time() - t0 > limit_minutes * 60:
            print("time limit, stopping; rerun to continue", flush=True)
            break
        batch = todo[i:i + 100]
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(lambda n: search_number(n, gap), batch))
        fails = 0
        for n, r in zip(batch, res):
            if r is None:
                fails += 1
            else:
                have[n] = r
        if i % 400 == 300 or fails:
            save()
        print(f"done {len(have)} of {len(nums)}, failed in this batch {fails}, {(time.time() - t0) / 60:.0f} min", flush=True)
        if fails > 20:
            print("many failures: pausing 3 minutes", flush=True)
            time.sleep(180)
    save()


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.gazette_fetch")
    p.add_argument("--numbers", action="store_true", help="second pass: company numbers for petition notices")
    p.add_argument("--by-number", action="store_true", help="search the Gazette for each company in data/petition_population.txt.gz")
    p.add_argument("--minutes", type=float, default=90, help="time limit for the second pass")
    p.add_argument("--from", dest="start", default="2022-01-01")
    p.add_argument("--out", default="output/gazette")
    a = p.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    f = out / "petitions.jsonl.gz"
    if a.by_number:
        from . import config
        return by_number(config.ROOT / "data" / "petition_population.txt.gz", out / "by_number.jsonl.gz", limit_minutes=a.minutes)
    if a.numbers:
        return fill_numbers(f, limit_minutes=a.minutes)
    done, rows = set(), []
    if f.exists():
        for line in gzip.open(f, "rt"):
            r = json.loads(line); rows.append(r); done.add(r.get("week"))
    d = date.fromisoformat(a.start)
    today = date.today()
    n_weeks = 0
    while d <= today:
        e = min(d + timedelta(days=6), today)
        if d.isoformat() not in done:
            for r in week_rows(d, e):
                r["week"] = d.isoformat(); rows.append(r)
            done.add(d.isoformat()); n_weeks += 1
            if n_weeks % 20 == 0:
                with gzip.open(f, "wt") as g:
                    g.writelines(json.dumps(r) + "\n" for r in rows)
                print("saved through", e, len(rows), "rows", flush=True)
        d += timedelta(days=7)
    with gzip.open(f, "wt") as g:
        g.writelines(json.dumps(r) + "\n" for r in rows)
    from collections import Counter
    codes = Counter(r["code"] for r in rows)
    with_num = sum(1 for r in rows if r["number"])
    print("rows", len(rows), "with company number", with_num, "codes", dict(codes.most_common(8)))


if __name__ == "__main__":
    main()

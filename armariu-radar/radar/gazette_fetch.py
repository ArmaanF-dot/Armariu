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


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.gazette_fetch")
    p.add_argument("--from", dest="start", default="2022-01-01")
    p.add_argument("--out", default="output/gazette")
    a = p.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    f = out / "petitions.jsonl.gz"
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

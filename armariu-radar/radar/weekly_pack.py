"""Module E: the weekly pack. Facts from Modules A to D in one Markdown file; a person adds judgement.

    python -m radar.weekly_pack [--week-start YYYY-MM-DD] [--out DIR]

Workflow (by hand, never an API call):
  1. This builds weekly_pack.md (lender table, top signals with explanations, scorecard line).
  2. Paste the pack plus commentary_prompt.md into a Claude chat for a first draft.
  3. Check every number against the pack, rewrite what you disagree with, add your own view.
  4. Publish. After the gate, append the featured signals to the live log.

The job fails loudly: on any problem it exits non-zero, writes WEEKLY_PACK_FAILED.txt, and (when
NTFY_TOPIC is set) sends an ntfy notice. It never silently skips a week.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

from . import config
from .charge_history import load_companies
from .charge_signals import RULE_VERSION, all_signals, in_window
from .lender_view import CAVEAT, build, render_md, week_of, write_csv
from .scorecard import scorecard_line

NOT_ADVICE = ("Armariu publishes company-level facts from public Companies House filings. It is not investment, "
              "legal or financial advice and makes no recommendation about any company or lender.")
PRIORITY = {"refinancing": 0, "add_on": 1, "first_facility": 2}
MAX_SIGNALS = 8


def pick_signals(signals: list[dict], n: int = MAX_SIGNALS, hide_property: bool = True) -> list[dict]:
    """Deterministic shortlist: refinancing first, one per company, banks-to-private-credit moves ahead."""
    seen, out = set(), []
    pool = [s for s in signals if s["signal_type"] in PRIORITY]
    pool.sort(key=lambda s: (PRIORITY[s["signal_type"]], s["signal_date"], s["company_number"]))
    for s in pool:
        if s["company_number"] in seen:
            continue
        if hide_property and s["sector"] == "Property and real estate":
            continue
        if "private individual" in (s["lender_names"] + s["other_lender"]).lower():
            continue   # individuals can be chargees; they stay out of anything published
        seen.add(s["company_number"])
        out.append(s)
        if len(out) >= n:
            break
    return out


def build_pack(companies, week_start: date, coverage_start, backtest_rows, run_date: date,
               hide_property: bool = True) -> str:
    we = week_start + timedelta(days=6)
    sigs = all_signals(companies)
    view = build(companies, week_start, coverage_start, sigs, include_property=not hide_property)
    shortlist = pick_signals(in_window(sigs, week_start, we), hide_property=hide_property)
    L = [f"# Armariu weekly pack: {week_start.isoformat()} to {we.isoformat()}", "",
         f"_Built {run_date.isoformat()}. Signal rules {RULE_VERSION}. Every figure below comes from Companies House "
         f"charge filings. {CAVEAT}_", "",
         "## 1. The read", "", "_Write two or three sentences with one clear view. Leave this for the person._", "",
         render_md(view).replace("## Lender view", "## 2. Lender view", 1), "",
         "## 3. Signals worth a look", "",
         f"_Window: {week_start} to {we}. Top {len(shortlist)} of {len(in_window(sigs, week_start, we))} signals. "
         f"Property borrowers {'hidden' if hide_property else 'included'}._", ""]
    for s in shortlist:
        L += [f"- **{s['signal_type'].replace('_', ' ')}** | {s['company_name']} ({s['company_number']}) | {s['sector']} | "
              f"{s['explanation']} [filing]({s['filing_url']})"]
    L += ["", "## 4. One exhibit", "", "_Pick the table or chart from section 2 that supports your view._", "",
          "## 5. Scorecard line", "", scorecard_line(backtest_rows), "",
          "## 6. Footer", "",
          "Data: UK Companies House charges register (open licence; attribute Companies House). "
          f"{CAVEAT} {NOT_ADVICE}", ""]
    return "\n".join(L)


COMMENTARY_PROMPT = """Here is this week's Armariu pack. Draft a first version of the newsletter using this template:
1. The read: two or three sentences, one clear view for the week.
2. Lender league table: top movers, new entrants, notable refinancing flows.
3. Signals worth a look: five to eight, each with the filing facts and a one-line reason.
4. One exhibit: a table or chart that supports the view.
5. Scorecard line: number of signals, hit rate against baseline, rule version.
6. Footer: sources, the filing-lag caveat, not-investment-advice note.
Voice: short and specific, one view per issue, every claim traceable to a filing in the pack, no named
individuals, no claims beyond the data (never invent amounts, prices, terms or purposes). Use only numbers in
the pack. Say plainly where the data is thin.
"""


def notify_failure(message: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "WEEKLY_PACK_FAILED.txt").write_text(message, encoding="utf-8")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            import requests
            requests.post(f"https://ntfy.sh/{topic}", data=message.encode(), headers={"Title": "Armariu weekly pack failed"}, timeout=10)
        except Exception:  # noqa: BLE001  (the file notice above is the fallback)
            pass


def run(week_start: date, out_dir, run_date: date | None = None, conn=None) -> Path:
    from .db import connect
    from .lender_view import coverage_start
    run_date = run_date or date.today()
    out_dir = Path(out_dir)
    conn = conn or connect()
    companies = load_companies(conn)
    if not companies:
        raise RuntimeError("The database holds no charges. Run python -m radar.dbsync pull first.")
    cov = coverage_start(conn)
    bt = None
    bt_path = config.OUTPUT_DIR / "scorecard" / "scorecard_backtest.csv"
    if bt_path.exists():
        bt = list(csv.DictReader(open(bt_path, encoding="utf-8")))
    pack = build_pack(companies, week_start, cov, bt, run_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    stale = out_dir / "WEEKLY_PACK_FAILED.txt"
    if stale.exists():
        stale.unlink()
    path = out_dir / "weekly_pack.md"
    path.write_text(pack, encoding="utf-8")
    (out_dir / "commentary_prompt.md").write_text(COMMENTARY_PROMPT, encoding="utf-8")
    write_csv(build(companies, week_start, None, include_property=True), out_dir)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.weekly_pack")
    p.add_argument("--week-start"); p.add_argument("--out", default=str(config.OUTPUT_DIR / "weekly_pack"))
    a = p.parse_args(argv)
    ws = date.fromisoformat(a.week_start) if a.week_start else week_of(date.today() - timedelta(days=7))[0]
    try:
        path = run(ws, a.out)
    except Exception:  # noqa: BLE001
        notify_failure(f"Weekly pack for week starting {ws} failed:\n{traceback.format_exc()}", Path(a.out))
        print("WEEKLY PACK FAILED. See WEEKLY_PACK_FAILED.txt", file=sys.stderr)
        raise SystemExit(1)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()

"""One scheduled pass of the whole pipeline. No AI involved.

    python -m radar.pipeline run [--minutes 10] [--enrich-limit 100] [--queue-limit 150]
    python -m radar.pipeline status

Steps: stream -> enrich borrowers -> fetch their full charge history -> apply agent results and reviewer flags -> re-apply
lenders.csv rules -> rebuild the classifier queue -> write weekly digests.
Each step is independent: a failure is logged and the remaining steps still run.
"""
from __future__ import annotations

import argparse
import json
import logging
import os

from . import classify, config, export, ingest
from .db import connect, get_meta, now_iso
from .rules import load_rules

log = logging.getLogger("radar.pipeline")


def run(minutes: float, enrich_limit: int, queue_limit: int) -> dict:
    conn = connect()
    summary: dict = {"started": now_iso()}

    stream_key = os.environ.get("CH_STREAM_KEY", "").strip()
    if stream_key:
        try:
            summary["stream_events"] = ingest.run_stream(conn, minutes, stream_key)
        except ingest.StreamGap as gap:
            summary["stream_warning"] = str(gap)
            log.error("%s", gap)
        except Exception as exc:  # keep going: the other steps are still useful
            summary["stream_error"] = repr(exc)
            log.exception("Stream step failed")
    else:
        summary["stream"] = "skipped (CH_STREAM_KEY not set)"

    rest_key = os.environ.get("CH_REST_KEY", "").strip()
    if rest_key and enrich_limit > 0:
        try:
            summary["enriched"] = ingest.enrich(conn, ingest.RestClient(rest_key), enrich_limit)
        except Exception as exc:
            summary["enrich_error"] = repr(exc)
            log.exception("Enrich step failed")
        try:
            summary["histories"] = ingest.history(conn, ingest.RestClient(rest_key), enrich_limit)
        except Exception as exc:
            summary["history_error"] = repr(exc)
            log.exception("Borrower history step failed")

    summary["agent_results"] = classify.apply_results(conn)
    summary["reviewer_flags"] = classify.apply_flags(conn)
    summary["rules"] = classify.refresh_rule_matches(conn, load_rules())
    summary["queued_names"] = classify.build_queue(conn, limit=queue_limit)
    summary["digests"] = [str(p.relative_to(config.ROOT)) for p in export.write_standard(conn)]
    summary["finished"] = now_iso()

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOGS_DIR / "pipeline.log", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")
    return summary


def status() -> dict:
    conn = connect()
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "charges": one("SELECT COUNT(*) FROM charges WHERE deleted = 0"),
        "companies_enriched": one("SELECT COUNT(*) FROM companies"),
        "lender_names": one("SELECT COUNT(DISTINCT name_norm) FROM charge_persons"),
        "classified": {r[0]: r[1] for r in conn.execute(
            "SELECT source || '/' || review_status, COUNT(*) FROM lender_classifications GROUP BY 1")},
        "unclassified_names": one(
            "SELECT COUNT(DISTINCT p.name_norm) FROM charge_persons p LEFT JOIN "
            "lender_classifications lc ON lc.name_norm = p.name_norm WHERE lc.name_norm IS NULL"),
        "stream_timepoint": get_meta(conn, "stream_timepoint"),
        "last_stream_gap": get_meta(conn, "stream_gap_at"),
        "latest_delivered_on": one("SELECT MAX(delivered_on) FROM charges"),
    }


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m radar.pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--minutes", type=float, default=10)
    r.add_argument("--enrich-limit", type=int, default=100)
    r.add_argument("--queue-limit", type=int, default=150)
    sub.add_parser("status")
    args = p.parse_args(argv)
    if args.cmd == "run":
        print(json.dumps(run(args.minutes, args.enrich_limit, args.queue_limit), indent=2))
    else:
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()

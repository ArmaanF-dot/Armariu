"""Offline demo: runs the whole pipeline on synthetic data, no API keys or Claude needed.

    python -m radar.demo

Writes everything to demo_output/ (git-ignored) so it never touches the real database.
"""
from __future__ import annotations

import shutil
from datetime import date, timedelta

from . import classify, config, export
from .db import connect
from .ingest import process_event
from .rules import load_rules
from .sample import sample_agent_results, sample_events


def main():
    out = config.ROOT / "demo_output"
    shutil.rmtree(out, ignore_errors=True)
    (out / "results").mkdir(parents=True)
    conn = connect(out / "demo.db")

    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    for ev in sample_events(last_monday):
        process_event(conn, ev)
    conn.commit()
    rules = load_rules()

    print("1) Rules pass:", classify.refresh_rule_matches(conn, rules))
    n = classify.build_queue(conn, out / "queue.jsonl")
    print(f"2) Queue for the classifier agent: {n} names -> demo_output/queue.jsonl")

    (out / "results" / "demo-classifier.jsonl").write_text(
        "\n".join(sample_agent_results()) + "\n", encoding="utf-8")
    print("3) Pretend agent answered. Applying:", classify.apply_results(conn, out / "results"))
    print("   (2 bad lines rejected on purpose: see logs/apply_rejections.log)")
    print("4) Queue now:", classify.build_queue(conn, out / "queue.jsonl"), "names left")

    paths = export.write_standard(conn, today, out / "weekly")
    print("5) Digests written:")
    for p in paths:
        print("   ", p.relative_to(config.ROOT))
    print("\nOpen the first .md file to see what the reviewer and newsletter agents would read.")


if __name__ == "__main__":
    main()

"""Drift check: has the refinance score's monthly lift slipped?

    python -m radar.drift [--dir output/refi_watch/sample] [--window 365]

Reads the monthly top-10% lift that radar.refi_accuracy stores for ordinary companies. Flags the model
when the latest FLAG_RUN months in a row all sit under FLOOR (3 months under 2.5x), the rule in
docs/model_rules.md. The weekly pack prints the same line.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config

FLOOR = 2.5
FLAG_RUN = 3
GROUP = "Ordinary companies (10 loans or fewer)"


def check(months: list[dict], floor: float = FLOOR, run: int = FLAG_RUN) -> dict:
    lifts = [(m["date"], m["lift"]) for m in months if m.get("lift") is not None]
    tail = lifts[-run:]
    flagged = len(tail) == run and all(l < floor for _, l in tail)
    below = sum(l < floor for _, l in lifts)
    return {"months": len(lifts), "below_floor": below, "latest": lifts[-1] if lifts else None,
            "flagged": flagged}


def line(out_dir=None, window: int = 365) -> str:
    p = Path(out_dir or config.OUTPUT_DIR / "refi_watch" / "sample") / f"accuracy_{window}d.json"
    try:
        months = json.loads(p.read_text(encoding="utf-8"))["monthly_top10"][GROUP]["months"]
    except (OSError, ValueError, KeyError):
        return "Model health: no monthly lift stored yet (run radar.refi_accuracy --population sample)."
    r = check(months)
    if not r["months"]:
        return "Model health: no monthly lift stored yet."
    status = ("**FLAG: last 3 months all under 2.5x. Refit and say so.**" if r["flagged"]
              else "steady (no run of 3 months under 2.5x)")
    return (f"Model health: ordinary companies, top 10% lift {r['latest'][1]}x in {r['latest'][0]}; "
            f"{r['below_floor']} of {r['months']} months under {FLOOR}x; {status}.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.drift")
    p.add_argument("--dir"); p.add_argument("--window", type=int, default=365)
    a = p.parse_args(argv)
    print(line(a.dir, a.window))


if __name__ == "__main__":
    main()

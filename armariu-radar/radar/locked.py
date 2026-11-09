"""The locked test window: months from 1 Nov 2026 are held back so no model is chosen on them.

The two earlier locked windows (90 days: Jan to Jul 2026; 12 months: Jul to Oct 2025) have been used,
so a model picked on them would look better than it is. Every test that picks or compares models
refuses to score dates on or after LOCKED_FROM unless asked to. See docs/model_rules.md.
"""
from __future__ import annotations

from datetime import date

LOCKED_FROM = date(2026, 11, 1)


def guard(dates, allow: bool = False) -> None:
    """Raise if any test date falls in the locked window, unless `allow` (a final, one-off check)."""
    bad = [d for d in dates if d >= LOCKED_FROM]
    if bad and not allow:
        raise SystemExit(
            f"Test dates from {LOCKED_FROM} are locked ({len(bad)} requested, first {min(bad)}). "
            "Choose models on earlier months. Use --use-locked only for the final check of a model "
            "already chosen (docs/model_rules.md).")

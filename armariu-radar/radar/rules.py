"""Rule-based lender classification from data/lenders.csv."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config
from .normalise import normalise_name

MATCH_TYPES = ("exact", "contains", "regex")
_TYPE_RANK = {"exact": 0, "contains": 1, "regex": 2}


@dataclass(frozen=True)
class Rule:
    match_type: str
    pattern: str          # normalised for exact/contains; raw regex for regex
    category: str
    parent_firm: str
    confidence: float
    source: str           # human | seed
    note: str = ""


def load_rules(path=None) -> list[Rule]:
    path = Path(path or config.LENDERS_CSV)
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    rules: list[Rule] = []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    for i, row in enumerate(reader, start=2):
        mt = (row.get("match_type") or "").strip().lower()
        cat = (row.get("category") or "").strip()
        pat = (row.get("pattern") or "").strip()
        if mt not in MATCH_TYPES:
            raise ValueError(f"{path.name} row {i}: bad match_type {mt!r}")
        if cat not in config.CATEGORIES:
            raise ValueError(f"{path.name} row {i}: bad category {cat!r}")
        if not pat:
            raise ValueError(f"{path.name} row {i}: empty pattern")
        if mt != "regex":
            pat = normalise_name(pat)
        else:
            re.compile(pat)
        rules.append(
            Rule(
                match_type=mt,
                pattern=pat,
                category=cat,
                parent_firm=(row.get("parent_firm") or "").strip(),
                confidence=float(row.get("confidence") or 1.0),
                source=(row.get("source") or "seed").strip().lower(),
                note=(row.get("note") or "").strip(),
            )
        )
    # Human rules first, then exact < contains < regex, then longest pattern first.
    rules.sort(
        key=lambda r: (0 if r.source == "human" else 1, _TYPE_RANK[r.match_type], -len(r.pattern))
    )
    return rules


def find_rule(name_norm: str, rules: list[Rule]) -> Optional[Rule]:
    padded = f" {name_norm} "
    for r in rules:
        if r.match_type == "exact":
            if name_norm == r.pattern:
                return r
        elif r.match_type == "contains":
            if f" {r.pattern} " in padded:  # token-boundary match: 'ARES' != 'CHARES'
                return r
        elif re.search(r.pattern, name_norm):
            return r
    return None

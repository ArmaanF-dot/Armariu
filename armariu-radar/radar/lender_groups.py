"""Lender groups for the monthly extract: one canonical lender name and one of five groups.

    clearing_bank | challenger_bank | private_credit_direct | abl_invoice | other

`data/lender_groups.csv` is a lookup table matched on the normalised chargee name (same matching
as lenders.csv, longest pattern first). Names the table does not cover fall back to their
lenders.csv category. No AI is involved: the same name always gets the same answer.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from . import config
from .normalise import normalise_name

GROUPS = ("clearing_bank", "challenger_bank", "private_credit_direct", "abl_invoice", "other")
GROUP_LABELS = {
    "clearing_bank": "Clearing bank",
    "challenger_bank": "Challenger bank",
    "private_credit_direct": "Private credit / direct lender",
    "abl_invoice": "Asset-based / invoice finance",
    "other": "Other",
}
GROUPS_CSV = config.ROOT / "data" / "lender_groups.csv"

# Fallback when no table row matches: lenders.csv category -> group.
CATEGORY_GROUP = {
    "direct_lender": "private_credit_direct",
    "abl_specialist": "abl_invoice",
}
_TYPE_RANK = {"exact": 0, "contains": 1, "regex": 2}
_TRAILING = {"GROUP", "HOLDINGS", "HOLDING", "UK"}


@dataclass(frozen=True)
class GroupRule:
    match_type: str
    pattern: str
    canonical: str
    lender_group: str
    note: str = ""


@dataclass(frozen=True)
class LenderGroup:
    canonical: str       # display name, e.g. "HSBC"
    key: str             # normalised, for counting
    group: str
    basis: str           # table | category | name
    detail_category: str  # the lenders.csv category (bank, property_lender, ...)


def load_group_rules(path=None) -> list[GroupRule]:
    path = Path(path or GROUPS_CSV)
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    rules = []
    for i, row in enumerate(csv.DictReader(io.StringIO("\n".join(lines))), start=2):
        mt = (row.get("match_type") or "").strip().lower()
        pat = (row.get("pattern") or "").strip()
        group = (row.get("lender_group") or "").strip()
        canonical = (row.get("canonical") or "").strip()
        if mt not in _TYPE_RANK:
            raise ValueError(f"{path.name} row {i}: bad match_type {mt!r}")
        if group not in GROUPS:
            raise ValueError(f"{path.name} row {i}: bad lender_group {group!r}")
        if not pat or not canonical:
            raise ValueError(f"{path.name} row {i}: pattern and canonical are required")
        if mt == "regex":
            re.compile(pat)
        else:
            pat = normalise_name(pat)
        rules.append(GroupRule(mt, pat, canonical, group, (row.get("note") or "").strip()))
    rules.sort(key=lambda r: (_TYPE_RANK[r.match_type], -len(r.pattern)))
    return rules


def _find(name_norm: str, rules: list[GroupRule]):
    padded = f" {name_norm} "
    # Longest pattern first across match types, so "HSBC INVOICE FINANCE" beats "HSBC".
    for r in sorted(rules, key=lambda r: (-len(r.pattern), _TYPE_RANK[r.match_type])):
        if r.match_type == "exact":
            hit = name_norm == r.pattern
        elif r.match_type == "contains":
            hit = f" {r.pattern} " in padded
        else:
            hit = re.search(r.pattern, name_norm) is not None
        if hit:
            return r
    return None


def lender_key(text: str) -> str:
    """Key for counting: 'Fortys Capital Group Limited' and 'Fortys Capital Group' agree."""
    tokens = normalise_name(text).split()
    while len(tokens) > 1 and tokens[-1] in _TRAILING:
        tokens.pop()
    return " ".join(tokens)


def group_lender(name_norm: str, category: str | None, parent_firm: str | None,
                 rules: list[GroupRule]) -> LenderGroup:
    category = category or "unknown"
    rule = _find(name_norm, rules)
    if rule:
        return LenderGroup(rule.canonical, lender_key(rule.canonical), rule.lender_group,
                           "table", category)
    group = CATEGORY_GROUP.get(category, "other")
    if parent_firm:
        return LenderGroup(parent_firm, lender_key(parent_firm), group, "category", category)
    from .normalise import family_key
    fam = family_key(name_norm)
    return LenderGroup(fam.title() if fam == fam.upper() else fam, lender_key(fam), group,
                       "name", category)

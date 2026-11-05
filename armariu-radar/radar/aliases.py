"""Module A: one lender is one entity.

    python -m radar.aliases review [--since 2025-11-03] [--top 20]   # unmapped names, most frequent first
    python -m radar.aliases coverage [--since 2025-11-03]            # share of charges that map

Order of matching (first hit wins, the same input always gives the same answer, no AI):
  1. data/lender_aliases.csv   hand-curated alias -> canonical lender          (how = alias)
  2. data/lender_groups.csv    the five-group lookup table from Week 1         (how = table)
  3. security agents / trustees stay their own class                            (how = agent)
  4. a parent firm already recorded in lender_classifications or lenders.csv   (how = parent)
  5. fuzzy token match against names already known, high threshold only        (how = fuzzy)
  6. nothing matched: kept as its own entity and sent to the review queue      (how = unmapped)
Fuzzy matches never merge anything below the threshold; below it the name goes to the review file.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from . import config
from .lender_groups import group_lender, lender_key, load_group_rules
from .normalise import family_key, normalise_name
from .rules import find_rule, load_rules

ALIASES_CSV = config.ROOT / "data" / "lender_aliases.csv"
CLASSES = ("bank", "direct_lender", "abl_invoice", "asset_finance", "trustee_agent",
           "government_backed", "property", "individual", "other")
FUZZY_THRESHOLD = 0.94
SUGGEST_THRESHOLD = 0.80
_CATEGORY_CLASS = {
    "bank": "bank", "direct_lender": "direct_lender", "abl_specialist": "abl_invoice",
    "asset_finance": "asset_finance", "security_agent": "trustee_agent",
    "public_sector": "government_backed", "property_lender": "property", "individual": "individual",
}
_NOISE = {"UK", "GROUP", "HOLDINGS", "HOLDING", "COMPANY", "THE"}


@dataclass(frozen=True)
class Resolved:
    canonical: str
    key: str
    cls: str
    how: str        # alias | table | agent | parent | fuzzy | unmapped
    category: str   # lenders.csv category behind it

    @property
    def mapped(self) -> bool:
        return self.how != "unmapped"

    @property
    def is_agent(self) -> bool:
        return self.cls == "trustee_agent"


_ROLE = re.compile(r"\s+(FOR ITSELF\b.*|(ACTING )?AS (COMMON |SECURITY |COLLATERAL |FACILITY |NOTE )?(TRUSTEE|AGENT)\b.*|"
                   r"ON BEHALF OF\b.*|IN ITS CAPACITY AS\b.*|ACTING (WITH |IN )?RESPECT OF\b.*|CO NO\b.*|COMPANY NO\b.*|COMPANY NUMBER\b.*|REGISTERED NUMBER\b.*|\d{6,8}$|"
                   r"(LONDON |UK )?BRANCH\b.*|A UK ESTABLISHMENT\b.*)$")
_LEAD = re.compile(r"^(A )?(UK )?ESTABLISHMENT OF\s+")
_TAIL = {"LIMITED", "LTD", "PLC", "LLP", "LP", "UK", "THE", "AND"}


def strip_role(norm: str) -> str:
    """'GLAS TRUST CORPORATION LIMITED AS SECURITY TRUSTEE' -> 'GLAS TRUST CORPORATION'.
    The role still decides the class (trustee_agent); it is only dropped from the entity name."""
    s = _ROLE.sub("", _LEAD.sub("", norm)).strip()
    toks = s.split()
    while len(toks) > 1 and toks[-1] in _TAIL:
        toks.pop()
    return " ".join(toks) or norm


def _display(norm: str) -> str:
    fam = family_key(strip_role(norm))
    return fam.title() if fam == fam.upper() else fam


def _tokens(norm: str) -> list[str]:
    return [t for t in norm.split() if t not in _NOISE]


_ORG_WORDS = set("""LIMITED LTD PLC LLP LP LLC INC CORP CORPORATION COMPANY CO SA NV AG GMBH BV SARL SPA AB AS OY
BANK BANKING TRUST TRUSTEE TRUSTEES CAPITAL FINANCE FINANCIAL FUNDING FUND FUNDS HOLDINGS HOLDING GROUP PARTNERS
PARTNERSHIP INVESTMENTS INVESTMENT SECURITIES MANAGEMENT SERVICES COUNCIL BOROUGH CITY COUNTY AUTHORITY ASSOCIATION
SOCIETY BUILDING UNIVERSITY COLLEGE SCHOOL MINISTERS SECRETARY STATE ENGLAND SCOTLAND WALES HOMES LENDING LOANS LOAN
MORTGAGE MORTGAGES PROPERTY PROPERTIES ESTATE ESTATES LEASING ASSET ASSETS CREDIT INVOICE COMMERCIAL DEVELOPMENT
DEVELOPMENTS VENTURES ENTERPRISES INTERNATIONAL UK BRITISH NATIONAL ROYAL BOARD AGENCY LENDER LENDERS PENSION SCHEME
SSAS SIPP THE AND OF FOR LTD. LIMITED. CHARITY FOUNDATION CHURCH DIOCESE HOUSING""".split())


def looks_like_person(name: str) -> bool:
    """Two to four plain words with no organisation word: almost certainly a person (e.g. JOHN SMITH)."""
    import re
    words = re.sub(r"[.,'()]", " ", (name or "").upper()).replace("&", " ").split()
    return 2 <= len(words) <= 4 and all(w.isalpha() for w in words) and not (set(words) & _ORG_WORDS)


def alias_key(raw: str) -> str:
    """Normalise a spelling: lower-case rules of the plan, via the repo's normaliser (upper case,
    no punctuation, no Ltd/PLC/LLP) plus a dropped trailing UK, then whitespace collapsed."""
    toks = strip_role(normalise_name(raw)).split()
    while len(toks) > 1 and toks[-1] == "UK":
        toks.pop()
    return " ".join(toks)


def load_aliases(path=None) -> dict[str, tuple[str, str, str]]:
    path = Path(path or ALIASES_CSV)
    if not path.exists():
        return {}
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    out: dict[str, tuple[str, str, str]] = {}
    for i, row in enumerate(csv.DictReader(io.StringIO("\n".join(lines))), start=2):
        canon, alias, cls = (row.get(k, "").strip() for k in ("canonical", "alias", "class"))
        if not canon or not alias:
            raise ValueError(f"{path.name} row {i}: canonical and alias are required")
        if cls not in CLASSES:
            raise ValueError(f"{path.name} row {i}: bad class {cls!r}")
        k = alias_key(alias)
        if k in out and out[k][0] != canon:
            raise ValueError(f"{path.name} row {i}: alias {alias!r} maps to two lenders")
        out[k] = (canon, cls, row.get("notes", "").strip())
    return out


class Resolver:
    """Maps any chargee name to one canonical lender. Build once, call resolve() many times."""

    def __init__(self, conn=None, aliases=None, group_rules=None, rules=None):
        self.aliases = load_aliases() if aliases is None else aliases
        self.group_rules = load_group_rules() if group_rules is None else group_rules
        try:
            self.rules = load_rules() if rules is None else rules
        except (OSError, ValueError):
            self.rules = []
        self.cache: dict[str, tuple[str, str | None, str | None]] = {}
        if conn is not None:
            for r in conn.execute("SELECT name_norm, category, parent_firm FROM lender_classifications"):
                self.cache[r["name_norm"]] = (r["category"], r["parent_firm"] or None, None)
        # Names the fuzzy step may match against: canonical lenders and parent firms already known.
        known: dict[str, tuple[str, str]] = {}
        for canon, cls, _ in self.aliases.values():
            known[alias_key(canon)] = (canon, cls)
        for r in self.group_rules:
            known.setdefault(alias_key(r.canonical), (r.canonical, _group_class(r.lender_group)))
        self._known = known
        self._memo: dict[str, Resolved] = {}

    def _category(self, norm: str) -> tuple[str, str | None]:
        if norm in self.cache:
            cat, parent, _ = self.cache[norm]
            return cat or "unknown", parent
        rule = find_rule(norm, self.rules) if norm else None
        return (rule.category, rule.parent_firm or None) if rule else ("unknown", None)

    def resolve(self, raw: str) -> Resolved:
        norm = normalise_name(raw)
        mk = (raw or "").strip().upper()   # raw, not norm: "X PLC" and a person called "X" normalise alike
        if mk in self._memo:
            return self._memo[mk]
        res = self._resolve(raw, norm)
        self._memo[mk] = res
        return res

    def _resolve(self, raw: str, norm: str) -> Resolved:
        cat, parent = self._category(norm)
        ak = alias_key(raw)
        hit = self.aliases.get(ak)
        if hit:
            return Resolved(hit[0], lender_key(hit[0]), hit[1], "alias", cat)
        g = group_lender(norm, cat, parent, self.group_rules)
        if cat == "security_agent":
            canon = parent or (g.canonical if g.basis == "table" else None)
            if not canon and ak and ak != norm:
                # the same firm acting as agent: 'CREDIT AGRICOLE CIB AS SECURITY TRUSTEE' keeps Credit Agricole's
                # key, so a lender moving into the agent role on its own deal is not read as a new lender
                inner = self._resolve(ak, ak)
                if inner.mapped and inner.how != "agent":
                    canon = inner.canonical
            canon = canon or _display(norm)
            return Resolved(canon, lender_key(canon), "trustee_agent", "agent", cat)
        if g.basis == "table":
            # Armaan's group table is the more specific call ("HSBC INVOICE FINANCE" is ABL, not a bank)
            cls = _group_class(g.group) if g.group != "other" else _CATEGORY_CLASS.get(cat, "other")
            return Resolved(g.canonical, g.key, cls, "table", cat)
        if parent:
            return Resolved(parent, lender_key(parent), _CATEGORY_CLASS.get(cat, "other"), "parent", cat)
        best = self._fuzzy(ak)
        if best:
            canon, cls, _ = best
            return Resolved(canon, lender_key(canon), cls, "fuzzy", cat)
        canon = _display(norm)
        cls = _CATEGORY_CLASS.get(cat, "other")
        if cls == "other" and looks_like_person(raw):   # never let a person's name through as a lender
            cls = "individual"
        return Resolved(canon, lender_key(canon), cls, "unmapped", cat)

    def _fuzzy(self, ak: str, threshold: float = FUZZY_THRESHOLD):
        """Best known lender at or above the threshold. Requires the same first four letters so
        'ALDERMORE' never drifts onto 'ALLICA'. Ties break alphabetically (deterministic)."""
        mine = _tokens(ak)
        if not mine:
            return None
        best = None
        for k in sorted(self._known):
            theirs = _tokens(k)
            if not theirs or theirs[0][:4] != mine[0][:4]:
                continue
            score = SequenceMatcher(None, " ".join(mine), " ".join(theirs)).ratio()
            if score >= threshold and (best is None or score > best[0]):
                best = (score, *self._known[k], k)
        return best[1:] if best else None

    def suggest(self, ak: str):
        """Nearest known lender above the lower suggestion bar. A hint for Armaan, never applied."""
        mine = _tokens(ak)
        best = None
        for k in sorted(self._known):
            theirs = _tokens(k)
            if not theirs or not mine:
                continue
            score = SequenceMatcher(None, " ".join(mine), " ".join(theirs)).ratio()
            if score >= SUGGEST_THRESHOLD and (best is None or score > best[0]):
                best = (score, self._known[k][0])
        return best


def _group_class(group: str) -> str:
    return {"clearing_bank": "bank", "challenger_bank": "bank", "private_credit_direct": "direct_lender",
            "abl_invoice": "abl_invoice"}.get(group, "other")


def chargee_names(conn, since: str | None = None):
    """(name_raw, filed date) for every chargee on the register and in borrower history."""
    q = ("SELECT p.name_raw AS n, c.delivered_on AS d FROM charge_persons p JOIN charges c "
         "ON c.company_number = p.company_number AND c.charge_id = p.charge_id WHERE c.deleted = 0")
    for r in conn.execute(q):
        if not since or (r["d"] or "") >= since:
            yield r["n"], r["d"]


def coverage(conn, resolver: Resolver, since: str | None = None, target_sector: str | None = None) -> dict:
    """Share of charges whose primary chargee maps to a canonical lender or trustee class."""
    total = mapped = 0
    for _, ch in _charges_with_names(conn, since):
        total += 1
        res = [resolver.resolve(n) for n in ch]
        if any(r.mapped for r in res):
            mapped += 1
    return {"charges": total, "mapped": mapped, "share": (mapped / total) if total else 0.0}


def _charges_with_names(conn, since):
    cur = {}
    for r in conn.execute("SELECT c.company_number, c.charge_id, c.delivered_on, p.name_raw FROM charges c "
                          "JOIN charge_persons p ON p.company_number = c.company_number AND p.charge_id = c.charge_id "
                          "WHERE c.deleted = 0 ORDER BY c.company_number, c.charge_id, p.name_raw"):
        if since and (r["delivered_on"] or "") < since:
            continue
        cur.setdefault((r["company_number"], r["charge_id"]), []).append(r["name_raw"])
    return cur.items()


def review_rows(conn, resolver: Resolver, since: str | None = None, top: int = 20) -> list[dict]:
    """Unmapped chargee names ranked by frequency, with a suggestion where one is close."""
    counts: Counter = Counter()
    example: dict[str, str] = {}
    for _, names in _charges_with_names(conn, since):
        for n in names:
            r = resolver.resolve(n)
            if not r.mapped:
                k = alias_key(n)
                counts[k] += 1
                example.setdefault(k, n)
    rows = []
    for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]:
        sug = resolver.suggest(k)
        rows.append({"alias_key": k, "example": example[k], "charges": n,
                     "suggested_canonical": sug[1] if sug else "",
                     "similarity": f"{sug[0]:.2f}" if sug else ""})
    return rows


def write_review(rows: list[dict], out_dir, run_date: date) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"unmapped_lenders_{run_date.isoformat()}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=("alias_key", "example", "charges", "suggested_canonical",
                                           "similarity", "canonical", "class"))
        w.writeheader()
        for r in rows:
            w.writerow({**r, "canonical": "", "class": ""})
    return path


def main(argv=None):
    from .db import connect
    p = argparse.ArgumentParser(prog="python -m radar.aliases")
    p.add_argument("cmd", choices=("review", "coverage"))
    p.add_argument("--since", help="only charges filed on or after this date (default: 12 months ago)")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "review"))
    a = p.parse_args(argv)
    since = a.since or (date.today() - timedelta(days=365)).isoformat()
    conn = connect()
    res = Resolver(conn)
    if a.cmd == "coverage":
        c = coverage(conn, res, since)
        print(f"{c['mapped']} of {c['charges']} charges since {since} map to a canonical lender "
              f"or trustee class ({c['share']:.1%}).")
        return
    rows = review_rows(conn, res, since, a.top)
    path = write_review(rows, a.out, date.today())
    print(f"Wrote {path} ({len(rows)} names). Fill the canonical and class columns, then add the "
          "rows to data/lender_aliases.csv.")


if __name__ == "__main__":
    main()

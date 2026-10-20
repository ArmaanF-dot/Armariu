"""Human review of lender classifications. Decisions are saved to data/lenders.csv as
source=human rules, so they are shared through git and always beat agent output.

    python -m radar.review list [--all]              # what needs a human decision
    python -m radar.review show "ACME CREDIT FUND II"
    python -m radar.review approve "ACME CREDIT FUND II"         # accept the agent's answer
    python -m radar.review set "ACME CREDIT FUND II" direct_lender --parent "Acme Capital"
"""
from __future__ import annotations

import argparse
import csv

from . import config
from .classify import refresh_rule_matches
from .db import connect
from .normalise import normalise_name
from .rules import load_rules


def add_human_rule(name: str, category: str, parent: str = "", note: str = "", path=None) -> str:
    if category not in config.CATEGORIES:
        raise SystemExit(f"Unknown category {category!r}. Choose from: {', '.join(config.CATEGORIES)}")
    norm = normalise_name(name)
    if not norm:
        raise SystemExit("Empty name")
    path = path or config.LENDERS_CSV
    text = path.read_text(encoding="utf-8")
    with open(path, "a", newline="", encoding="utf-8") as fh:
        if not text.endswith("\n"):
            fh.write("\n")
        csv.writer(fh).writerow(["exact", norm, category, parent, "1.0", "human", note])
    return norm


def _list(conn, show_all: bool):
    statuses = ("needs_review", "auto_ok") if show_all else ("needs_review",)
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT lc.*, COUNT(p.charge_id) AS n FROM lender_classifications lc "
        f"JOIN charge_persons p ON p.name_norm = lc.name_norm "
        f"WHERE lc.review_status IN ({marks}) GROUP BY lc.name_norm ORDER BY n DESC",
        statuses,
    ).fetchall()
    unclassified = conn.execute(
        "SELECT COUNT(DISTINCT p.name_norm) FROM charge_persons p "
        "LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm WHERE lc.name_norm IS NULL"
    ).fetchone()[0]
    for r in rows:
        print(f"[{r['review_status']:>12}] {r['n']:>4} charges  {r['category']:<15} "
              f"conf={r['confidence'] or 0:.2f}  {r['name_norm']}\n{'':>20}reason: {r['reason'] or ''}")
    print(f"\n{len(rows)} shown. {unclassified} names still waiting for the classifier agent.")


def _show(conn, name: str):
    norm = normalise_name(name)
    lc = conn.execute("SELECT * FROM lender_classifications WHERE name_norm = ?", (norm,)).fetchone()
    print(dict(lc) if lc else f"{norm}: not classified yet")
    for r in conn.execute(
        "SELECT c.company_number, co.name, c.delivered_on, c.classification_text FROM charge_persons p "
        "JOIN charges c ON c.company_number=p.company_number AND c.charge_id=p.charge_id "
        "LEFT JOIN companies co ON co.company_number=c.company_number "
        "WHERE p.name_norm = ? ORDER BY c.delivered_on DESC LIMIT 10", (norm,)
    ):
        print(f"  {r['delivered_on']}  {r['company_number']}  {r['name'] or ''}  {r['classification_text'] or ''}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.review")
    sub = p.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list"); l.add_argument("--all", action="store_true",
                                               help="also show auto_ok agent rows")
    s = sub.add_parser("show"); s.add_argument("name")
    a = sub.add_parser("approve"); a.add_argument("name")
    st = sub.add_parser("set")
    st.add_argument("name"); st.add_argument("category")
    st.add_argument("--parent", default=""); st.add_argument("--note", default="")
    args = p.parse_args(argv)
    conn = connect()
    if args.cmd == "list":
        _list(conn, args.all)
    elif args.cmd == "show":
        _show(conn, args.name)
    else:
        if args.cmd == "approve":
            row = conn.execute("SELECT * FROM lender_classifications WHERE name_norm = ?",
                               (normalise_name(args.name),)).fetchone()
            if not row:
                raise SystemExit("Nothing to approve: that name has no classification yet.")
            norm = add_human_rule(args.name, row["category"], row["parent_firm"] or "",
                                  "approved agent result")
        else:
            norm = add_human_rule(args.name, args.category, args.parent, args.note)
        refresh_rule_matches(conn, load_rules())
        print(f"Saved human rule for {norm}. Commit data/lenders.csv to share it.")


if __name__ == "__main__":
    main()

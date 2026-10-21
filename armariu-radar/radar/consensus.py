"""Second opinions and a referee for lender classifications, so fewer names need a person.

    first   = the classifier agent's answer (lender_classifications, source=agent)
    second  = the verifier agent (Sonnet, once a day): classifies the same name independently,
              from the same evidence plus the Companies House owner lookup, without seeing the first answer
    referee = the referee agent (Opus): only names where the first two disagree; sees both answers

Rules (plain Python, no AI):
    agreed      first and second give the same type, both sure enough -> used without asking you
    unsure      same type but low confidence, or both "can't tell"    -> you
    disputed    different types                                        -> referee
    settled     referee sides with one of them, confidently           -> used without asking you
    unresolved  referee unsure or picks something else                 -> you, with all three opinions

Your decisions and the rules always win: this only ever changes rows the agents own
(source=agent). Every automatic answer stays listed in the app's Double-check section.

Files (agents never touch the database):
    classify/second_queue.jsonl   pipeline -> verifier      classify/second/*.jsonl   verifier -> pipeline
    classify/referee_queue.jsonl  pipeline -> referee       classify/referee/*.jsonl  referee -> pipeline
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import config, owners, sectors
from .db import now_iso
from .normalise import family_key, normalise_name

AGREE_MIN = 0.60            # both answers at least this sure...
AGREE_MIN_DIRECT = 0.70     # ...or this, when they agree it's a direct lender
REFEREE_MIN = 0.80          # the referee must be this sure to settle a dispute...
REFEREE_MIN_DIRECT = 0.85   # ...or this, to settle on direct lender
SECOND_BATCH = 40
REFEREE_BATCH = 20
_MAX_REASON = 300


def ask_id(name: str, category, confidence, updated_at) -> str:
    return hashlib.sha1(f"{name}|{category}|{confidence}|{updated_at}".encode()).hexdigest()[:12]


def _dirs():
    # Resolved at call time so tests can point config.ROOT elsewhere.
    root = config.ROOT / "classify"
    return root / "second", root / "referee", root / "second_queue.jsonl", root / "referee_queue.jsonl"


def _current(conn, name: str):
    """The agent row for a name and the ask_id that row is being judged on."""
    row = conn.execute("SELECT * FROM lender_classifications WHERE name_norm = ? AND source = 'agent'", (name,)).fetchone()
    if not row:
        return None, None
    c = conn.execute("SELECT ask_id, first_updated_at FROM consensus WHERE name_norm = ?", (name,)).fetchone()
    if c and c["first_updated_at"] == row["updated_at"]:
        return row, c["ask_id"]   # may have been settled by the referee: keep judging the original question
    return row, ask_id(name, row["category"], row["confidence"], row["updated_at"])


# --------------------------------------------------------------------------- evidence

def evidence(conn, name: str, examples: int = 5) -> dict:
    """What a reviewer agent sees about a lender name. Never includes another agent's answer."""
    display, n = conn.execute(
        "SELECT MIN(name_raw), COUNT(*) FROM charge_persons WHERE name_norm = ?", (name,)).fetchone()
    ex = []
    for e in conn.execute(
            "SELECT c.company_number, c.charge_id, c.created_on, c.delivered_on, c.status, c.classification_text, "
            "c.floating_covers_all, co.name AS company_name, co.sic_codes FROM charge_persons p JOIN charges c "
            "ON c.company_number = p.company_number AND c.charge_id = p.charge_id "
            "LEFT JOIN companies co ON co.company_number = c.company_number "
            "WHERE p.name_norm = ? AND c.deleted = 0 ORDER BY c.delivered_on DESC LIMIT ?", (name, examples)):
        sic = json.loads(e["sic_codes"]) if e["sic_codes"] else []
        others = [r[0] for r in conn.execute(
            "SELECT name_raw FROM charge_persons WHERE company_number = ? AND charge_id = ? AND name_norm != ?",
            (e["company_number"], e["charge_id"], name))]
        ex.append({
            "borrower": e["company_name"], "borrower_number": e["company_number"], "borrower_sic": sic,
            "borrower_sector": sectors.label(sectors.sectors_of(sic)[0]),
            "created_on": e["created_on"], "delivered_on": e["delivered_on"], "status": e["status"],
            "charge_type": (e["classification_text"] or "")[:200] or None,
            "covers_all_assets": e["floating_covers_all"], "other_chargees": others[:4],
            "filing_url": config.FILING_URL.format(company=e["company_number"], charge_id=e["charge_id"]),
        })
    fam = family_key(name)
    variants = [r[0] for r in conn.execute("SELECT DISTINCT name_norm FROM charge_persons")
                if r[0] != name and family_key(r[0]) == fam and fam != r[0]] if fam != name else []
    return {"name_norm": name, "display_name": display, "n_charges": n, "examples": ex,
            "companies_house": owners.profile(conn, name), "name_variants": variants[:6]}


# --------------------------------------------------------------------------- queues

def write_second_queue(conn, path=None, limit: int = SECOND_BATCH * 3) -> int:
    """Names that deserve a second opinion: the classifier was unsure, or its answer would put the
    name in the headline (direct lender, security agent). Each asked once per classifier answer."""
    path = Path(path or _dirs()[2])
    rows = conn.execute(
        "SELECT lc.name_norm, lc.category, lc.confidence, lc.parent_firm, lc.reason, lc.updated_at, "
        "(SELECT COUNT(*) FROM charge_persons p WHERE p.name_norm = lc.name_norm) AS n "
        "FROM lender_classifications lc WHERE lc.source = 'agent' "
        "AND (lc.review_status = 'needs_review' OR lc.category IN ('direct_lender', 'security_agent')) "
        "ORDER BY (lc.review_status = 'needs_review') DESC, n DESC").fetchall()
    lines = []
    for r in rows:
        _, aid = _current(conn, r["name_norm"])
        if conn.execute("SELECT 1 FROM opinions WHERE name_norm = ? AND role = 'second' AND ask_id = ?",
                        (r["name_norm"], aid)).fetchone():
            continue
        # Remember exactly what the classifier said, for the referee and the app later.
        conn.execute("INSERT OR IGNORE INTO opinions VALUES (?,?,?,?,?,?,?,?)",
                     (r["name_norm"], "first", aid, r["category"], r["confidence"], r["parent_firm"],
                      (r["reason"] or "")[:_MAX_REASON * 2], now_iso()))
        lines.append(json.dumps({"ask_id": aid, **evidence(conn, r["name_norm"])}, ensure_ascii=False))
        if len(lines) >= limit:
            break
    conn.commit()
    _write(path, lines)
    return len(lines)


def write_referee_queue(conn, path=None) -> int:
    path = Path(path or _dirs()[3])
    lines = []
    for c in conn.execute("SELECT * FROM consensus WHERE state = 'disputed'").fetchall():
        row, aid = _current(conn, c["name_norm"])
        if not row or aid != c["ask_id"]:
            continue
        if conn.execute("SELECT 1 FROM opinions WHERE name_norm = ? AND role = 'referee' AND ask_id = ?",
                        (c["name_norm"], aid)).fetchone():
            continue
        ops = {o["role"]: o for o in conn.execute(
            "SELECT * FROM opinions WHERE name_norm = ? AND ask_id = ?", (c["name_norm"], aid))}
        if "first" not in ops or "second" not in ops:
            continue
        pair = [ops["first"], ops["second"]]
        if int(aid, 16) % 2:  # don't always show the classifier first: avoids order bias
            pair.reverse()
        opinions = [{"label": label, "category": o["category"], "confidence": o["confidence"],
                     "parent_firm": o["parent_firm"], "reason": o["reason"]} for label, o in zip("AB", pair)]
        lines.append(json.dumps({"ask_id": aid, **evidence(conn, c["name_norm"]), "opinions": opinions},
                                ensure_ascii=False))
    _write(path, lines)
    return len(lines)


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- answers in

def load(conn, role: str, folder: Path) -> dict:
    """Read an agent's answer files. Answers for a question that has since changed are skipped."""
    known = {r[0] for r in conn.execute("SELECT DISTINCT name_norm FROM charge_persons")}
    ok = skipped = 0
    for f in sorted(Path(folder).glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                name = normalise_name(obj["name_norm"])
                cat = obj["category"]
                conf = float(obj["confidence"])
                aid = str(obj["ask_id"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                skipped += 1
                continue
            parent = obj.get("parent_firm") or None
            if (name not in known or cat not in config.CATEGORIES or not 0 <= conf <= 1
                    or (parent is not None and (not isinstance(parent, str) or len(parent) > 100))):
                skipped += 1
                continue
            row, current = _current(conn, name)
            if not row or aid != current:
                skipped += 1  # stale: the classifier has answered again, or a rule now decides this name
                continue
            conn.execute("INSERT OR REPLACE INTO opinions VALUES (?,?,?,?,?,?,?,?)",
                         (name, role, aid, cat, conf, parent.strip() if parent else None,
                          str(obj.get("reason") or "")[:_MAX_REASON], now_iso()))
            ok += 1
    conn.commit()
    return {"ok": ok, "skipped": skipped}


def load_all(conn) -> dict:
    second, referee, _, _ = _dirs()
    return {"second": load(conn, "second", second), "referee": load(conn, "referee", referee)}


# --------------------------------------------------------------------------- comparing

def judge(first: dict, second: dict | None, referee: dict | None) -> tuple[str, str | None]:
    """Pure rules: (state, category to use)."""
    if not second:
        return "", None
    f, s = first["category"], second["category"]
    if referee:
        r, rc = referee["category"], referee["confidence"] or 0
        bar = REFEREE_MIN_DIRECT if r == "direct_lender" else REFEREE_MIN
        if r != "unknown" and r in (f, s) and rc >= bar:
            return "settled", r
        return "unresolved", None
    if f == s and f != "unknown":
        bar = AGREE_MIN_DIRECT if f == "direct_lender" else AGREE_MIN
        if min(first["confidence"] or 0, second["confidence"] or 0) >= bar:
            return "agreed", f
        return "unsure", None
    if f == s:
        return "unsure", None   # both "can't tell": a referee wouldn't know more
    return "disputed", None


def reconcile(conn) -> dict:
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT * FROM lender_classifications WHERE source = 'agent'").fetchall():
        name = row["name_norm"]
        _, aid = _current(conn, name)
        ops = {o["role"]: dict(o) for o in conn.execute(
            "SELECT * FROM opinions WHERE name_norm = ? AND ask_id = ?", (name, aid))}
        if "second" not in ops:
            continue
        first = ops.get("first") or {"category": row["category"], "confidence": row["confidence"],
                                     "parent_firm": row["parent_firm"], "reason": row["reason"]}
        state, cat = judge(first, ops["second"], ops.get("referee"))
        counts[state] = counts.get(state, 0) + 1
        prev = conn.execute("SELECT state, ask_id FROM consensus WHERE name_norm = ?", (name,)).fetchone()
        if prev and prev["ask_id"] == aid and prev["state"] == state:
            continue
        flagged = "| reviewer:" in (row["reason"] or "")
        if state == "agreed" and not flagged:
            conn.execute("UPDATE lender_classifications SET review_status = 'auto_ok', reason = ?, "
                         "parent_firm = COALESCE(parent_firm, ?) WHERE name_norm = ?",
                         (f"{first['reason'] or ''} | second opinion agrees ({round((ops['second']['confidence'] or 0) * 100)}%): "
                          f"{ops['second']['reason'] or ''}"[:_MAX_REASON * 2], ops["second"]["parent_firm"], name))
        elif state == "settled" and not flagged:
            ref = ops["referee"]
            conn.execute("UPDATE lender_classifications SET category = ?, confidence = ?, review_status = 'auto_ok', "
                         "parent_firm = COALESCE(?, parent_firm), reason = ? WHERE name_norm = ?",
                         (cat, ref["confidence"], ref["parent_firm"], f"Referee: {ref['reason'] or ''}"[:_MAX_REASON * 2], name))
        else:
            conn.execute("UPDATE lender_classifications SET review_status = 'needs_review' WHERE name_norm = ?", (name,))
        conn.execute("INSERT OR REPLACE INTO consensus VALUES (?,?,?,?,?,?)",
                     (name, aid, row["updated_at"], state, cat, now_iso()))
    conn.commit()
    return counts


def owns_status(conn, name: str, updated_at) -> bool:
    """True when the consensus decides this row's review status (so the plain thresholds don't)."""
    c = conn.execute("SELECT first_updated_at FROM consensus WHERE name_norm = ?", (name,)).fetchone()
    return bool(c and c["first_updated_at"] == updated_at)


def opinions_for(conn, name: str) -> dict:
    """Everything the agents said about a name's current question, for the app."""
    row, aid = _current(conn, name)
    if not row:
        return {"state": None, "opinions": []}
    c = conn.execute("SELECT state FROM consensus WHERE name_norm = ? AND ask_id = ?", (name, aid)).fetchone()
    order = {"first": 0, "second": 1, "referee": 2}
    ops = sorted((dict(o) for o in conn.execute("SELECT role, category, confidence, parent_firm, reason FROM opinions "
                                                 "WHERE name_norm = ? AND ask_id = ?", (name, aid))),
                 key=lambda o: order.get(o["role"], 9))
    return {"state": c["state"] if c else None, "opinions": ops}

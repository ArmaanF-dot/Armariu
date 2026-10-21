"""Classification: rules first, then a queue of unknown lender names for the classifier agent.

The unit of classification is a distinct lender NAME, not a charge. Each name is classified
once and cached in lender_classifications, so the agent's workload shrinks over time.

Trust order (highest first):
  human rules in data/lenders.csv  ->  seed rules in data/lenders.csv  ->  agent results
Agents can never overwrite a human or seed classification.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from . import config, owners
from .db import now_iso
from .normalise import normalise_name
from .rules import find_rule

log = logging.getLogger("radar.classify")

_MAX_REASON = 300
_MAX_PARENT = 100


# --------------------------------------------------------------------------- rules

def refresh_rule_matches(conn, rules) -> dict:
    """Re-apply lenders.csv to every known lender name."""
    counts = {"rule_matched": 0, "released": 0}
    names = conn.execute(
        "SELECT name_norm, MIN(name_raw) AS display FROM charge_persons GROUP BY name_norm"
    ).fetchall()
    for row in names:
        norm, display = row["name_norm"], row["display"]
        rule = find_rule(norm, rules)
        existing = conn.execute(
            "SELECT source FROM lender_classifications WHERE name_norm = ?", (norm,)
        ).fetchone()
        if rule:
            conn.execute(
                "INSERT INTO lender_classifications VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(name_norm) DO UPDATE SET display_name=excluded.display_name, "
                "category=excluded.category, parent_firm=excluded.parent_firm, "
                "confidence=excluded.confidence, source=excluded.source, "
                "review_status=excluded.review_status, reason=excluded.reason, "
                "updated_at=excluded.updated_at",
                (
                    norm, display, rule.category, rule.parent_firm or None, rule.confidence,
                    rule.source, "ok" if rule.source == "human" else "auto_ok",
                    f"rule:{rule.match_type}:{rule.pattern}", now_iso(),
                ),
            )
            counts["rule_matched"] += 1
        elif existing and existing["source"] in ("human", "seed"):
            # The rule that classified this name was removed: send it back to the queue.
            conn.execute("DELETE FROM lender_classifications WHERE name_norm = ?", (norm,))
            counts["released"] += 1
    conn.commit()
    counts["restatused"] = restatus(conn)
    return counts


# --------------------------------------------------------------------------- queue

def build_queue(conn, path=None, limit: int = 150, examples: int = 3) -> int:
    """Write unclassified lender names (most frequent first) to classify/queue.jsonl."""
    path = Path(path or config.QUEUE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = conn.execute(
        "SELECT p.name_norm, MIN(p.name_raw) AS display, COUNT(*) AS n, "
        "MAX(c.delivered_on) AS last_delivered "
        "FROM charge_persons p JOIN charges c "
        "  ON c.company_number = p.company_number AND c.charge_id = p.charge_id "
        "LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm "
        "WHERE lc.name_norm IS NULL AND c.deleted = 0 "
        "GROUP BY p.name_norm ORDER BY n DESC, last_delivered DESC LIMIT ?",
        (limit,),
    ).fetchall()
    lines = []
    for row in names:
        ex_rows = conn.execute(
            "SELECT c.company_number, c.charge_id, c.created_on, c.delivered_on, "
            "c.classification_text, c.secured_text, c.floating_covers_all, co.name AS company_name, "
            "co.sic_codes "
            "FROM charge_persons p JOIN charges c "
            "  ON c.company_number = p.company_number AND c.charge_id = p.charge_id "
            "LEFT JOIN companies co ON co.company_number = c.company_number "
            "WHERE p.name_norm = ? ORDER BY c.delivered_on DESC LIMIT ?",
            (row["name_norm"], examples),
        ).fetchall()
        ex = []
        for e in ex_rows:
            co_chargees = [
                r["name_raw"] for r in conn.execute(
                    "SELECT name_raw FROM charge_persons WHERE company_number=? AND charge_id=? "
                    "AND name_norm != ?", (e["company_number"], e["charge_id"], row["name_norm"]),
                )
            ]
            ex.append({
                "company_number": e["company_number"],
                "company_name": e["company_name"],
                "sic_codes": json.loads(e["sic_codes"]) if e["sic_codes"] else None,
                "charge_id": e["charge_id"],
                "created_on": e["created_on"],
                "delivered_on": e["delivered_on"],
                "charge_type": (e["classification_text"] or "")[:200] or None,
                "secured": (e["secured_text"] or "")[:200] or None,
                "floating_covers_all": e["floating_covers_all"],
                "other_chargees": co_chargees[:4],
                "filing_url": config.FILING_URL.format(company=e["company_number"],
                                                       charge_id=e["charge_id"]),
            })
        lines.append(json.dumps({
            "name_norm": row["name_norm"],
            "display_name": row["display"],
            "n_charges": row["n"],
            "examples": ex,
            "companies_house": owners.profile(conn, row["name_norm"]),
        }, ensure_ascii=False))
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, path)
    return len(lines)


# --------------------------------------------------------------------------- agent results

def _status_for(category: str, confidence: float) -> str:
    if category == "unknown":
        return "needs_review"
    if category == "direct_lender":
        bar = config.AUTO_OK_CONFIDENCE_DIRECT
    elif category in config.LOW_STAKES:
        bar = config.AUTO_OK_CONFIDENCE_LOW_STAKES
    else:
        bar = config.AUTO_OK_CONFIDENCE
    return "auto_ok" if (confidence or 0) >= bar else "needs_review"


def restatus(conn) -> int:
    """Re-check agent answers against the current thresholds (they change as the policy does).
    Names a reviewer flagged stay with a person."""
    from .consensus import owns_status
    changed = 0
    for r in conn.execute("SELECT name_norm, category, confidence, review_status, reason, updated_at "
                          "FROM lender_classifications WHERE source = 'agent'").fetchall():
        if "| reviewer:" in (r["reason"] or "") or owns_status(conn, r["name_norm"], r["updated_at"]):
            continue  # a reviewer flag, or the second opinion / referee, decides this one
        new = _status_for(r["category"], r["confidence"])
        if new != r["review_status"]:
            conn.execute("UPDATE lender_classifications SET review_status = ? WHERE name_norm = ?", (new, r["name_norm"]))
            changed += 1
    conn.commit()
    return changed


def _validate(obj, known_names) -> tuple[dict | None, str | None]:
    if not isinstance(obj, dict):
        return None, "not a JSON object"
    name = obj.get("name_norm")
    if not isinstance(name, str) or not name.strip():
        return None, "missing name_norm"
    norm = normalise_name(name)
    if norm not in known_names:
        return None, f"unknown name_norm {name!r}"
    cat = obj.get("category")
    if cat not in config.CATEGORIES:
        return None, f"bad category {cat!r}"
    try:
        conf = float(obj.get("confidence"))
    except (TypeError, ValueError):
        return None, "confidence is not a number"
    if not 0.0 <= conf <= 1.0:
        return None, "confidence outside 0..1"
    parent = obj.get("parent_firm") or None
    if parent is not None and (not isinstance(parent, str) or len(parent) > _MAX_PARENT):
        return None, "bad parent_firm"
    reason = obj.get("reason") or ""
    if not isinstance(reason, str):
        return None, "bad reason"
    return {
        "name_norm": norm, "category": cat, "confidence": conf,
        "parent_firm": parent.strip() if parent else None, "reason": reason.strip()[:_MAX_REASON],
    }, None


def _already_applied(conn, key: str, digest: str) -> bool:
    row = conn.execute("SELECT sha256 FROM applied_results WHERE filename = ?", (key,)).fetchone()
    return bool(row and row["sha256"] == digest)


def _record_applied(conn, key, digest, ok, bad):
    conn.execute(
        "INSERT OR REPLACE INTO applied_results VALUES (?,?,?,?,?)",
        (key, digest, now_iso(), ok, bad),
    )


def _log_rejection(fname: str, lineno: int, why: str):
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOGS_DIR / "apply_rejections.log", "a", encoding="utf-8") as fh:
        fh.write(f"{now_iso()}\t{fname}:{lineno}\t{why}\n")


def apply_results(conn, results_dir=None) -> dict:
    """Load agent result files (classify/results/*.jsonl) into lender_classifications."""
    results_dir = Path(results_dir or config.RESULTS_DIR)
    totals = {"files": 0, "ok": 0, "rejected": 0}
    known = {r["name_norm"] for r in conn.execute("SELECT DISTINCT name_norm FROM charge_persons")}
    for f in sorted(results_dir.glob("*.jsonl")):
        raw = f.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        key = f"results:{f.name}"
        if _already_applied(conn, key, digest):
            continue
        ok = bad = 0
        for lineno, line in enumerate(raw.decode("utf-8", "replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1; _log_rejection(f.name, lineno, "invalid JSON"); continue
            row, why = _validate(obj, known)
            if why:
                bad += 1; _log_rejection(f.name, lineno, why); continue
            existing = conn.execute(
                "SELECT source FROM lender_classifications WHERE name_norm = ?", (row["name_norm"],)
            ).fetchone()
            if existing and existing["source"] in ("human", "seed"):
                bad += 1; _log_rejection(f.name, lineno, "name already classified by a rule"); continue
            display = conn.execute(
                "SELECT MIN(name_raw) AS d FROM charge_persons WHERE name_norm = ?", (row["name_norm"],)
            ).fetchone()["d"]
            conn.execute(
                "INSERT OR REPLACE INTO lender_classifications VALUES (?,?,?,?,?,?,?,?,?)",
                (row["name_norm"], display, row["category"], row["parent_firm"], row["confidence"],
                 "agent", _status_for(row["category"], row["confidence"]), row["reason"], now_iso()),
            )
            ok += 1
        _record_applied(conn, key, digest, ok, bad)
        totals["files"] += 1; totals["ok"] += ok; totals["rejected"] += bad
    conn.commit()
    return totals


def apply_flags(conn, reviews_dir=None) -> dict:
    """Reviewer flags (reviews/*-flags.jsonl) push agent classifications back to needs_review."""
    reviews_dir = Path(reviews_dir or (config.ROOT / "reviews"))
    totals = {"files": 0, "flagged": 0, "ignored": 0}
    for f in sorted(reviews_dir.glob("*-flags.jsonl")):
        raw = f.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        key = f"flags:{f.name}"
        if _already_applied(conn, key, digest):
            continue
        flagged = ignored = 0
        for lineno, line in enumerate(raw.decode("utf-8", "replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                norm = normalise_name(obj["name_norm"])
                issue = str(obj.get("issue") or "")[:_MAX_REASON]
                suggested = obj.get("suggested_category")
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                ignored += 1; _log_rejection(f.name, lineno, "bad flag line"); continue
            row = conn.execute(
                "SELECT source, reason FROM lender_classifications WHERE name_norm = ?", (norm,)
            ).fetchone()
            if not row:
                ignored += 1; _log_rejection(f.name, lineno, "flag for unclassified name"); continue
            if row["source"] != "agent":
                # Human/seed rows are never changed by agents; leave a note for a person.
                ignored += 1
                _log_rejection(f.name, lineno, f"reviewer disputes {row['source']} rule: {issue}")
                continue
            note = f"reviewer: {issue}" + (f" (suggests {suggested})" if suggested else "")
            conn.execute(
                "UPDATE lender_classifications SET review_status = 'needs_review', reason = ?, "
                "updated_at = ? WHERE name_norm = ?",
                (f"{row['reason'] or ''} | {note}"[-_MAX_REASON * 2:], now_iso(), norm),
            )
            flagged += 1
        _record_applied(conn, key, digest, flagged, ignored)
        totals["files"] += 1; totals["flagged"] += flagged; totals["ignored"] += ignored
    conn.commit()
    return totals

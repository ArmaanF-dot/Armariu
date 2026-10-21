"""Data and actions behind the review app. Plain functions so they can be tested without HTTP."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .. import config, consensus, dbsync, export, owners, sectors, signals as sig
from ..classify import refresh_rule_matches
from ..db import connect, get_meta
from ..normalise import family_key, normalise_name
from ..review import add_human_rule
from ..rules import load_rules

# Categories a person can choose, in the order shown (keys 1-8). "unknown" is left out on purpose:
# "skip for now" is the human version of "can't tell".
CHOICES = [
    ("direct_lender", "Direct lender", "Private credit fund, debt fund, unitranche or mezzanine lender"),
    ("bank", "Bank", "High-street, challenger, international or investment bank, building society"),
    ("security_agent", "Security agent", "Trustee or agent holding security for other lenders"),
    ("abl_specialist", "Asset-based lender", "Invoice finance, factoring, receivables or inventory lending"),
    ("asset_finance", "Asset finance", "Leasing, hire purchase, vehicle or equipment finance"),
    ("property_lender", "Property lender", "Mortgage, buy-to-let, bridging or development lender to property companies"),
    ("public_sector", "Public sector", "Government body or government-backed lender"),
    ("individual", "Individual", "A private person or family vehicle"),
    ("other", "Other", "Landlord, supplier, group company, pension trustee, crowdfunding platform"),
]
CATEGORY_LABELS = {k: label for k, label, _ in CHOICES} | {"unknown": "Can't tell"}
SOURCE_LABELS = {"human": "You", "seed": "Starter rule", "agent": "Classifier agent"}
DRAFT_STATUSES = ("new", "ready", "published")
NOTES_FILE = "data/lender_notes.json"   # your research notes on lenders, saved to GitHub like decisions
_EXAMPLES = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel(p: Path) -> str:
    return p.relative_to(config.ROOT).as_posix()


def _safe_path(rel: str, allowed_dir: str, suffix: str = ".md") -> Path:
    """Resolve a relative path and refuse anything outside allowed_dir (no ../ tricks)."""
    base = (config.ROOT / allowed_dir).resolve()
    p = (config.ROOT / rel).resolve()
    if base not in p.parents or p.suffix != suffix:
        raise ValueError(f"Not allowed: {rel}")
    return p


# --------------------------------------------------------------------------- git + sync

class Git:
    """Runs git in the background so the app never waits on the network. One job at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.status = {"state": "idle", "message": "", "at": None}
        self.has_remote = False
        self.in_repo = False
        self.check_repo()

    def run(self, *args, check=True) -> str:
        res = subprocess.run(["git", *args], cwd=config.ROOT, capture_output=True, text=True)
        if check and res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout).strip().splitlines()[-1] if (res.stderr or res.stdout).strip() else f"git {args[0]} failed")
        return res.stdout.strip()

    def check_repo(self):
        try:
            self.in_repo = self.run("rev-parse", "--is-inside-work-tree") == "true"
            self.has_remote = bool(self.run("remote")) if self.in_repo else False
        except (RuntimeError, FileNotFoundError):
            self.in_repo = self.has_remote = False

    def commit_and_push(self, paths: list[str], message: str) -> None:
        if not self.in_repo:
            return
        threading.Thread(target=self._commit_and_push, args=(paths, message), daemon=True).start()

    def _commit_and_push(self, paths, message):
        with self.lock:
            try:
                self.status = {"state": "saving", "message": "Saving to GitHub", "at": _now()}
                self.run("add", "--", *paths)
                if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=config.ROOT).returncode == 0:
                    self.status = {"state": "ok", "message": "Saved", "at": _now()}
                    return
                self.run("commit", "--quiet", "-m", message)
                if self.has_remote:
                    if subprocess.run(["git", "push", "--quiet"], cwd=config.ROOT,
                                      capture_output=True).returncode != 0:
                        self.run("pull", "--rebase", "--autostash", "--quiet")
                        self.run("push", "--quiet")
                self.status = {"state": "ok", "message": "Saved to GitHub" if self.has_remote else "Saved",
                               "at": _now()}
            except RuntimeError as exc:
                self.status = {"state": "error", "at": _now(),
                               "message": f"Saved on this PC, but not uploaded: {exc}"}


class Sync:
    """Keeps the local copy current: pull the repo, download the cloud database, re-apply your rules."""

    INTERVAL = 180  # seconds

    def __init__(self, git: Git, db_lock: threading.RLock):
        self.git, self.db_lock = git, db_lock
        self.status = {"state": "never", "message": "Not synced yet", "at": None, "mode": "local"}
        self._wake = threading.Event()

    def run_once(self) -> dict:
        self.status = dict(self.status, state="syncing", message="Checking for updates")
        try:
            if self.git.has_remote:
                with self.git.lock:
                    self.git.run("pull", "--rebase", "--autostash", "--quiet")
                with self.db_lock:
                    got = dbsync.pull() == 0
                mode = "cloud" if got else "local"
            else:
                mode = "local"
            with self.db_lock:
                conn = connect()
                try:
                    refresh_rule_matches(conn, load_rules())
                finally:
                    conn.close()
            self.status = {"state": "ok", "message": "Up to date", "at": _now(), "mode": mode}
        except Exception as exc:  # shown in the app; the next sync tries again
            self.status = dict(self.status, state="error", at=_now(), message=f"Couldn't sync: {exc}")
        return self.status

    def loop(self):
        while True:
            self.run_once()
            self._wake.wait(self.INTERVAL)
            self._wake.clear()

    def wake(self):
        self._wake.set()


# --------------------------------------------------------------------------- reading

def version() -> str:
    """Cheap fingerprint of everything the app shows. The page polls it to update live."""
    parts = []
    for p in [config.DB_PATH, config.LENDERS_CSV, config.ROOT / "drafts" / "status.json", config.ROOT / NOTES_FILE]:
        try:
            st = p.stat(); parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        except FileNotFoundError:
            parts.append(f"{p.name}:-")
    for d in ("inbox/maintainer", "drafts", "reviews", "output/weekly"):
        folder = config.ROOT / d
        if folder.exists():
            for p in sorted(folder.glob("*.md")):
                parts.append(f"{p.name}:{p.stat().st_mtime_ns}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _examples(conn, name_norm: str, limit: int = _EXAMPLES) -> list[dict]:
    rows = conn.execute(
        "SELECT c.company_number, c.charge_id, c.delivered_on, c.created_on, c.status, "
        "c.classification_text, c.secured_text, c.floating_covers_all, co.name AS company_name, "
        "co.sic_codes FROM charge_persons p JOIN charges c "
        "ON c.company_number = p.company_number AND c.charge_id = p.charge_id "
        "LEFT JOIN companies co ON co.company_number = c.company_number "
        "WHERE p.name_norm = ? AND c.deleted = 0 ORDER BY c.delivered_on DESC LIMIT ?",
        (name_norm, limit)).fetchall()
    out = []
    for r in rows:
        others = [x[0] for x in conn.execute(
            "SELECT name_raw FROM charge_persons WHERE company_number=? AND charge_id=? AND name_norm != ?",
            (r["company_number"], r["charge_id"], name_norm))]
        out.append({
            "company_number": r["company_number"], "company_name": r["company_name"],
            "sic_codes": json.loads(r["sic_codes"]) if r["sic_codes"] else [],
            "delivered_on": r["delivered_on"], "created_on": r["created_on"], "status": r["status"],
            "charge_type": r["classification_text"], "secured": r["secured_text"],
            "all_assets": r["floating_covers_all"], "other_chargees": others,
            "filing_url": config.FILING_URL.format(company=r["company_number"], charge_id=r["charge_id"]),
        })
    return out


def _history(conn, name_norm: str) -> list[dict]:
    """Every charge in this lender's favour, oldest first, for the lending-history strip."""
    return [{"delivered_on": r[0], "created_on": r[1], "status": r[2]} for r in conn.execute(
        "SELECT c.delivered_on, c.created_on, c.status FROM charge_persons p JOIN charges c "
        "ON c.company_number = p.company_number AND c.charge_id = p.charge_id "
        "WHERE p.name_norm = ? AND c.deleted = 0 ORDER BY c.delivered_on", (name_norm,))]


def _lender_rows(conn, where: str, params=()) -> list[dict]:
    rows = conn.execute(
        "SELECT p.name_norm, MIN(p.name_raw) AS display, COUNT(*) AS n, MAX(c.delivered_on) AS latest, "
        "MIN(c.delivered_on) AS first, COUNT(DISTINCT p.company_number) AS borrowers, "
        "SUM(c.status = 'outstanding') AS live, "
        "lc.category, lc.parent_firm, lc.confidence, lc.source, lc.review_status, lc.reason "
        "FROM charge_persons p JOIN charges c ON c.company_number = p.company_number "
        "AND c.charge_id = p.charge_id AND c.deleted = 0 "
        "LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm "
        f"WHERE {where} GROUP BY p.name_norm ORDER BY n DESC, latest DESC", params).fetchall()
    notes = _notes()
    lent_to: dict[str, dict] = {}
    for r2 in conn.execute(
            "SELECT DISTINCT p.name_norm, p.company_number, co.sic_codes FROM charge_persons p "
            "LEFT JOIN companies co ON co.company_number = p.company_number"):
        first = sectors.sectors_of(json.loads(r2["sic_codes"]) if r2["sic_codes"] else [])[0]
        d = lent_to.setdefault(r2["name_norm"], {})
        d[first] = d.get(first, 0) + 1
    top = lambda n: [{"key": k, "label": sectors.label(k), "count": c}  # noqa: E731
                     for k, c in sorted(lent_to.get(n, {}).items(), key=lambda kv: -kv[1])]
    return [{
        "sectors": top(r["name_norm"]),
        "name": r["name_norm"], "display": r["display"], "charges": r["n"], "latest": r["latest"],
        "first": r["first"], "borrowers": r["borrowers"], "outstanding": r["live"] or 0,
        "note": (notes.get(r["name_norm"]) or {}).get("note", ""),
        "category": r["category"], "category_label": CATEGORY_LABELS.get(r["category"] or "", ""),
        "parent": r["parent_firm"], "confidence": r["confidence"], "source": r["source"],
        "source_label": SOURCE_LABELS.get(r["source"] or "", "Not classified yet"),
        "status": r["review_status"] or "unclassified", "reason": r["reason"],
    } for r in rows]


def _notes() -> dict:
    try:
        return json.loads((config.ROOT / NOTES_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _md_files(folder: str, pattern: str = "*.md") -> list[dict]:
    d = config.ROOT / folder
    if not d.exists():
        return []
    files = sorted((p for p in d.glob(pattern) if p.is_file()), key=lambda p: p.name, reverse=True)
    return [{"path": _rel(p), "name": p.name,
             "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
             "content": p.read_text(encoding="utf-8", errors="replace")} for p in files]


def _draft_status() -> dict:
    p = config.ROOT / "drafts" / "status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _property_only(conn, names: list[str]) -> bool:
    """True when every borrower of these lenders whose sector we know is a property company."""
    marks = ",".join("?" * len(names))
    secs = [sectors.sectors_of(json.loads(r[0]) if r[0] else [])[0] for r in conn.execute(
        f"SELECT DISTINCT co.sic_codes FROM charge_persons p JOIN companies co ON co.company_number = p.company_number "
        f"WHERE p.name_norm IN ({marks})", names)]
    known = [x for x in secs if x != "unknown"]
    return bool(known) and all(x == "real_estate" for x in known)


def _families(conn, rows: list[dict]) -> list[dict]:
    """Merge name variants, and lenders controlled by the same company, into one item: the lead
    (most charges) plus its members."""
    owner_of = owners.groups(conn)
    parent: dict[str, str] = {}

    def find(k):
        while parent.setdefault(k, k) != k:
            k = parent[k]
        return k
    for r in rows:  # union by name stem, then by controlling company
        find(family_key(r["name"]))
    by_group: dict[str, str] = {}
    for r in rows:
        g = owner_of.get(r["name"])
        gid = g and (g.get("number") or g.get("name"))
        if gid:
            if gid in by_group:
                parent[find(family_key(r["name"]))] = find(by_group[gid])
            else:
                by_group[gid] = family_key(r["name"])
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(find(family_key(r["name"])), []).append(r)
    out = []
    for key, members in groups.items():
        members.sort(key=lambda r: (-r["charges"], r["name"]))
        lead = dict(members[0])
        names = [m["name"] for m in members]
        lead["members"] = [{"name": m["name"], "display": m["display"], "charges": m["charges"],
                            "category": m["category"], "confidence": m["confidence"]} for m in members]
        stems = {family_key(m["name"]) for m in members}
        # A name stem can also catch future names; an owner group can't (names vary too much).
        lead["family"] = next(iter(stems)) if len(members) > 1 and len(stems) == 1 else None
        g = owner_of.get(lead["name"])
        lead["group_family"] = g["name"] if len(members) > 1 and len(stems) > 1 and g else None
        lead["family_charges"] = sum(m["charges"] for m in members)
        lead["property_only"] = _property_only(conn, names)
        cats = {m["category"] or "unknown" for m in members}
        lead["priority"] = "low" if lead["property_only"] or not cats & {"direct_lender", "security_agent", "unknown"} else "high"
        ex = []
        for n in names:
            ex += _examples(conn, n)
        lead["examples"] = sorted(ex, key=lambda e: e["delivered_on"] or "", reverse=True)[:_EXAMPLES]
        hist = []
        for n in names:
            hist += _history(conn, n)
        lead["history"] = sorted(hist, key=lambda h: h["delivered_on"] or "")
        lead["profile"] = owners.profile(conn, lead["name"])
        ag = consensus.opinions_for(conn, lead["name"])
        lead["opinions"], lead["consensus"] = ag["opinions"], ag["state"]
        out.append(lead)
    out.sort(key=lambda r: (-r["family_charges"], r["name"]))
    return out


def decisions(conn) -> dict:
    """What needs a person, ordered so the decisions that change the headline come first.

    - review: the agent couldn't decide, and the answer could matter (it might be a direct lender
      or security agent, or the agent can't tell) and the lender doesn't only lend on property.
    - low: everything else that's unsure: property-only lenders, low-stakes types. Safe to clear in bulk.
    - confirm: confident agent answers that put a name in the headline tiers. Worth a glance.
    - waiting: names the classifier agent hasn't seen yet.
    - auto: low-stakes answers the agent was sure enough of to use; there to double-check, optional.
    Name variants ('X CAPITAL 6', 'X CAPITAL 7') are one item: one decision covers them all.
    """
    unsure = _families(conn, _lender_rows(conn, "lc.review_status = 'needs_review'"))
    review = [r for r in unsure if r["priority"] == "high"]
    low = [r for r in unsure if r["priority"] == "low"]
    confirm = _families(conn, _lender_rows(
        conn, "lc.source = 'agent' AND lc.review_status = 'auto_ok' AND lc.category IN ('direct_lender', 'security_agent')"))
    waiting = _families(conn, _lender_rows(conn, "lc.name_norm IS NULL"))
    # Answers the agent was sure enough of to use without asking: listed so a person can
    # double-check them when they like. Not counted as waiting.
    auto = _families(conn, _lender_rows(
        conn, "lc.source = 'agent' AND lc.review_status = 'auto_ok' AND lc.category NOT IN ('direct_lender', 'security_agent')"))
    auto.sort(key=lambda r: (r["confidence"] or 0, -r["family_charges"]))
    status = _draft_status()
    drafts = _md_files("drafts", "*-draft.md")
    for d in drafts:
        d["status"] = status.get(d["name"], {}).get("status", "new")
    return {
        "lenders": {"review": review, "low": low, "confirm": confirm, "waiting": waiting, "auto": auto},
        "names": {"review": sum(len(r["members"]) for r in review), "low": sum(len(r["members"]) for r in low)},
        "messages": _md_files("inbox/maintainer"),
        "drafts": [d for d in drafts if d["status"] != "published"],
        "choices": [{"key": k, "label": label, "help": help_} for k, label, help_ in CHOICES],
    }


def documents() -> dict:
    status = _draft_status()
    drafts = _md_files("drafts", "*-draft.md")
    for d in drafts:
        d["status"] = status.get(d["name"], {}).get("status", "new")
    return {
        "drafts": drafts,
        "reviews": _md_files("reviews", "*-review.md"),
        "digests": _md_files("output/weekly"),
        "messages_done": _md_files("inbox/maintainer/done"),
    }


def charges(conn, q: str = "", tier: str = "", category: str = "", check: str = "",
            date_from: str = "", date_to: str = "", limit: int = 100, offset: int = 0,
            status: str = "", sector: str = "", hide_property: bool = False) -> dict:
    where, params = ["c.deleted = 0"], []
    if date_from:
        where.append("c.delivered_on >= ?"); params.append(date_from)
    if date_to:
        where.append("c.delivered_on <= ?"); params.append(date_to)
    if status == "repaid":
        where.append("c.status != 'outstanding'")
    elif status == "outstanding":
        where.append("c.status = 'outstanding'")
    if q:
        like = f"%{q.strip()}%"
        where.append("(co.name LIKE ? OR c.company_number LIKE ? OR EXISTS (SELECT 1 FROM charge_persons pp "
                     "WHERE pp.company_number = c.company_number AND pp.charge_id = c.charge_id "
                     "AND (pp.name_raw LIKE ? OR pp.name_norm LIKE ?)))")
        params += [like, like, like, like.upper()]
    rows = conn.execute(
        "SELECT c.company_number, c.charge_id, c.delivered_on, c.created_on, c.status, c.satisfied_on, "
        "c.classification_text, c.floating_covers_all, co.name AS company_name, co.sic_codes "
        "FROM charges c LEFT JOIN companies co ON co.company_number = c.company_number "
        f"WHERE {' AND '.join(where)} ORDER BY c.delivered_on DESC, c.company_number LIMIT 5000",
        params).fetchall()
    out, tier_counts, sector_counts = [], {}, {}
    for c in rows:
        secs = sectors.sectors_of(json.loads(c["sic_codes"]) if c["sic_codes"] else [])
        if hide_property and secs[0] == "real_estate":
            continue
        persons = conn.execute(
            "SELECT p.name_raw, p.name_norm, lc.category, lc.review_status FROM charge_persons p "
            "LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm "
            "WHERE p.company_number = ? AND p.charge_id = ? ORDER BY p.name_raw",
            (c["company_number"], c["charge_id"])).fetchall()
        cats = [p["category"] or "unknown" for p in persons] or ["unknown"]
        statuses = [p["review_status"] or "unclassified" for p in persons] or ["unclassified"]
        worst = min(statuses, key=lambda s: export._STATUS_RANK.get(s, 0))
        primary = export._primary(cats)
        t = export._tier(primary, worst, cats)
        needs_check = worst != "ok"
        if category and primary != category:
            continue
        if check == "yes" and not needs_check or check == "no" and needs_check:
            continue
        for k in secs:
            sector_counts[k] = sector_counts.get(k, 0) + 1
        if sector and sector not in secs:
            continue
        tier_counts[t] = tier_counts.get(t, 0) + 1
        if tier and t != tier:
            continue
        out.append({
            "tier": t, "delivered_on": c["delivered_on"], "created_on": c["created_on"],
            "company_number": c["company_number"], "company_name": c["company_name"],
            "status": c["status"], "satisfied_on": c["satisfied_on"],
            "charge_type": c["classification_text"], "all_assets": c["floating_covers_all"],
            "primary": primary, "primary_label": CATEGORY_LABELS.get(primary, primary),
            "needs_check": needs_check,
            "sector": secs[0], "sector_label": sectors.label(secs[0]), "sectors": secs,
            "chargees": [{"name": p["name_norm"], "display": p["name_raw"],
                          "category": p["category"], "status": p["review_status"] or "unclassified"}
                         for p in persons],
            "filing_url": config.FILING_URL.format(company=c["company_number"], charge_id=c["charge_id"]),
        })
    return {"total": len(out), "rows": out[offset:offset + limit], "offset": offset, "limit": limit,
            "tier_counts": {t: tier_counts.get(t, 0) for t in "ABCDE"},
            "sector_counts": _sector_list(sector_counts)}


def _sector_list(counts: dict) -> list[dict]:
    return [{"key": k, "label": label, "count": counts.get(k, 0)} for k, label in sectors.SECTORS.items()]


def lenders(conn, q: str = "", category: str = "", status: str = "") -> dict:
    where, params = ["1=1"], []
    if q:
        where.append("(p.name_raw LIKE ? OR p.name_norm LIKE ? OR lc.parent_firm LIKE ?)")
        params += [f"%{q}%", f"%{q.upper()}%", f"%{q}%"]
    if category:
        where.append("lc.category = ?"); params.append(category)
    if status == "unclassified":
        where.append("lc.name_norm IS NULL")
    elif status:
        where.append("lc.review_status = ?"); params.append(status)
    rows = _lender_rows(conn, " AND ".join(where), params)
    return {"total": len(rows), "rows": rows[:500]}


def lender(conn, name: str) -> dict:
    norm = normalise_name(name)
    rows = _lender_rows(conn, "p.name_norm = ?", (norm,))
    if not rows:
        raise KeyError(f"No lender called {name}")
    item = rows[0]
    item["examples"] = _examples(conn, norm, limit=200)
    item["history"] = _history(conn, norm)
    item["profile"] = owners.profile(conn, norm)
    ag = consensus.opinions_for(conn, norm)
    item["opinions"], item["consensus"] = ag["opinions"], ag["state"]
    return item


def directory(conn) -> dict:
    """Every lender name on the register grouped by type, plus rules that haven't matched anything yet."""
    rows = _lender_rows(conn, "1=1")
    owner_of = owners.groups(conn)
    size: dict[str, int] = {}
    for r in rows:
        g = owner_of.get(r["name"])
        r["group"] = g["name"] if g else None
        if g:
            size[g["name"]] = size.get(g["name"], 0) + 1
    for r in rows:
        r["group_size"] = size.get(r["group"], 0) if r["group"] else 0
    counts: dict[str, int] = {}
    for r in rows:
        k = r["category"] or "unclassified"
        counts[k] = counts.get(k, 0) + 1
    seen = {r["name"] for r in rows}
    try:
        rules = load_rules()
    except (OSError, ValueError):
        rules = []
    watch = [{"pattern": r.pattern, "match": r.match_type, "category": r.category,
              "category_label": CATEGORY_LABELS.get(r.category, r.category), "parent": r.parent_firm,
              "source": SOURCE_LABELS.get(r.source, r.source), "note": r.note}
             for r in rules if r.match_type == "exact" and r.pattern not in seen
             or r.match_type == "contains" and not any(f" {r.pattern} " in f" {n} " for n in seen)]
    order = [k for k, _, _ in CHOICES] + ["unknown", "unclassified"]
    return {
        "rows": rows, "total": len(rows),
        "categories": [{"key": k, "label": CATEGORY_LABELS.get(k, "Not classified yet"), "count": counts.get(k, 0)}
                       for k in order if counts.get(k)],
        "watch": watch,
        "sectors": [{"key": k, "label": label} for k, label in sectors.SECTORS.items()],
    }


def signals(conn, kind: str = "") -> dict:
    found = sig.all_signals(conn)
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "counts": sig.counts(found),
        "types": [{"key": k, "label": label, "help": help_, "theme": sig.THEME_OF[k]}
                  for k, (label, help_) in sig.TYPES.items()],
        "themes": [{"key": k, "label": label, "help": help_} for k, (label, help_) in sig.THEMES.items()],
        "sectors": [{"key": k, "label": label} for k, label in sectors.SECTORS.items()],
        "lender_types": [{"key": k, "label": label} for k, label, _ in CHOICES] + [{"key": "unknown", "label": "Can't tell"}],
        "rows": [s for s in found if not kind or s["type"] == kind],
        "total": len(found),
        "companies": one("SELECT COUNT(DISTINCT company_number) FROM charges WHERE deleted = 0"),
        "histories": one("SELECT COUNT(*) FROM borrower_history_fetched f WHERE EXISTS "
                         "(SELECT 1 FROM charges c WHERE c.company_number = f.company_number)"),
    }


def company(conn, number: str) -> dict:
    number = (number or "").strip().upper()
    if not number or not conn.execute("SELECT 1 FROM charges WHERE company_number = ? UNION "
                                      "SELECT 1 FROM borrower_history WHERE company_number = ?",
                                      (number, number)).fetchone():
        raise KeyError(f"No borrower {number}")
    namer = sig.Namer(conn)
    charges, fetched = sig.company_charges(conn, number, namer)
    prof = conn.execute("SELECT * FROM companies WHERE company_number = ?", (number,)).fetchone()
    if not prof and os.environ.get("CH_REST_KEY", "").strip():
        # Not fetched by the cloud yet: look it up now with this PC's key (one request).
        try:
            from ..ingest import RestClient
            p = RestClient(os.environ["CH_REST_KEY"].strip(), budget_seconds=20).company(number) or {}
            conn.execute("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)",
                         (number, p.get("company_name"), p.get("company_status") or "not-found", p.get("type"),
                          p.get("date_of_creation"), json.dumps(p.get("sic_codes") or []), _now()))
            conn.commit()
            prof = conn.execute("SELECT * FROM companies WHERE company_number = ?", (number,)).fetchone()
        except Exception:  # offline or key problem: the cloud run fills it in
            pass
    name = prof["name"] if prof else None
    for c in charges:
        c["primary_label"] = CATEGORY_LABELS.get(c["primary"], c["primary"])
        for x in c["lenders"]:
            x["category_label"] = CATEGORY_LABELS.get(x["category"], x["category"])
            x["known"] = bool(conn.execute("SELECT 1 FROM charge_persons WHERE name_norm = ? LIMIT 1",
                                           (x["name"],)).fetchone())
    return {
        "number": number, "name": name,
        "status": prof["company_status"] if prof else None,
        "created": prof["date_of_creation"] if prof else None,
        "sic_codes": json.loads(prof["sic_codes"]) if prof and prof["sic_codes"] else [],
        "sector_label": sectors.label(sectors.sectors_of(json.loads(prof["sic_codes"]) if prof and prof["sic_codes"] else [])[0]),
        "charges": list(reversed(charges)), "history_fetched": fetched,
        "signals": sig.for_company(number, name, charges, fetched, date.today(),
                                   json.loads(prof["sic_codes"]) if prof and prof["sic_codes"] else []),
        "outstanding": sum(1 for c in charges if c["status"] == "outstanding"),
    }


_ACTIVITY = [
    (re.compile(r"^pipeline:"), "cloud", "Cloud run: new filings, queue and digests"),
    (re.compile(r"^agent\(classifier\)"), "agent", "Classifier agent answered new lender names"),
    (re.compile(r"^agent\(verifier\)"), "agent", "Second-opinion agent re-checked unsure names"),
    (re.compile(r"^agent\(referee\)"), "agent", "Referee agent settled disagreements"),
    (re.compile(r"^agent\(reviewer\)"), "agent", "Reviewer agent checked a week"),
    (re.compile(r"^agent\(newsletter\)"), "agent", "Newsletter agent wrote or updated a draft"),
    (re.compile(r"^review: "), "you", None),
]


def activity(git: Git, limit: int = 8) -> list[dict]:
    """The radar's recent history, read from the git log: cloud runs, agent runs, your decisions."""
    if not git.in_repo:
        return []
    try:
        out = git.run("log", f"-{limit * 3}", "--format=%cI%x09%s")
    except RuntimeError:
        return []
    items = []
    for line in out.splitlines():
        at, _, subject = line.partition("\t")
        for rx, kind, text in _ACTIVITY:
            if rx.search(subject):
                items.append({"at": at, "kind": kind,
                              "text": text or "You: " + subject[len("review: "):]})
                break
        if len(items) >= limit:
            break
    return items


def overview(conn, git: Git) -> dict:
    """Everything the Overview page draws, computed from the same rows the Charges page lists."""
    from datetime import timedelta
    rows = charges(conn, limit=5000)["rows"]
    found = sig.all_signals(conn)
    today = date.today()
    day0 = today - timedelta(days=29)
    days = {(day0 + timedelta(days=i)).isoformat(): dict.fromkeys("ABCDE", 0) for i in range(30)}
    week0 = today - timedelta(days=today.weekday() + 7 * 11)
    weeks = {(week0 + timedelta(days=7 * i)).isoformat(): dict.fromkeys("ABCDE", 0) for i in range(12)}
    mix, tiers = {}, dict.fromkeys("ABCDE", 0)
    for r in rows:
        tiers[r["tier"]] += 1
        mix[r["primary"]] = mix.get(r["primary"], 0) + 1
        d = r["delivered_on"]
        if not d:
            continue
        if d in days:
            days[d][r["tier"]] += 1
        dd = date.fromisoformat(d[:10])
        wk = (dd - timedelta(days=dd.weekday())).isoformat()
        if wk in weeks:
            weeks[wk][r["tier"]] += 1
    since = (today - timedelta(days=29)).isoformat()
    repaid_30 = sum(1 for r in rows if r["status"] != "outstanding" and (r["satisfied_on"] or "") >= since)
    (ls, le), (ts, te) = export.week_bounds(today)
    this_week = [r for r in rows if r["delivered_on"] and ts.isoformat() <= r["delivered_on"] <= te.isoformat()]
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    waiting = {
        "review": one("SELECT COUNT(*) FROM lender_classifications WHERE review_status = 'needs_review'"),
        "confirm": one("SELECT COUNT(*) FROM lender_classifications WHERE source = 'agent' AND review_status = 'auto_ok'"),
        "unclassified": one("SELECT COUNT(DISTINCT p.name_norm) FROM charge_persons p LEFT JOIN lender_classifications lc "
                            "ON lc.name_norm = p.name_norm WHERE lc.name_norm IS NULL"),
        "messages": len(list((config.ROOT / "inbox" / "maintainer").glob("*.md"))),
        "drafts": sum(1 for d in _md_files("drafts", "*-draft.md")
                      if _draft_status().get(d["name"], {}).get("status", "new") != "published"),
    }
    covered, note = export.coverage(conn, ts, te)
    began = (get_meta(conn, "coverage_start") or (one("SELECT MIN(first_seen_at) FROM charges") or "")[:10] or None)
    last_run = None
    if git.in_repo:
        try:
            last_run = git.run("log", "-1", "--format=%cI", "--grep=^pipeline:") or None
        except RuntimeError:
            pass
    return {
        "today": today.isoformat(), "coverage_start": began,
        "daily": [{"date": k, **v} for k, v in days.items()],
        "weekly": [{"date": k, **v} for k, v in weeks.items()],
        "mix": [{"key": k, "label": CATEGORY_LABELS.get(k, k), "count": n}
                for k, n in sorted(mix.items(), key=lambda kv: -kv[1])],
        "tiers": tiers, "total": len(rows), "repaid_30": repaid_30,
        "repaid_total": sum(1 for r in rows if r["status"] != "outstanding"),
        "this_week": {"start": ts.isoformat(), "end": te.isoformat(), "total": len(this_week),
                      "direct": sum(1 for r in this_week if r["tier"] == "A"), "covered": covered, "note": note},
        "lenders": one("SELECT COUNT(DISTINCT name_norm) FROM charge_persons"),
        "latest": sorted((r for r in rows if r["delivered_on"]), key=lambda r: r["delivered_on"], reverse=True)[:6],
        "waiting": waiting, "activity": activity(git, 6), "last_pipeline_commit": last_run,
        "signals": {"counts": sig.counts(found), "top": found[:5], "total": len(found),
                    "labels": {k: label for k, (label, _) in sig.TYPES.items()}},
    }


def summary(conn, git: Git, sync: Sync) -> dict:
    today = date.today()
    (ls, le), (ts, te) = export.week_bounds(today)
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    this_week = export.collect(conn, ts, te)
    last_week = export.collect(conn, ls, le)
    tiers = lambda rows: {t: sum(1 for r in rows if r["tier"] == t) for t, _ in export.TIERS}  # noqa: E731
    covered, note = export.coverage(conn, ts, te)
    last_run = None
    if git.in_repo:
        try:
            last_run = git.run("log", "-1", "--format=%cI", "--grep=^pipeline:") or None
        except RuntimeError:
            pass
    return {
        "charges": one("SELECT COUNT(*) FROM charges WHERE deleted = 0"),
        "lenders": one("SELECT COUNT(DISTINCT name_norm) FROM charge_persons"),
        "latest_delivered": one("SELECT MAX(delivered_on) FROM charges"),
        "latest_seen": one("SELECT MAX(last_event_at) FROM charges"),
        "coverage_start": (get_meta(conn, "coverage_start")
                           or (one("SELECT MIN(first_seen_at) FROM charges") or "")[:10] or None),
        "stream_timepoint": get_meta(conn, "stream_timepoint"),
        "stream_gap_at": get_meta(conn, "stream_gap_at"),
        "this_week": {"start": ts.isoformat(), "end": te.isoformat(), "tiers": tiers(this_week),
                      "total": len(this_week), "covered": covered, "coverage_note": note},
        "last_week": {"start": ls.isoformat(), "end": le.isoformat(), "tiers": tiers(last_week),
                      "total": len(last_week)},
        "last_pipeline_commit": last_run,
        "activity": activity(git),
        "sync": sync.status, "git": git.status,
        "repo": {"in_repo": git.in_repo, "has_remote": git.has_remote},
    }


# --------------------------------------------------------------------------- actions

def decide(conn, git: Git, name: str, category: str, parent: str = "", note: str = "",
           also: list[str] | None = None, future: str = "") -> dict:
    """Classify one name, plus any of its variants (`also`), and optionally every future name
    containing `future` (a whole-word phrase of the name, e.g. 'FORTYS CAPITAL')."""
    if category not in CATEGORY_LABELS or category == "unknown":
        raise ValueError(f"Unknown category {category!r}")
    names = [name] + [n for n in (also or []) if n != name][:200]
    for n in names:
        if not conn.execute("SELECT 1 FROM charge_persons WHERE name_norm = ?", (normalise_name(n),)).fetchone():
            raise KeyError(f"No lender called {n}")
    pattern = normalise_name(future or "")
    if pattern:
        if len(pattern) < 6 or f" {pattern} " not in f" {normalise_name(name)} ":
            raise ValueError("The phrase for future names must be whole words from the lender's name, 6 letters or more")
    parent = (parent or "").strip()[:100]
    done = [add_human_rule(n, category, parent, (note or "set in review app")[:200]) for n in names]
    if pattern:
        add_human_rule(pattern, category, parent, "family rule set in review app", match_type="contains")
    refresh_rule_matches(conn, load_rules())
    label = done[0] if len(done) == 1 else f"{done[0]} and {len(done) - 1} variant{'s' if len(done) > 2 else ''}"
    git.commit_and_push(["data/lenders.csv"], f"review: {label} is {CATEGORY_LABELS[category].lower()}"
                        + (f" (and future names containing {pattern})" if pattern else ""))
    return {"names": done, "category": category, "future": pattern or None}


def file_as(conn, git: Git, names: list[str], category: str) -> dict:
    """Bulk: give many names the same type, e.g. 'these are all property lenders'."""
    if category not in CATEGORY_LABELS or category == "unknown":
        raise ValueError(f"Unknown category {category!r}")
    done = []
    for n in names[:500]:
        if conn.execute("SELECT 1 FROM charge_persons WHERE name_norm = ?", (normalise_name(n),)).fetchone():
            done.append(add_human_rule(n, category, "", "filed in bulk in review app"))
    if done:
        refresh_rule_matches(conn, load_rules())
        git.commit_and_push(["data/lenders.csv"], f"review: {len(done)} names are {CATEGORY_LABELS[category].lower()}")
    return {"filed": done, "category": category}


def approve(conn, git: Git, names: list[str]) -> dict:
    done = []
    for name in names:
        row = conn.execute("SELECT * FROM lender_classifications WHERE name_norm = ?",
                           (normalise_name(name),)).fetchone()
        if not row or row["category"] == "unknown":
            continue
        done.append(add_human_rule(name, row["category"], row["parent_firm"] or "", "approved in review app"))
    if done:
        refresh_rule_matches(conn, load_rules())
        label = done[0] if len(done) == 1 else f"{len(done)} lenders"
        git.commit_and_push(["data/lenders.csv"], f"review: approve {label}")
    return {"approved": done}


def save_note(conn, git: Git, name: str, note: str) -> dict:
    norm = normalise_name(name)
    if not conn.execute("SELECT 1 FROM charge_persons WHERE name_norm = ?", (norm,)).fetchone():
        raise KeyError(f"No lender called {name}")
    note = (note or "").replace("\r\n", "\n").strip()[:4000]
    data = _notes()
    if note:
        data[norm] = {"note": note, "at": _now()}
    else:
        data.pop(norm, None)
    path = config.ROOT / NOTES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    git.commit_and_push([NOTES_FILE], f"review: note on {norm}")
    return {"name": norm, "note": note}


def message_done(git: Git, rel: str) -> dict:
    p = _safe_path(rel, "inbox/maintainer")
    if p.parent != (config.ROOT / "inbox" / "maintainer").resolve():
        raise ValueError("Only messages in inbox/maintainer can be archived")
    dest = p.parent / "done" / p.name
    dest.parent.mkdir(exist_ok=True)
    shutil.move(str(p), str(dest))
    git.commit_and_push(["inbox/maintainer"], f"review: archive {p.name}")
    return {"archived": _rel(dest)}


def save_draft(git: Git, rel: str, content: str) -> dict:
    p = _safe_path(rel, "drafts")
    p.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
    git.commit_and_push(["drafts"], f"review: edit {p.name}")
    return {"saved": _rel(p)}


def set_draft_status(git: Git, rel: str, status: str) -> dict:
    if status not in DRAFT_STATUSES:
        raise ValueError(f"Unknown status {status!r}")
    p = _safe_path(rel, "drafts")
    store = config.ROOT / "drafts" / "status.json"
    data = _draft_status()
    data[p.name] = {"status": status, "at": _now()}
    store.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    git.commit_and_push(["drafts/status.json"], f"review: {p.name} marked {status}")
    return {"draft": _rel(p), "status": status}


def wait_for_git(git: Git, timeout: float = 10.0) -> None:
    """Used by tests: wait until a background commit finishes."""
    end = time.monotonic() + timeout
    time.sleep(0.05)
    while time.monotonic() < end:
        if git.lock.acquire(timeout=0.1):
            git.lock.release()
            if git.status["state"] != "saving":
                return
        time.sleep(0.05)


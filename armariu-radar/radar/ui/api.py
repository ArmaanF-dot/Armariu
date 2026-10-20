"""Data and actions behind the review app. Plain functions so they can be tested without HTTP."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .. import config, dbsync, export
from ..classify import refresh_rule_matches
from ..db import connect, get_meta
from ..normalise import normalise_name
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
    ("public_sector", "Public sector", "Government body or government-backed lender"),
    ("individual", "Individual", "A private person or family vehicle"),
    ("other", "Other", "Landlord, supplier, group company, pension trustee, crowdfunding platform"),
]
CATEGORY_LABELS = {k: label for k, label, _ in CHOICES} | {"unknown": "Can't tell"}
SOURCE_LABELS = {"human": "You", "seed": "Starter rule", "agent": "Classifier agent"}
DRAFT_STATUSES = ("new", "ready", "published")
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
    for p in [config.DB_PATH, config.LENDERS_CSV, config.ROOT / "drafts" / "status.json"]:
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
        "lc.category, lc.parent_firm, lc.confidence, lc.source, lc.review_status, lc.reason "
        "FROM charge_persons p JOIN charges c ON c.company_number = p.company_number "
        "AND c.charge_id = p.charge_id AND c.deleted = 0 "
        "LEFT JOIN lender_classifications lc ON lc.name_norm = p.name_norm "
        f"WHERE {where} GROUP BY p.name_norm ORDER BY n DESC, latest DESC", params).fetchall()
    return [{
        "name": r["name_norm"], "display": r["display"], "charges": r["n"], "latest": r["latest"],
        "category": r["category"], "category_label": CATEGORY_LABELS.get(r["category"] or "", ""),
        "parent": r["parent_firm"], "confidence": r["confidence"], "source": r["source"],
        "source_label": SOURCE_LABELS.get(r["source"] or "", "Not classified yet"),
        "status": r["review_status"] or "unclassified", "reason": r["reason"],
    } for r in rows]


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


def decisions(conn) -> dict:
    review = _lender_rows(conn, "lc.review_status = 'needs_review'")
    confirm = _lender_rows(conn, "lc.source = 'agent' AND lc.review_status = 'auto_ok'")
    waiting = _lender_rows(conn, "lc.name_norm IS NULL")
    for group in (review, confirm, waiting):
        for item in group:
            item["examples"] = _examples(conn, item["name"])
            item["history"] = _history(conn, item["name"])
    status = _draft_status()
    drafts = _md_files("drafts", "*-draft.md")
    for d in drafts:
        d["status"] = status.get(d["name"], {}).get("status", "new")
    return {
        "lenders": {"review": review, "confirm": confirm, "waiting": waiting},
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
            date_from: str = "", date_to: str = "", limit: int = 100, offset: int = 0) -> dict:
    where, params = ["c.deleted = 0"], []
    if date_from:
        where.append("c.delivered_on >= ?"); params.append(date_from)
    if date_to:
        where.append("c.delivered_on <= ?"); params.append(date_to)
    if q:
        like = f"%{q.strip()}%"
        where.append("(co.name LIKE ? OR c.company_number LIKE ? OR EXISTS (SELECT 1 FROM charge_persons pp "
                     "WHERE pp.company_number = c.company_number AND pp.charge_id = c.charge_id "
                     "AND (pp.name_raw LIKE ? OR pp.name_norm LIKE ?)))")
        params += [like, like, like, like.upper()]
    rows = conn.execute(
        "SELECT c.company_number, c.charge_id, c.delivered_on, c.created_on, c.status, c.satisfied_on, "
        "c.classification_text, c.floating_covers_all, co.name AS company_name "
        "FROM charges c LEFT JOIN companies co ON co.company_number = c.company_number "
        f"WHERE {' AND '.join(where)} ORDER BY c.delivered_on DESC, c.company_number LIMIT 5000",
        params).fetchall()
    out, tier_counts = [], {}
    for c in rows:
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
            "chargees": [{"name": p["name_norm"], "display": p["name_raw"],
                          "category": p["category"], "status": p["review_status"] or "unclassified"}
                         for p in persons],
            "filing_url": config.FILING_URL.format(company=c["company_number"], charge_id=c["charge_id"]),
        })
    return {"total": len(out), "rows": out[offset:offset + limit], "offset": offset, "limit": limit,
            "tier_counts": {t: tier_counts.get(t, 0) for t in "ABCDE"}}


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
    return item


_ACTIVITY = [
    (re.compile(r"^pipeline:"), "cloud", "Cloud run: new filings, queue and digests"),
    (re.compile(r"^agent\(classifier\)"), "agent", "Classifier agent answered new lender names"),
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

def decide(conn, git: Git, name: str, category: str, parent: str = "", note: str = "") -> dict:
    if category not in CATEGORY_LABELS or category == "unknown":
        raise ValueError(f"Unknown category {category!r}")
    if not conn.execute("SELECT 1 FROM charge_persons WHERE name_norm = ?", (normalise_name(name),)).fetchone():
        raise KeyError(f"No lender called {name}")
    norm = add_human_rule(name, category, (parent or "").strip()[:100], (note or "set in review app")[:200])
    refresh_rule_matches(conn, load_rules())
    git.commit_and_push(["data/lenders.csv"], f"review: {norm} is {CATEGORY_LABELS[category].lower()}")
    return {"name": norm, "category": category}


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


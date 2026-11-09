"""Dated backups of the saved databases, kept as assets on a GitHub release.

The 'radar-data' and 'radar-backfill' branches hold only the latest copy (radar/dbsync.py), so a
bad save would otherwise lose the history for good. This module copies whatever is on a branch
into a release (one per database) and thins the old copies out:

    python -m radar.dbbackup save [--backfill]       # back up the copy on the branch (skips if already there)
    python -m radar.dbbackup list [--backfill]       # backups, newest first
    python -m radar.dbbackup restore NAME [--backfill] [--local-only]
        # NAME is a backup's name from `list`, or a .db.gz file you downloaded from the Releases page.
        # Checks it, then puts it back on the branch (and in data/), so the next cloud run uses it.
        # --local-only: only replace your local data/ copy.

Kept: the newest KEEP_RUNS backups, plus the newest of each day for KEEP_DAYS days, plus the
newest of each ISO week for KEEP_WEEKS weeks (counted back from the newest backup, so a pipeline
that stops for a while never ages its own backups out). That is at most about 54 files per database.

Uses the GitHub CLI (`gh`, already on GitHub's runners; on your PC: `winget install GitHub.cli`
then `gh auth login`). Release assets don't count towards the 100 MB file limit of the branches
and are private when the repo is. See docs/backups.md.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, dbsync

KEEP_RUNS = 14
KEEP_DAYS = 14
KEEP_WEEKS = 26

TARGETS = {
    False: {"tag": "db-backups-radar", "prefix": "radar", "branch": dbsync.BRANCH,
            "file": dbsync.FILE, "db": lambda: config.DB_PATH},
    True: {"tag": "db-backups-backfill", "prefix": "backfill", "branch": dbsync.BACKFILL_BRANCH,
           "file": dbsync.BACKFILL_FILE, "db": lambda: config.BACKFILL_DB_PATH},
}

_STAMP = "%Y%m%dT%H%M%SZ"
_NAME_RE = re.compile(r"^(?P<prefix>[a-z]+)-(?P<stamp>\d{8}T\d{6}Z)\.db\.gz$")


def backup_name(prefix: str, when: datetime) -> str:
    return f"{prefix}-{when.astimezone(timezone.utc).strftime(_STAMP)}.db.gz"


def parse_name(name: str, prefix: str) -> datetime | None:
    m = _NAME_RE.match(name)
    if not m or m["prefix"] != prefix:
        return None
    return datetime.strptime(m["stamp"], _STAMP).replace(tzinfo=timezone.utc)


def to_delete(names: list[str], prefix: str, keep_runs: int = KEEP_RUNS,
              keep_days: int = KEEP_DAYS, keep_weeks: int = KEEP_WEEKS) -> list[str]:
    """Which backups the retention rule drops. Names it can't parse are never touched."""
    dated = sorted(((t, n) for n in names if (t := parse_name(n, prefix))), reverse=True)
    if len(dated) <= keep_runs:
        return []
    newest = dated[0][0]
    keep = {n for _, n in dated[:keep_runs]}
    day_floor = newest.date() - timedelta(days=keep_days)
    week_floor = newest.date() - timedelta(weeks=keep_weeks)
    days, weeks = set(), set()
    for t, n in dated:   # newest first, so the first one seen in a day/week is its newest
        d, wk = t.date(), t.isocalendar()[:2]
        if d > day_floor and d not in days:
            days.add(d); keep.add(n)
        if d > week_floor and wk not in weeks:
            weeks.add(wk); keep.add(n)
    return [n for _, n in dated if n not in keep]


# --- gh wrappers (patched in tests) ----------------------------------------------------------

def _gh(*args: str) -> str:
    cmd = ["gh", *args]
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo and args[:1] == ("release",):
        cmd += ["--repo", repo]
    res = subprocess.run(cmd, cwd=config.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout.decode()


def list_assets(tag: str) -> list[dict] | None:
    """[{name, size}] on the release, or None when the release doesn't exist yet."""
    try:
        out = _gh("release", "view", tag, "--json", "assets")
    except RuntimeError as e:
        if "not found" in str(e).lower():
            return None
        raise
    return [{"name": a["name"], "size": a.get("size", 0)} for a in json.loads(out).get("assets", [])]


def ensure_release(tag: str, prefix: str) -> None:
    if list_assets(tag) is None:
        _gh("release", "create", tag, "--title", f"Database backups ({prefix})", "--prerelease",
            "--latest=false", "--notes",
            "Dated copies of the saved database, made by radar/dbbackup.py. Restore one with "
            "`python -m radar.dbbackup restore NAME` (see armariu-radar/docs/backups.md). "
            "Old copies are thinned out automatically.")


def upload(tag: str, path: Path) -> None:
    _gh("release", "upload", tag, str(path))


def delete_asset(tag: str, name: str) -> None:
    _gh("release", "delete-asset", tag, name, "--yes")


def download(tag: str, name: str, dest_dir: Path) -> Path:
    _gh("release", "download", tag, "--pattern", name, "--dir", str(dest_dir), "--clobber")
    return dest_dir / name


# --- commands --------------------------------------------------------------------------------

def save(backfill: bool = False, remote: str = "origin") -> int:
    """Back up the copy currently on the branch, named after the time it was saved, then prune."""
    t = TARGETS[backfill]
    try:
        if not dbsync._branch_exists(remote, t["branch"]):
            print(f"No '{t['branch']}' branch yet: nothing to back up.")
            return 0
        dbsync._git("fetch", "--quiet", remote, f"refs/heads/{t['branch']}")
        saved_at = dbsync._git("log", "-1", "--format=%cI", "FETCH_HEAD").decode().strip()
        data = dbsync._git("show", f"FETCH_HEAD:{t['file']}")
    except RuntimeError as e:
        print(f"::error::Could not read '{t['branch']}' to back it up: {e}")
        return 5
    name = backup_name(t["prefix"], datetime.fromisoformat(saved_at))
    ensure_release(t["tag"], t["prefix"])
    names = [a["name"] for a in list_assets(t["tag"]) or []]
    if name in names:
        print(f"{name} is already backed up.")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            gz = Path(tmp) / name
            gz.write_bytes(data)
            db = Path(tmp) / "check.db"
            try:
                db.write_bytes(gzip.decompress(data))
                problem = dbsync.integrity_problem(db)
            except (OSError, EOFError, gzip.BadGzipFile) as e:
                problem = str(e)
            if problem:
                # Still keep it (it may be all there is), but say so loudly.
                print(f"::warning::The copy on '{t['branch']}' fails the integrity check ({problem}); "
                      "backing it up anyway.")
            upload(t["tag"], gz)
        names.append(name)
        print(f"Backed up {t['branch']} as {name} ({len(data) / 1e6:.1f} MB) on release {t['tag']}")
    for old in to_delete(names, t["prefix"]):
        delete_asset(t["tag"], old)
        print(f"Removed old backup {old}")
    return 0


def show(backfill: bool = False) -> int:
    t = TARGETS[backfill]
    assets = list_assets(t["tag"])
    if not assets:
        print(f"No backups yet on release {t['tag']}.")
        return 1
    dated = sorted(((parse_name(a["name"], t["prefix"]), a) for a in assets),
                   key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for when, a in dated:
        print(f"{a['name']}  {a['size'] / 1e6:6.1f} MB")
    return 0


def restore(name: str, backfill: bool = False, local_only: bool = False, remote: str = "origin") -> int:
    t = TARGETS[backfill]
    db_path = Path(t["db"]())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        given = Path(name)
        if given.is_file():
            gz = given
        else:
            print(f"Downloading {name} from release {t['tag']} ...")
            gz = download(t["tag"], name, tmp)
        candidate = tmp / "restore.db"
        try:
            with gzip.open(gz, "rb") as src, open(candidate, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except (OSError, EOFError) as e:
            print(f"{gz} isn't a readable .db.gz file: {e}")
            return 3
        problem = dbsync.integrity_problem(candidate)
        if problem:
            print(f"{gz.name} fails the integrity check, so nothing was restored: {problem}")
            return 3
        counts = dbsync.row_counts(candidate)
        print(f"{gz.name} passes the integrity check ({sum(counts.values()):,} rows in {len(counts)} tables).")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.exists():
            keep = db_path.with_name(db_path.name + ".before-restore")
            shutil.copy2(db_path, keep)
            print(f"Your previous local copy is kept as {keep}")
        shutil.copy2(candidate, db_path)
        print(f"Restored to {db_path}")
    if local_only:
        return 0
    # An older backup can legitimately have fewer rows than a broken live copy, so skip the guard.
    status = dbsync.push(db_path, remote=remote, branch=t["branch"], file=t["file"], check=False)
    if status == 0:
        print(f"Branch '{t['branch']}' now holds the restored copy; the next cloud run starts from it.")
    return status


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    backfill = "--backfill" in argv
    local_only = "--local-only" in argv
    argv = [a for a in argv if a not in ("--backfill", "--local-only")]
    try:
        if argv[:1] == ["save"]:
            return save(backfill)
        if argv[:1] == ["list"]:
            return show(backfill)
        if argv[:1] == ["restore"] and len(argv) == 2:
            return restore(argv[1], backfill, local_only)
    except RuntimeError as e:
        print(f"::error::{e}")
        return 5
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())

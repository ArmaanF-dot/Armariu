"""Keep data/radar.db on a separate git branch, so the pipeline can run on GitHub Actions
(where nothing is kept between runs) and you can download the latest copy to your PC.

    python -m radar.dbsync push    # save data/radar.db to the 'radar-data' branch
    python -m radar.dbsync pull    # replace data/radar.db with the copy on 'radar-data'
    python -m radar.dbsync push --backfill   # same for data/backfill.db on 'radar-backfill'
    python -m radar.dbsync pull --backfill   # (older charge histories, radar/backfill.py)

The branch holds a single commit with one file, radar.db.gz, and is force-pushed each time, so
the repo doesn't grow with every save. The branch is written with git plumbing commands, so your
working folder and current branch are never touched.

Because each push replaces the only copy on the branch, `push` refuses to save a database that
fails SQLite's integrity check, or that has lost a big share of the rows in any sizeable table
compared with the copy already on the branch (a half-written or fresh database after a failed
download). Set RADAR_ALLOW_SHRINK=1 to save a smaller database on purpose. `pull` checks the
download the same way before replacing your local copy. Dated copies of every saved database are
kept as release assets by radar/dbbackup.py; see docs/backups.md to restore one.

Exit codes: 0 ok, 1 nothing to do (no local database / no branch yet), 2 too big for GitHub,
3 failed the integrity check, 4 lost too many rows, 5 git or network error.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config

BRANCH = "radar-data"
FILE = "radar.db.gz"
BACKFILL_BRANCH = "radar-backfill"   # older charge histories (radar/backfill.py), kept apart
BACKFILL_FILE = "backfill.db.gz"
MAX_BYTES = 90 * 1024 * 1024  # GitHub rejects files over 100 MB

# Shrink guard: a table that held at least SHRINK_MIN_ROWS rows on the branch may not lose more
# than SHRINK_MAX_DROP of them in one save. The pipeline deletes and re-inserts rows per company,
# so ordinary runs move counts by far less than this.
SHRINK_MIN_ROWS = 1000
SHRINK_MAX_DROP = 0.10

OK, NOTHING, TOO_BIG, CORRUPT, SHRUNK, GIT_ERROR = 0, 1, 2, 3, 4, 5


def _git(*args, input_bytes=None) -> bytes:
    res = subprocess.run(["git", *args], cwd=config.ROOT, input=input_bytes,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout


def _ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def integrity_problem(path) -> str | None:
    """None when SQLite's full integrity check passes, otherwise what it reported."""
    try:
        conn = _ro_connect(path)
        try:
            rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return str(e)
    if rows == ["ok"]:
        return None
    return "; ".join(rows[:5]) + (f" (and {len(rows) - 5} more)" if len(rows) > 5 else "")


def row_counts(path) -> dict[str, int]:
    conn = _ro_connect(path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def shrink_problems(old: dict[str, int], new: dict[str, int]) -> list[str]:
    """Tables that vanished or lost too many rows between the saved copy and the new one."""
    problems = []
    for table, before in sorted(old.items()):
        if before < SHRINK_MIN_ROWS:
            continue
        after = new.get(table)
        if after is None:
            problems.append(f"{table}: table missing (had {before:,} rows)")
        elif after < before * (1 - SHRINK_MAX_DROP):
            problems.append(f"{table}: {before:,} -> {after:,} rows ({after / before - 1:+.0%})")
    return problems


def _branch_exists(remote: str, branch: str) -> bool:
    """True/False from the remote; RuntimeError when the remote can't be reached."""
    return bool(_git("ls-remote", "--heads", remote, f"refs/heads/{branch}").strip())


def _fetch_saved(remote: str, branch: str, file: str, dest: Path) -> bool:
    """Download the copy on the branch to dest (uncompressed). False when there is no branch."""
    if not _branch_exists(remote, branch):
        return False
    _git("fetch", "--quiet", remote, f"refs/heads/{branch}")
    dest.write_bytes(gzip.decompress(_git("show", f"FETCH_HEAD:{file}")))
    return True


def check_against_saved(db_path: Path, remote: str, branch: str, file: str) -> int:
    """Run before overwriting the branch: integrity of the new copy, then the shrink guard."""
    problem = integrity_problem(db_path)
    if problem:
        print(f"::error::{db_path.name} failed the SQLite integrity check, so it was NOT saved "
              f"and the copy on '{branch}' is kept: {problem}")
        return CORRUPT
    if os.environ.get("RADAR_ALLOW_SHRINK") == "1":
        print("RADAR_ALLOW_SHRINK=1: skipping the row-count comparison.")
        return OK
    with tempfile.TemporaryDirectory() as tmp:
        saved = Path(tmp) / "saved.db"
        try:
            if not _fetch_saved(remote, branch, file, saved):
                return OK   # first save: nothing to compare with
        except RuntimeError as e:
            print(f"::error::Could not download the copy on '{branch}' to compare with, so nothing "
                  f"was saved: {e}")
            return GIT_ERROR
        except (OSError, EOFError, gzip.BadGzipFile) as e:
            print(f"::error::The copy on '{branch}' can't be decompressed ({e}). Nothing was saved. "
                  "Restore a backup (docs/backups.md) or set RADAR_ALLOW_SHRINK=1 to replace it.")
            return CORRUPT
        if integrity_problem(saved):
            print(f"::error::The copy on '{branch}' fails the integrity check. Nothing was saved. "
                  "Restore a backup (docs/backups.md) or set RADAR_ALLOW_SHRINK=1 to replace it.")
            return CORRUPT
        old, new = row_counts(saved), row_counts(db_path)
    problems = shrink_problems(old, new)
    if problems:
        print(f"::error::{db_path.name} has far fewer rows than the copy on '{branch}', so it was NOT "
              f"saved and the saved copy is kept. If this is on purpose, rerun with "
              f"RADAR_ALLOW_SHRINK=1. " + " | ".join(problems))
        return SHRUNK
    print(f"Checks passed: integrity ok, row counts {sum(old.values()):,} -> {sum(new.values()):,}")
    return OK


def push(db_path=None, remote: str = "origin", branch: str = BRANCH, file: str = FILE,
         check: bool = True) -> int:
    db_path = Path(db_path or config.DB_PATH)
    if not db_path.exists():
        print(f"No database at {db_path}; nothing to save.")
        return NOTHING
    if check:
        status = check_against_saved(db_path, remote, branch, file)
        if status != OK:
            return status
    with tempfile.TemporaryDirectory() as tmp:
        gz = Path(tmp) / file
        with open(db_path, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        size = gz.stat().st_size
        if size > MAX_BYTES:
            print(f"Compressed database is {size / 1e6:.0f} MB, too big for GitHub (limit 100 MB). "
                  "Time to prune old data or move the database elsewhere.")
            return TOO_BIG
        blob = _git("hash-object", "-w", str(gz)).decode().strip()
    tree = _git("mktree", input_bytes=f"100644 blob {blob}\t{file}\n".encode()).decode().strip()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit = _git("commit-tree", tree, "-m", f"database snapshot {stamp}").decode().strip()
    _git("push", "--force", "--quiet", remote, f"{commit}:refs/heads/{branch}")
    print(f"Saved database ({size / 1e6:.1f} MB compressed) to branch {branch}")
    return OK


def pull(db_path=None, remote: str = "origin", branch: str = BRANCH, file: str = FILE) -> int:
    db_path = Path(db_path or config.DB_PATH)
    try:
        if not _branch_exists(remote, branch):
            print(f"No '{branch}' branch on {remote} yet: starting with a fresh database.")
            return NOTHING
        _git("fetch", "--quiet", remote, f"refs/heads/{branch}")
        data = _git("show", f"FETCH_HEAD:{file}")
    except RuntimeError as e:
        print(f"::error::Could not download the database from '{branch}': {e}")
        return GIT_ERROR
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".download")
    try:
        tmp.write_bytes(gzip.decompress(data))
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        print(f"::error::The copy on '{branch}' can't be decompressed ({e}). Your local database "
              "was left alone. Restore a backup: see docs/backups.md.")
        return CORRUPT
    problem = integrity_problem(tmp)
    if problem:
        tmp.unlink(missing_ok=True)
        print(f"::error::The copy on '{branch}' fails the SQLite integrity check ({problem}). Your "
              "local database was left alone. Restore a backup: see docs/backups.md.")
        return CORRUPT
    tmp.replace(db_path)
    try:
        _source_file(db_path).write_text(_git("rev-parse", f"FETCH_HEAD:{file}").decode().strip(), encoding="utf-8")
    except RuntimeError:
        pass
    print(f"Downloaded database to {db_path} ({db_path.stat().st_size / 1e6:.1f} MB)")
    return OK


def _source_file(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".source")


def fetch_if_changed(db_path=None, remote: str = "origin") -> tuple[Path | None, str | None]:
    """Download the cloud copy to a temporary file next to the database, without touching the
    database itself, and only if the cloud copy changed since the last download.
    Returns (downloaded file or None, its id). Put it in place with `install()`.
    Raises RuntimeError when there is no cloud copy."""
    db_path = Path(db_path or config.DB_PATH)
    _git("fetch", "--quiet", remote, f"refs/heads/{BRANCH}")
    blob = _git("rev-parse", f"FETCH_HEAD:{FILE}").decode().strip()
    src = _source_file(db_path)
    if db_path.exists() and src.exists() and src.read_text(encoding="utf-8").strip() == blob:
        return None, blob
    data = _git("cat-file", "blob", blob)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".download")
    tmp.write_bytes(gzip.decompress(data))
    return tmp, blob


def install(tmp: Path, blob: str, db_path=None) -> None:
    """Swap a downloaded copy into place (quick; call it while holding the app's database lock)."""
    db_path = Path(db_path or config.DB_PATH)
    tmp.replace(db_path)
    _source_file(db_path).write_text(blob, encoding="utf-8")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kw = {}
    if "--backfill" in argv:
        argv.remove("--backfill")
        kw = {"db_path": config.BACKFILL_DB_PATH, "branch": BACKFILL_BRANCH, "file": BACKFILL_FILE}
    if argv[:1] == ["push"]:
        return push(**kw)
    if argv[:1] == ["pull"]:
        return pull(**kw)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())

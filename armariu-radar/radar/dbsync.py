"""Keep data/radar.db on a separate git branch, so the pipeline can run on GitHub Actions
(where nothing is kept between runs) and you can download the latest copy to your PC.

    python -m radar.dbsync push    # save data/radar.db to the 'radar-data' branch
    python -m radar.dbsync pull    # replace data/radar.db with the copy on 'radar-data'

The branch holds a single commit with one file, radar.db.gz, and is force-pushed each time, so
the repo doesn't grow with every save. The branch is written with git plumbing commands, so your
working folder and current branch are never touched.
"""
from __future__ import annotations

import gzip
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config

BRANCH = "radar-data"
FILE = "radar.db.gz"
MAX_BYTES = 90 * 1024 * 1024  # GitHub rejects files over 100 MB


def _git(*args, input_bytes=None) -> bytes:
    res = subprocess.run(["git", *args], cwd=config.ROOT, input=input_bytes,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout


def push(db_path=None, remote: str = "origin") -> int:
    db_path = Path(db_path or config.DB_PATH)
    if not db_path.exists():
        print(f"No database at {db_path}; nothing to save.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        gz = Path(tmp) / FILE
        with open(db_path, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        size = gz.stat().st_size
        if size > MAX_BYTES:
            print(f"Compressed database is {size / 1e6:.0f} MB, too big for GitHub (limit 100 MB). "
                  "Time to prune old data or move the database elsewhere.")
            return 2
        blob = _git("hash-object", "-w", str(gz)).decode().strip()
    tree = _git("mktree", input_bytes=f"100644 blob {blob}\t{FILE}\n".encode()).decode().strip()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit = _git("commit-tree", tree, "-m", f"database snapshot {stamp}").decode().strip()
    _git("push", "--force", "--quiet", remote, f"{commit}:refs/heads/{BRANCH}")
    print(f"Saved database ({size / 1e6:.1f} MB compressed) to branch {BRANCH}")
    return 0


def pull(db_path=None, remote: str = "origin") -> int:
    db_path = Path(db_path or config.DB_PATH)
    try:
        _git("fetch", "--quiet", remote, f"refs/heads/{BRANCH}")
    except RuntimeError:
        print(f"No '{BRANCH}' branch on {remote} yet: starting with a fresh database.")
        return 1
    data = _git("show", f"FETCH_HEAD:{FILE}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".download")
    tmp.write_bytes(gzip.decompress(data))
    tmp.replace(db_path)
    print(f"Downloaded database to {db_path} ({db_path.stat().st_size / 1e6:.1f} MB)")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["push"]:
        return push()
    if argv[:1] == ["pull"]:
        return pull()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())

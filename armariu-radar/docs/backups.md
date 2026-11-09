# Database backups and how to restore one

The cloud workflow keeps the live database as a single file on the `radar-data` branch
(`radar.db.gz`), and the older-data sample on `radar-backfill` (`backfill.db.gz`). Each run
replaces that file. Three things now stop a bad run from losing data:

1. **Checks before saving.** `radar.dbsync push` only replaces the saved copy if the new database
   passes SQLite's full integrity check, and if no table that held 1,000+ rows has lost more than
   10% of them. Otherwise the step fails, GitHub emails you, and the saved copy stays as it was.
2. **Checks when downloading.** At the start of a run, `radar.dbsync pull` checks the download
   the same way. If the saved copy is broken, or the download fails, the run stops instead of
   carrying on with an empty database. (A missing branch, on the very first run, still starts fresh.)
3. **Dated backups.** Every saved copy is also kept as a file on a GitHub release:
   **Releases → "Database backups (radar)"** (tag `db-backups-radar`) and
   **"Database backups (backfill)"** (tag `db-backups-backfill`). Files are named after the time
   the copy was saved, in UTC, e.g. `radar-20261109T182512Z.db.gz`.

## What is kept

- the last 14 saves (about 2 days at one run every 3 hours),
- plus the newest save of each day for 14 days,
- plus the newest save of each week for 26 weeks (about 6 months).

That is at most about 54 files per database, roughly 1 GB for `radar` and 0.75 GB for
`backfill` at today's sizes. Release files don't count towards the 100 MB limit on the branches,
GitHub sets no total size limit on releases, and they are private while the repo is private.
Windows are counted back from the newest backup, not from today, so if the pipeline stops for a
month its backups are not thinned away.

The 90 MB per-file limit on the branches (`dbsync.MAX_BYTES`) still applies to the live copy;
each backup file can be up to 2 GB.

## Restore a backup

First **pause the cloud runs**, so a run doesn't overwrite your restore halfway:
Actions → radar → ⋯ → **Disable workflow**.

### Option A: from your PC with the GitHub CLI (one command)

One-time setup: `winget install GitHub.cli`, then `gh auth login`.

```
.venv\Scripts\python.exe -m radar.dbbackup list              # backups, newest first
.venv\Scripts\python.exe -m radar.dbbackup restore radar-20261109T182512Z.db.gz
```

`restore` downloads the file, checks it with SQLite's integrity check, copies it to
`data/radar.db` (your previous local copy is kept as `data/radar.db.before-restore`), and puts it
back on the `radar-data` branch, so the next cloud run starts from it. For the backfill sample,
add `--backfill` to both commands. To look at an old copy without touching the cloud, add
`--local-only`.

### Option B: download it in the browser

1. Open the repo's **Releases** page, then **Database backups (radar)**, and download the file you
   want from **Assets**.
2. Run `restore` with the downloaded file instead of a name:
   ```
   .venv\Scripts\python.exe -m radar.dbbackup restore "%USERPROFILE%\Downloads\radar-20261109T182512Z.db.gz"
   ```

Then **re-enable the workflow** (Actions → radar → **Enable workflow**) and start one run by hand
to check it goes green.

## When a run says the database "has far fewer rows"

The error line lists each table and how many rows it lost. If that is a real problem (a failed
download, a bug), nothing needs restoring: the saved copy was kept. Fix the cause and the next
run carries on. If you shrank the database on purpose (for example after pruning old data), set
the repository variable `RADAR_ALLOW_SHRINK` to `1` (Settings → Secrets and variables → Actions
→ Variables), run the workflow once, then **delete the variable** so the guard is back on.
Locally: `set RADAR_ALLOW_SHRINK=1` before `radar.dbsync push`.

## Check a backup now and then

A backup that has never been restored doesn't count. Once a month, run
`python -m radar.dbbackup restore NAME --local-only` on a recent backup and open the review app.
That only touches your local copy.

## Notes

- Backups run in two places in `.github/workflows/radar.yml`: before the pipeline (backs up the
  copy about to be replaced, if it isn't already; a failure here only warns, so a GitHub hiccup
  doesn't cost a run of stream data) and as the last step (backs up what this run saved; a
  failure here fails the run so you get an email).
- They use the workflow's own `GITHUB_TOKEN` with `contents: write`. No new secrets.
- If you ever turn on **immutable releases** in the repo settings, exclude these two releases, or
  old backups can't be removed.
- If the database moves to Cloudflare R2 later (hosted backend plan, Phase 0 point 2), the same
  retention rule (`dbbackup.to_delete`) can be reused there.

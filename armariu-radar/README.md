# Armariu deal radar

Watches UK Companies House charge filings and sorts each lender into a category, so the weekly
newsletter can show which companies granted security to **private credit / direct lenders**.
Plain Python does the data work for free. Claude agents, running on your own Claude Pro plans,
handle the parts that need judgement.

```
Companies House stream ─▶ pipeline (Python, no AI) ─▶ data/radar.db
                               │
                               ├─▶ classify/queue.jsonl ─▶ CLASSIFIER agent ─▶ classify/results/
                               │                                                      │
                               │◀─────────── validated & loaded on next run ◀──────────┘
                               │
                               └─▶ output/weekly/ ─▶ REVIEWER agent ─▶ reviews/
                                                 └─▶ NEWSLETTER agent ─▶ drafts/ ─▶ you edit & publish
```

The agents never talk directly. They hand work over through files in this folder, so each one
can run on its own schedule. Everything runs on one PC with one Claude Pro plan. See
`CLAUDE.md` for the rules every agent follows.

## Setup (about 15 minutes)

1. **Folder and git.** Unzip somewhere permanent (e.g. `C:\Users\you\PycharmProjects\armariu-radar`)
   and run `git init` there, then `git add -A` and `git commit -m "start"`. Git gives you a history of every
   agent run, so you can undo a bad one. Optional: add a private GitHub repo as a remote
   (`git remote add origin ...`, then `git push -u origin main`) for an off-machine backup. The scripts push
   automatically when a remote exists. Without git the scripts still work, just with no history.
2. **Python.** Python 3.10+. In the folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate          (Mac: source .venv/bin/activate)
   pip install -r requirements.txt
   python -m unittest discover -s tests
   python -m radar.demo            # offline demo on fake data; read demo_output/weekly/*.md
   ```
3. **Companies House keys.** Register free at
   https://developer.company-information.service.gov.uk, then create two applications: one
   **REST** key and one **Stream** key. Copy `.env.example` to `.env` and paste both keys in.
   `.env` is git-ignored. Never commit it.
4. **Claude Code.** Install Claude Code, run `claude` once in the folder and sign in with your
   Claude account. The scheduled agents use that login.
5. **First run by hand**, so you see it working:
   ```
   python -m radar.pipeline run --minutes 5
   python -m radar.pipeline status
   powershell -ExecutionPolicy Bypass -File scripts\run_agent.ps1 -Agent classifier
   ```
   The agent's log is in `logs/`.
6. **Schedule it** (Windows Task Scheduler; run PowerShell in the folder):
   ```
   powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1
   ```
   This registers four tasks: pipeline every 30 min, classifier every 2 h, reviewer and newsletter
   every 4 h. Agents with nothing to do exit straight away and use no Claude usage. Remove the tasks
   with `-Uninstall`. On a Mac, use `scripts/*.sh` with cron (example lines are at the top of each script).

**Usage on one Pro plan:** the scheduled agents share your plan's limits with your normal chats.
Most of the usage is the classifier in your first couple of weeks, while the lender list fills up
(up to 60 names per run). It drops off once most lenders are known. If you hit limits, raise the
classifier interval in `install_tasks.ps1` or lower `CLASSIFIER_BATCH` in `radar/agentcheck.py`.

## Your weekly routine (about 15 minutes)

1. `python -m radar.review list` shows the lender names the agents weren't sure about. For each one:
   `python -m radar.review approve "NAME"` if the agent was right, or
   `python -m radar.review set "NAME" direct_lender --parent "Firm"` to correct it.
   These decisions go into `data/lenders.csv` and always beat the agents. Commit that file
   now and then, so your decisions are in the history.
2. Read `reviews/<week>-review.md`.
3. Edit `drafts/<week>-draft.md`, check the "To verify" section against the filing links, and publish.

To ask an agent for something, drop a note in `inbox/<agent>/` (for example
`inbox/classifier/20261020T1200Z-from-armaan.md`). It's picked up on that agent's next run.
Agents leave notes for you in `inbox/maintainer/`.

## Guardrails built in

- **Agents can only write to their own folders.** The run scripts use Claude Code's `dontAsk`
  mode with a fixed list of allowed tools. There is no shell access, no access to `.env`, and
  no publishing.
- **Every agent answer is validated** before use (`radar/classify.py`). Unknown names, bad
  categories and out-of-range confidences are rejected and logged in `logs/apply_rejections.log`.
- **Humans outrank agents.** Rules in `lenders.csv` can't be overwritten by agent output.
- **Low confidence goes to a person.** Below 0.85 (0.90 for direct lenders), a classification is
  marked `needs_review`, and every agent-made classification is marked "Check? = yes" in the digest.
- **No wasted usage.** Each agent checks for work first and exits within a second when there's
  nothing to do. Each run is capped at 40 turns and 60 names.

## Limits to know about

- **A charge isn't a deal.** Filings show that security was granted and to whom. They don't show
  the amount, the price, or the terms.
- **Security agents hide the lender.** Syndicated and unitranche deals often name only a
  trustee or agent (Tier B).
- **Filings lag.** Companies must register a charge within 21 days of creating it, so treat this
  as a lagging signal.
- **No history at first.** The stream starts from the moment you first connect. To load past
  charges for specific companies, run `python -m radar.ingest backfill --company 01234567`
  (rate-limited to 500 calls per 5 minutes).
- **Offline gaps.** Tasks only run while your PC is on and you're logged in. If it's off long
  enough, the stream position expires. The pipeline logs a warning and restarts from "now", so
  filings during the gap are missed. For full coverage, leave the PC on, or later move the
  pipeline to an always-on machine.
- **The seed rules are a starting point.** The names in `data/lenders.csv` marked `verify`
  came from general knowledge. Check them, because a wrong rule repeats on every filing.
- **Agent runs use your Claude plan.** The same usage limits apply as for your normal chats.

## Files

| Path | What |
|---|---|
| `radar/ingest.py` | Stream, REST backfill, borrower enrichment |
| `radar/classify.py` | Rules, queue, validating agent results and reviewer flags |
| `radar/export.py` | Weekly digests (tiers A–E) |
| `radar/review.py` | Human review CLI |
| `radar/pipeline.py` | The scheduled pass that runs all of the above |
| `radar/agentcheck.py` | "Is there work?" check plus task file for each agent run |
| `agents/*.md` | The three agents' instructions |
| `scripts/` | Run scripts for Windows (`.ps1`) and Mac/Linux (`.sh`), plus Task Scheduler setup |
| `schema.sql` | Database tables |
| `data/lenders.csv` | Lender rules (human-maintained) |

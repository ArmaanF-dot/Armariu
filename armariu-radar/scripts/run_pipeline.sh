#!/usr/bin/env bash
# Mac/Linux version of run_pipeline.ps1.
# Example crontab line (every 30 minutes):  */30 * * * * cd /path/to/armariu-radar && scripts/run_pipeline.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
PY="python3"; [ -x .venv/bin/python ] && PY=".venv/bin/python"
LOCK=".agent-pipeline.lock"
if [ -f "$LOCK" ] && [ -n "$(find "$LOCK" -mmin -60 2>/dev/null)" ]; then exit 0; fi
echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT
IN_REPO=0; git rev-parse --is-inside-work-tree >/dev/null 2>&1 && IN_REPO=1
HAS_REMOTE=0; [ "$IN_REPO" = 1 ] && [ -n "$(git remote)" ] && HAS_REMOTE=1
[ "$HAS_REMOTE" = 1 ] && { git pull --rebase --autostash --quiet || true; }
"$PY" -m radar.pipeline run --minutes "${MINUTES:-10}" >> logs/pipeline-console.log 2>&1
[ "$IN_REPO" = 1 ] || exit 0
git add -- classify/queue.jsonl output/weekly
if ! git diff --cached --quiet; then
  git commit --quiet -m "pipeline: update queue and digests"
  if [ "$HAS_REMOTE" = 1 ]; then
    git push --quiet || { git pull --rebase --quiet && git push --quiet; }
  fi
fi

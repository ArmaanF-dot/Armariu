#!/usr/bin/env bash
# Mac/Linux version of run_agent.ps1. Usage: scripts/run_agent.sh classifier|reviewer|newsletter
# Example crontab line (hourly):  0 * * * * cd /path/to/armariu-radar && scripts/run_agent.sh reviewer
set -euo pipefail
AGENT="${1:?usage: run_agent.sh classifier|reviewer|newsletter}"
MAX_TURNS="${MAX_TURNS:-40}"
cd "$(dirname "$0")/.."
mkdir -p logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/${AGENT}-${STAMP}.log"
PY="python3"; [ -x .venv/bin/python ] && PY=".venv/bin/python"

case "$AGENT" in
  classifier) ALLOWED=(Read Glob Grep "Edit(./classify/results/**)" "Edit(./inbox/**)" WebSearch); OWNED=(classify/results inbox) ;;
  reviewer)   ALLOWED=(Read Glob Grep "Edit(./reviews/**)" "Edit(./inbox/**)" WebSearch "WebFetch(domain:find-and-update.company-information.service.gov.uk)"); OWNED=(reviews inbox) ;;
  newsletter) ALLOWED=(Read Glob Grep "Edit(./drafts/**)" "Edit(./inbox/**)"); OWNED=(drafts inbox) ;;
  *) echo "unknown agent $AGENT"; exit 1 ;;
esac

LOCK=".agent-${AGENT}.lock"
if [ -f "$LOCK" ] && [ -n "$(find "$LOCK" -mmin -120 2>/dev/null)" ]; then exit 0; fi
echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT

IN_REPO=0; git rev-parse --is-inside-work-tree >/dev/null 2>&1 && IN_REPO=1
HAS_REMOTE=0; [ "$IN_REPO" = 1 ] && [ -n "$(git remote)" ] && HAS_REMOTE=1
[ "$HAS_REMOTE" = 1 ] && { git pull --rebase --autostash --quiet || true; }
set +e; "$PY" -m radar.agentcheck prepare "$AGENT"; RC=$?; set -e
[ "$RC" -eq 3 ] && exit 0
[ "$RC" -ne 0 ] && { echo "agentcheck failed ($RC)" >> "$LOG"; exit 1; }

echo "$(date) starting $AGENT" >> "$LOG"
claude -p "Your task for this run is in the file .agent-${AGENT}-task.md. Read it, then do it, following your role instructions." \
  --append-system-prompt-file "agents/${AGENT}.md" \
  --permission-mode dontAsk --max-turns "$MAX_TURNS" --output-format text \
  --allowedTools "${ALLOWED[@]}" \
  --disallowedTools "Read(./.env)" "Read(./data/radar.db)" Bash >> "$LOG" 2>&1

"$PY" -m radar.agentcheck finish "$AGENT" >> "$LOG" 2>&1
[ "$IN_REPO" = 1 ] || exit 0
git add -- "${OWNED[@]}"
if ! git diff --cached --quiet; then
  git commit --quiet -m "agent(${AGENT}): run ${STAMP}"
  if [ "$HAS_REMOTE" = 1 ]; then
    git push --quiet || { git pull --rebase --quiet && git push --quiet; }
  fi
fi

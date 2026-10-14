#!/usr/bin/env bash
# run-queue.sh : one spool queue for site audits (2026-10-14; replaces fixed lane files).
# Read-only toward audited sites: GET/HEAD only (all fetching happens in run-full-audit.sh).
#
#   run-queue.sh add "slug|url|Business Name|flags" [...]   queue jobs (same line + CSR/PROXY flags as run-lane.sh)
#   run-queue.sh add --file <lane-file>                      queue every line of a file
#   run-queue.sh work [N] [--stay]                           start N workers (default below); foreground,
#                                                            returns when all workers exit. Without --stay a
#                                                            worker exits once $QUEUE_DIR/pending/ is empty.
#   run-queue.sh status                                      counts, running jobs + elapsed min, stale heartbeats
#   run-queue.sh stop                                        TERM the live workers (by PID from their heartbeat);
#                                                            each finishes its current job, then exits
#   run-queue.sh requeue <job file name>                     move a running/ job whose worker is dead back to pending/
#
# Why: on 2026-10-14 one fixed lane worked alone for 58.6 min while the CPU was 91-92% idle, and 74% of
# request-to-report time was queue wait. Here every idle worker takes the oldest pending job.
# Queue folder: QUEUE_DIR (default $KIT_HOME/queue; see queue/README.md).
#
# Mechanics:
#   - claim = atomic rename pending/<job> -> running/<job> (one filesystem; two workers can never both win).
#   - the job file gets worker, start/end UTC, rc and run_log appended, then moves to done/ (rc 0) or failed/.
#   - per-slug lock: flock on $QUEUE_DIR/locks/<slug>.lock (taken inside run-lane.sh run_site, shared with
#     run-lane.sh lanes). A job whose slug is already running is skipped and stays pending.
#   - heartbeat: $QUEUE_DIR/heartbeat/<worker>.txt every QUEUE_HEARTBEAT_S (30 s): UTC, state, slug, stage
#     (last "=== [n] ... ===" line in the run log). status flags heartbeats older than 3 min.
#   - disk gate per job (not per batch): before each claim, free GB for KIT_WORK >= AUDIT_MIN_FREE_GB (the
#     run-full-audit.sh stage-0 rule; default 2 as in run-lane.sh). Below it the job stays pending, the
#     heartbeat says WAITING-DISK, and the worker sleeps QUEUE_DISK_SLEEP_S (60 s). Nothing is deleted.
#   - run logs: $CASES_DIR/<slug>/run-<UTC>.log, never overwritten; run1.log -> newest (run-lane.sh).
#   - stdin of every child: $EMPTY_STDIN (an empty file, default $KIT_WORK/.empty-stdin).
#
# DEFAULT WORKERS = 1, MAX 2 (v1.0.0, owner: bots often run on shared machines). "work N" or QUEUE_WORKERS
# above 2 is clamped to 2 unless QUEUE_WORKERS_UNSAFE=1. Earlier basis for 4, measured 2026-10-14 with sar on one box:
#   box = 4 cores / 15 GB RAM. One lane: CPU 91-92% idle, load 0.17-0.32 (19:30-19:40). Three lanes
#   (20:20-21:00): CPU 61-79% idle (avg 68%), load avg 1.5-1.7, RAM used 16-20% (>= 12 GB available).
#   So one lane costs about 11% CPU and 0.5 load; 4 workers project to about 43% CPU and load ~2.2 on
#   4 cores, with RAM far from the limit. The heavy, shared part is Chrome/Lighthouse: with 3 lanes 12% of
#   Lighthouse time already overlapped another Lighthouse (no box-wide Lighthouse lock yet), so
#   go no higher by default. Start at 4; raise one at a time only while sar idle stays above 30%.
#   On a smaller machine, set QUEUE_WORKERS to the core count or fewer.
#
# Env: QUEUE_DIR, QUEUE_WORKERS (default 1, max 2), QUEUE_WORKERS_UNSAFE=1 (lift the max), QUEUE_POLL_S (5), QUEUE_HEARTBEAT_S (30), QUEUE_DISK_SLEEP_S (60);
# tests also use AUDIT_CASE_ROOT and QUEUE_AUDIT_CMD (fake audit).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=run-lane.sh
. "$HERE/run-lane.sh"        # run_site, disk_ok, free_gb, CASE_ROOT, QDIR, EMPTY, AUDIT_MIN_FREE_GB
export LC_ALL=C
DEFAULT_WORKERS=1
MAX_WORKERS=2
POLL="${QUEUE_POLL_S:-5}"
HB_S="${QUEUE_HEARTBEAT_S:-30}"
DISK_SLEEP="${QUEUE_DISK_SLEEP_S:-60}"
STALE_S=180
mkdir -p "$QDIR"/{pending,running,done,failed,locks,heartbeat,logs}

utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

cmd_add() {
  local lines=() line slug url rest name tmp
  if [ "${1:-}" = "--file" ]; then mapfile -t lines < "${2:?file}"; else lines=("$@"); fi
  [ "${#lines[@]}" -gt 0 ] || { echo "usage: run-queue.sh add \"slug|url|Business Name|flags\""; return 2; }
  for line in "${lines[@]}"; do
    IFS='|' read -r slug url rest <<< "$line"
    slug="${slug// }"
    [ -z "$slug" ] && continue
    [[ "$slug" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "REJECTED bad slug: $line"; return 2; }
    [[ "$url" =~ ^(https?|file):// ]] || { echo "REJECTED bad url: $line"; return 2; }
    name="$(date -u +%Y%m%dT%H%M%S.%NZ)-$$-$slug.job"
    tmp="$QDIR/pending/.tmp.$name"
    printf '%s\nqueued_utc=%s\n' "$line" "$(utc)" > "$tmp"
    mv "$tmp" "$QDIR/pending/$name"          # appears in pending/ whole, never half-written
    echo "QUEUED $slug $name"
  done
}

# ---- worker ---------------------------------------------------------------------------------------
W=""; WPID=""; STATE="starting"; CUR_SLUG=""; CUR_JOB=""; CUR_LOG=""; CUR_START=""; NOTE=""; STOP=0
write_hb() {
  local now el="" stage="" tmp
  now=$(date -u +%s)
  [ -n "$CUR_START" ] && el=$(awk -v a="$now" -v b="$CUR_START" 'BEGIN{printf "%.1f", (a-b)/60}')
  if [ -z "$CUR_LOG" ] && [ -n "$CUR_SLUG" ] && [ -L "$CASE_ROOT/$CUR_SLUG/run1.log" ] &&
     [ "$(stat -c %Y "$CASE_ROOT/$CUR_SLUG/run1.log")" -ge "${CUR_START:-0}" ]; then
    CUR_LOG="$CASE_ROOT/$CUR_SLUG/$(readlink "$CASE_ROOT/$CUR_SLUG/run1.log")"   # this run's log (run_site made the link)
  fi
  [ -n "$CUR_LOG" ] && [ -f "$CUR_LOG" ] && stage=$(tail -c 200000 "$CUR_LOG" | grep -a '^=== \[' | tail -1)
  tmp="$QDIR/heartbeat/.$W.txt.$BASHPID"
  printf 'utc=%s\nworker=%s pid=%s\nstate=%s\nslug=%s\njob=%s\nelapsed_min=%s\nstage=%s\nrun_log=%s\nnote=%s\n' \
    "$(utc)" "$W" "$WPID" "$STATE" "$CUR_SLUG" "$CUR_JOB" "$el" "$stage" "$CUR_LOG" "$NOTE" > "$tmp"
  mv -f "$tmp" "$QDIR/heartbeat/$W.txt"
}
# The heartbeat loop runs in a child, so it reads the worker's state from this small file.
save_state() {
  printf 'STATE=%q\nCUR_SLUG=%q\nCUR_JOB=%q\nCUR_LOG=%q\nCUR_START=%q\nNOTE=%q\n' \
    "$STATE" "$CUR_SLUG" "$CUR_JOB" "$CUR_LOG" "$CUR_START" "$NOTE" > "$QDIR/heartbeat/.$W.state.tmp"
  mv -f "$QDIR/heartbeat/.$W.state.tmp" "$QDIR/heartbeat/.$W.state"
  write_hb
}
nap() { sleep "$1" & local p=$!; wait "$p"; kill "$p" 2>&-; }   # a TERM to the worker ends the nap at once

worker() {
  W="$1"; WPID=$BASHPID
  local stay="$2" f b line rc claimed slug any hbpid
  save_state
  ( while kill -0 "$WPID" 2>&-; do
      [ -f "$QDIR/heartbeat/.$W.state" ] && . "$QDIR/heartbeat/.$W.state"
      [ "$STATE" = exited ] && break
      write_hb; sleep "$HB_S"
    done ) &
  hbpid=$!
  trap 'STOP=1' TERM INT
  trap 'STATE=exited; NOTE=""; CUR_SLUG=""; CUR_JOB=""; CUR_LOG=""; CUR_START=""; save_state; kill "$hbpid" 2>&-' EXIT
  echo "WORKER-START $W pid=$WPID $(utc)"
  while [ "$STOP" = 0 ]; do
    if ! disk_ok; then
      STATE="WAITING-DISK"; NOTE="free=$(free_gb)GB need=${AUDIT_MIN_FREE_GB}GB (AUDIT_MIN_FREE_GB); jobs stay pending; free disk space (the kit never deletes files)"
      save_state; echo "WAITING-DISK $(utc) $NOTE"; nap "$DISK_SLEEP"; continue
    fi
    NOTE=""; claimed=0; any=0
    for f in "$QDIR"/pending/*.job; do
      [ -e "$f" ] || continue
      any=1; b=$(basename "$f")
      slug=$(head -1 "$f" | cut -d'|' -f1); slug="${slug// }"
      # cheap probe first: skip a slug that is running now (run_site re-checks with the real lock)
      flock -n "$QDIR/locks/$slug.lock" true 2>&- || continue
      mv "$f" "$QDIR/running/$b" 2>&- || continue          # another worker won the rename
      line=$(head -1 "$QDIR/running/$b")
      CUR_SLUG="$slug"; CUR_JOB="$b"; CUR_START=$(date -u +%s); CUR_LOG=""; STATE="running"
      printf 'worker=%s pid=%s\nstart_utc=%s\nstart_epoch=%s\n' "$W" "$WPID" "$(utc)" "$CUR_START" >> "$QDIR/running/$b"
      save_state
      run_site "$line" < "$EMPTY"; rc=$?
      if [ "$RUN_SITE_LOCKED" = 1 ]; then                    # lost the race for the slug lock: give it back
        printf 'returned_utc=%s reason=slug-locked\n' "$(utc)" >> "$QDIR/running/$b"
        mv "$QDIR/running/$b" "$QDIR/pending/$b"
        CUR_SLUG=""; CUR_JOB=""; CUR_START=""; STATE="idle"; save_state
        continue
      fi
      printf 'end_utc=%s\nrc=%s\nrun_log=%s\n' "$(utc)" "$rc" "$RUN_SITE_LOG" >> "$QDIR/running/$b"
      if [ "$rc" -eq 0 ]; then mv "$QDIR/running/$b" "$QDIR/done/$b"; else mv "$QDIR/running/$b" "$QDIR/failed/$b"; fi
      echo "JOB-END $slug rc=$rc $(utc) log=$RUN_SITE_LOG"
      CUR_SLUG=""; CUR_JOB=""; CUR_START=""; CUR_LOG=""; STATE="idle"; save_state
      claimed=1; break                                       # re-scan: always take the oldest next
    done
    [ "$claimed" = 1 ] && continue
    [ "$any" = 0 ] && [ "$stay" = 0 ] && break               # drain mode: nothing left to do
    STATE="idle"; save_state; nap "$POLL"
  done
  echo "WORKER-EXIT $W $(utc)"
}

live_workers() {   # prints "name pid" for heartbeats whose worker is alive and not exited
  local h st pid
  for h in "$QDIR"/heartbeat/*.txt; do
    [ -e "$h" ] || continue
    st=$(sed -n 's/^state=//p' "$h"); pid=$(sed -n 's/^worker=.* pid=//p' "$h")
    [ "$st" != exited ] && [ -n "$pid" ] && kill -0 "$pid" 2>&- && echo "$(basename "$h" .txt) $pid"
  done
}

cmd_work() {
  local n="${QUEUE_WORKERS:-$DEFAULT_WORKERS}" stay=0 a i pids=() live
  for a in "$@"; do case "$a" in --stay) stay=1 ;; *[!0-9]*|'') echo "bad arg $a"; return 2 ;; *) n="$a" ;; esac; done
  [ "$n" -ge 1 ] || { echo "N must be >= 1"; return 2; }
  if [ "$n" -gt "$MAX_WORKERS" ] && [ "${QUEUE_WORKERS_UNSAFE:-0}" != 1 ]; then
    echo "NOTE: $n workers asked; using $MAX_WORKERS. More parallel audits load the audited sites and this (possibly shared) machine harder. Set QUEUE_WORKERS_UNSAFE=1 to allow more."
    n="$MAX_WORKERS"
  fi
  live=$(live_workers)
  [ -n "$live" ] && echo "NOTE: workers already running (total will be higher than $n):" && echo "$live"
  for i in $(seq 1 "$n"); do
    worker "w$i-$$" "$stay" < "$EMPTY" >> "$QDIR/logs/w$i-$$.log" 2>&1 &
    pids+=($!); echo "STARTED w$i-$$ pid=$! log=$QDIR/logs/w$i-$$.log"
  done
  trap 'kill -TERM "${pids[@]}" 2>&-' TERM INT
  for i in "${pids[@]}"; do while kill -0 "$i" 2>&-; do wait "$i"; done; done
  echo "ALL WORKERS EXITED $(utc)"
  cmd_status
}

cmd_status() {
  local c f now st el h age pid
  now=$(date -u +%s)
  printf 'pending=%s running=%s done=%s failed=%s\n' \
    "$(ls "$QDIR/pending" | grep -c '\.job$')" "$(ls "$QDIR/running" | grep -c '\.job$')" \
    "$(ls "$QDIR/done" | grep -c '\.job$')" "$(ls "$QDIR/failed" | grep -c '\.job$')"
  for f in "$QDIR"/running/*.job; do
    [ -e "$f" ] || continue
    st=$(sed -n 's/^start_epoch=//p' "$f" | tail -1); pid=$(sed -n 's/^worker=.* pid=//p' "$f" | tail -1)
    el=$(awk -v a="$now" -v b="${st:-$now}" 'BEGIN{printf "%.1f", (a-b)/60}')
    alive=dead; [ -n "$pid" ] && kill -0 "$pid" 2>&- && alive=alive
    echo "RUNNING $(head -1 "$f" | cut -d'|' -f1) elapsed=${el} min worker=$(sed -n 's/^worker=\([^ ]*\).*/\1/p' "$f" | tail -1) ($alive) job=$(basename "$f")"
  done
  for f in "$QDIR"/failed/*.job; do
    [ -e "$f" ] || continue
    echo "FAILED $(head -1 "$f" | cut -d'|' -f1) $(grep '^rc=' "$f" | tail -1) job=$(basename "$f")"
  done
  for h in "$QDIR"/heartbeat/*.txt; do
    [ -e "$h" ] || continue
    st=$(sed -n 's/^state=//p' "$h"); [ "$st" = exited ] && continue
    age=$(( now - $(stat -c %Y "$h") ))
    c="WORKER $(basename "$h" .txt) state=$st slug=$(sed -n 's/^slug=//p' "$h") hb_age=${age}s stage=$(sed -n 's/^stage=//p' "$h")"
    [ "$age" -gt "$STALE_S" ] && c="STALE $c (no heartbeat for > 3 min)"
    echo "$c"
  done
}

cmd_stop() {
  local w pid
  live_workers | while read -r w pid; do kill -TERM "$pid" && echo "TERM sent to $w pid=$pid (finishes its current job)"; done
}

cmd_requeue() {
  local b="${1:?job file name in running/}" f pid
  f="$QDIR/running/$(basename "$b")"; [ -f "$f" ] || { echo "no such running job: $b"; return 2; }
  pid=$(sed -n 's/^worker=.* pid=//p' "$f" | tail -1)
  [ -n "$pid" ] && kill -0 "$pid" 2>&- && { echo "worker pid $pid is alive; not requeued"; return 2; }
  printf 'requeued_utc=%s reason=worker-dead\n' "$(utc)" >> "$f"
  mv "$f" "$QDIR/pending/$(basename "$b")" && echo "REQUEUED $(basename "$b")"
}

case "${1:-}" in
  add) shift; cmd_add "$@" ;;
  work) shift; cmd_work "$@" ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  requeue) shift; cmd_requeue "$@" ;;
  *) sed -n '2,13p' "$HERE/run-queue.sh"; exit 2 ;;
esac

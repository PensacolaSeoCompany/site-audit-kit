# queue/ — runtime state of run-queue.sh (only this README is in git)

This is the default `QUEUE_DIR` (override it with the `QUEUE_DIR` env var). run-queue.sh creates the
folders below on first use.

One file per audit job. A job file's first line is `slug|url|Business Name|flags`; the queue appends
`queued_utc`, `worker`, `start_utc`, `end_utc`, `rc` and `run_log` lines as it moves.

| folder | meaning |
|---|---|
| pending/ | waiting; workers take the oldest first (file names start with the UTC queue time) |
| running/ | claimed by one worker (atomic rename from pending/) |
| done/ | finished with rc=0 |
| failed/ | finished with rc other than 0 (the rc is in the file) |
| locks/ | `<slug>.lock`, flock'd while that slug runs (shared with run-lane.sh) |
| heartbeat/ | `<worker>.txt`, rewritten every 30 s: UTC, state, slug, stage, note (WAITING-DISK reason) |
| logs/ | one log per worker: WORKER-START, LANE-DONE, JOB-END lines |

Commands: `bash run-queue.sh add|work|status|stop|requeue` (usage: run the script with no arguments).
Do not delete job files by hand while workers run; moving job files between folders is the design.
Run logs live in `$CASES_DIR/<slug>/run-<UTC>.log` (default `work/cases/<slug>/`).

Workers: `./run-queue.sh work` starts 1 worker by default (`QUEUE_WORKERS` or `work N` to change). The
maximum is 2, because bots often run on shared machines and every worker adds load to the audited sites
and to this machine. A higher number is clamped to 2 with a note, unless `QUEUE_WORKERS_UNSAFE=1` is set.

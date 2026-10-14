#!/usr/bin/env python3
"""Tests for run-queue.sh (the spool queue that replaced fixed lane files). Run: python3 tests/test_queue.py

Incident (2026-10-14 review): 3 fixed lane files left one lane working for 58 min with the CPU 91-92%
idle; one client site ran in two lanes at once; re-runs overwrote run1.log; no heartbeat, no exit code.

No real audit runs here. QUEUE_AUDIT_CMD points to a fake audit under tests/_out/queue/ that sleeps,
records its start/end time, and exits with the code written in its URL (".../fail7/" -> exit 7).
The URLs are file:// URLs, so the render-page picker never touches the network.
QUEUE_DIR, AUDIT_CASE_ROOT, KIT_WORK (empty stdin file, work folders) and REPORTS_DIR point into
tests/_out/queue/, so nothing outside it is written."""
import glob, os, warnings, shutil, signal, subprocess, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.environ.get('QUEUE_SCRIPT') or os.path.join(HERE, '..', 'run-queue.sh')   # QUEUE_SCRIPT: control runs only
OUT = os.path.join(HERE, '_out', 'queue')
RUN_ID = 'run-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}'

FAKE = r'''#!/usr/bin/env bash
# fake run-full-audit.sh: args = url slug name [render pages]
s=$(date +%s.%N)
echo "FAKE start $2"
echo "=== [1] Full crawl (fake) ==="
sleep "${FAKE_SLEEP:-1}"
e=$(date +%s.%N)
echo "$2 $s $e $$" >> "$FAKE_RECORD"
echo "FAKE end $2"
case "$1" in *fail*) c=${1##*fail}; exit "${c%%/*}";; esac
exit 0
'''


class Queue(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter('ignore', ResourceWarning)
        # one fresh folder per test run and test (nothing is deleted; a few KB per run)
        base = os.path.join(OUT, RUN_ID, self._testMethodName); os.makedirs(base)
        self.qdir, self.cases = os.path.join(base, 'q'), os.path.join(base, 'cases')
        os.makedirs(self.cases)
        self.fake = os.path.join(base, 'fake-audit.sh')
        open(self.fake, 'w').write(FAKE); os.chmod(self.fake, 0o755)
        self.record = os.path.join(base, 'record.txt')
        self.env = dict(os.environ, QUEUE_DIR=self.qdir, AUDIT_CASE_ROOT=self.cases,
                        KIT_WORK=os.path.join(base, 'work'), REPORTS_DIR=os.path.join(base, 'reports'),
                        QUEUE_AUDIT_CMD=self.fake, FAKE_RECORD=self.record, FAKE_SLEEP='1',
                        QUEUE_POLL_S='0.2', QUEUE_HEARTBEAT_S='1', AUDIT_MIN_FREE_GB='0')

    def q(self, *args, timeout=90, env=None):
        return subprocess.run(['bash', QUEUE, *args], env=env or self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def add(self, slug, fail=None):
        url = f'file:///nonexistent/{slug}/' + (f'fail{fail}/' if fail else '')
        r = self.q('add', f'{slug}|{url}|{slug.title()} LLC|')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def rows(self):
        return [l.split() for l in open(self.record).read().splitlines()] if os.path.exists(self.record) else []

    def jobs(self, state):
        return sorted(glob.glob(os.path.join(self.qdir, state, '*.job')))

    # (a) 2 workers + 6 jobs, every job runs exactly once
    def test_a_each_job_runs_exactly_once(self):
        slugs = [f'site{i}' for i in range(6)]
        for s in slugs: self.add(s)
        t0 = time.time()
        r = self.q('work', '2')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(x[0] for x in self.rows()), slugs)    # each slug once, none twice
        self.assertEqual(len(self.jobs('done')), 6)
        self.assertEqual(self.jobs('pending') + self.jobs('running') + self.jobs('failed'), [])
        self.assertLess(time.time() - t0, 6 * 1 + 2.5, 'two workers should run in parallel')
        for j in self.jobs('done'):
            body = open(j).read()
            for key in ('rc=0', 'start_utc=', 'end_utc=', 'worker=', 'run_log='): self.assertIn(key, body)

    # (b) two jobs with the same slug never overlap in time
    def test_b_same_slug_never_overlaps(self):
        self.add('dup'); self.add('dup'); self.add('other')
        r = self.q('work', '3')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dup = sorted((float(s), float(e)) for slug, s, e, _ in self.rows() if slug == 'dup')
        self.assertEqual(len(dup), 2, self.rows())
        self.assertLessEqual(dup[0][1], dup[1][0], f'same slug overlapped: {dup}')
        self.assertEqual(len(self.jobs('done')), 3)

    # (c) a failing job lands in failed/ with its exit code
    def test_c_failure_keeps_exit_code(self):
        self.add('bad', fail=7); self.add('good')
        r = self.q('work', '2')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        failed = self.jobs('failed')
        self.assertEqual(len(failed), 1)
        self.assertIn('rc=7', open(failed[0]).read())
        self.assertIn('bad', os.path.basename(failed[0]))
        self.assertEqual(len(self.jobs('done')), 1)

    # (d) a second run of the same slug does not overwrite the first run log
    def test_d_run_logs_never_overwritten(self):
        case = os.path.join(self.cases, 'twice'); os.makedirs(case)
        open(os.path.join(case, 'run1.log'), 'w').write('LEGACY RUN\n')   # a run1.log from before the queue
        self.add('twice')
        self.assertEqual(self.q('work', '1').returncode, 0)
        time.sleep(1.1)
        self.add('twice')
        self.assertEqual(self.q('work', '1').returncode, 0)
        logs = sorted(glob.glob(os.path.join(case, 'run-*.log')))
        texts = [open(p).read() for p in logs]
        self.assertEqual(sum('FAKE start twice' in t for t in texts), 2, logs)   # two separate run logs
        self.assertTrue(any('LEGACY RUN' in t for t in texts), 'the old run1.log content was lost')
        # run1.log = the newest run, and publish-run.sh's pick (newest run*.log by mtime) reads that run
        newest = max((p for p in logs if 'FAKE' in open(p).read()), key=os.path.getmtime)
        self.assertEqual(os.path.realpath(os.path.join(case, 'run1.log')), os.path.realpath(newest))
        pick = subprocess.run(['bash', '-c', f'ls -t "{case}"/run*.log | head -1'], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL).stdout.strip()
        self.assertEqual(open(pick).read(), open(newest).read())

    # extra: disk gate per job leaves the job in pending and says why in the heartbeat
    def test_e_disk_gate_waits_in_pending(self):
        self.add('diskwait')
        env = dict(self.env, AUDIT_MIN_FREE_GB='999999')
        p = subprocess.Popen(['bash', QUEUE, 'work', '1'], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            time.sleep(2.5)
            self.assertEqual(len(self.jobs('pending')), 1)
            self.assertEqual(self.rows(), [])
            hb = ''.join(open(f).read() for f in glob.glob(os.path.join(self.qdir, 'heartbeat', '*.txt')))
            self.assertIn('WAITING-DISK', hb)
            st = self.q('status')
            self.assertIn('pending=1', st.stdout)
        finally:
            os.killpg(p.pid, signal.SIGTERM)   # only the process group this test started
            p.wait(timeout=20)

    # extra: status prints counts and elapsed minutes of a running job
    def test_f_status_shows_running(self):
        self.add('slowone')
        env = dict(self.env, FAKE_SLEEP='4')
        p = subprocess.Popen(['bash', QUEUE, 'work', '1'], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            time.sleep(2)
            st = self.q('status').stdout
            self.assertIn('running=1', st)
            self.assertIn('slowone', st)
            self.assertIn('min', st)
            hb = ''.join(open(f).read() for f in glob.glob(os.path.join(self.qdir, 'heartbeat', '*.txt')))
            self.assertIn('stage==== [1] Full crawl (fake) ===', hb)   # stage read from the run log tail
            p.wait(timeout=30)
            self.assertIn('done=1', self.q('status').stdout)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM); p.wait(timeout=20)

    # portability (2026-10-14): the PROXY flag reads CRAWL_PROXY from the env; without it the job fails
    # with rc 78 and a one-line reason instead of running unproxied against a bot-walled site
    def test_g_proxy_flag_needs_crawl_proxy(self):
        r = self.q('add', 'walled|file:///nonexistent/walled/|Walled LLC|PROXY')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = dict(self.env); env.pop('CRAWL_PROXY', None)
        self.assertEqual(self.q('work', '1', env=env).returncode, 0)
        failed = self.jobs('failed')
        self.assertEqual(len(failed), 1)
        self.assertIn('rc=78', open(failed[0]).read())
        self.assertEqual(self.rows(), [], 'the audit ran without a proxy')
        log = os.path.join(self.cases, 'walled', 'run1.log')
        self.assertIn('CRAWL_PROXY', open(log).read())


if __name__ == '__main__':
    unittest.main(verbosity=2)

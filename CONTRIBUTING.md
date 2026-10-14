# Contributing

Short rules. Every one exists because breaking it once shipped a wrong report.

## Run the tests

    ./install.sh                         # once
    for t in tests/test_*.py; do python3 "$t" || echo "FAILED: $t"; done

Each test file runs on its own (`python3 tests/test_publish_gate.py`). Tests need no network and write only
under `tests/_out/` (rebuilt on every run). Real crawl data and real client HTML never go in `tests/`:
build a small synthetic fixture (`example.com`, `example.org`, `test.example`).

## Watch it fail first

A test or a gate you never saw fail proves nothing. For every fix:

1. Write the test against the OLD code and run it. It must fail, for the reason you expect.
2. Fix the code. Run the test again. It must pass.
3. Say both results in your pull request ("red on main: X; green after: Y").

If the test passes before your fix, it tests nothing. Make it sharper until it fails.
A control that can silently test nothing (an empty fixture, a check that never runs, a regex that
matches nothing) is the most common way this goes wrong.

## The rules file

The rules file is the list of text that must never appear in a published report: `report-rules.json`
(the older name `owner-rules.json` is still read; `KIT_RULES` points at another file). (confirm: final
file name) `publish-run.sh` reads it and refuses (exit 3) to publish a page that breaks a rule;
`./audit.sh` prints the same matches as warnings.

- `page_forbidden`: refused anywhere in the page.
- `finding_forbidden`: refused only in finding text (titles, walk-through steps, details).
- Each rule: `id`, `since`, `why`, `re` (case-insensitive Python regex).

Add a test in `tests/test_publish_gate.py` for every new rule: one page that must be refused and one
near-miss page that must still publish.

## Add a check

1. Put the check in `audit-checks.py` as a new numbered section (`# N. Name`), printing a clear
   marker line in the log. Or, for a whole new stage, add a script and a `step` + `DONE[key]` in
   `run-full-audit.sh` so the completion gate can see whether it ran.
2. Turn its log output into findings in `checks-to-findings.py`. Each finding is
   `{"severity", "title", "detail", "urls"}`; severity is one of critical, high, medium, low, note.
3. Decide what the check says when it **could not** measure (blocked page, timeout, challenge page,
   client-rendered shell). That is "could not check" or "not measured", never a zero and never "broken".
4. Write a synthetic fixture that triggers it and one that must not trigger it (the false-positive
   case). Watch both fail before the code exists.
5. Document it: one card in `docs/03-CHECKS-AND-FALSE-POSITIVES.md` (what it finds, how it misfires,
   how to verify), and an entry in `docs/KNOWN-PITFALLS.md` if it guards a real failure.

Never add a check that reports title or meta description LENGTH. Never report a 403/429/bot wall as a
broken page.

## Code rules

- Find paths from the script's own location (`KIT_HOME`); write only under `KIT_WORK`, `reports/`
  or `tests/_out/`. Source `kit-paths.sh` from shell scripts.
- Never delete files to free disk or to clean up a run. Stop and report instead.
- No secrets, server IPs, personal paths or client names in code, tests, docs or commit messages.
- A network call must time out. A child process must get an empty stdin (`$EMPTY_STDIN`).
- Kill only processes you started, by PID. Leave no Chrome running.

## Pull requests

One change per pull request. Say what failed before, what the test is, and what you ran.

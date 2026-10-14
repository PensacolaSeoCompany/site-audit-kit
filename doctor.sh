#!/usr/bin/env bash
# doctor.sh: print the kit's version table (OS, python, node, npm, puppeteer-core, lighthouse, chrome, disk)
# with OK/FAIL per row. Changes nothing. Same as ./install.sh --check. Exit 0 all OK, 1 a row FAILED.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" --check "$@"

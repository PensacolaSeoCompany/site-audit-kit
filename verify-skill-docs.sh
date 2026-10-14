#!/usr/bin/env bash
# Fails if the CODE has checks/stages the SKILL doc does not describe.
# Makes no network requests (compares local files only).
# EXISTS BECAUSE: on 2026-07-31 the skill doc was reported "updated" four times when it was not.
# Checks lived in audit-checks.py while SKILL.md described fewer, and "updated" was reported from the
# edit made rather than from the file's actual state. Verification must not depend on self-report.
# Usage: bash verify-skill-docs.sh   (SKILL_DOC overrides the doc path; default $KIT_HOME/SKILL.md)
KIT_HOME="${KIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
S="${SKILL_DOC:-$KIT_HOME/SKILL.md}"; [ -f "$S" ] || [ -n "${SKILL_DOC:-}" ] || S="$KIT_HOME/skill/SKILL.md"
C="$KIT_HOME/audit-checks.py"
O="$KIT_HOME/run-full-audit.sh"
for f in "$S" "$C" "$O"; do [ -f "$f" ] || { echo "❌ missing $f"; exit 2; }; done
MISS=0
echo "=== CHECKS in code vs documented in SKILL.md ==="
for n in $(grep -oE '^\s+# [0-9]+\. [A-Z]' "$C" | grep -oE '[0-9]+'); do
  if grep -qE "\*\*$n\.|Check $n\b|Checks [0-9]+-[0-9]+" "$S"; then
    printf "  check %-3s ✓\n" "$n"
  else
    printf "  check %-3s ✗ NOT DOCUMENTED\n" "$n"; MISS=$((MISS+1))
  fi
done
echo "=== ORCHESTRATOR stages vs documented ==="
for st in $(grep -oE '^step [0-9]+[ab]?' "$O" | awk '{print $2}'); do
  # match the canonical STAGE TABLE row (| 6a | …), or prose mentions. The first version only
  # checked prose and reported all 6 table rows as missing — a gate that cries wolf trains you to
  # ignore it, which is worse than no gate.
  if grep -qE "^\| \*{0,2}$st\*{0,2} \||stage $st\b|Stage $st\b|step $st\b" "$S"; then
    printf "  stage %-4s ✓\n" "$st"
  else
    printf "  stage %-4s ✗ NOT DOCUMENTED\n" "$st"; MISS=$((MISS+1))
  fi
done
echo
if [ "$MISS" -gt 0 ]; then
  echo "❌ SKILL DOCS OUT OF DATE — $MISS check(s) exist in code but are undocumented."
  echo "   Do NOT report the skill doc as updated until this prints clean."
  exit 1
fi
echo "✅ every check in code is documented in SKILL.md"

#!/usr/bin/env bash
# Yank all GLOSTAT v1.x releases from PyPI (reversible action).
#
# Requirements:
#   - PYPI_TOKEN env var set to a valid PyPI API token with project scope
#     (https://pypi.org/manage/account/token/)
#
# Effect:
#   - `pip install glostat` (unpinned) will avoid these versions
#   - `pip install glostat==1.4.1` (pinned) still works — files remain on PyPI
#   - Reversible via PyPI web UI: Manage Project → Releases → Unyank
#
# To DELETE (irreversible): use PyPI web UI individually per release after
# v2.0.0 is verified stable for ~30 days. PyPI does not expose programmatic
# deletion to avoid accidents.

set -euo pipefail

if [[ -z "${PYPI_TOKEN:-}" ]]; then
  echo "ERROR: PYPI_TOKEN env var not set" >&2
  echo "Create one at https://pypi.org/manage/account/token/ (project-scoped)" >&2
  exit 1
fi

PROJECT="glostat"
REASON="Licensing cleanup — superseded by v2.0.0. Past versions contained derivative-work references that have been removed in v2.0.0."

VERSIONS=(
  "1.3.0" "1.3.1"
  "1.4.0" "1.4.1"
  "1.5.0"
  "1.6.0" "1.6.1" "1.6.2"
  "1.7.0"
  "1.8.0"
  "1.9.0" "1.9.1"
)

for v in "${VERSIONS[@]}"; do
  echo ">>> Yanking ${PROJECT}==${v}"
  curl -sS -X POST \
    -H "Authorization: token ${PYPI_TOKEN}" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "yanked=true" \
    --data-urlencode "yanked_reason=${REASON}" \
    "https://pypi.org/manage/project/${PROJECT}/release/${v}/" \
    && echo "    OK" \
    || echo "    FAILED (may need web UI fallback)"
  sleep 1
done

echo
echo "Verify with: curl -s https://pypi.org/pypi/${PROJECT}/json | jq '.releases | to_entries | map({version: .key, yanked: .value[0].yanked}) | sort_by(.version)'"

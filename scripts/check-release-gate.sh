#!/bin/bash
#
# Run the release drift gate on a commit of osism/release
#
# Checks that the OSISM components pinned by the given commit of this
# repository fit together: the catalog plugins of src/check-drift.py read
# every repository at the version that commit pins (latest/ and
# src/drift-allowlist.yml are read from the commit, never from the working
# tree) and the gate fails on a finding that would break the release, e.g.
# a role that python-osism calls but no pinned playbook repository provides.
# Allowlisted findings do not block. scripts/create-tags.sh runs it on the
# commit it tags; run it by hand on origin/main once the pins of a release
# round are merged.
#
# Usage: ./scripts/check-release-gate.sh <sha>
#
# Exits with 0 if there is no blocking finding, 1 on a blocking finding and
# 2 if an input could not be read (e.g. a missing pin or tag).
#
# Wrapper for src/check-drift.py: uv provisions its dependencies from
# requirements.txt; plain python3 is the fallback and requires them to be
# installed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_DRIFT_PY="$REPO_ROOT/src/check-drift.py"

if [ $# -ne 1 ]; then
    echo "Usage: $0 <sha>" >&2
    exit 2
fi

cd "$REPO_ROOT"

if command -v uv >/dev/null 2>&1; then
    exec uv run -q --no-project --with-requirements requirements.txt \
        python3 "$CHECK_DRIFT_PY" --group catalog --release-gate "$1"
else
    exec python3 "$CHECK_DRIFT_PY" --group catalog --release-gate "$1"
fi

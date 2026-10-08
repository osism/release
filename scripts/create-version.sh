#!/bin/bash
#
# Create a release version from the current latest/ state
#
# Usage: ./scripts/create-version.sh <version>
# Example: ./scripts/create-version.sh 10.0.0
#
# Wrapper for src/create-version.py: uv provisions its dependencies
# (GitPython, PyYAML) from the inline script metadata (PEP 723); plain
# python3 is the fallback and requires them to be installed. The helper
# reads latest/base.yml and the git tags of this repository, so it is
# always run from the repository root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CREATE_VERSION_PY="$REPO_ROOT/src/create-version.py"

if [ $# -eq 0 ]; then
    echo "Error: Version parameter required" >&2
    echo "Usage: $0 <version>" >&2
    echo "Example: $0 10.0.0" >&2
    exit 1
fi

cd "$REPO_ROOT"

if command -v uv >/dev/null 2>&1; then
    exec uv run -q "$CREATE_VERSION_PY" "$@"
else
    exec python3 "$CREATE_VERSION_PY" "$@"
fi

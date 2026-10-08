#!/bin/bash
#
# Create a release version from the current latest/ state
#
# Freezes latest/ into <version>/base.yml (src/create-version.py), commits
# it on a branch named after the version and opens a pull request via gh,
# like the "Prepare <version> release" PRs of earlier releases. Run it on
# an up-to-date main; with --no-pr only the version directory is created.
#
# Usage: ./scripts/create-version.sh [--no-pr] <version>
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

DO_PR=true
VERSION=""

usage() {
    echo "Usage: $0 [--no-pr] <version>"
    echo ""
    echo "Options:"
    echo "  -n, --no-pr   Only create the version directory, do not commit or open a pull request"
    echo "  -h, --help    Show this help"
    echo ""
    echo "Example: $0 10.0.0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -n|--no-pr)
            DO_PR=false
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Error: Unknown option $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [ -n "$VERSION" ]; then
                echo "Error: Only one version can be given" >&2
                usage >&2
                exit 1
            fi
            VERSION="$1"
            shift
            ;;
    esac
done

if [ -z "$VERSION" ]; then
    echo "Error: Version parameter required" >&2
    usage >&2
    exit 1
fi

cd "$REPO_ROOT"

# Check the preconditions of the pull request before anything is written,
# so that a failing check never leaves a version directory behind
if [ "$DO_PR" = true ]; then
    if ! command -v gh >/dev/null 2>&1; then
        echo "Error: The GitHub CLI (gh) is required to open the pull request (use --no-pr to skip it)" >&2
        exit 1
    fi

    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    if [ "$CURRENT_BRANCH" != "main" ]; then
        echo "Error: A release version is created from main, but the current branch is $CURRENT_BRANCH" >&2
        exit 1
    fi

    if git show-ref --verify --quiet "refs/heads/$VERSION"; then
        echo "Error: Branch $VERSION already exists" >&2
        exit 1
    fi
fi

if command -v uv >/dev/null 2>&1; then
    uv run -q "$CREATE_VERSION_PY" "$VERSION"
else
    python3 "$CREATE_VERSION_PY" "$VERSION"
fi

if [ "$DO_PR" = false ]; then
    exit 0
fi

# A core image version without a matching git tag is written as FIXME;
# such a base.yml must not be proposed as a release
if grep -q "FIXME" "$VERSION/base.yml"; then
    echo "" >&2
    echo "Error: $VERSION/base.yml contains FIXME values, not committing it" >&2
    echo "Fix the missing versions, then commit $VERSION/base.yml and open the pull request manually" >&2
    exit 1
fi

TITLE="Prepare $VERSION release"

echo ""
git switch -c "$VERSION"
git add "$VERSION/base.yml"
# The pathspec keeps unrelated staged changes out of the commit;
# -s adds Signed-off-by from the git config identity
git commit -s -m "$TITLE" -- "$VERSION/base.yml"

echo ""
echo "Pushing $VERSION and creating pull request..."
git push -u origin "$VERSION"

gh pr create \
    --title "$TITLE" \
    --body "Adds \`$VERSION/base.yml\`, frozen from the current \`latest/\` state with \`scripts/create-version.sh\`."

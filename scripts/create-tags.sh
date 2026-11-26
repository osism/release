#!/bin/bash

# Script to create and push git tags for OSISM projects
# Usage: ./create-tags.sh [--allow NAME]... [--skip-check-versions]
#                         [--skip-drift-gate] v0.20250920.0
#
# The tags are only created on the pushed state of main: the script fetches
# origin and stops unless the current branch is main and HEAD equals
# origin/main, because the component builds check the tags out from origin.
# The commit HEAD points to at that moment is the one that is checked and
# tagged, also if the local branch moves while the script runs.
#
# Before any tag is created, scripts/check-versions.sh checks the commit to
# be tagged (latest/ and etc/ of that commit, extracted with git archive,
# not the working tree). If it fails, also because a version could not be
# determined, no tag is created. Pins of the images built from these tags
# whose newest published version is the version being tagged count as
# current, so moving tags after a first build is not stopped; all other pins
# have to be current, the component repositories must not have untagged
# changes, and every directly pinned component must carry <version> (or a
# newer tag), also if it has no changes since its previous tag. --allow NAME (repeatable) is passed to the check and accepts the
# rows of one pin, symlink or repository that are intentionally not current;
# --skip-check-versions skips the check altogether.
#
# Then scripts/check-release-gate.sh runs the release drift gate on the same
# commit, also with --skip-check-versions: it reads the OSISM repositories at
# the versions that commit pins and fails on a finding that breaks the
# release, e.g. a role that python-osism calls but no pinned playbook
# repository provides. If it fails, also because an input could not be read,
# no tag is created. --skip-drift-gate skips the gate.
#
# For every project the tag <project>-<version> is created on the checked
# commit and pushed to origin. If a tag already exists (locally and/or on the
# remote), the script asks whether to move it to the checked commit (the
# existing tag is deleted locally and on the remote and created again) or
# to ignore it (the existing tag is left untouched). This allows re-running
# the script after tags have been created on the wrong commit.

usage() {
    echo "Usage: $0 [--allow NAME]... [--skip-check-versions] [--skip-drift-gate] <version>"
    echo ""
    echo "Options:"
    echo "  --allow NAME            Pass --allow NAME to scripts/check-versions.sh:"
    echo "                          do not fail on this pin, symlink or repository"
    echo "  --skip-check-versions   Do not run scripts/check-versions.sh first"
    echo "  --skip-drift-gate       Do not run the release drift gate"
    echo "                          (scripts/check-release-gate.sh) first"
    echo "  -h, --help              Show this help"
    echo ""
    echo "Example: $0 v0.20250920.0"
}

SKIP_CHECK_VERSIONS=false
SKIP_DRIFT_GATE=false
ALLOW_ARGS=()
VERSION=""

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-check-versions)
            SKIP_CHECK_VERSIONS=true
            shift
            ;;
        --skip-drift-gate)
            SKIP_DRIFT_GATE=true
            shift
            ;;
        --allow)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                echo "Error: --allow requires a name" >&2
                usage >&2
                exit 1
            fi
            ALLOW_ARGS+=(--allow "$2")
            shift 2
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

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

if ! git fetch -q origin; then
    echo "Error: Could not fetch origin" >&2
    exit 1
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "Error: Tags are created on main, but the current branch is $CURRENT_BRANCH" >&2
    exit 1
fi

# The commit that is checked and tagged, captured once so that a local
# branch advance while the script runs cannot tag an unchecked commit
TARGET_SHA=$(git rev-parse HEAD)
TARGET_SHORT=$(git rev-parse --short "$TARGET_SHA")
if [ "$TARGET_SHA" != "$(git rev-parse origin/main)" ]; then
    echo "Error: HEAD ($TARGET_SHORT) is not origin/main ($(git rev-parse --short origin/main)): pull or push first" >&2
    exit 1
fi

if [ "$SKIP_CHECK_VERSIONS" = true ]; then
    echo "Skipping scripts/check-versions.sh (--skip-check-versions)"
else
    CHECK_DIR=$(mktemp -d)
    trap 'rm -rf "$CHECK_DIR"' EXIT
    if ! (set -o pipefail; git archive "$TARGET_SHA" latest etc | tar -x -C "$CHECK_DIR"); then
        echo "Error: Could not extract latest/ and etc/ of $TARGET_SHORT" >&2
        exit 1
    fi
    if ! "$REPO_ROOT/scripts/check-versions.sh" \
        --latest "$CHECK_DIR/latest" \
        --repositories "$CHECK_DIR/etc/changelog-repositories.yml" \
        --tagging "$VERSION" \
        --release-tag "$VERSION" \
        "${ALLOW_ARGS[@]}"; then
        echo "Error: scripts/check-versions.sh failed for $TARGET_SHORT, no tags created" >&2
        echo "Merge the missing updates and tag the components first, or use --allow NAME for a row that is intended" >&2
        exit 1
    fi
fi

if [ "$SKIP_DRIFT_GATE" = true ]; then
    echo "Skipping the release drift gate (--skip-drift-gate)"
else
    DRIFT_GATE_STATUS=0
    "$REPO_ROOT/scripts/check-release-gate.sh" "$TARGET_SHA" || DRIFT_GATE_STATUS=$?
    if [ "$DRIFT_GATE_STATUS" -eq 1 ]; then
        echo "Error: release drift gate failed for $TARGET_SHORT, no tags created" >&2
        echo "Fix the findings above, or re-run with --skip-drift-gate to release anyway" >&2
        exit 1
    elif [ "$DRIFT_GATE_STATUS" -ne 0 ]; then
        echo "Error: release drift gate could not run for $TARGET_SHORT, no tags created" >&2
        echo "Fix the source error above, or re-run with --skip-drift-gate to release anyway" >&2
        exit 1
    fi
fi

# List of projects
PROJECTS=(
    "kolla"
    "osism-kubernetes"
    "kolla-ansible"
    "ceph-ansible"
    "osism-ansible"
    "inventory-reconciler"
)

echo "Creating tags with version: $VERSION"
echo "Projects: ${PROJECTS[*]}"
echo "Target commit: $TARGET_SHA ($(git log -1 --format=%s "$TARGET_SHA"))"
echo

# Process each project
for project in "${PROJECTS[@]}"; do
    tag_name="${project}-${VERSION}"
    echo "Tag: $tag_name"

    # Check if tag already exists (locally or remote) and where it points to
    local_commit=""
    remote_commit=""

    local_commit=$(git rev-parse --verify --quiet "refs/tags/$tag_name^{commit}")
    remote_commit=$(git ls-remote --tags origin "refs/tags/$tag_name^{}" "refs/tags/$tag_name" | tail -n 1 | cut -f1)

    if [ -n "$local_commit" ] || [ -n "$remote_commit" ]; then
        echo "⚠️  Tag $tag_name already exists"
        if [ -n "$local_commit" ]; then
            echo "    local:  $local_commit"
        else
            echo "    local:  (missing)"
        fi
        if [ -n "$remote_commit" ]; then
            echo "    remote: $remote_commit"
        else
            echo "    remote: (missing)"
        fi

        if [ "$local_commit" = "$TARGET_SHA" ] && [ "$remote_commit" = "$TARGET_SHA" ]; then
            echo "✅ Tag $tag_name already points to the target commit, nothing to do"
            echo
            continue
        fi

        answer=""
        while [[ ! "$answer" =~ ^[MmIi]$ ]]; do
            read -r -p "Move tag $tag_name to the target commit or ignore it? [m]ove/[i]gnore: " answer
        done

        if [[ "$answer" =~ ^[Ii]$ ]]; then
            echo "Ignoring $tag_name, existing tag is left untouched"
            echo
            continue
        fi

        if [ -n "$local_commit" ]; then
            if git tag -d "$tag_name" >/dev/null; then
                echo "  Deleted local tag $tag_name"
            else
                echo "❌ Failed to delete local tag $tag_name"
                echo
                continue
            fi
        fi
        if [ -n "$remote_commit" ]; then
            if git push origin ":refs/tags/$tag_name"; then
                echo "  Deleted remote tag $tag_name"
            else
                echo "❌ Failed to delete remote tag $tag_name"
                echo
                continue
            fi
        fi
    fi

    # Create the tag
    if git tag "$tag_name" "$TARGET_SHA"; then
        echo "✅ Tag $tag_name created successfully"

        # Push the tag to origin
        if git push origin "$tag_name"; then
            echo "✅ Tag $tag_name pushed to origin"
        else
            echo "❌ Failed to push tag $tag_name to origin"
        fi
    else
        echo "❌ Failed to create tag $tag_name"
    fi

    echo
done

echo "Tag creation and push process completed."

#!/bin/bash

# Script to create and push git tags for OSISM projects
# Usage: ./create-tags.sh v0.20250920.0
#
# The tags are only created on the pushed state of main: the script fetches
# origin and stops unless the current branch is main and HEAD equals
# origin/main, because the component builds check the tags out from origin.
#
# For every project the tag <project>-<version> is created on the current
# HEAD and pushed to origin. If a tag already exists (locally and/or on the
# remote), the script asks whether to move it to the current HEAD (the
# existing tag is deleted locally and on the remote and created again) or
# to ignore it (the existing tag is left untouched). This allows re-running
# the script after tags have been created on the wrong commit.

# Check if version parameter is provided
if [ $# -eq 0 ]; then
    echo "Error: Version parameter required"
    echo "Usage: $0 <version>"
    echo "Example: $0 v0.20250920.0"
    exit 1
fi

VERSION="$1"

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

if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    echo "Error: HEAD ($(git rev-parse --short HEAD)) is not origin/main ($(git rev-parse --short origin/main)): pull or push first" >&2
    exit 1
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

HEAD_COMMIT=$(git rev-parse HEAD)

echo "Creating tags with version: $VERSION"
echo "Projects: ${PROJECTS[*]}"
echo "Target commit: $HEAD_COMMIT ($(git log -1 --format=%s HEAD))"
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

        if [ "$local_commit" = "$HEAD_COMMIT" ] && [ "$remote_commit" = "$HEAD_COMMIT" ]; then
            echo "✅ Tag $tag_name already points to the current HEAD, nothing to do"
            echo
            continue
        fi

        answer=""
        while [[ ! "$answer" =~ ^[MmIi]$ ]]; do
            read -r -p "Move tag $tag_name to the current HEAD or ignore it? [m]ove/[i]gnore: " answer
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
    if git tag "$tag_name"; then
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

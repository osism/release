#!/bin/bash

# Script to create and push git tags for OSISM projects
# Usage: ./create-tags.sh v0.20250920.0

# Check if version parameter is provided
if [ $# -eq 0 ]; then
    echo "Error: Version parameter required"
    echo "Usage: $0 <version>"
    echo "Example: $0 v0.20250920.0"
    exit 1
fi

VERSION="$1"

# List of projects
PROJECTS=(
    "osism-kubernetes"
    "kolla-ansible"
    "ceph-ansible"
    "osism-ansible"
    "inventory-reconciler"
)

echo "Creating tags with version: $VERSION"
echo "Projects: ${PROJECTS[@]}"
echo

# Process each project
for project in "${PROJECTS[@]}"; do
    tag_name="${project}-${VERSION}"
    echo "Creating tag: $tag_name"

    # Check if tag already exists (locally or remote)
    local_exists=false
    remote_exists=false

    if git rev-parse "refs/tags/$tag_name" >/dev/null 2>&1; then
        local_exists=true
    fi

    if git ls-remote --tags origin "refs/tags/$tag_name" | grep -q .; then
        remote_exists=true
    fi

    if $local_exists || $remote_exists; then
        echo "⚠️  Tag $tag_name already exists (local: $local_exists, remote: $remote_exists)"
        read -r -p "Delete and recreate tag $tag_name? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            if $local_exists; then
                git tag -d "$tag_name"
                echo "  Deleted local tag $tag_name"
            fi
            if $remote_exists; then
                git push origin ":refs/tags/$tag_name"
                echo "  Deleted remote tag $tag_name"
            fi
        else
            echo "Skipping $tag_name"
            echo
            continue
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

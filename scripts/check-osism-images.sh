#!/bin/bash
#
# Check the other images of a release build in the registry
#
# Checks the images that a tag in the container image repositories other
# than osism/container-images-kolla pushes (osism-ansible, osism-kubernetes,
# kolla-ansible, ceph-ansible, inventory-reconciler): the tag of every image
# has to point to a complete image (manifest, config and all layer blobs) in
# the registry that carries a cosign signature. The image has to be built
# from the current state of the release tag <image>-<tag> of this
# repository: its labels name the build (kolla-ansible: also the commit of
# the release tag and its OpenStack version, ceph-ansible: its Ceph release)
# and it was pushed after the commit of the release tag was made. Every image
# is printed with the digest of its tag. Nothing is changed in the registry;
# the exit code is 1 if a check fails.
#
# Usage: ./scripts/check-osism-images.sh [-r <registry>] <tag>
# Example: ./scripts/check-osism-images.sh v0.20261008.0
#
# Options:
#   -r, --registry   Harbor registry (default: osism.harbor.regio.digital)
#
# The release tags have to exist in this checkout (git fetch --tags).
#
# The osism project can be read anonymously; HARBOR_USERNAME and
# HARBOR_PASSWORD are used if they are set.
#
# Wrapper for src/check-osism-images.py: uv provisions its dependencies
# (requests, PyYAML) from the inline script metadata (PEP 723); plain
# python3 is the fallback and requires them to be installed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_OSISM_IMAGES_PY="$REPO_ROOT/src/check-osism-images.py"

if command -v uv >/dev/null 2>&1; then
    exec uv run -q "$CHECK_OSISM_IMAGES_PY" "$@"
else
    exec python3 "$CHECK_OSISM_IMAGES_PY" "$@"
fi

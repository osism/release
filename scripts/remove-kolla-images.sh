#!/bin/bash
#
# Remove the kolla images of a release build from the registry
#
# Needed when a tag was built in osism/container-images-kolla from the wrong
# state: the images of the build and its SBOM image have to be removed before
# the tag can be built again cleanly. The script reads the SBOM image
# kolla/release/<openstack version>/sbom:<tag>, asks for every image it lists
# whether to remove it, and finally asks whether to remove the SBOM image
# itself. Images that are already gone are skipped, so an interrupted run can
# simply be repeated.
#
# Usage: ./scripts/remove-kolla-images.sh [-n] [-o <openstack version>] [-r <registry>] <tag>
# Example: ./scripts/remove-kolla-images.sh v0.20261008.0
#
# Options:
#   -n, --dry-run             Only list the images, remove nothing
#   -o, --openstack-version   OpenStack version of the build (default: looked
#                             up in the registry)
#   -r, --registry            Harbor registry (default: osism.harbor.regio.digital)
#
# The Harbor credentials are read from HARBOR_USERNAME and HARBOR_PASSWORD
# and asked for if they are not set (not needed with --dry-run). The account
# needs the permission to delete artifacts in the kolla project.
#
# Wrapper for src/remove-kolla-images.py: uv provisions its dependencies
# (requests, PyYAML) from the inline script metadata (PEP 723); plain
# python3 is the fallback and requires them to be installed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOVE_KOLLA_IMAGES_PY="$REPO_ROOT/src/remove-kolla-images.py"

if command -v uv >/dev/null 2>&1; then
    exec uv run -q "$REMOVE_KOLLA_IMAGES_PY" "$@"
else
    exec python3 "$REMOVE_KOLLA_IMAGES_PY" "$@"
fi

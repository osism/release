#!/bin/bash
#
# Check the kolla images of a release build in the registry
#
# Reads the SBOM image kolla/release/<openstack version>/sbom:<tag> of a build
# of osism/container-images-kolla and checks for every image it lists that its
# tag points to a complete image (manifest, config and all layer blobs) in the
# registry, that the labels of the image name the build and its OpenStack
# version and that the image carries a cosign signature. Every image is
# printed with the digest of its tag. The SBOM image has to be signed as well.
# Nothing is changed in the registry; the exit code is 1 if a check fails.
#
# Usage: ./scripts/check-kolla-images.sh [-j <jobs>] [-o <openstack version>] [-r <registry>] <tag>
# Example: ./scripts/check-kolla-images.sh v0.20261008.0
#
# Options:
#   -j, --jobs                Images checked in parallel (default: 8)
#   -o, --openstack-version   OpenStack version of the build (default: looked
#                             up in the registry)
#   -r, --registry            Harbor registry (default: osism.harbor.regio.digital)
#
# The kolla project can be read anonymously; HARBOR_USERNAME and
# HARBOR_PASSWORD are used if they are set.
#
# Wrapper for src/check-kolla-images.py: uv provisions its dependencies
# (requests, PyYAML) from the inline script metadata (PEP 723); plain
# python3 is the fallback and requires them to be installed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_KOLLA_IMAGES_PY="$REPO_ROOT/src/check-kolla-images.py"

if command -v uv >/dev/null 2>&1; then
    exec uv run -q "$CHECK_KOLLA_IMAGES_PY" "$@"
else
    exec python3 "$CHECK_KOLLA_IMAGES_PY" "$@"
fi

#!/bin/bash
#
# Check that latest/ pins the newest OSISM component versions
#
# Renovate opens a pull request for every new version of an OSISM
# component, but nothing reminds of a pull request that was not merged.
# This script compares the OSISM versions pinned in latest/base.yml and in
# the Ceph and OpenStack files the symlinks of latest/ point to (defaults,
# generics, the playbooks, the osism.* collections, the osism package and
# the OSISM images) with the newest published versions, and the
# netbox-manager, openstack-image-manager and openstack-flavor-manager pins
# of the python-osism version in use with the newest releases on PyPI. For
# an outdated pin the open Renovate pull request is named, if there is one.
# Run it before creating the tags of a release (step 2 of the release
# process).
#
# Usage: ./scripts/check-versions.sh [-v]
#
# Options:
#   -v, --verbose   Also list the pins that are current
#
# Exits with 1 if a pin is outdated or its newest version could not be
# determined.
#
# Wrapper for src/check-versions.py: uv provisions its dependencies
# (requests, PyYAML) from the inline script metadata (PEP 723); plain
# python3 is the fallback and requires them to be installed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_VERSIONS_PY="$REPO_ROOT/src/check-versions.py"

cd "$REPO_ROOT"

if command -v uv >/dev/null 2>&1; then
    exec uv run -q "$CHECK_VERSIONS_PY" "$@"
else
    exec python3 "$CHECK_VERSIONS_PY" "$@"
fi

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "requests",
#     "pyyaml",
# ]
# ///
#
# Helper for scripts/check-osism-images.sh: checks that the images of a
# release build in the container image repositories other than
# osism/container-images-kolla are complete in the registry and are the
# images of the build.
#
# A tag v<version> in these repositories pushes the image
# <registry>/osism/<image>:<version>, built from the state of this repository
# at the tag <image>-v<version>. For every image the digest of the tag is
# looked up as a Harbor artifact and checked:
#
# - the manifest the registry serves for the tag has the digest
# - the config blob and every layer blob of the manifest exist
# - the label org.opencontainers.image.version names the build
# - the image was pushed after the commit of the tag <image>-v<version> of
#   this repository was made: an image pushed before it was built before the
#   tag was moved to this commit
# - kolla-ansible: the label de.osism.commit.release names the commit of the
#   tag, the label de.osism.release.openstack the OpenStack version of
#   latest/openstack.yml at the tag
# - ceph-ansible: the label de.osism.release.ceph names the Ceph release of
#   latest/ceph_ansible.yml at the tag
# - the digest carries a cosign signature
#
# Nothing is changed in the registry.

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

from kolla_registry import (
    DEFAULT_REGISTRY,
    Harbor,
    RegistryError,
    check_artifact,
    normalize_tag,
)
from release_tags import ReleaseError, parse_time, release_commit, release_series

PROJECT = "osism"
# The images of the tags that scripts/create-tags.sh creates, without kolla
IMAGES = [
    "osism-ansible",
    "osism-kubernetes",
    "kolla-ansible",
    "ceph-ansible",
    "inventory-reconciler",
]
VERSION_LABEL = "org.opencontainers.image.version"
COMMIT_LABEL = "de.osism.commit.release"
# Images that name the commit of this repository they were built from
COMMIT_IMAGES = ["kolla-ansible"]
# Image -> label of the series it is built for, the files in latest/ naming
# the series (the first one that exists at the tag) and their key. Release
# snapshots from before latest/ceph_ansible.yml existed name the Ceph release
# of ceph-ansible with latest/ceph.yml.
SERIES = {
    "kolla-ansible": (
        "de.osism.release.openstack",
        ["openstack.yml"],
        "openstack_version",
    ),
    "ceph-ansible": (
        "de.osism.release.ceph",
        ["ceph_ansible.yml", "ceph.yml"],
        "ceph_version",
    ),
}


def check_labels(config, expected):
    labels = (config.get("config") or {}).get("Labels") or {}
    return [
        f"the label {label} is {labels.get(label)}, not {value}"
        for label, value in expected.items()
        if labels.get(label) != value
    ]


def check_image(harbor, image, version):
    """The digest of the image and its problems; no digest if it is missing."""
    release_tag = f"{image}-v{version}"
    expected = {VERSION_LABEL: f"v{version}"}
    problems = []
    commit = None
    try:
        commit, committed = release_commit(release_tag)
        if image in COMMIT_IMAGES:
            expected[COMMIT_LABEL] = commit
        if image in SERIES:
            label, files, key = SERIES[image]
            expected[label] = release_series(release_tag, files, key)
    except ReleaseError as e:
        problems.append(str(e))

    artifact, image_problems, configs = check_artifact(harbor, image, version)
    problems += image_problems
    if artifact is None:
        return None, problems
    for config in configs:
        problems += check_labels(config, expected)
    if commit and parse_time(artifact["push_time"]) < committed:
        problems.append(
            f"pushed at {artifact['push_time']}, before the commit {commit[:7]} "
            f"of the tag {release_tag} ({committed.isoformat()}): not built "
            "from the current state of the tag"
        )
    return artifact["digest"], problems


def run(args):
    version = normalize_tag(args.tag)
    harbor = Harbor(
        args.registry,
        os.environ.get("HARBOR_USERNAME", ""),
        os.environ.get("HARBOR_PASSWORD", ""),
        project=PROJECT,
    )
    print(f"Images of v{version} in {args.registry}/{PROJECT}")
    print()

    def check_entry(image):
        try:
            return check_image(harbor, image, version)
        except (RegistryError, requests.RequestException) as e:
            return None, [str(e)]

    failed = 0
    with ThreadPoolExecutor(len(IMAGES)) as pool:
        results = pool.map(check_entry, IMAGES)
        for index, (image, (digest, problems)) in enumerate(zip(IMAGES, results), 1):
            prefix = f"[{index}/{len(IMAGES)}]"
            reference = f"{args.registry}/{PROJECT}/{image}:{version}"
            if digest:
                reference += f"@{digest}"
            if not problems:
                print(f"{prefix} {reference}: ok")
                continue
            failed += 1
            print(f"{prefix} {reference}: FAILED")
            for problem in problems:
                print(f"    {problem}")

    print()
    print(f"Images: {len(IMAGES) - failed} ok, {failed} failed")
    if failed:
        print(f"Check of the images of v{version} FAILED")
        return 1
    print(f"Check of the images of v{version} passed")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Check that the images of a release build of osism-ansible, "
            "osism-kubernetes, kolla-ansible, ceph-ansible and "
            "inventory-reconciler are in the registry with all their blobs, "
            "are built from the release tags of this repository and are signed"
        )
    )
    parser.add_argument("tag", help="Tag of the build, e.g. v0.20261008.0")
    parser.add_argument(
        "-r",
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"Harbor registry (default: {DEFAULT_REGISTRY})",
    )
    args = parser.parse_args()

    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

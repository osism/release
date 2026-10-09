#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "requests",
#     "pyyaml",
# ]
# ///
#
# Helper for scripts/check-kolla-images.sh: checks that every kolla image of
# a release build is complete in the registry and is the image of the build.
#
# The SBOM image kolla/release/<openstack version>/sbom:<tag> of the build
# lists its images by tag only. For every image the digest of its tag is
# looked up as a Harbor artifact and checked:
#
# - the manifest the registry serves for the tag has the digest
# - the config blob and every layer blob of the manifest exist
# - the labels de.osism.version and de.osism.release.openstack name the
#   build and its OpenStack version: image tags only carry the build date,
#   so a build of the same day moves them to its own images
# - the label de.osism.commit.release names the commit of the release tag
#   kolla-v<version> of this repository (a short hash)
# - the digest carries a cosign signature
# - the digest is the one of the SBOM entry, if the entry lists one
#
# The SBOM image itself has to carry a cosign signature as well.
# The OpenStack version is the one of latest/openstack.yml at the release
# tag, unless --openstack-version is given; a version that differs from the
# release tag fails the check. The release tag is read from the local
# checkout and has to point to the same commit as on origin
# (src/release_tags.py). Nothing is changed in the registry.

import argparse
import os
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor

import requests

from kolla_registry import (
    DEFAULT_REGISTRY,
    PROJECT,
    SBOM_REPOSITORY,
    Harbor,
    RegistryError,
    check_artifact,
    normalize_tag,
    parse_image,
    sbom_images,
    signed,
)
from release_tags import ReleaseError, release_commit, release_series

VERSION_LABEL = "de.osism.version"
OPENSTACK_LABEL = "de.osism.release.openstack"
COMMIT_LABEL = "de.osism.commit.release"
# The label is a short hash; shorter ones are not unique enough
MIN_COMMIT_LENGTH = 7
DEFAULT_JOBS = 8


def sbom_digests(sbom):
    """Image -> digest of the SBOM entries that list one."""
    return {
        entry["image"]: entry["digest"]
        for entry in sbom["images"]
        if entry.get("digest")
    }


def check_labels(config, tag, openstack_version, commit):
    labels = (config.get("config") or {}).get("Labels") or {}
    problems = []
    version = labels.get(VERSION_LABEL)
    if version is None or normalize_tag(version) != tag:
        problems.append(f"the label {VERSION_LABEL} is {version}, not v{tag}")
    if labels.get(OPENSTACK_LABEL) != openstack_version:
        problems.append(
            f"the label {OPENSTACK_LABEL} is {labels.get(OPENSTACK_LABEL)}, "
            f"not {openstack_version}"
        )
    label_commit = labels.get(COMMIT_LABEL) or ""
    if len(label_commit) < MIN_COMMIT_LENGTH or not commit.startswith(label_commit):
        problems.append(
            f"the label {COMMIT_LABEL} is {labels.get(COMMIT_LABEL)}, "
            f"not the commit {commit[:7]} of the release tag kolla-v{tag}"
        )
    return problems


def check_image(
    harbor, repository, image_tag, tag, openstack_version, sbom_digest, commit
):
    """The digest of the image and its problems; no digest if it is missing."""
    artifact, problems, configs = check_artifact(harbor, repository, image_tag)
    if artifact is None:
        return None, problems
    digest = artifact["digest"]
    for config in configs:
        problems += check_labels(config, tag, openstack_version, commit)
    if sbom_digest and sbom_digest != digest:
        problems.append(f"the SBOM lists the digest {sbom_digest}")
    return digest, problems


def run(args):
    tag = normalize_tag(args.tag)
    release_tag = f"kolla-v{tag}"
    commit, _ = release_commit(release_tag)
    release_openstack = release_series(
        release_tag, ["openstack.yml"], "openstack_version"
    )
    openstack_version = args.openstack_version or release_openstack
    harbor = Harbor(
        args.registry,
        os.environ.get("HARBOR_USERNAME", ""),
        os.environ.get("HARBOR_PASSWORD", ""),
    )
    print(f"Release tag: {release_tag} ({commit[:7]}, OpenStack {release_openstack})")
    series_ok = openstack_version == release_openstack
    if not series_ok:
        print(
            f"  FAILED: the release tag names OpenStack {release_openstack}, "
            f"not {openstack_version}"
        )

    sbom_repository = SBOM_REPOSITORY.format(openstack_version=openstack_version)
    sbom_ref = f"{args.registry}/{PROJECT}/{sbom_repository}:{tag}"
    sbom_artifact = harbor.artifact(sbom_repository, tag, with_accessory=True)
    if sbom_artifact is None:
        raise RegistryError(f"SBOM image {sbom_ref} does not exist")

    sbom = harbor.read_sbom(openstack_version, tag)
    images = sbom_images(sbom)
    digests = sbom_digests(sbom)
    print(f"SBOM: {sbom_ref}")
    print(f"  digest: {sbom_artifact['digest']}")
    print(f"  OpenStack version: {sbom.get('openstack_version')}")
    print(f"  created: {sbom.get('created')}")
    print(f"  images: {len(images)}")
    sbom_signed = signed(harbor, sbom_repository, sbom_artifact)
    if not sbom_signed:
        print("  FAILED: no cosign signature")
    print()

    def check_entry(image):
        try:
            repository, image_tag = parse_image(image, args.registry, openstack_version)
            return check_image(
                harbor,
                repository,
                image_tag,
                tag,
                openstack_version,
                digests.get(image),
                commit,
            )
        except (RegistryError, requests.RequestException) as e:
            return None, [str(e)]

    failed = 0
    pool = ThreadPoolExecutor(args.jobs)
    try:
        results = pool.map(check_entry, images)
        for index, (image, (digest, problems)) in enumerate(zip(images, results), 1):
            prefix = f"[{index}/{len(images)}]"
            reference = f"{image}@{digest}" if digest else image
            if not problems:
                print(f"{prefix} {reference}: ok")
                continue
            failed += 1
            print(f"{prefix} {reference}: FAILED")
            for problem in problems:
                print(f"    {problem}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    print()
    print(f"Images: {len(images) - failed} ok, {failed} failed")
    if failed or not sbom_signed or not series_ok:
        print(f"Check of {sbom_ref} FAILED")
        return 1
    print(f"Check of {sbom_ref} passed")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Check that every kolla image of a release build is in the registry "
            "with all its blobs, belongs to the build and is signed"
        )
    )
    parser.add_argument("tag", help="Tag of the build, e.g. v0.20261008.0")
    parser.add_argument(
        "-o",
        "--openstack-version",
        help="OpenStack version of the build (default: latest/openstack.yml at the release tag kolla-v<version>)",
    )
    parser.add_argument(
        "-r",
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"Harbor registry (default: {DEFAULT_REGISTRY})",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Images checked in parallel (default: {DEFAULT_JOBS})",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs has to be at least 1")

    try:
        return run(args)
    except (
        RegistryError,
        ReleaseError,
        requests.RequestException,
        tarfile.TarError,
    ) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

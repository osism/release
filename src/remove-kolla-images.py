#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "requests",
#     "pyyaml",
# ]
# ///
#
# Helper for scripts/remove-kolla-images.sh: removes the kolla images of a
# release build from the registry, so that a tag that was built wrongly can
# be built again cleanly.
#
# A release build of osism/container-images-kolla pushes its images to
# <registry>/kolla/release/<openstack version>/<image>:<version>.<date> and
# lists them in the SBOM image kolla/release/<openstack version>/sbom:<tag>
# (FROM scratch with a single /images.yml). The SBOM is read via the registry
# API, every image it lists is removed after an explicit confirmation, and
# the SBOM image itself last, so that an interrupted run can be repeated:
# images that are already gone are reported and skipped.
#
# The registry is a Harbor: an image is removed as a Harbor artifact, which
# removes its accessories (the cosign signature) along with it. If the
# artifact carries further tags, only the tag is removed. An image that is
# also listed in another SBOM of the same OpenStack version (image tags only
# carry the build date, so builds of the same day share them) is pointed out
# before the confirmation.

import argparse
import getpass
import os
import sys
import tarfile

import requests

from kolla_registry import (
    DEFAULT_REGISTRY,
    PROJECT,
    SBOM_REPOSITORY,
    Harbor,
    RegistryError,
    artifact_tags,
    find_openstack_version,
    normalize_tag,
    parse_image,
    sbom_images,
)


def confirm(question):
    """Ask until yes, no or quit is answered; end of input counts as quit."""
    while True:
        try:
            answer = input(f"{question} [yes/no/quit] ").strip().lower()
        except EOFError:
            print()
            return "quit"
        if answer in ("yes", "no", "quit"):
            return answer


def describe(artifact, tag):
    print(f"    digest: {artifact['digest']}")
    others = [t for t in artifact_tags(artifact) if t != tag]
    if others:
        print(f"    also tagged {', '.join(others)}: only the tag {tag} is removed")


def credentials(dry_run):
    username = os.environ.get("HARBOR_USERNAME", "")
    password = os.environ.get("HARBOR_PASSWORD", "")
    if dry_run:
        return username, password
    if not username:
        username = input("Harbor username: ").strip()
    if not password:
        password = getpass.getpass(f"Harbor password for {username}: ")
    if not username or not password:
        raise RegistryError("Removing images requires Harbor credentials")
    return username, password


def run(args):
    tag = normalize_tag(args.tag)
    harbor = Harbor(args.registry, *credentials(args.dry_run))

    openstack_version = args.openstack_version or find_openstack_version(harbor, tag)

    sbom_repository = SBOM_REPOSITORY.format(openstack_version=openstack_version)
    sbom_ref = f"{args.registry}/{PROJECT}/{sbom_repository}:{tag}"
    sbom_artifact = harbor.artifact(sbom_repository, tag)
    if sbom_artifact is None:
        raise RegistryError(f"SBOM image {sbom_ref} does not exist")

    sbom = harbor.read_sbom(openstack_version, tag)
    # Every image is validated before the first one is removed
    images = [
        (image, *parse_image(image, args.registry, openstack_version))
        for image in sbom_images(sbom)
    ]
    print(f"SBOM: {sbom_ref}")
    print(f"  OpenStack version: {sbom.get('openstack_version')}")
    print(f"  created: {sbom.get('created')}")
    print(f"  images: {len(images)}")
    print()

    print(f"Checking the other SBOMs of {sbom_repository} for shared images...")
    references = harbor.other_sbom_references(openstack_version, tag)
    print()

    removed, kept, missing = [], [], []
    for index, (image, repository, image_tag) in enumerate(images, 1):
        prefix = f"[{index}/{len(images)}] {image}"
        artifact = harbor.artifact(repository, image_tag)
        if artifact is None:
            print(f"{prefix}: not in the registry, skipped")
            missing.append(image)
            continue

        print(prefix)
        describe(artifact, image_tag)
        if image in references:
            print(
                "    WARNING: also listed in the SBOM(s) "
                f"{', '.join(sorted(references[image]))}, which break if it is removed"
            )
        if args.dry_run:
            kept.append(image)
            continue

        answer = confirm(f"Remove {image}?")
        if answer == "quit":
            print("Stopped, the remaining images and the SBOM image are kept")
            return 1
        if answer == "no":
            kept.append(image)
            continue
        harbor.remove(repository, image_tag, artifact)
        removed.append(image)
        print("    removed")

    print()
    if args.dry_run:
        print(
            f"Images: {len(kept)} in the registry, {len(missing)} not in the registry"
        )
    else:
        print(
            f"Images: {len(removed)} removed, {len(kept)} kept, "
            f"{len(missing)} not in the registry"
        )
    print()

    print(f"SBOM image: {sbom_ref}")
    describe(sbom_artifact, tag)
    if args.dry_run:
        print("Dry run, nothing removed")
        return 0
    if kept:
        print(
            f"    WARNING: {len(kept)} image(s) of this SBOM are kept, without the "
            "SBOM this script cannot find them anymore"
        )
    if confirm(f"Remove {sbom_ref}?") == "yes":
        harbor.remove(sbom_repository, tag, sbom_artifact)
        print("    removed")
    else:
        print("    kept")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Remove the kolla images of a release build and its SBOM image "
            "from the registry, with a confirmation for every image"
        )
    )
    parser.add_argument("tag", help="Tag of the build, e.g. v0.20261008.0")
    parser.add_argument(
        "-o",
        "--openstack-version",
        help="OpenStack version of the build (default: looked up in the registry)",
    )
    parser.add_argument(
        "-r",
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"Harbor registry (default: {DEFAULT_REGISTRY})",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Only list the images, remove nothing",
    )
    args = parser.parse_args()

    try:
        return run(args)
    except (RegistryError, requests.RequestException, tarfile.TarError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

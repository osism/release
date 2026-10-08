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
import io
import os
import re
import sys
import tarfile
from http.cookiejar import DefaultCookiePolicy
from urllib.parse import quote

import requests
import yaml

DEFAULT_REGISTRY = "osism.harbor.regio.digital"
PROJECT = "kolla"
SBOM_REPOSITORY = "release/{openstack_version}/sbom"
SBOM_FILE = "images.yml"
TIMEOUT = 60
PAGE_SIZE = 100

MANIFEST_TYPES = [
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
]
INDEX_TYPES = MANIFEST_TYPES[2:]


class RegistryError(Exception):
    pass


def check(response, what):
    if not response.ok:
        raise RegistryError(
            f"{what} failed with {response.status_code}: {response.text.strip()}"
        )
    return response


def artifact_tags(artifact):
    return [t["name"] for t in artifact.get("tags") or []]


class Harbor:
    def __init__(self, registry, username="", password=""):
        self.registry = registry
        self.session = requests.Session()
        if username:
            self.session.auth = (username, password)
        # Harbor answers with a session cookie and checks a CSRF token for
        # requests that send it back; with basic auth alone it does not.
        self.session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        self.tokens = {}

    def api_url(self, repository, reference=None, suffix=""):
        """Harbor API URL of a repository or an artifact.

        The repository name has to be URL-encoded twice when it contains
        slashes.
        """
        url = (
            f"https://{self.registry}/api/v2.0/projects/{PROJECT}/repositories/"
            f"{quote(quote(repository, safe=''), safe='')}"
        )
        if reference is not None:
            url += f"/artifacts/{quote(reference, safe=':')}"
        return url + suffix

    def api_list(self, url, params, what):
        page = 1
        while True:
            response = check(
                self.session.get(
                    url,
                    params={**params, "page": page, "page_size": PAGE_SIZE},
                    timeout=TIMEOUT,
                ),
                what,
            )
            items = response.json()
            yield from items
            if len(items) < PAGE_SIZE:
                return
            page += 1

    def artifact(self, repository, reference):
        """The artifact of a tag or digest, None if it does not exist."""
        response = self.session.get(
            self.api_url(repository, reference),
            params={"with_tag": "true"},
            timeout=TIMEOUT,
        )
        if response.status_code == 404:
            return None
        return check(response, f"Lookup of {repository}:{reference}").json()

    def remove(self, repository, tag, artifact):
        """Remove the artifact, or only the tag if the artifact carries others."""
        suffix = "" if artifact_tags(artifact) == [tag] else f"/tags/{tag}"
        check(
            self.session.delete(
                self.api_url(repository, artifact["digest"], suffix), timeout=TIMEOUT
            ),
            f"Removal of {repository}:{tag}",
        )

    def registry_get(self, repository, path, headers=None):
        """GET on the registry API with a pull token of Harbor's token service."""
        if repository not in self.tokens:
            response = check(
                self.session.get(
                    f"https://{self.registry}/service/token",
                    params={
                        "service": "harbor-registry",
                        "scope": f"repository:{PROJECT}/{repository}:pull",
                    },
                    timeout=TIMEOUT,
                ),
                f"Token request for {repository}",
            )
            self.tokens[repository] = response.json()["token"]
        # Not via the session: the token replaces the basic auth, and requests
        # drops it when a blob download is redirected to the storage backend.
        return check(
            requests.get(
                f"https://{self.registry}/v2/{PROJECT}/{repository}/{path}",
                headers={
                    "Authorization": f"Bearer {self.tokens[repository]}",
                    **(headers or {}),
                },
                timeout=TIMEOUT,
            ),
            f"Download of {repository} {path}",
        )

    def sbom_versions(self, tag):
        """The OpenStack versions with an SBOM image of the tag."""
        versions = []
        for repository in self.api_list(
            f"https://{self.registry}/api/v2.0/projects/{PROJECT}/repositories",
            {"q": "name=~sbom"},
            "Search for SBOM repositories",
        ):
            match = re.fullmatch(rf"{PROJECT}/release/([^/]+)/sbom", repository["name"])
            if match and self.artifact(
                SBOM_REPOSITORY.format(openstack_version=match.group(1)), tag
            ):
                versions.append(match.group(1))
        return sorted(versions)

    def read_sbom(self, openstack_version, tag):
        """The images.yml of the SBOM image of the tag."""
        repository = SBOM_REPOSITORY.format(openstack_version=openstack_version)
        accept = {"Accept": ", ".join(MANIFEST_TYPES)}
        manifest = self.registry_get(repository, f"manifests/{tag}", accept).json()
        if manifest.get("mediaType") in INDEX_TYPES:
            # Attestation manifests are listed with the platform unknown/unknown
            images = [
                m
                for m in manifest["manifests"]
                if m.get("platform", {}).get("os") != "unknown"
            ]
            if not images:
                raise RegistryError(
                    f"No image manifest in the index of {repository}:{tag}"
                )
            manifest = self.registry_get(
                repository, f"manifests/{images[0]['digest']}", accept
            ).json()

        for layer in manifest["layers"]:
            blob = self.registry_get(repository, f"blobs/{layer['digest']}")
            with tarfile.open(fileobj=io.BytesIO(blob.content)) as archive:
                for member in archive.getmembers():
                    if member.isfile() and os.path.normpath(member.name) == SBOM_FILE:
                        return yaml.safe_load(archive.extractfile(member).read())
        raise RegistryError(f"{SBOM_FILE} not found in {repository}:{tag}")

    def other_sbom_references(self, openstack_version, tag):
        """Image -> the other SBOM tags of the OpenStack version listing it."""
        repository = SBOM_REPOSITORY.format(openstack_version=openstack_version)
        references = {}
        for artifact in self.api_list(
            self.api_url(repository, suffix="/artifacts"),
            {"with_tag": "true"},
            f"Listing of {repository}",
        ):
            for other in artifact_tags(artifact):
                if other == tag:
                    continue
                try:
                    images = sbom_images(self.read_sbom(openstack_version, other))
                except (
                    RegistryError,
                    requests.RequestException,
                    tarfile.TarError,
                ) as e:
                    print(f"Warning: SBOM {other} not checked: {e}", file=sys.stderr)
                    continue
                for image in images:
                    references.setdefault(image, []).append(other)
        return references


def normalize_tag(tag):
    """The SBOM is tagged with the version without the leading v."""
    return tag[1:] if re.match(r"v\d", tag) else tag


def sbom_images(sbom):
    if not isinstance(sbom, dict) or not isinstance(sbom.get("images"), list):
        raise RegistryError(f"{SBOM_FILE} has no list of images")
    images = [
        entry.get("image") if isinstance(entry, dict) else None
        for entry in sbom["images"]
    ]
    if not all(isinstance(image, str) for image in images):
        raise RegistryError(f"{SBOM_FILE} lists an entry without an image")
    return images


def parse_image(image, registry, openstack_version):
    """Repository and tag of an image of the SBOM.

    Only images in the release namespace of the SBOM are accepted.
    """
    match = re.fullmatch(
        rf"{re.escape(registry)}/{PROJECT}/release/{re.escape(openstack_version)}"
        r"/([^/:@]+):([^/:@]+)",
        image,
    )
    if not match:
        raise RegistryError(
            f"{image} is not an image of {registry}/{PROJECT}/release/"
            f"{openstack_version}"
        )
    return f"release/{openstack_version}/{match.group(1)}", match.group(2)


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

    openstack_version = args.openstack_version
    if not openstack_version:
        versions = harbor.sbom_versions(tag)
        if not versions:
            raise RegistryError(
                f"No SBOM image {PROJECT}/release/<openstack version>/sbom:{tag} "
                f"on {args.registry}"
            )
        if len(versions) > 1:
            raise RegistryError(
                f"SBOM images of {tag} exist for the OpenStack versions "
                f"{', '.join(versions)}, choose one with --openstack-version"
            )
        openstack_version = versions[0]

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

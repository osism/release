# Access to the kolla release images of a Harbor registry, shared by
# src/remove-kolla-images.py and src/check-kolla-images.py.
#
# A release build of osism/container-images-kolla pushes its images to
# <registry>/kolla/release/<openstack version>/<image>:<version>.<date> and
# lists them in the SBOM image kolla/release/<openstack version>/sbom:<tag>
# (FROM scratch with a single /images.yml). The SBOM is read via the registry
# API, the images via the Harbor API and the registry API.

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


def image_manifests(manifest):
    """The image manifests of an index.

    Attestation manifests are listed with the platform unknown/unknown.
    """
    return [
        m for m in manifest["manifests"] if m.get("platform", {}).get("os") != "unknown"
    ]


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

    def artifact(self, repository, reference, with_accessory=False):
        """The artifact of a tag or digest, None if it does not exist."""
        params = {"with_tag": "true"}
        if with_accessory:
            params["with_accessory"] = "true"
        response = self.session.get(
            self.api_url(repository, reference), params=params, timeout=TIMEOUT
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

    def registry_request(self, method, repository, path, headers=None, **kwargs):
        """Request on the registry API with a pull token of Harbor's token service."""
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
        return requests.request(
            method,
            f"https://{self.registry}/v2/{PROJECT}/{repository}/{path}",
            headers={
                "Authorization": f"Bearer {self.tokens[repository]}",
                **(headers or {}),
            },
            timeout=TIMEOUT,
            **kwargs,
        )

    def registry_get(self, repository, path, headers=None):
        return check(
            self.registry_request("GET", repository, path, headers),
            f"Download of {repository} {path}",
        )

    def blob_exists(self, repository, digest):
        # The registry answers with a redirect to the storage backend, which
        # already shows that the blob exists
        response = self.registry_request(
            "HEAD", repository, f"blobs/{digest}", allow_redirects=False
        )
        if response.status_code == 404:
            return False
        if not response.is_redirect:
            check(response, f"Lookup of {repository} blob {digest}")
        return True

    def manifest(self, repository, reference):
        """The response with the manifest of a tag or digest."""
        return self.registry_get(
            repository,
            f"manifests/{reference}",
            {"Accept": ", ".join(MANIFEST_TYPES)},
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
        manifest = self.manifest(repository, tag).json()
        if manifest.get("mediaType") in INDEX_TYPES:
            images = image_manifests(manifest)
            if not images:
                raise RegistryError(
                    f"No image manifest in the index of {repository}:{tag}"
                )
            manifest = self.manifest(repository, images[0]["digest"]).json()

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


def find_openstack_version(harbor, tag):
    """The OpenStack version of the only SBOM image of the tag."""
    versions = harbor.sbom_versions(tag)
    if not versions:
        raise RegistryError(
            f"No SBOM image {PROJECT}/release/<openstack version>/sbom:{tag} "
            f"on {harbor.registry}"
        )
    if len(versions) > 1:
        raise RegistryError(
            f"SBOM images of {tag} exist for the OpenStack versions "
            f"{', '.join(versions)}, choose one with --openstack-version"
        )
    return versions[0]


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

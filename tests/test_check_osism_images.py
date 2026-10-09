import argparse
import hashlib
import importlib.util
import json
import pathlib

import pytest
import responses

import release_tags

# check-osism-images.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "check-osism-images.py"
_spec = importlib.util.spec_from_file_location("check_osism_images", _SRC)
coi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coi)

REGISTRY = "harbor.example.com"
API = f"https://{REGISTRY}/api/v2.0/projects/osism/repositories"
V2 = f"https://{REGISTRY}/v2/osism"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
VERSION = "0.20261008.0"
COMMITTED = "2026-10-08T12:00:00+00:00"
PUSHED = "2026-10-08T12:30:00.123Z"
SIGNATURE = [{"type": "signature.cosign", "digest": "sha256:sig"}]


def release_files(ceph_file="ceph_ansible.yml"):
    """latest/ of a release snapshot: OpenStack 2026.1, Ceph reef."""
    return {
        "latest/openstack-2026.1.yml": "openstack_version: '2026.1'\n",
        "latest/ceph-reef.yml": "ceph_version: reef\n",
        "latest/openstack.yml": ("symlink", "openstack-2026.1.yml"),
        f"latest/{ceph_file}": ("symlink", "ceph-reef.yml"),
    }


def tag_images(release_repo, images=coi.IMAGES):
    for image in images:
        release_repo.tag(f"{image}-v{VERSION}")


@pytest.fixture(autouse=True)
def tags_root(release_repo, monkeypatch):
    monkeypatch.setattr(release_tags, "REPO_ROOT", release_repo.checkout)


@pytest.fixture
def commit(release_repo):
    commit = release_repo.commit(release_files(), date=COMMITTED)
    tag_images(release_repo)
    return commit


def labels(image, commit, **overrides):
    result = {"org.opencontainers.image.version": f"v{VERSION}"}
    if image == "kolla-ansible":
        result["de.osism.commit.release"] = commit
        result["de.osism.release.openstack"] = "2026.1"
    if image == "ceph-ansible":
        result["de.osism.release.ceph"] = "reef"
    return {**result, **overrides}


def add_image(image, labels, accessories=SIGNATURE, missing=(), pushed=PUSHED):
    """An image in the registry, returns its digest."""
    config = f"sha256:config-{image}"
    layers = [f"sha256:{image}-1", f"sha256:{image}-2"]
    content = json.dumps(
        {
            "mediaType": DOCKER_MANIFEST,
            "config": {"digest": config},
            "layers": [{"digest": layer} for layer in layers],
        }
    ).encode()
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    for blob in [config, *layers]:
        responses.head(
            f"{V2}/{image}/blobs/{blob}", status=404 if blob in missing else 200
        )
    responses.get(f"{V2}/{image}/blobs/{config}", json={"config": {"Labels": labels}})
    responses.get(
        f"{API}/{image}/artifacts/{VERSION}",
        json={
            "digest": digest,
            "tags": [{"name": VERSION}],
            "accessories": accessories,
            "push_time": pushed,
        },
    )
    responses.get(f"{V2}/{image}/manifests/{VERSION}", body=content)
    return digest


def args(**kwargs):
    return argparse.Namespace(**{"tag": f"v{VERSION}", "registry": REGISTRY, **kwargs})


@pytest.fixture(autouse=True)
def token():
    responses.get(f"https://{REGISTRY}/service/token", json={"token": "t"})


@responses.activate
def test_all_images_ok(commit, capsys):
    digests = {image: add_image(image, labels(image, commit)) for image in coi.IMAGES}
    assert coi.run(args()) == 0
    out = capsys.readouterr().out
    for index, image in enumerate(coi.IMAGES, 1):
        assert (
            f"[{index}/5] {REGISTRY}/osism/{image}:{VERSION}@{digests[image]}: ok"
            in out
        )
    assert "Images: 5 ok, 0 failed" in out
    # Nothing is changed in the registry
    assert all(c.request.method in ("GET", "HEAD") for c in responses.calls)


@responses.activate
def test_problems_are_reported(commit, capsys):
    responses.get(f"{API}/osism-ansible/artifacts/{VERSION}", status=404)
    add_image(
        "osism-kubernetes",
        labels(
            "osism-kubernetes", commit, **{"org.opencontainers.image.version": "v1"}
        ),
    )
    add_image(
        "kolla-ansible",
        labels(
            "kolla-ansible",
            commit,
            **{
                "de.osism.commit.release": "deb956e",
                "de.osism.release.openstack": "2025.1",
            },
        ),
        pushed="2026-10-08T10:25:46.781Z",
    )
    add_image(
        "ceph-ansible",
        labels("ceph-ansible", commit),
        accessories=[],
        missing=("sha256:ceph-ansible-2",),
    )
    add_image("inventory-reconciler", labels("inventory-reconciler", commit))
    assert coi.run(args()) == 1
    out = capsys.readouterr().out
    assert f"osism-ansible:{VERSION}: FAILED\n    not in the registry" in out
    assert f"the label org.opencontainers.image.version is v1, not v{VERSION}" in out
    assert f"the label de.osism.commit.release is deb956e, not {commit}" in out
    assert "the label de.osism.release.openstack is 2025.1, not 2026.1" in out
    assert (
        f"pushed at 2026-10-08T10:25:46.781Z, before the commit {commit[:7]} of "
        f"the tag kolla-ansible-v{VERSION} (2026-10-08T12:00:00+00:00)" in out
    )
    assert "the blob sha256:ceph-ansible-2 is missing" in out
    assert "no cosign signature" in out
    assert "Images: 1 ok, 4 failed" in out


@responses.activate
def test_missing_release_tag(release_repo, capsys):
    commit = release_repo.commit(release_files(), date=COMMITTED)
    tag_images(release_repo, coi.IMAGES[:-1])
    for image in coi.IMAGES:
        add_image(image, labels(image, commit))
    assert coi.run(args()) == 1
    out = capsys.readouterr().out
    assert (
        f"inventory-reconciler:{VERSION}@sha256:" in out
        and f"the tag inventory-reconciler-v{VERSION} does not exist" in out
    )
    assert "Images: 4 ok, 1 failed" in out


def test_ceph_release_of_old_snapshots(release_repo):
    release_repo.commit(release_files(ceph_file="ceph.yml"), date=COMMITTED)
    tag_images(release_repo)
    label, files, key = coi.SERIES["ceph-ansible"]
    assert coi.release_series(f"ceph-ansible-v{VERSION}", files, key) == "reef"
    label, files, key = coi.SERIES["kolla-ansible"]
    assert coi.release_series(f"kolla-ansible-v{VERSION}", files, key) == "2026.1"
    with pytest.raises(coi.ReleaseError):
        coi.release_series(f"kolla-ansible-v{VERSION}", ["missing.yml"], key)

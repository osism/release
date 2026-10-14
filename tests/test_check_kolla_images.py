import argparse
import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile

import pytest
import responses
import yaml

import release_tags
from kolla_tag_inputs import InputError, Inputs

# check-kolla-images.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "check-kolla-images.py"
_spec = importlib.util.spec_from_file_location("check_kolla_images", _SRC)
cki = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cki)

REGISTRY = "harbor.example.com"
API = f"https://{REGISTRY}/api/v2.0/projects/kolla/repositories"
V2 = f"https://{REGISTRY}/v2/kolla"
PREFIX = f"{REGISTRY}/kolla/release/2025.1"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
LABELS = {
    "de.osism.version": "v0.20261008.0",
    "de.osism.release.openstack": "2025.1",
}
SIGNATURE = [{"type": "signature.cosign", "digest": "sha256:sig"}]


def repo_url(repository):
    return f"{API}/{repository.replace('/', '%252F')}"


def digest_of(content):
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def sbom_blob(entries):
    data = yaml.safe_dump(
        {
            "openstack_version": "2025.1",
            "created": "2026-10-08T09:42:36+00:00",
            "images": entries,
        }
    ).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("images.yml")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def add_sbom(entries, accessories=SIGNATURE):
    responses.get(
        f"{repo_url('release/2025.1/sbom')}/artifacts/0.20261008.0",
        json={
            "digest": "sha256:sbom",
            "tags": [{"name": "0.20261008.0"}],
            "accessories": accessories,
        },
    )
    responses.get(
        f"{V2}/release/2025.1/sbom/manifests/0.20261008.0",
        json={"mediaType": DOCKER_MANIFEST, "layers": [{"digest": "sha256:layer"}]},
    )
    responses.get(
        f"{V2}/release/2025.1/sbom/blobs/sha256:layer",
        body=sbom_blob([e if isinstance(e, dict) else {"image": e} for e in entries]),
    )


def add_manifest(repository, name, labels=LABELS, missing=(), redirected=()):
    """An image manifest with its blobs, returns its content."""
    config = f"sha256:config-{name}"
    layers = [f"sha256:{name}-1", f"sha256:{name}-2"]
    content = json.dumps(
        {
            "mediaType": DOCKER_MANIFEST,
            "config": {"digest": config},
            "layers": [{"digest": layer} for layer in layers],
        }
    ).encode()
    for blob in [config, *layers]:
        if blob in missing:
            responses.head(f"{V2}/{repository}/blobs/{blob}", status=404)
        elif blob in redirected:
            responses.head(
                f"{V2}/{repository}/blobs/{blob}",
                status=307,
                headers={"Location": "https://storage.example.com/blob"},
            )
        else:
            responses.head(f"{V2}/{repository}/blobs/{blob}")
    responses.get(
        f"{V2}/{repository}/blobs/{config}", json={"config": {"Labels": labels}}
    )
    return content


def add_image(
    name,
    tag,
    accessories=SIGNATURE,
    served=None,
    **kwargs,
):
    """An image in the registry, returns its digest.

    served replaces the manifest the registry serves for the tag.
    """
    repository = f"release/2025.1/{name}"
    content = add_manifest(repository, name, **kwargs)
    digest = digest_of(content)
    responses.get(
        f"{repo_url(repository)}/artifacts/{tag}",
        json={"digest": digest, "tags": [{"name": tag}], "accessories": accessories},
    )
    responses.get(f"{V2}/{repository}/manifests/{tag}", body=served or content)
    return digest


def args(**kwargs):
    defaults = dict(
        tag="v0.20261008.0",
        openstack_version="2025.1",
        registry=REGISTRY,
        jobs=2,
    )
    return argparse.Namespace(**{**defaults, **kwargs})


RELEASE_FILES = {
    "latest/openstack-2025.1.yml": "openstack_version: '2025.1'\n",
    "latest/openstack.yml": ("symlink", "openstack-2025.1.yml"),
}


@pytest.fixture(autouse=True)
def release(release_repo, monkeypatch):
    """The release tag kolla-v0.20261008.0; the images name its commit."""
    monkeypatch.setattr(release_tags, "REPO_ROOT", release_repo.checkout)
    commit = release_repo.commit(RELEASE_FILES)
    release_repo.tag("kolla-v0.20261008.0")
    monkeypatch.setitem(LABELS, "de.osism.commit.release", commit[:7])
    return commit


@pytest.fixture(autouse=True)
def token():
    responses.get(f"https://{REGISTRY}/service/token", json={"token": "t"})


CHECK_TAGS = cki.check_tags


@pytest.fixture(autouse=True)
def no_tags(monkeypatch):
    """The registry tests do not run the tag resolution."""
    monkeypatch.setattr(cki, "check_tags", lambda args, tag, openstack_version: True)


@responses.activate
def test_all_images_ok(capsys):
    keystone = add_image("keystone", "26.0.1.20261008")
    nova = add_image("nova-api", "31.0.1.20261008", redirected=("sha256:nova-api-1",))
    add_sbom(
        [
            f"{PREFIX}/keystone:26.0.1.20261008",
            {"image": f"{PREFIX}/nova-api:31.0.1.20261008", "digest": nova},
        ]
    )
    assert cki.run(args()) == 0
    out = capsys.readouterr().out
    assert f"[1/2] {PREFIX}/keystone:26.0.1.20261008@{keystone}: ok" in out
    assert f"[2/2] {PREFIX}/nova-api:31.0.1.20261008@{nova}: ok" in out
    assert "Images: 2 ok, 0 failed" in out
    # Nothing is changed in the registry
    assert all(c.request.method in ("GET", "HEAD") for c in responses.calls)


@responses.activate
def test_problems_are_reported(capsys):
    responses.get(
        f"{repo_url('release/2025.1/gone')}/artifacts/1.0.0.20261008", status=404
    )
    add_image("keystone", "26.0.1.20261008", missing=("sha256:keystone-2",))
    add_image(
        "cron",
        "3.0.20261008",
        labels={**LABELS, "de.osism.version": "v0.20261008.1"},
    )
    add_image("nova-api", "31.0.1.20261008", accessories=[])
    add_image("glance-api", "30.0.1.20261008", served=b'{"layers": []}')
    add_image("heat-api", "24.0.1.20261008")
    add_sbom(
        [
            f"{PREFIX}/gone:1.0.0.20261008",
            f"{PREFIX}/keystone:26.0.1.20261008",
            f"{PREFIX}/cron:3.0.20261008",
            f"{PREFIX}/nova-api:31.0.1.20261008",
            f"{PREFIX}/glance-api:30.0.1.20261008",
            {"image": f"{PREFIX}/heat-api:24.0.1.20261008", "digest": "sha256:old"},
            f"{REGISTRY}/osism/osism:1.0.0",
        ]
    )
    assert cki.run(args()) == 1
    out = capsys.readouterr().out
    assert f"{PREFIX}/gone:1.0.0.20261008: FAILED\n    not in the registry" in out
    assert "the blob sha256:keystone-2 is missing" in out
    assert "the label de.osism.version is v0.20261008.1, not v0.20261008.0" in out
    assert "no cosign signature" in out
    assert "the manifest of 30.0.1.20261008 does not have the digest" in out
    assert "the SBOM lists the digest sha256:old" in out
    assert f"{REGISTRY}/osism/osism:1.0.0 is not an image of" in out
    assert "Images: 0 ok, 7 failed" in out


@responses.activate
def test_index_is_checked_with_its_attestation():
    repository = "release/2025.1/keystone"
    image = add_manifest(repository, "image")
    attestation = add_manifest(repository, "attestation", labels={})
    index = json.dumps(
        {
            "mediaType": OCI_INDEX,
            "manifests": [
                {"digest": digest_of(image), "platform": {"os": "linux"}},
                {"digest": digest_of(attestation), "platform": {"os": "unknown"}},
            ],
        }
    ).encode()
    responses.get(
        f"{repo_url(repository)}/artifacts/26.0.1.20261008",
        json={"digest": digest_of(index), "accessories": SIGNATURE},
    )
    responses.get(f"{V2}/{repository}/manifests/26.0.1.20261008", body=index)
    responses.get(f"{V2}/{repository}/manifests/{digest_of(image)}", body=image)
    responses.get(
        f"{V2}/{repository}/manifests/{digest_of(attestation)}", body=attestation
    )
    add_sbom([f"{PREFIX}/keystone:26.0.1.20261008"])
    assert cki.run(args()) == 0
    heads = [c.request.url for c in responses.calls if c.request.method == "HEAD"]
    assert f"{V2}/{repository}/blobs/sha256:attestation-1" in heads


@responses.activate
def test_sigstore_bundle_is_a_signature():
    repository = "release/2025.1/keystone"
    bundle = [{"type": "subject.accessory", "digest": "sha256:bundle"}]
    add_image("keystone", "26.0.1.20261008", accessories=bundle)
    responses.get(
        f"{V2}/{repository}/manifests/sha256:bundle",
        json={"artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json"},
    )
    add_sbom([f"{PREFIX}/keystone:26.0.1.20261008"], accessories=bundle)
    responses.get(
        f"{V2}/release/2025.1/sbom/manifests/sha256:bundle",
        json={"artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json"},
    )
    assert cki.run(args()) == 0


@responses.activate
def test_unsigned_sbom_fails(capsys):
    add_image("keystone", "26.0.1.20261008")
    add_sbom(
        [f"{PREFIX}/keystone:26.0.1.20261008"],
        accessories=[{"type": "subject.accessory", "digest": "sha256:sbom-spdx"}],
    )
    responses.get(
        f"{V2}/release/2025.1/sbom/manifests/sha256:sbom-spdx",
        json={"artifactType": "application/spdx+json"},
    )
    assert cki.run(args()) == 1
    out = capsys.readouterr().out
    assert "  FAILED: no cosign signature" in out
    assert "Images: 1 ok, 0 failed" in out


@responses.activate
def test_missing_sbom_aborts():
    responses.get(
        f"{repo_url('release/2025.1/sbom')}/artifacts/0.20261008.0", status=404
    )
    with pytest.raises(cki.RegistryError):
        cki.run(args())


@responses.activate
def test_openstack_version_from_the_release_tag(release, capsys):
    add_image("keystone", "26.0.1.20261008")
    add_sbom([f"{PREFIX}/keystone:26.0.1.20261008"])
    assert cki.run(args(openstack_version=None)) == 0
    out = capsys.readouterr().out
    assert f"Release tag: kolla-v0.20261008.0 ({release[:7]}, OpenStack 2025.1)" in out


@responses.activate
def test_commit_label_of_another_commit(release, capsys):
    add_image(
        "keystone",
        "26.0.1.20261008",
        labels={**LABELS, "de.osism.commit.release": "a5d59bd"},
    )
    add_image(
        "nova-api",
        "31.0.1.20261008",
        labels={**LABELS, "de.osism.commit.release": release[:4]},
    )
    add_image(
        "cron",
        "3.0.20261008",
        labels={k: v for k, v in LABELS.items() if k != "de.osism.commit.release"},
    )
    add_image(
        "heat-api",
        "24.0.1.20261008",
        labels={**LABELS, "de.osism.commit.release": release},
    )
    add_sbom(
        [
            f"{PREFIX}/keystone:26.0.1.20261008",
            f"{PREFIX}/nova-api:31.0.1.20261008",
            f"{PREFIX}/cron:3.0.20261008",
            f"{PREFIX}/heat-api:24.0.1.20261008",
        ]
    )
    assert cki.run(args()) == 1
    out = capsys.readouterr().out
    suffix = f"not the commit {release[:7]} of the release tag kolla-v0.20261008.0"
    assert f"the label de.osism.commit.release is a5d59bd, {suffix}" in out
    assert f"the label de.osism.commit.release is {release[:4]}, {suffix}" in out
    assert f"the label de.osism.commit.release is None, {suffix}" in out
    assert f"{PREFIX}/heat-api:24.0.1.20261008@" in out
    assert "Images: 1 ok, 3 failed" in out


@responses.activate
def test_openstack_version_differs_from_the_release_tag(
    release_repo, monkeypatch, capsys
):
    moved = release_repo.commit(
        {
            "latest/openstack-2026.1.yml": "openstack_version: '2026.1'\n",
            "latest/openstack.yml": ("symlink", "openstack-2026.1.yml"),
        }
    )
    release_repo.tag("kolla-v0.20261008.0")
    monkeypatch.setitem(LABELS, "de.osism.commit.release", moved[:7])
    add_image("keystone", "26.0.1.20261008")
    add_sbom([f"{PREFIX}/keystone:26.0.1.20261008"])
    assert cki.run(args(openstack_version="2025.1")) == 1
    out = capsys.readouterr().out
    assert "FAILED: the release tag names OpenStack 2026.1, not 2025.1" in out
    assert "Images: 1 ok, 0 failed" in out


@responses.activate
def test_build_for_another_series_than_the_tag(release_repo):
    release_repo.commit(
        {
            "latest/openstack-2026.1.yml": "openstack_version: '2026.1'\n",
            "latest/openstack.yml": ("symlink", "openstack-2026.1.yml"),
        }
    )
    release_repo.tag("kolla-v0.20261008.0")
    responses.get(
        f"{repo_url('release/2026.1/sbom')}/artifacts/0.20261008.0", status=404
    )
    with pytest.raises(cki.RegistryError, match="release/2026.1/sbom"):
        cki.run(args(openstack_version=None))


def test_missing_release_tag():
    with pytest.raises(cki.ReleaseError, match="kolla-v0.20261009.0"):
        cki.run(args(tag="v0.20261009.0"))


def tag_args(**kwargs):
    defaults = dict(
        registry=REGISTRY,
        kolla_ansible_version=None,
        inventory_reconciler_version=None,
        kolla_ansible_ref=None,
        defaults_ref=None,
        predicted=False,
        base_dir=None,
        remote_fallback=False,
    )
    return argparse.Namespace(**{**defaults, **kwargs})


PROMETHEUS_ROLE = {
    "prometheus_server_image": "registry.osism.tech/kolla/release/prometheus-server",
    "prometheus_server_tag": "{{ prometheus_tag }}",
    "prometheus_server_image_full": "{{ prometheus_server_image }}:{{ prometheus_server_tag }}",
    "prometheus_valkey_exporter_image": "registry.osism.tech/kolla/release/prometheus-valkey-exporter",
    "prometheus_valkey_exporter_tag": "{{ prometheus_tag }}",
    "prometheus_valkey_exporter_image_full": "{{ prometheus_valkey_exporter_image }}:{{ prometheus_valkey_exporter_tag }}",
}


def tag_inputs():
    build = "harbor.example.com/kolla/release/2026.1"
    return Inputs(
        mode="predicted",
        series="2026.1",
        sbom={
            "images": [
                {"image": f"{build}/prometheus-server:3.5.5.20261008"},
                {"image": f"{build}/prometheus-valkey-exporter:1.82.0.20261008"},
                {"image": f"{build}/aodh-expirer:22.0.1.20261008"},
            ]
        },
        roles={"prometheus": PROMETHEUS_ROLE},
        overlay={},
        versions={"kolla_prometheus_version": "3.5.5.20261008"},
        defaults=[
            (
                "002-images-kolla.yml",
                {"prometheus_tag": "{{ kolla_prometheus_version }}"},
            )
        ],
        sources=[("SBOM", "kolla/release/2026.1/sbom:0.20261008.0 (the same version)")],
        notes=["a note"],
    )


def test_tag_section_reports_mismatches(monkeypatch, capsys):
    monkeypatch.setattr(cki, "gather", lambda options, registry: tag_inputs())
    assert CHECK_TAGS(tag_args(), "0.20261008.0", "2026.1") is False
    out = capsys.readouterr().out
    assert "Tag resolution (predicted):" in out
    assert (
        "  SBOM:          kolla/release/2026.1/sbom:0.20261008.0 (the same version)"
        in out
    )
    assert "  note: a note" in out
    assert "  prometheus-valkey-exporter:1.82.0.20261008: FAILED" in out
    assert (
        "    prometheus_valkey_exporter_image_full (prometheus) resolves to tag 3.5.5.20261008"
        in out
    )
    assert (
        '      prometheus_tag = "{{ kolla_prometheus_version }}" (defaults all/002-images-kolla.yml)'
        in out
    )
    assert "      kolla_prometheus_version = 3.5.5.20261008 (versions.yml)" in out
    assert "prometheus_valkey_exporter_image =" not in out  # only tag/version hops
    assert "  Not deployed by kolla-ansible: aodh-expirer" in out
    assert "Tags: 1 ok, 1 failed, 1 not deployed (1 of 2 image references)" in out


def test_tag_section_passes_without_findings(monkeypatch, capsys):
    inputs = tag_inputs()
    inputs.versions["kolla_prometheus_valkey_exporter_version"] = "1.82.0.20261008"
    inputs.roles["prometheus"] = {
        **PROMETHEUS_ROLE,
        "prometheus_valkey_exporter_tag": "{{ kolla_prometheus_valkey_exporter_version }}",
    }
    monkeypatch.setattr(cki, "gather", lambda options, registry: inputs)
    assert CHECK_TAGS(tag_args(), "0.20261008.0", "2026.1") is True
    assert (
        "Tags: 2 ok, 0 failed, 1 not deployed (0 of 2 image references)"
        in capsys.readouterr().out
    )


def test_tag_input_error_fails_the_section(monkeypatch, capsys):
    def fail(options, registry):
        raise InputError("the patch 0001-x.patch does not apply")

    monkeypatch.setattr(cki, "gather", fail)
    assert CHECK_TAGS(tag_args(), "0.20261008.0", "2026.1") is False
    out = capsys.readouterr().out
    assert "Tag resolution:" in out
    assert "  Error: the patch 0001-x.patch does not apply" in out


def test_tag_options_from_args(monkeypatch):
    seen = []
    monkeypatch.setattr(
        cki, "gather", lambda options, registry: seen.append(options) or tag_inputs()
    )
    CHECK_TAGS(tag_args(), "0.20261008.0", "2026.1")
    CHECK_TAGS(
        tag_args(
            kolla_ansible_version="v0.20261009.0",
            inventory_reconciler_version="0.20261007.0",
            defaults_ref="main",
            predicted=True,
            base_dir=["/src"],
        ),
        "0.20261008.0",
        "2026.1",
    )
    first, second = seen
    assert (
        first.version,
        first.inventory_reconciler_version,
        first.build,
        first.series,
    ) == (
        "0.20261008.0",
        "0.20261008.0",
        "0.20261008.0",
        "2026.1",
    )
    assert (second.version, second.inventory_reconciler_version) == (
        "0.20261009.0",
        "0.20261007.0",
    )
    assert (
        second.defaults_ref == "main"
        and second.predicted
        and second.base_dirs == ("/src",)
    )


@responses.activate
def test_tag_failure_fails_the_check(monkeypatch, capsys):
    monkeypatch.setattr(cki, "check_tags", lambda args, tag, openstack_version: False)
    keystone = add_image("keystone", "26.0.1.20261008")
    add_sbom([{"image": f"{PREFIX}/keystone:26.0.1.20261008", "digest": keystone}])
    assert cki.run(args()) == 1
    assert "FAILED" in capsys.readouterr().out

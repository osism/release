import argparse
import importlib.util
import io
import pathlib
import tarfile

import pytest
import responses
import yaml

# remove-kolla-images.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "remove-kolla-images.py"
_spec = importlib.util.spec_from_file_location("remove_kolla_images", _SRC)
rki = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rki)

REGISTRY = "harbor.example.com"
API = f"https://{REGISTRY}/api/v2.0/projects/kolla/repositories"
V2 = f"https://{REGISTRY}/v2/kolla"
PREFIX = f"{REGISTRY}/kolla/release/2025.1"


def repo_url(repository):
    return f"{API}/{repository.replace('/', '%252F')}"


def sbom_blob(images):
    data = yaml.safe_dump(
        {
            "openstack_version": "2025.1",
            "created": "2026-10-08T09:42:36+00:00",
            "images": [{"image": image} for image in images],
        }
    ).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("images.yml")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def add_sbom(tag, images):
    digest = f"sha256:layer{tag}"
    responses.get(
        f"{V2}/release/2025.1/sbom/manifests/{tag}",
        json={
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "layers": [{"digest": digest}],
        },
    )
    responses.get(f"{V2}/release/2025.1/sbom/blobs/{digest}", body=sbom_blob(images))


def add_artifact(repository, tag, digest, tags=None):
    responses.get(
        f"{repo_url(repository)}/artifacts/{tag}",
        json={"digest": digest, "tags": [{"name": t} for t in tags or [tag]]},
    )


def add_missing(repository, tag):
    responses.get(f"{repo_url(repository)}/artifacts/{tag}", status=404)


def answers(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def args(**kwargs):
    defaults = dict(
        tag="v0.20261008.0",
        openstack_version="2025.1",
        registry=REGISTRY,
        dry_run=False,
    )
    return argparse.Namespace(**{**defaults, **kwargs})


@pytest.fixture
def build(monkeypatch):
    """A build with one missing image, one image only tagged by the build, one
    image with a further tag and one image shared with another SBOM."""
    monkeypatch.setenv("HARBOR_USERNAME", "robot")
    monkeypatch.setenv("HARBOR_PASSWORD", "secret")
    responses.get(f"https://{REGISTRY}/service/token", json={"token": "t"})
    images = [
        f"{PREFIX}/gone:1.0.0.20261008",
        f"{PREFIX}/keystone:26.0.1.20261008",
        f"{PREFIX}/nova-api:31.0.1.20261008",
        f"{PREFIX}/cron:3.0.20261008",
    ]
    add_artifact("release/2025.1/sbom", "0.20261008.0", "sha256:sbom")
    add_sbom("0.20261008.0", images)
    responses.get(
        f"{repo_url('release/2025.1/sbom')}/artifacts",
        json=[
            {"digest": "sha256:sbom", "tags": [{"name": "0.20261008.0"}]},
            {"digest": "sha256:other", "tags": [{"name": "0.20261008.1"}]},
        ],
    )
    add_sbom("0.20261008.1", [f"{PREFIX}/cron:3.0.20261008"])
    add_missing("release/2025.1/gone", "1.0.0.20261008")
    add_artifact("release/2025.1/keystone", "26.0.1.20261008", "sha256:keystone")
    add_artifact(
        "release/2025.1/nova-api",
        "31.0.1.20261008",
        "sha256:nova",
        ["31.0.1.20261008", "pinned"],
    )
    add_artifact("release/2025.1/cron", "3.0.20261008", "sha256:cron")
    for url in (
        f"{repo_url('release/2025.1/keystone')}/artifacts/sha256:keystone",
        f"{repo_url('release/2025.1/nova-api')}/artifacts/sha256:nova/tags/31.0.1.20261008",
        f"{repo_url('release/2025.1/cron')}/artifacts/sha256:cron",
        f"{repo_url('release/2025.1/sbom')}/artifacts/sha256:sbom",
    ):
        responses.delete(url)


def deletes():
    return [c.request.url for c in responses.calls if c.request.method == "DELETE"]


def test_normalize_tag():
    assert rki.normalize_tag("v0.20261008.0") == "0.20261008.0"
    assert rki.normalize_tag("0.20261008.0") == "0.20261008.0"


def test_parse_image():
    assert rki.parse_image(
        f"{PREFIX}/nova-api:31.0.1.20261008", REGISTRY, "2025.1"
    ) == (
        "release/2025.1/nova-api",
        "31.0.1.20261008",
    )
    for image in (
        f"{REGISTRY}/kolla/nova-api:2025.1",
        f"{REGISTRY}/kolla/release/2024.2/nova-api:31.0.1.20261008",
        "other.example.com/kolla/release/2025.1/nova-api:31.0.1.20261008",
        f"{PREFIX}/nova-api",
    ):
        with pytest.raises(rki.RegistryError):
            rki.parse_image(image, REGISTRY, "2025.1")


@responses.activate
def test_removes_images_and_sbom(build, monkeypatch, capsys):
    answers(monkeypatch, "maybe", "yes", "yes", "yes", "yes")
    assert rki.run(args()) == 0
    assert deletes() == [
        f"{repo_url('release/2025.1/keystone')}/artifacts/sha256:keystone",
        f"{repo_url('release/2025.1/nova-api')}/artifacts/sha256:nova/tags/31.0.1.20261008",
        f"{repo_url('release/2025.1/cron')}/artifacts/sha256:cron",
        f"{repo_url('release/2025.1/sbom')}/artifacts/sha256:sbom",
    ]
    out = capsys.readouterr().out
    assert "gone:1.0.0.20261008: not in the registry, skipped" in out
    assert "also tagged pinned: only the tag 31.0.1.20261008 is removed" in out
    assert "also listed in the SBOM(s) 0.20261008.1" in out
    assert "Images: 3 removed, 0 kept, 1 not in the registry" in out


@responses.activate
def test_quit_keeps_the_sbom(build, monkeypatch):
    answers(monkeypatch, "yes", "quit")
    assert rki.run(args()) == 1
    assert deletes() == [
        f"{repo_url('release/2025.1/keystone')}/artifacts/sha256:keystone",
    ]


@responses.activate
def test_kept_images_are_pointed_out(build, monkeypatch, capsys):
    answers(monkeypatch, "no", "yes", "no", "no")
    assert rki.run(args()) == 0
    assert deletes() == [
        f"{repo_url('release/2025.1/nova-api')}/artifacts/sha256:nova/tags/31.0.1.20261008",
    ]
    assert "2 image(s) of this SBOM are kept" in capsys.readouterr().out


@responses.activate
def test_dry_run_asks_nothing(build, monkeypatch, capsys):
    monkeypatch.delenv("HARBOR_USERNAME")
    monkeypatch.delenv("HARBOR_PASSWORD")
    answers(monkeypatch)
    assert rki.run(args(dry_run=True)) == 0
    assert deletes() == []
    assert "Images: 3 in the registry, 1 not in the registry" in capsys.readouterr().out


@responses.activate
def test_openstack_version_is_looked_up(build, monkeypatch):
    responses.get(
        API,
        json=[
            {"name": "kolla/sbom"},
            {"name": "kolla/release/sbom"},
            {"name": "kolla/release/2024.2/sbom"},
            {"name": "kolla/release/2025.1/sbom"},
        ],
    )
    add_missing("release/2024.2/sbom", "0.20261008.0")
    answers(monkeypatch, "no", "no", "no", "no")
    assert rki.run(args(openstack_version=None)) == 0


@responses.activate
def test_foreign_image_aborts_before_removing(build, monkeypatch):
    responses.replace(
        responses.GET,
        f"{V2}/release/2025.1/sbom/blobs/sha256:layer0.20261008.0",
        body=sbom_blob(
            [f"{PREFIX}/keystone:26.0.1.20261008", f"{REGISTRY}/osism/osism:1.0.0"]
        ),
    )
    answers(monkeypatch)
    with pytest.raises(rki.RegistryError):
        rki.run(args())
    assert deletes() == []

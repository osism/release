import ast
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import responses

import kolla_tag_inputs
import release_tags
from kolla_registry import RegistryError
from kolla_tag_inputs import InputError, Options, gather

FIXTURES = Path(__file__).parent / "fixtures"
SERIES = "2026.1"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

PROMETHEUS_DEFAULTS = (
    "prometheus_marker: upstream\n"
    'prometheus_server_image: "{{ docker_image_url }}prometheus-server"\n'
)
PATCH = """--- a/ansible/roles/prometheus/defaults/main.yml
+++ b/ansible/roles/prometheus/defaults/main.yml
@@ -1 +1 @@
-prometheus_marker: upstream
+prometheus_marker: patched
"""
BAD_PATCH = """--- a/ansible/roles/prometheus/defaults/main.yml
+++ b/ansible/roles/prometheus/defaults/main.yml
@@ -1 +1 @@
-no_such_line: here
+no_such_line: there
"""
TEMPLATE = (
    'openstack_release: "{{ openstack_version }}"\n'
    'openstack_previous_release: "{{ openstack_previous_version }}"\n'
    'openstackclient: "{{ openstackclient_version }}"\n'
    "kolla_prometheus_version: \"{{ versions['prometheus']|default(openstack_version) }}\"\n"
    "template_marker: %s\n"
)
OPENSTACK_FILE = (
    "openstack_version: '2026.1'\n"
    "openstack_previous_version: '2025.2'\n"
    "ansible_core_version: '%s'\n"
    "docker_images:\n"
    "  openstackclient: 10.3.0\n"
)
SBOM = {
    "openstack_version": SERIES,
    "images": [{"image": "h/kolla/release/2026.1/prometheus-server:3.5.5.1"}],
    "versions": {"prometheus": "3.5.5.1"},
}
OTHER_SBOM = {**SBOM, "versions": {"prometheus": "9.9.9.9"}}


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_repo(path, snapshots):
    """A git repository with one commit per snapshot; returns the commits.

    snapshots: [(refs, files)]: each commit's tree is exactly files, and refs
    ("branch:<name>" or "tag:<name>") point to it.
    """
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "scratch")
    commits = []
    for refs, files in snapshots:
        for child in path.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for name, content in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        git(path, "add", "-A")
        git(path, "commit", "-q", "--allow-empty", "-m", "snapshot")
        commit = git(path, "rev-parse", "HEAD")
        commits.append(commit)
        for ref in refs:
            kind, name = ref.split(":", 1)
            git(path, "branch" if kind == "branch" else "tag", "-f", name, commit)
    return commits


def templates_files(marker, patch=PATCH):
    return {
        "files/src/templates/versions.yml.j2": TEMPLATE % marker,
        f"overlays/{SERIES}/kolla-ansible.yml": "overlay_marker: here\n",
        f"patches/{SERIES}/0001-marker.patch": patch,
    }


class FakeRegistry:
    def __init__(self, sboms=None, images=None):
        self.sboms = sboms or {}  # (series, reference) -> SBOM
        self.images = images or {}  # (image, version) -> labels

    def sbom(self, series, reference):
        if (series, reference) not in self.sboms:
            raise RegistryError(f"no SBOM {series} {reference}")
        return self.sboms[(series, reference)]

    def sbom_exists(self, series, reference):
        return (series, reference) in self.sboms

    def image(self, image, version):
        if (image, version) not in self.images:
            return None
        return f"sha256:{image}-{version}", self.images[(image, version)]


def sboms():
    return {
        (SERIES, "0.1.0"): SBOM,
        (SERIES, "0.2.0"): SBOM,
        (SERIES, "0.0.9"): OTHER_SBOM,
    }


def no_next(version):
    return None


@pytest.fixture
def world(tmp_path, release_repo, monkeypatch, request):
    """osism/release with its tags, and local clones of the three source repos."""
    ansible_core = getattr(request, "param", "2.18.18")
    monkeypatch.setattr(release_tags, "REPO_ROOT", release_repo.checkout)
    release = release_repo.commit(
        {
            f"latest/openstack-{SERIES}.yml": OPENSTACK_FILE % ansible_core,
            "latest/openstack.yml": ("symlink", f"openstack-{SERIES}.yml"),
            "latest/base.yml": "defaults_version: v0.20261005.0\n",
        }
    )
    for tag in (
        "kolla-ansible-v0.1.0",
        "inventory-reconciler-v0.1.0",
        "kolla-ansible-v0.2.0",
        "inventory-reconciler-v0.2.0",
    ):
        release_repo.tag(tag)
    base = tmp_path / "src"
    kolla_ansible = make_repo(
        base / "kolla-ansible",
        [
            (
                [f"branch:stable/{SERIES}"],
                {
                    "ansible/roles/prometheus/defaults/main.yml": PROMETHEUS_DEFAULTS,
                    "ansible/roles/empty/defaults/main.yml": "---\n",
                },
            )
        ],
    )
    templates = make_repo(
        base / "container-image-kolla-ansible",
        [
            (["tag:v0.1.0"], templates_files("a")),
            (["branch:main"], templates_files("b")),
        ],
    )
    defaults = make_repo(
        base / "defaults",
        [
            (
                ["tag:v0.20261005.0"],
                {
                    "all/001-common.yml": "common_marker: one\n",
                    "all/002-images-kolla.yml": "prometheus_tag: x\n",
                    "all/099-kolla.yml": "\n",
                },
            ),
            (["tag:v0.2"], {"all/001-common.yml": "common_marker: two\n"}),
        ],
    )
    return SimpleNamespace(
        base=base,
        release=release,
        kolla_ansible=kolla_ansible,
        templates=templates,
        defaults=defaults,
    )


def options(world, **kwargs):
    defaults = dict(
        version="0.1.0",
        inventory_reconciler_version="0.1.0",
        build="0.1.0",
        series=SERIES,
        base_dirs=(str(world.base),),
    )
    return Options(**{**defaults, **kwargs})


def test_predicted_reads_every_input_at_its_commit(world):
    inputs = gather(options(world), FakeRegistry(sboms()), read_next=no_next)
    assert inputs.mode == "predicted"
    assert inputs.series == SERIES
    assert inputs.sbom is SBOM
    assert inputs.roles["prometheus"]["prometheus_marker"] == "patched"
    assert inputs.roles["empty"] == {}
    assert inputs.versions == {
        "openstack_release": SERIES,
        "openstack_previous_release": "2025.2",
        "openstackclient": "10.3.0",
        "kolla_prometheus_version": "3.5.5.1",
        "template_marker": "a",
    }
    assert inputs.overlay == {"overlay_marker": "here"}
    assert [name for name, _ in inputs.defaults] == [
        "001-common.yml",
        "002-images-kolla.yml",
        "099-kolla.yml",
    ]
    assert dict(inputs.defaults)["099-kolla.yml"] == {}
    sources = dict(inputs.sources)
    assert sources["SBOM"] == "kolla/release/2026.1/sbom:0.1.0 (the same version)"
    assert world.kolla_ansible[0][:12] in sources["role defaults"]
    assert "1 patches" in sources["role defaults"]
    assert f"v0.1.0 @ {world.templates[0][:12]}" in sources["versions.yml"]
    assert world.release[:12] in sources["versions.yml"]
    assert f"v0.20261005.0 @ {world.defaults[0][:12]}" in sources["defaults"]
    assert inputs.notes == []


def test_templates_from_main_without_a_tag(world):
    inputs = gather(
        options(world, version="0.2.0", build="0.2.0"),
        FakeRegistry(sboms()),
        read_next=no_next,
    )
    assert inputs.versions["template_marker"] == "b"
    assert f"main @ {world.templates[1][:12]}" in dict(inputs.sources)["versions.yml"]


def test_kolla_ansible_ref_override(world):
    inputs = gather(
        options(world, kolla_ansible_ref="main"),
        FakeRegistry(sboms()),
        read_next=no_next,
    )
    assert inputs.versions["template_marker"] == "b"


def test_defaults_ref_override(world):
    inputs = gather(
        options(world, defaults_ref="v0.2"), FakeRegistry(sboms()), read_next=no_next
    )
    assert inputs.defaults == [("001-common.yml", {"common_marker": "two"})]
    assert "--defaults-ref" in dict(inputs.sources)["defaults"]


def test_next_file_selects_another_build(world):
    inputs = gather(
        options(world),
        FakeRegistry(sboms()),
        read_next=lambda version: {"openstack_sbom": "0.0.9"},
    )
    assert inputs.sbom is OTHER_SBOM
    assert inputs.versions["kolla_prometheus_version"] == "9.9.9.9"
    assert "next/kolla-ansible-v0.1.0.yml" in dict(inputs.sources)["SBOM"]
    assert "0.0.9" in inputs.notes[0]
    assert "run the script for 0.0.9" in inputs.notes[0]


def test_patch_that_does_not_apply_is_an_error(world):
    git(world.base / "container-image-kolla-ansible", "checkout", "-q", "v0.1.0")
    target = (
        world.base
        / "container-image-kolla-ansible"
        / f"patches/{SERIES}/0002-bad.patch"
    )
    target.write_text(BAD_PATCH)
    repo = world.base / "container-image-kolla-ansible"
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bad patch")
    git(repo, "tag", "-f", "v0.1.0")
    with pytest.raises(InputError, match="0002-bad.patch does not apply"):
        gather(options(world), FakeRegistry(sboms()), read_next=no_next)


def test_missing_patch_program_is_an_error(world, monkeypatch):
    monkeypatch.setattr(kolla_tag_inputs.shutil, "which", lambda name: None)
    with pytest.raises(InputError, match="patch program"):
        gather(options(world), FakeRegistry(sboms()), read_next=no_next)


@pytest.mark.parametrize("world", ["2.19.1"], indirect=True)
def test_ansible_core_other_than_2_18_is_refused(world):
    with pytest.raises(InputError, match="ansible-core 2.19.1"):
        gather(options(world), FakeRegistry(sboms()), read_next=no_next)


def test_missing_sbom_is_an_error(world):
    with pytest.raises(RegistryError):
        gather(options(world), FakeRegistry({}), read_next=no_next)


HARBOR = "harbor.example.com"
ARTIFACT = f"https://{HARBOR}/api/v2.0/projects/osism/repositories/kolla-ansible/artifacts/0.1.0"


def harbor_registry():
    from kolla_registry import Harbor

    return kolla_tag_inputs.Registry(Harbor(HARBOR), Harbor(HARBOR, project="osism"))


@responses.activate
def test_registry_reads_labels_by_digest_from_the_amd64_image():
    v2 = f"https://{HARBOR}/v2/osism/kolla-ansible"
    responses.get(f"https://{HARBOR}/service/token", json={"token": "t"})
    responses.get(
        ARTIFACT, json={"digest": "sha256:index", "tags": [{"name": "0.1.0"}]}
    )
    responses.get(
        f"{v2}/manifests/sha256:index",
        json={
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": "sha256:arm",
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
                {
                    "digest": "sha256:amd",
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
            ],
        },
    )
    responses.get(
        f"{v2}/manifests/sha256:amd",
        json={
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:config"},
            "layers": [],
        },
    )
    responses.get(
        f"{v2}/blobs/sha256:config",
        json={"config": {"Labels": {"de.osism.sbom": "0.1.0"}}},
    )
    found = harbor_registry().image("kolla-ansible", "0.1.0")
    assert found == ("sha256:index", {"de.osism.sbom": "0.1.0"})
    # read by the digest, so a tag moving meanwhile cannot change the labels
    assert not any(
        call.request.url.endswith("/manifests/0.1.0") for call in responses.calls
    )


@responses.activate
def test_registry_missing_image():
    responses.get(ARTIFACT, status=404)
    assert harbor_registry().image("kolla-ansible", "0.1.0") is None


def test_versions_context_matches_render_versions_py():
    tree = ast.parse((FIXTURES / "render-versions.py.txt").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "render"
        and node.args
        and isinstance(node.args[0], ast.Dict)
    ]
    first = min(calls, key=lambda node: node.lineno)
    assert {key.value for key in first.args[0].keys} == set(
        kolla_tag_inputs.VERSIONS_CONTEXT_KEYS
    )


SBOM_DIGEST = "sha256:" + "d" * 64


def labels(world, **overrides):
    """The labels of a kolla-ansible image built from the fixture sources."""
    values = {
        "de.osism.release.openstack": SERIES,
        "de.osism.commit.kolla_ansible": world.kolla_ansible[0],
        "de.osism.commit.release": world.release,
        "org.opencontainers.image.revision": world.templates[0],
        "de.osism.sbom": "0.1.0",
        "de.osism.sbom.digest": SBOM_DIGEST,
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def registry(world, kolla_ansible=None, reconciler=None, digest_served=True):
    """kolla-ansible and inventory-reconciler 0.1.0 with the given labels;
    reconciler=False leaves the inventory-reconciler image out."""
    sbom_map = sboms()
    if digest_served:
        sbom_map[(SERIES, SBOM_DIGEST)] = SBOM
    images = {
        ("kolla-ansible", "0.1.0"): (
            labels(world) if kolla_ansible is None else kolla_ansible
        )
    }
    if reconciler is not False:
        images[("inventory-reconciler", "0.1.0")] = (
            {"de.osism.commit.defaults": world.defaults[0]}
            if reconciler is None
            else reconciler
        )
    return FakeRegistry(sbom_map, images)


def test_recorded_reads_every_input_from_its_label(world):
    inputs = gather(options(world), registry(world), read_next=no_next)
    assert inputs.mode == "recorded"
    assert inputs.sbom is SBOM
    assert inputs.roles["prometheus"]["prometheus_marker"] == "patched"
    assert inputs.versions["template_marker"] == "a"
    assert inputs.overlay == {"overlay_marker": "here"}
    assert dict(inputs.defaults)["001-common.yml"] == {"common_marker": "one"}
    sources = dict(inputs.sources)
    assert sources["image"] == "osism/kolla-ansible:0.1.0@sha256:kolla-ansible-0.1.0"
    assert sources["SBOM"] == f"kolla/release/{SERIES}/sbom@{SBOM_DIGEST} (build 0.1.0)"
    assert world.kolla_ansible[0][:12] in sources["role defaults"]
    assert world.release[:12] in sources["versions.yml"]
    assert world.defaults[0][:12] in sources["defaults"]
    assert inputs.notes == []


def test_short_revision_label(world):
    # Images labelled before the baseline carry a short revision.
    short = labels(
        world, **{"org.opencontainers.image.revision": world.templates[0][:7]}
    )
    inputs = gather(
        options(world), registry(world, kolla_ansible=short), read_next=no_next
    )
    assert inputs.versions["template_marker"] == "a"


def test_image_built_before_the_label_baseline_points_to_predicted(world):
    old = labels(world, **{"de.osism.sbom": None, "de.osism.sbom.digest": None})
    with pytest.raises(InputError) as error:
        gather(options(world), registry(world, kolla_ansible=old), read_next=no_next)
    message = str(error.value)
    assert "de.osism.sbom, de.osism.sbom.digest" in message
    assert "osism/issues#1463" in message
    assert "--predicted" in message


def test_unknown_revision_is_an_error(world):
    bad = labels(world, **{"org.opencontainers.image.revision": "deadbee"})
    with pytest.raises(InputError, match="org.opencontainers.image.revision=deadbee"):
        gather(options(world), registry(world, kolla_ansible=bad), read_next=no_next)


def test_sbom_digest_no_longer_served_is_an_error(world):
    with pytest.raises(InputError, match="no longer serves"):
        gather(options(world), registry(world, digest_served=False), read_next=no_next)


def test_sbom_of_another_build_is_noted(world):
    other = labels(world, **{"de.osism.sbom": "0.0.9"})
    inputs = gather(
        options(world), registry(world, kolla_ansible=other), read_next=no_next
    )
    assert any("run the script for 0.0.9" in note for note in inputs.notes)


def test_defaults_label_missing_is_an_error(world):
    reconciler = {"de.osism.commit.release": world.release}
    with pytest.raises(InputError, match="de.osism.commit.defaults"):
        gather(
            options(world), registry(world, reconciler=reconciler), read_next=no_next
        )


def test_inventory_reconciler_not_built_yet_takes_predicted_defaults(world):
    inputs = gather(
        options(world), registry(world, reconciler=False), read_next=no_next
    )
    assert dict(inputs.defaults)["001-common.yml"] == {"common_marker": "one"}
    assert any(
        "inventory-reconciler:0.1.0 does not exist yet" in note for note in inputs.notes
    )


def test_defaults_ref_overrides_the_label(world):
    inputs = gather(
        options(world, defaults_ref="v0.2"), registry(world), read_next=no_next
    )
    assert inputs.defaults == [("001-common.yml", {"common_marker": "two"})]


def test_predicted_flag_ignores_an_existing_image(world):
    inputs = gather(options(world, predicted=True), registry(world), read_next=no_next)
    assert inputs.mode == "predicted"


def test_release_label_not_in_checkout_is_an_error_with_fetch_hint(world):
    bad = labels(world, **{"de.osism.commit.release": "f" * 40})
    with pytest.raises(InputError, match="git fetch"):
        gather(options(world), registry(world, kolla_ansible=bad), read_next=no_next)

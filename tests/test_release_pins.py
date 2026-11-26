"""release_pins.gate_refs: every pinned repo's tag, and the ceph pin check."""

import re
import subprocess

import pytest

from osism_drift import release_pins, source
from osism_drift.config import Config, Remote
from osism_drift.http import SourceError

_PIN = "v0.20261111.0"


def _base(drop=None, **over):
    keys = {
        "playbooks_version": _PIN,
        "manager_playbooks_version": _PIN,
        "generics_version": _PIN,
        "defaults_version": _PIN,
    }
    keys.pop(drop, None)
    lines = [f"{k}: '{v}'" for k, v in {**keys, **over}.items()]
    lines += [
        "ansible_collections:",
        "  osism.commons: '0.20261123.0'",
        "  osism.services: '0.20261111.0'",
        "  community.general: '9.0.0'",
        "osism_projects:",
        "  osism: '0.20261111.1'",
    ]
    return "\n".join(lines).encode()


def _ceph(**over):
    keys = {"playbooks_version": _PIN, "generics_version": _PIN, **over}
    return "\n".join(f"{k}: '{v}'" for k, v in keys.items()).encode()


def _stub(monkeypatch, files):
    def read(repo, path, config):
        assert repo == "release"
        if path not in files:
            raise SourceError(f"{path} missing")
        return files[path]

    monkeypatch.setattr(source, "read", read)
    monkeypatch.setattr(
        source,
        "read_optional",
        lambda repo, path, config: files.get(path),
    )


def _files(base=None, **extra):
    return {
        "latest/base.yml": base or _base(),
        "latest/ceph_ansible.yml": _ceph(),
        **extra,
    }


def test_gate_refs_maps_every_row(monkeypatch):
    _stub(monkeypatch, _files())
    tag = "refs/tags/" + _PIN
    assert release_pins.gate_refs(None) == {
        "ansible_playbooks": tag,
        "ansible_playbooks_manager": tag,
        "generics": tag,
        "defaults": tag,
        "ansible_collection_commons": "refs/tags/v0.20261123.0",
        "ansible_collection_services": "refs/tags/v0.20261111.0",
        "python_osism": "refs/tags/v0.20261111.1",
    }


def test_gate_refs_missing_pin_raises(monkeypatch):
    _stub(monkeypatch, _files(_base(drop="generics_version")))
    with pytest.raises(SourceError, match="generics_version"):
        release_pins.gate_refs(None)


def test_gate_refs_missing_osism_project_raises(monkeypatch):
    base = _base().replace(b"osism_projects:\n  osism: '0.20261111.1'", b"")
    _stub(monkeypatch, _files(base))
    with pytest.raises(SourceError, match="osism_projects"):
        release_pins.gate_refs(None)


def test_gate_refs_ceph_file_disagrees_raises(monkeypatch):
    other = "v0.20261023.0"
    files = _files()
    files["latest/ceph_ansible.yml"] = _ceph(playbooks_version=other)
    _stub(monkeypatch, files)
    with pytest.raises(SourceError) as exc:
        release_pins.gate_refs(None)
    msg = str(exc.value)
    for part in ("latest/ceph_ansible.yml", "latest/base.yml", other, _PIN):
        assert part in msg


def test_gate_refs_falls_back_to_ceph_yml(monkeypatch):
    files = _files()
    del files["latest/ceph_ansible.yml"]
    files["latest/ceph.yml"] = _ceph(generics_version="v0.20261023.0")
    _stub(monkeypatch, files)
    with pytest.raises(SourceError, match=re.escape("latest/ceph.yml")):
        release_pins.gate_refs(None)


def test_gate_refs_without_ceph_selector_raises(monkeypatch):
    files = _files()
    del files["latest/ceph_ansible.yml"]
    _stub(monkeypatch, files)
    with pytest.raises(SourceError) as exc:
        release_pins.gate_refs(None)
    assert "latest/ceph_ansible.yml" in str(exc.value)
    assert "latest/ceph.yml" in str(exc.value)


def test_gate_refs_ceph_file_missing_build_key_raises(monkeypatch):
    files = _files()
    files["latest/ceph_ansible.yml"] = b"playbooks_version: '%s'\n" % _PIN.encode()
    _stub(monkeypatch, files)
    with pytest.raises(SourceError) as exc:
        release_pins.gate_refs(None)
    assert "latest/ceph_ansible.yml" in str(exc.value)
    assert "generics_version" in str(exc.value)


def test_gate_refs_ignores_unread_ceph_key(monkeypatch):
    files = _files()
    files["latest/ceph_ansible.yml"] = _ceph(defaults_version="v0.20200101.0")
    _stub(monkeypatch, files)
    assert release_pins.gate_refs(None)["defaults"] == "refs/tags/" + _PIN


def test_gate_refs_ceph_file_not_a_mapping_raises(monkeypatch):
    files = _files()
    files["latest/ceph_ansible.yml"] = b"ceph-reef.yml\n"
    _stub(monkeypatch, files)
    with pytest.raises(SourceError, match="latest/ceph_ansible.yml is not a mapping"):
        release_pins.gate_refs(None)


def test_gate_refs_base_not_a_mapping_raises(monkeypatch):
    _stub(monkeypatch, _files(b"- a\n- b\n"))
    with pytest.raises(SourceError, match="latest/base.yml is not a mapping"):
        release_pins.gate_refs(None)


def _git(repo, *args):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_gate_refs_local_git_symlinked_selector(tmp_path):
    repo = tmp_path / "release"
    (repo / "latest").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "latest" / "base.yml").write_bytes(_base())
    (repo / "latest" / "ceph-reef.yml").write_bytes(
        _ceph(playbooks_version="v0.20261023.0")
    )
    (repo / "latest" / "ceph_ansible.yml").symlink_to("ceph-reef.yml")
    _git(repo, "add", "latest")
    _git(repo, "commit", "-q", "-m", "c")
    sha = _git(repo, "rev-parse", "HEAD")
    config = Config(
        remote=Remote(
            github_raw="https://raw.example/",
            github_api="https://api.example/",
            branch="main",
        ),
        release_version="x",
        plugins={},
        base_dirs=(str(tmp_path),),
        ref_overrides={"release": sha},
    )
    with pytest.raises(SourceError, match=r"playbooks_version is v0\.20261023\.0, but"):
        release_pins.gate_refs(config)

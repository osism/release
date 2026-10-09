import importlib.util
import pathlib
import sys

import pytest

# create-version.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "create-version.py"
_spec = importlib.util.spec_from_file_location("create_version", _SRC)
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)

FILES = {"latest/base.yml": "manager_version: latest\n"}


@pytest.fixture
def repo(release_repo, monkeypatch):
    release_repo.commit(FILES)
    monkeypatch.chdir(release_repo.checkout)
    return release_repo


def latest(prefix="osism-ansible-v"):
    return cv.get_latest_tag_version(cv.remote_tag_names(), prefix)


def test_highest_version_on_the_same_commit(repo):
    repo.tag("osism-ansible-v0.20261009.0")
    repo.tag("osism-ansible-v0.20261008.0")
    assert latest() == "0.20261009.0"


def test_older_version_on_a_newer_commit(repo):
    repo.tag("osism-ansible-v0.20261009.0")
    repo.commit({"latest/other.yml": "other: 1\n"}, date="2026-10-10T12:00:00+00:00")
    repo.tag("osism-ansible-v0.20261008.0")
    assert latest() == "0.20261009.0"


def test_build_number_is_compared_numerically(repo):
    repo.tag("osism-ansible-v0.20261008.9")
    repo.tag("osism-ansible-v0.20261008.10")
    assert latest() == "0.20261008.10"


def test_newer_version_only_on_origin(repo):
    repo.tag("osism-ansible-v0.20261008.0")
    repo.tag("osism-ansible-v0.20261009.0")
    repo.delete_local_tag("osism-ansible-v0.20261009.0")
    assert latest() == "0.20261009.0"


def test_kolla_is_not_kolla_ansible(repo):
    repo.tag("kolla-ansible-v0.20261010.0")
    repo.tag("kolla-v0.20261008.0")
    assert latest("kolla-v") == "0.20261008.0"
    assert latest("kolla-ansible-v") == "0.20261010.0"


def test_no_tag(repo):
    assert latest() is None


def test_failed_lookup_leaves_no_directory(repo, tmp_path, monkeypatch):
    repo.set_origin(tmp_path / "gone.git")
    monkeypatch.setattr(sys, "argv", ["create-version.py", "11.0.1"])
    with pytest.raises(SystemExit) as exit_:
        cv.main()
    assert exit_.value.code == 1
    assert not (repo.checkout / "11.0.1").exists()

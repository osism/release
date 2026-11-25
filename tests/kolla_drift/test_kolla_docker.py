import pytest
from osism_drift import kolla_docker, source
from osism_drift.http import SourceError
from osism_drift.kolla_docker import parse


def test_normalises_hyphen_to_underscore():
    assert parse(["nova", "mariadb-server", "openstack-base"]) == {
        "nova",
        "mariadb_server",
        "openstack_base",
    }


def test_empty():
    assert parse([]) == set()


def _listing(by_path):
    def list_dir_at_ref(repo, path, ref, config, dirs_only=False, missing_ok=False):
        assert repo == "kolla" and dirs_only and missing_ok
        return by_path.get(path, [])

    return list_dir_at_ref


def test_list_services_old_layout(monkeypatch):
    monkeypatch.setattr(source, "list_dir_at_ref", _listing({"docker": ["nova"]}))
    assert kolla_docker.list_services("stable/2026.1", None) == (["nova"], "docker")


def test_list_services_moved_layout(monkeypatch):
    listing = _listing({"kolla/docker": ["nova"]})
    monkeypatch.setattr(source, "list_dir_at_ref", listing)
    assert kolla_docker.list_services("stable/2026.2", None) == (
        ["nova"],
        "kolla/docker",
    )


def test_list_services_neither_layout_is_loud(monkeypatch):
    monkeypatch.setattr(source, "list_dir_at_ref", _listing({}))
    with pytest.raises(SourceError, match="kolla@stable/X"):
        kolla_docker.list_services("stable/X", None)

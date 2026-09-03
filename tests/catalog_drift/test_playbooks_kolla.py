import dataclasses
from pathlib import Path

import pytest
import responses

from osism_drift import playbooks
from osism_drift.config import Config, Remote, PluginCfg, SourceCfg
from osism_drift.source import SourceError

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"

SITE = b"""
- name: Group hosts based on configuration
  hosts: all
- name: Apply role redis
  hosts: redis
- name: Apply role blazar
  hosts: blazar
- name: Apply role mariadb
  hosts: mariadb
- name: Apply role rabbitmq (outward)
  hosts: rabbitmq
"""


@pytest.fixture
def cfg():
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,  # fixture kolla-ansible is not a git checkout
        release_version="latest",
        plugins={"catalog_role_missing": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/A")},
        releases=("A", "B"),
    )


def _mock_kolla(ref, top_level):
    responses.add(
        responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
    )
    responses.add(
        responses.GET,
        f"{RAW}/openstack/kolla-ansible/{ref}/ansible/site.yml",
        body=SITE,
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}/openstack/kolla-ansible/contents/ansible?ref={ref}",
        json=[{"name": n, "type": "file"} for n in top_level],
        status=200,
    )


@responses.activate
def test_site_roles_minus_unsupported(cfg):
    _mock_kolla("stable/A", ["nova.yml", "roles"])
    files = playbooks.kolla_files("A", cfg)
    assert "kolla-redis.yml" in files  # source 2: Apply role
    assert "kolla-nova.yml" in files  # source 3: top-level ansible/*.yml
    # UNSUPPORTED_ROLES only cuts mariadb from source 2 (the split site.yml
    # roles) in isolation, as tested here -- the real image still ships
    # kolla-mariadb.yml, via the top-level ansible/*.yml copy (source 3) and
    # OSISM's own per-release override (source 4).
    assert "kolla-mariadb.yml" not in files
    assert "kolla-rabbitmq-outward.yml" in files  # rabbitmq (outward) rename


@responses.activate
def test_release_specific_osism_playbooks(cfg):
    _mock_kolla("stable/A", [])
    files = playbooks.kolla_files("A", cfg)
    assert "kolla-common.yml" in files  # files/playbooks/A/
    # No mariadb_backup/recovery.yml upstream this time -> the Containerfile:
    # 174-175 alias guard must not fire unconditionally.
    assert "kolla-mariadb-backup.yml" not in files
    assert "kolla-mariadb-recovery.yml" not in files


@responses.activate
def test_interface_applies_render_transform(cfg):
    _mock_kolla("stable/A", [])
    iface = playbooks.kolla_interface("A", cfg)
    assert iface["redis"] == "kolla"  # prefix stripped
    assert iface["kolla-facts"] == "kolla"  # KEEP_PREFIX keeps it
    assert "blazar" not in iface  # HIDE


@responses.activate
def test_kolla_host_and_post_deploy_collisions_are_dropped(cfg):
    # Containerfile:176 rm -f's these two after copying every top-level
    # ansible/*.yml as kolla-<name>.yml: kolla-ansible's own kolla-host.yml
    # and post-deploy.yml would otherwise double-prefix into names the build
    # never actually ships.
    _mock_kolla("stable/A", ["kolla-host.yml", "post-deploy.yml"])
    files = playbooks.kolla_files("A", cfg)
    assert "kolla-kolla-host.yml" not in files
    assert "kolla-post-deploy.yml" not in files


@responses.activate
def test_mariadb_backup_recovery_get_hyphenated_aliases(cfg):
    # Containerfile:174-175: when kolla-ansible ships the underscore-named
    # maintenance playbooks, the build also keeps a hyphenated alias of each.
    _mock_kolla("stable/A", ["mariadb_backup.yml", "mariadb_recovery.yml"])
    files = playbooks.kolla_files("A", cfg)
    assert "kolla-mariadb_backup.yml" in files
    assert "kolla-mariadb_recovery.yml" in files
    assert "kolla-mariadb-backup.yml" in files
    assert "kolla-mariadb-recovery.yml" in files


@responses.activate
def test_release_without_override_dir_raises(cfg):
    # Release "C" has no files/playbooks/C/ in the fixture tree at all.
    # Containerfile:224 is `COPY --link files/playbooks/$OPENSTACK_VERSION/
    # kolla-*.yml /ansible/` -- the same wildcard-COPY construct osism_files()
    # already argues fails the image build outright when its source
    # directory is gone -- so a missing override directory here must raise
    # SourceError too, not be mistaken for a legitimate absence. release_refs
    # pins "C" straight to the already mocked stable/A ref, so only the
    # files/playbooks/C/ lookup is at stake.
    _mock_kolla("stable/A", [])
    c = dataclasses.replace(cfg, release_refs={"kolla_ansible": {"C": "stable/A"}})
    with pytest.raises(SourceError):
        playbooks.kolla_files("C", c)

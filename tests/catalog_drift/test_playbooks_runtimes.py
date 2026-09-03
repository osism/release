import dataclasses
import shutil
from pathlib import Path

import pytest
import responses

from osism_drift import playbooks
from osism_drift.config import Config, Remote, PluginCfg, SourceCfg
from osism_drift.source import SourceError

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"

# Every ansible-playbooks ENVIRONMENTS entry the osism-ansible fixture script
# declares (see fixtures/container-image-osism-ansible/files/src/
# generate-playbook-symlinks.py) needs a mocked directory listing -- including
# "kubernetes", which has no matching upstream directory today, to exercise
# the missing_ok legitimate-absence path. "configuration.yml" is deliberately
# duplicated across "generic" and "manager": both strip to the role name
# "configuration", exercising the cross-prefix collision render-playbooks.py
# resolves by PREFIXES order (see test_osism_interface_collision_last_prefix_wins).
_AP_ENV_FILES = {
    "ceph": ["validate-ceph-mons.yml"],
    "generic": ["ntp.yml", "common.yml", "_helper.yml", "configuration.yml"],
    "manager": ["operator.yml", "network.yml", "configuration.yml"],
    "state": ["bootstrap.yml"],
}


@pytest.fixture
def cfg():
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,  # ansible-playbooks/ceph-ansible fixtures aren't git checkouts
        release_version="latest",
        plugins={"catalog_role_missing": PluginCfg(enabled=True)},
        sources={
            "ansible_playbooks": SourceCfg(owner="osism", branch="main"),
            "ceph_ansible": SourceCfg(owner="ceph", branch="main"),
        },
        releases=("A", "B"),
    )


def _mock_ansible_playbooks(ref="v1"):
    for env, names in _AP_ENV_FILES.items():
        responses.add(
            responses.GET,
            f"{API}/osism/ansible-playbooks/contents/playbooks/{env}",
            json=[{"name": n, "type": "file"} for n in names],
            status=200,
        )
    # "kubernetes" is listed in ENVIRONMENTS but ansible-playbooks carries no
    # such directory -- a genuine 404, not a corrupt input.
    responses.add(
        responses.GET,
        f"{API}/osism/ansible-playbooks/contents/playbooks/kubernetes",
        status=404,
    )


def _mock_ceph_ansible(ref="stable-9.0", names=None):
    if names is None:
        names = [
            "rolling_update.yml",
            "purge-cluster.yml",
            "purge-container-cluster.yml",
        ]
    responses.add(
        responses.GET,
        f"{API}/ceph/ceph-ansible/contents/infrastructure-playbooks",
        json=[{"name": n, "type": "file"} for n in names]
        + [{"name": "README.md", "type": "file"}],
        status=200,
    )


# ---------------------------------------------------------------- osism-ansible


@responses.activate
def test_osism_symlink_naming(cfg):
    """<env>/<name>.yml becomes <env>-<name>.yml, minus SKIP."""
    _mock_ansible_playbooks()
    files = playbooks.osism_files(cfg)
    assert "generic-ntp.yml" in files
    assert "ceph-validate-ceph-mons.yml" in files
    assert "manager-network.yml" not in files  # SKIP
    assert "manager-operator.yml" in files  # NOT skipped -> feeds KEEP_PREFIX
    assert "state-bootstrap.yml" in files  # present in files() even though
    # "state" isn't in render-playbooks.py's PREFIXES (osism_interface hides it)
    assert not any(f.startswith("kubernetes-") for f in files)  # legitimate absence


@responses.activate
def test_osism_underscore_guard_is_dead_code(cfg):
    """generate-playbook-symlinks.py's "not name.startswith('_')" guard tests
    the *env-prefixed* name ("generic-_helper.yml"), which can never start
    with "_" for a real environment name -- so it filters nothing today, and
    an underscore-prefixed source file is symlinked and surfaces like any
    other. Mirrored here rather than the (unenforced) filename convention the
    guard reads as intending, per the fidelity-to-the-actual-build mandate.
    """
    _mock_ansible_playbooks()
    files = playbooks.osism_files(cfg)
    assert "generic-_helper.yml" in files


@responses.activate
def test_osism_interface_hides_and_keeps(cfg):
    _mock_ansible_playbooks()
    iface = playbooks.osism_interface(cfg)
    assert iface["ntp"] == "generic"
    assert iface["manager-operator"] == "manager"  # KEEP_PREFIX
    assert "common" not in iface  # HIDE["generic"]
    assert "bootstrap" not in iface  # state/ not in PREFIXES
    assert iface["_helper"] == "generic"  # dead underscore guard: surfaces too


@responses.activate
def test_osism_interface_collision_last_prefix_wins(cfg):
    """generic-configuration.yml and manager-configuration.yml both strip to
    "configuration". render-playbooks.py's own `for prefix in PREFIXES: ...`
    loop means the LAST prefix in PREFIXES order overwrites the dict entry;
    the fixture's PREFIXES lists "manager" after "generic"."""
    _mock_ansible_playbooks()
    iface = playbooks.osism_interface(cfg)
    assert iface["configuration"] == "manager"


@responses.activate
def test_osism_own_files_playbooks_copy_is_mirrored(cfg, tmp_path):
    """Containerfile:19 (`COPY files/playbooks/* /ansible/`) is a real COPY
    step even though the real repo's files/playbooks/ is empty today; a file
    placed there lands unprefixed in /ansible and reaches the interface like
    any ansible-playbooks-sourced file with the same shape would.

    Builds an isolated copy of the fixture's container-image-osism-ansible
    tree under tmp_path rather than writing into the checked-in fixtures
    directory, so no test run leaves state behind for other tests to trip
    over.
    """
    _mock_ansible_playbooks()
    own_repo = tmp_path / "container-image-osism-ansible"
    shutil.copytree(FIXT / "container-image-osism-ansible", own_repo)
    (own_repo / "files" / "playbooks" / "generic-extra.yml").write_text(
        "---\n- hosts: all\n"
    )
    c = dataclasses.replace(cfg, base_dirs=(str(tmp_path), str(FIXT)))

    files = playbooks.osism_files(c)
    assert "generic-extra.yml" in files
    assert playbooks.osism_interface(c)["extra"] == "generic"


# ------------------------------------------------------------------ ceph-ansible


@responses.activate
def test_ceph_keeps_full_stem(cfg):
    _mock_ceph_ansible()
    iface = playbooks.ceph_interface(cfg)
    assert iface["ceph-pools"] == "ceph"  # prefix retained


@responses.activate
def test_ceph_files_unions_flavour_and_toplevel(cfg):
    _mock_ceph_ansible()
    files = playbooks.ceph_files(cfg)
    assert "ceph-configure-lvm-volumes.yml" in files  # top-level, flavour-independent
    assert "ceph-pools.yml" in files  # squid flavour dir
    assert "ceph-rolling_update.yml" in files  # upstream infrastructure-playbooks


@responses.activate
def test_ceph_upgrade_alias_always_created(cfg):
    # Containerfile:158 `ln -s ... ceph-upgrade.yml` is unconditional.
    _mock_ceph_ansible()
    assert "ceph-upgrade.yml" in playbooks.ceph_files(cfg)


@responses.activate
def test_ceph_purge_survivors_only_the_osism_pair(cfg):
    # Containerfile:161-162: rm -f wipes every ceph-purge-*.yml (OSISM's own
    # flavour copies AND upstream's purge-*.yml alike), then only the two
    # explicitly staged files are moved back.
    _mock_ceph_ansible()  # upstream ships purge-cluster.yml, purge-container-cluster.yml
    files = playbooks.ceph_files(cfg)
    assert "ceph-purge-storage-node.yml" in files
    assert "ceph-purge-cluster.yml" in files
    assert "ceph-purge-container-cluster.yml" not in files  # upstream-only, wiped


def test_ceph_flavours_excludes_the_ceph_yml_alias(cfg):
    # fixtures/release/latest/ carries both ceph-squid.yml (a flavour) and
    # ceph.yml (a symlink alias to a flavour in the real repo, e.g.
    # -> ceph-reef.yml). "ceph.yml" itself never matches the "ceph-*.yml"
    # prefix filter, so only "squid" is a flavour.
    assert playbooks._ceph_flavours(cfg) == ["squid"]


@responses.activate
def test_ceph_mirrors_known_upstream_typos(cfg):
    """Containerfile:154 copies to "/ansible/ceph-site.ym" (missing the "l")
    and Containerfile:156 copies "dashboard.yml" unprefixed; neither ever
    matches render-playbooks.py's "ceph-*.yml" glob, so neither may appear
    here. Pinned so a well-meaning future "fix" of this mirror doesn't
    quietly start including them.
    """
    _mock_ceph_ansible()
    files = playbooks.ceph_files(cfg)
    assert "ceph-site.yml" not in files
    assert "dashboard.yml" not in files


# --------------------------------------------------------------- osism-kubernetes


def test_kubernetes_files_lists_prefixed(cfg):
    assert playbooks.kubernetes_files(cfg) == frozenset(
        {"kubernetes-kubeconfig.yml", "kubernetes-k3s.yml"}
    )


def test_kubernetes_strips_prefix(cfg):
    assert playbooks.kubernetes_interface(cfg)["kubeconfig"] == "kubernetes"


# --------------------------------------------------------------------- dispatch


@responses.activate
def test_playbook_files_dispatch(cfg):
    _mock_ansible_playbooks()
    _mock_ceph_ansible()
    assert playbooks.playbook_files("osism-ansible", "A", cfg) == playbooks.osism_files(
        cfg
    )
    assert playbooks.playbook_files("ceph-ansible", "A", cfg) == playbooks.ceph_files(
        cfg
    )
    assert playbooks.playbook_files(
        "osism-kubernetes", "A", cfg
    ) == playbooks.kubernetes_files(cfg)
    with pytest.raises(SourceError, match="unknown runtime"):
        playbooks.playbook_files("nope", "A", cfg)

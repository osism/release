"""Tests for the two name-resolution functions layered on top of the four
runtime interfaces: `resolves` (mirrors apply.py's role lookup against the
merged MAP_ROLE2ENVIRONMENT-equivalent map) and `validate_resolves` (mirrors
validate.py's per-runtime file-existence dispatch, which never touches that
map at all). `runtime_interface` is the merge the first of those checks
membership against.
"""

from pathlib import Path
from types import MappingProxyType

import pytest
import responses

from osism_drift import playbooks
from osism_drift.config import Config, Remote, PluginCfg, SourceCfg
from osism_drift.source import SourceError

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"

# Minimal upstream kolla-ansible site.yml: no "Apply role" plays here at all --
# the "barbican" role this test needs reaches the interface via the
# top-level-ansible-file path (Containerfile:171-172), not the site.yml split,
# so the split-script path is exercised elsewhere (test_playbooks_kolla.py)
# and kept out of this file's fixture data.
_SITE = b"""
- name: Group hosts based on configuration
  hosts: all
"""

# ansible-playbooks directories the fixture's generate-playbook-symlinks.py
# ENVIRONMENTS list requires a listing for. "generic" carries "validate-ntp.yml"
# so that osism-ansible's real derived-name convention (VALIDATE_PLAYBOOKS["ntp"]
# has no "playbook" key -> playbook name derived as "validate-ntp") has an
# actual file to resolve against, per the brief's real data point. It also
# carries "barbican.yml" -- the SAME basename kolla-ansible ships as a
# top-level playbook below -- so a real cross-runtime collision (the central
# design point of `runtime_interface`) is exercised against product code
# rather than a hand-built dict.
_AP_ENV_FILES = {
    "ceph": ["validate-ceph-mons.yml"],
    "generic": ["validate-ntp.yml", "common.yml", "barbican.yml"],
    "manager": ["operator.yml"],
    "state": ["bootstrap.yml"],
}


@pytest.fixture
def cfg():
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,  # kolla-ansible/ansible-playbooks/ceph-ansible
        # fixtures are not git checkouts -- reads for them go through the
        # mocked responses below rather than a local working tree.
        release_version="latest",
        plugins={"catalog_role_missing": PluginCfg(enabled=True)},
        sources={
            "kolla_ansible": SourceCfg(owner="openstack", branch="stable/A"),
            "ansible_playbooks": SourceCfg(owner="osism", branch="main"),
            "ceph_ansible": SourceCfg(owner="ceph", branch="main"),
        },
        releases=("A", "B"),
    )


def _mock_all(kolla_releases=("A",)):
    """Mock every remote read `runtime_interface`/`playbook_files` need across
    all four runtimes, so a single fixture covers `resolves` (needs the full
    merge), `validate_resolves` (needs one runtime's own file set), and the
    per-release cache tests in this module.

    `kolla_releases` mocks release_to_ref's commits/site.yml/top-level-listing
    trio once per release given -- the only release-dependent runtime (the
    other three always pin to release/latest/*, independent of the `release`
    argument, per kolla_interface's/osism_interface's/ceph_interface's own
    docstrings) -- so a test exercising two releases mocks both refs.
    """
    for release in kolla_releases:
        ref = f"stable/{release}"
        responses.add(
            responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
        )
        responses.add(
            responses.GET,
            f"{RAW}/openstack/kolla-ansible/{ref}/ansible/site.yml",
            body=_SITE,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{API}/openstack/kolla-ansible/contents/ansible?ref={ref}",
            json=[{"name": "barbican.yml", "type": "file"}],
            status=200,
        )
    # osism-ansible / ansible-playbooks: fixture's generate-playbook-symlinks.py
    # ENVIRONMENTS lists ceph/generic/manager/state/kubernetes; "kubernetes" has
    # no matching upstream directory today, a genuine 404 (legitimate absence).
    responses.add(
        responses.GET,
        f"{API}/osism/ansible-playbooks/commits/v1",
        json={"sha": "deadbeef"},
        status=200,
    )
    for env, names in _AP_ENV_FILES.items():
        responses.add(
            responses.GET,
            f"{API}/osism/ansible-playbooks/contents/playbooks/{env}",
            json=[{"name": n, "type": "file"} for n in names],
            status=200,
        )
    responses.add(
        responses.GET,
        f"{API}/osism/ansible-playbooks/contents/playbooks/kubernetes",
        status=404,
    )
    # ceph-ansible upstream infrastructure-playbooks, at the "squid" flavour's
    # pinned ceph_ansible_version (release/latest/ceph-squid.yml -> stable-9.0).
    # "validate.yml" mirrors the real VALIDATE_PLAYBOOKS["ceph-config"] shape
    # (runtime ceph-ansible, playbook "validate") for validate_resolves's
    # ceph-ansible branch.
    responses.add(
        responses.GET,
        f"{API}/ceph/ceph-ansible/contents/infrastructure-playbooks",
        json=[
            {"name": "rolling_update.yml", "type": "file"},
            {"name": "validate.yml", "type": "file"},
        ],
        status=200,
    )


# ------------------------------------------------------------- resolves()


IFACE = {
    "redis": frozenset({"kolla"}),
    "kolla-facts": frozenset({"kolla"}),  # a KEEP_PREFIX-style key: literal
    "ceph-ceph": frozenset({"ceph"}),
    "ceph-pools": frozenset({"ceph"}),
    "facts": frozenset({"generic", "kolla"}),  # a real collision
}


def test_membership_and_provenance():
    assert playbooks.resolves("redis", IFACE)
    assert not playbooks.resolves("valkey", IFACE)


def test_ceph_special_case():
    """apply.py forces role 'ceph' to the ceph runtime's ceph-ceph.yml."""
    assert playbooks.resolves("ceph", IFACE)


def test_ceph_special_case_absent_ceph_ceph():
    # If the ceph runtime does not ship "ceph-ceph" at all, the special case
    # must not fall back to the plain membership check.
    assert not playbooks.resolves("ceph", {"ceph-pools": frozenset({"ceph"})})


def test_prefixed_key_resolves_only_when_the_prefixed_name_is_itself_a_key():
    # "kolla-facts" and "ceph-pools" are the literal keys apply.py's own
    # KEEP_PREFIX / ceph-ansible-full-stem conventions produce -- resolved by
    # plain membership, no prefix stripping involved.
    assert playbooks.resolves("kolla-facts", IFACE)
    assert playbooks.resolves("ceph-pools", IFACE)


def test_bare_prefix_strip_does_not_reconstruct_a_bare_role():
    # "redis" is not in kolla's KEEP_PREFIX, so MAP_ROLE2ENVIRONMENT never
    # carries a "kolla-redis" key; apply.py's prefix strip runs only AFTER a
    # successful dict lookup (or an explicit --environment override this
    # catalog check cannot see), so it never turns "kolla-redis" back into a
    # hit on the bare "redis" entry. `osism apply kolla-redis` (no override)
    # falls through to environment "custom" and fails.
    assert not playbooks.resolves("kolla-redis", IFACE)


def test_unknown_role_does_not_resolve():
    assert not playbooks.resolves("nova-compute-does-not-exist", IFACE)


# ------------------------------------------------------------- runtime_interface()


@responses.activate
def test_runtime_interface_merges_all_four_runtimes(cfg):
    _mock_all()
    iface = playbooks.runtime_interface("A", cfg)
    assert iface["validate-ntp"] == frozenset({"generic"})  # osism-ansible
    assert iface["ceph-pools"] == frozenset({"ceph"})  # ceph-ansible
    assert iface["kubeconfig"] == frozenset({"kubernetes"})  # osism-kubernetes
    # The central design point: kolla-ansible (top-level ansible/barbican.yml)
    # and osism-ansible (generic/barbican.yml) both advertise "barbican" --
    # the merge keeps BOTH environments rather than picking a winner.
    assert iface["barbican"] == frozenset({"kolla", "generic"})


@responses.activate
def test_runtime_interface_is_memoized_on_config(cfg):
    _mock_all()
    first = playbooks.runtime_interface("A", cfg)
    # A second call must not re-issue any of the mocked requests: with
    # responses.activate, an un-mocked call raises ConnectionError, so simply
    # not raising here (after resetting the registry) proves the cache hit.
    responses.reset()
    second = playbooks.runtime_interface("A", cfg)
    assert first is second
    assert cfg.playbooks_cache["A"] is first
    # Returned as a read-only view: a caller mutating it would otherwise
    # corrupt the cache for every later call this run.
    assert isinstance(first, MappingProxyType)
    with pytest.raises(TypeError):
        first["nope"] = frozenset({"kolla"})


@responses.activate
def test_runtime_interface_cache_keyed_by_release(cfg):
    _mock_all(kolla_releases=("A", "B"))
    a = playbooks.runtime_interface("A", cfg)
    b = playbooks.runtime_interface("B", cfg)
    assert a is not b
    assert set(cfg.playbooks_cache) == {"A", "B"}
    assert cfg.playbooks_cache["A"] is a
    assert cfg.playbooks_cache["B"] is b


# ------------------------------------------------------------- validate_resolves()


@responses.activate
def test_validate_resolves_uses_the_named_runtime(cfg):
    """validate.py never consults the merged map; it dispatches per runtime."""
    _mock_all()
    assert playbooks.validate_resolves("kolla-ansible", None, "barbican", "A", cfg)
    assert playbooks.validate_resolves(
        "osism-ansible", "generic", "validate-ntp", "A", cfg
    )
    # 'validate-ntp' exists in osism-ansible, but not as a kolla playbook
    assert not playbooks.validate_resolves(
        "kolla-ansible", None, "validate-ntp", "A", cfg
    )


@responses.activate
def test_validate_resolves_ceph_ansible_branch(cfg):
    # Real shape: VALIDATE_PLAYBOOKS["ceph-config"] has runtime ceph-ansible
    # and playbook "validate", probing the file "ceph-validate.yml".
    _mock_all()
    assert playbooks.validate_resolves("ceph-ansible", None, "validate", "A", cfg)
    assert not playbooks.validate_resolves(
        "ceph-ansible", None, "no-such-validator", "A", cfg
    )


@responses.activate
def test_validate_resolves_wrong_environment_does_not_resolve(cfg):
    # osism-ansible ships "generic-validate-ntp.yml", not "manager-validate-ntp.yml":
    # the environment is part of the filename, so passing the wrong one must fail.
    _mock_all()
    assert not playbooks.validate_resolves(
        "osism-ansible", "manager", "validate-ntp", "A", cfg
    )


@responses.activate
def test_validate_resolves_osism_ansible_requires_an_environment(cfg):
    # validate.py:102 reads VALIDATE_PLAYBOOKS[validator]["environment"]
    # unconditionally and raises KeyError if it's missing -- a malformed
    # catalog entry, not a legitimate "no environment" case, so this must
    # fail loud rather than silently probe a "None-validate-ntp.yml" file.
    _mock_all()
    with pytest.raises(SourceError, match="no environment"):
        playbooks.validate_resolves("osism-ansible", None, "validate-ntp", "A", cfg)


@responses.activate
def test_validate_resolves_unknown_runtime_raises(cfg):
    _mock_all()
    with pytest.raises(SourceError, match="unknown runtime"):
        playbooks.validate_resolves("nope", None, "validate-ntp", "A", cfg)

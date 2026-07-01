from pathlib import Path
import pytest
import responses
from osism_drift.config import (
    Config,
    Remote,
    PluginCfg,
    SourceCfg,
    Allowlist,
    AllowEntry,
)
from osism_drift.drift import kolla_enablement_build as plugin

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://raw.githubusercontent.com/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,  # FIXT/kolla is a plain dir, not a git repo -> remote
        release_version="latest",
        plugins={"kolla_enablement_build": PluginCfg(enabled=True)},
        sources={"kolla": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=("A", "B"),
    )


def _mock_commit(ref):
    responses.add(responses.GET, f"{API}/openstack/kolla/commits/{ref}", status=200)


def _mock_docker(dirs):
    # responses serves duplicate-URL registrations in registration order, and
    # ignores the ?ref= query string when matching. release_range is ["A","B"],
    # so the first docker listing is consumed by release A, the second by B.
    responses.add(
        responses.GET,
        f"{API}/openstack/kolla/contents/docker",
        json=[{"name": d, "type": "dir"} for d in dirs],
        status=200,
    )


def _mock_all():
    _mock_commit("stable/A")
    _mock_commit("stable/B")
    _mock_docker(["foo", "bar", "multi-word"])  # release A
    _mock_docker(["foo", "bar", "baz", "multi-word"])  # release B


@responses.activate
def test_flags_enabled_buildable_not_built(cfg):
    _mock_all()
    drifts = plugin.run(cfg, Allowlist(()))
    assert [(d.image, d.found_src) for d in drifts] == [
        ("foo", "osism/release latest/openstack-A.yml")
    ]


@responses.activate
def test_allowlist_marks_allowlisted(cfg):
    _mock_all()
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_enablement_build", image="foo", reason="tracked in #289"
            ),
        )
    )
    drifts = plugin.run(cfg, al)
    assert len(drifts) == 1 and drifts[0].allowlisted is True

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
from osism_drift.drift import kolla_orphan_config as plugin

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,  # FIXT/kolla-ansible is a plain dir -> remote (mocked)
        release_version="latest",
        plugins={"kolla_orphan_config": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=("A", "B"),
    )


def _mock_upstream(defines):
    # upstream defines these enable_* at both releases; orphan_ids = osism - upstream.
    body = ("".join(f'enable_{k}: "no"\n' for k in sorted(defines))).encode()
    for ref in ("stable/A", "stable/B"):
        responses.add(
            responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
        )
        responses.add(
            responses.GET,
            f"{RAW}/openstack/kolla-ansible/{ref}/ansible/group_vars/all.yml",
            body=body,
            status=200,
        )


@responses.activate
def test_flags_dead_service_companion_vars(cfg):
    # upstream defines only foo/bar -> dead = {feat, off, multi_word}. Only feat
    # has companion/image vars in the fixtures.
    _mock_upstream({"foo", "bar"})
    drifts = plugin.run(cfg, Allowlist(()))
    assert sorted(d.image for d in drifts) == [
        "feat_api_image",
        "feat_api_port",
        "feat_internal_fqdn",
    ]
    # the live var and the enable flags are never swept
    images = [d.image for d in drifts]
    assert "live_image" not in images
    assert not any(i.startswith("enable_") for i in images)


@responses.activate
def test_found_src_points_to_each_file(cfg):
    _mock_upstream({"foo", "bar"})
    by = {d.image: d for d in plugin.run(cfg, Allowlist(()))}
    assert "all/050-images.yml" in by["feat_api_image"].found_src
    assert "all/099-kolla.yml" in by["feat_api_port"].found_src


@responses.activate
def test_no_dead_services_returns_empty(cfg):
    # upstream defines everything OSISM enables -> nothing orphaned -> no sweep.
    _mock_upstream({"foo", "bar", "feat", "off", "multi-word"})
    assert plugin.run(cfg, Allowlist(())) == []


@responses.activate
def test_respects_orphan_allowlist(cfg):
    # Allowlisting feat as an orphan (OSISM invention) excludes it from the dead
    # set, so its companion vars are not swept either.
    _mock_upstream({"foo", "bar"})
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_enablement_orphan",
                image="feat",
                reason="OSISM-invented",
            ),
        )
    )
    assert plugin.run(cfg, al) == []


@responses.activate
def test_own_allowlist_marks_allowlisted(cfg):
    _mock_upstream({"foo", "bar"})
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_orphan_config",
                image="feat_api_port",
                reason="kept intentionally",
            ),
        )
    )
    by = {d.image: d for d in plugin.run(cfg, al)}
    assert by["feat_api_port"].allowlisted is True
    assert by["feat_internal_fqdn"].allowlisted is False

import dataclasses
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
from osism_drift.drift import kolla_secrets_orphan as plugin

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,  # FIXT/kolla-ansible is a plain dir, not a git repo -> remote
        release_version="latest",
        plugins={"kolla_secrets_orphan": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=("A", "B"),
    )


def _mock_passwords(ref, keys):
    # release_to_ref probes stable/<r> first (200), then read_at_ref the
    # passwords.yml at that ref. Distinct refs -> distinct URLs.
    body = ("".join(f"{k}:\n" for k in sorted(keys))).encode()
    responses.add(
        responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
    )
    responses.add(
        responses.GET,
        f"{RAW}/openstack/kolla-ansible/{ref}/etc/kolla/passwords.yml",
        body=body,
        status=200,
    )


@responses.activate
def test_flags_orphan_secrets_per_release(cfg):
    # fixtures: A secrets {keystone, foo, orphan_a}, B secrets {keystone, orphan_b}
    _mock_passwords("stable/A", {"keystone_password", "foo_password"})
    _mock_passwords("stable/B", {"keystone_password"})
    drifts = plugin.run(cfg, Allowlist(()))
    assert sorted(d.image for d in drifts) == ["orphan_a_password", "orphan_b_password"]
    by = {d.image: d for d in drifts}
    assert "secrets.yml.A" in by["orphan_a_password"].found_src
    assert "passwords.yml" in by["orphan_a_password"].expected_src
    assert all(not d.allowlisted for d in drifts)


@responses.activate
def test_present_secret_is_not_orphan(cfg):
    # keystone_password exists upstream at both releases -> never flagged.
    _mock_passwords("stable/A", {"keystone_password", "foo_password"})
    _mock_passwords("stable/B", {"keystone_password"})
    drifts = plugin.run(cfg, Allowlist(()))
    assert all(d.image != "keystone_password" for d in drifts)


@responses.activate
def test_allowlist_marks_allowlisted(cfg):
    _mock_passwords("stable/A", {"keystone_password", "foo_password"})
    _mock_passwords("stable/B", {"keystone_password"})
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_secrets_orphan",
                image="orphan_a_password",
                reason="OSISM-invented secret",
            ),
        )
    )
    drifts = plugin.run(cfg, al)
    a = [d for d in drifts if d.image == "orphan_a_password"][0]
    assert a.allowlisted is True
    assert a.reason == "OSISM-invented secret"


@responses.activate
def test_missing_template_skips_release(cfg):
    # A release with no cfg-cookiecutter secrets template is skipped, not an error.
    c = dataclasses.replace(cfg, releases=("A", "B", "Z"))  # Z has no fixture template
    _mock_passwords("stable/A", {"keystone_password", "foo_password"})
    _mock_passwords("stable/B", {"keystone_password"})
    drifts = plugin.run(c, Allowlist(()))
    # Z contributes nothing; A and B behave as before.
    assert sorted(d.image for d in drifts) == ["orphan_a_password", "orphan_b_password"]

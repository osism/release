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
from osism_drift.drift import kolla_groupvars_missing as plugin

API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


def _write_defaults(tmp_path, files, versions="", overlays=None):
    dall = tmp_path / "defaults" / "all"
    dall.mkdir(parents=True)
    for name, text in files.items():
        (dall / name).write_text(text)
    # OSISM also supplies group_vars via container-image-kolla-ansible's rendered
    # versions.yml; osism_groupvars_keys reads its top-level keys too.
    vt = tmp_path / "container-image-kolla-ansible" / "files" / "src" / "templates"
    vt.mkdir(parents=True)
    (vt / "versions.yml.j2").write_text(versions)
    # ...and via the per-release overlays/<release>/kolla-ansible.yml the image
    # bakes into group_vars/all at deploy time (the third supply path).
    for release, text in (overlays or {}).items():
        od = tmp_path / "container-image-kolla-ansible" / "overlays" / release
        od.mkdir(parents=True)
        (od / "kolla-ansible.yml").write_text(text)


def _cfg(tmp_path, releases=("A", "B")):
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        remote_fallback=True,  # kolla-ansible is not local here -> remote
        release_version="latest",
        plugins={"kolla_groupvars_missing": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=releases,
    )


def _mock_release(ref, keys):
    # release_to_ref probes stable/<r> (200), then the monolithic all.yml.
    body = ("".join(f"{k}: x\n" for k in sorted(keys))).encode()
    responses.add(
        responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
    )
    responses.add(
        responses.GET,
        f"{RAW}/openstack/kolla-ansible/{ref}/ansible/group_vars/all.yml",
        body=body,
        status=200,
    )


def _mock_upstream(keys):
    for ref in ("stable/A", "stable/B"):
        _mock_release(ref, keys)


@responses.activate
def test_flags_upstream_key_missing_from_osism(tmp_path):
    _write_defaults(
        tmp_path, {"001-kolla-defaults.yml": "keystone_public_port: 5000\n"}
    )
    _mock_upstream({"keystone_public_port", "keystone_listen_port"})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["keystone_listen_port"]
    assert all(not d.allowlisted for d in drifts)


@responses.activate
def test_present_in_osism_is_not_missing(tmp_path):
    _write_defaults(
        tmp_path,
        {
            "001-kolla-defaults.yml": "keystone_public_port: 5000\nkeystone_listen_port: 5000\n"
        },
    )
    _mock_upstream({"keystone_public_port", "keystone_listen_port"})
    assert plugin.run(_cfg(tmp_path), Allowlist(())) == []


@responses.activate
def test_osism_key_in_any_file_suppresses(tmp_path):
    # The missing-check reads the union of osism/defaults all/*.yml: a key that
    # lives in a NON-001 file still counts as present (layout-agnostic).
    _write_defaults(
        tmp_path,
        {
            "001-kolla-defaults.yml": "keystone_public_port: 5000\n",
            "keystone.yml": 'keystone_listen_port: "5000"\n',
        },
    )
    _mock_upstream({"keystone_public_port", "keystone_listen_port"})
    assert plugin.run(_cfg(tmp_path), Allowlist(())) == []


@responses.activate
def test_versions_template_supplied_key_not_flagged(tmp_path):
    # openstack_release is supplied by the rendered versions.yml (second delivery
    # path), not by osism/defaults. It must NOT be flagged as missing.
    _write_defaults(
        tmp_path,
        {"001-kolla-defaults.yml": "foo: 1\n"},
        versions='openstack_release: "{{ openstack_version }}"\n',
    )
    _mock_upstream({"foo", "openstack_release"})
    assert plugin.run(_cfg(tmp_path), Allowlist(())) == []


@responses.activate
def test_overlay_supplied_key_not_flagged(tmp_path):
    # ceph_cinder_keyring is supplied by the container-image-kolla-ansible
    # per-release overlay (third delivery path), not by osism/defaults. Upstream
    # still defines it (2024.2 cinder role references it), so it must NOT be
    # flagged as missing.
    _write_defaults(
        tmp_path,
        {"001-kolla-defaults.yml": "foo: 1\n"},
        overlays={
            "2024.2": 'ceph_cinder_keyring: "client.cinder.keyring"\n',
            # a deeper tree under overlays/release/ must not be read as a var file
            "release/6.0.1": "should_not_be_counted: 1\n",
        },
    )
    _mock_upstream({"foo", "ceph_cinder_keyring", "should_not_be_counted"})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    # ceph_cinder_keyring drops (overlay supplies it); the nested release/ file is
    # not a per-release overlay, so should_not_be_counted stays reported.
    assert [d.image for d in drifts] == ["should_not_be_counted"]


@responses.activate
def test_union_across_releases_not_intersection(tmp_path):
    # only_at_a is defined upstream ONLY at release A. OSISM's single defaults
    # set must satisfy every supported release, so a key needed at ANY release
    # and absent from OSISM is flagged (union, not intersection).
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": "foo: 1\n"})
    _mock_release("stable/A", {"foo", "only_at_a"})
    _mock_release("stable/B", {"foo"})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["only_at_a"]


@responses.activate
def test_allowlist_marks_allowlisted(tmp_path):
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": "foo: 1\n"})
    _mock_upstream({"foo", "openstack_release"})
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_groupvars_missing",
                image="openstack_release",
                reason="OSISM sets the release by another mechanism",
            ),
        )
    )
    drifts = plugin.run(_cfg(tmp_path), al)
    entry = [d for d in drifts if d.image == "openstack_release"][0]
    assert entry.allowlisted is True


def test_empty_release_range_raises(tmp_path):
    from osism_drift.source import SourceError

    _write_defaults(tmp_path, {"001-kolla-defaults.yml": "foo: 1\n"})
    # Empty release dir -> release_range() derives an empty range (no network).
    (tmp_path / "release" / "latest").mkdir(parents=True)
    with pytest.raises(SourceError, match="empty supported release range"):
        plugin.run(_cfg(tmp_path, releases=()), Allowlist(()))


@responses.activate
def test_missing_key_at_newest_routes_to_001(tmp_path):
    # missing_var is defined at the newest release (B) -> remediation names 001.
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": "other: 1\n"})
    _mock_release("stable/A", {"other", "missing_var"})
    _mock_release("stable/B", {"other", "missing_var"})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    entry = next(d for d in drifts if d.image == "missing_var")
    assert "all/001-kolla-defaults.yml" in entry.remediation


@responses.activate
def test_missing_key_dropped_by_newest_routes_to_010(tmp_path):
    # missing_var is defined at older release A but not at newest B -> all/010-A.yml.
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": "other: 1\n"})
    _mock_release("stable/A", {"other", "missing_var"})
    _mock_release("stable/B", {"other"})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    entry = next(d for d in drifts if d.image == "missing_var")
    assert "all/010-A.yml" in entry.remediation


@responses.activate
def test_two_keys_with_different_homes_have_distinct_remediations(tmp_path):
    # key1 defined at newest B -> 001; key2 defined only at A -> 010-A.
    # Different homes -> distinct (summary, remediation) -> separate report blocks.
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": "other: 1\n"})
    _mock_release("stable/A", {"other", "key1", "key2"})
    _mock_release("stable/B", {"other", "key1"})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    by_image = {d.image: d for d in drifts}
    d1, d2 = by_image["key1"], by_image["key2"]
    assert (d1.summary, d1.remediation) != (d2.summary, d2.remediation)

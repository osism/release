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
from osism_drift.source import SourceError
from osism_drift.drift import kolla_image_orphan as plugin

API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"

CATALOGUE = (
    "docker_namespace: osism\n"
    'nova_api_image: "{{ docker_image_url }}nova-api"\n'
    'nova_api_tag: "{{ nova_tag }}"\n'
    'nova_tag: "x"\n'
    'monasca_api_image: "{{ docker_image_url }}monasca-api"\n'
    'monasca_api_tag: "{{ monasca_tag }}"\n'
    'monasca_tag: "x"\n'
    'osismbuilt_image: "{{ docker_image_url }}osismbuilt"\n'
    'osismbuilt_tag: "x"\n'
    'nova_api_image_full: "derived-should-be-ignored"\n'
)


def _cfg(tmp_path):
    cat = tmp_path / "defaults" / "all"
    cat.mkdir(parents=True)
    (cat / "002-images-kolla.yml").write_text(CATALOGUE)
    # a non-kolla catalogue file that must be ignored (D3 scoping)
    (cat / "002-images-ceph.yml").write_text(
        'ceph_image: "{{ docker_image_url }}ceph"\n'
    )
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        remote_fallback=True,
        release_version="latest",
        plugins={"kolla_image_orphan": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=("A", "B"),
    )


def _mock_roles(ref, roles):
    responses.add(
        responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
    )
    responses.add(
        responses.GET,
        f"{API}/openstack/kolla-ansible/contents/ansible/roles?ref={ref}",
        json=[{"name": r, "type": "dir"} for r in roles],
        status=200,
    )
    for role, body in roles.items():
        responses.add(
            responses.GET,
            f"{RAW}/openstack/kolla-ansible/{ref}/ansible/roles/{role}/defaults/main.yml",
            body=body,
            status=200,
        )


def _mock_upstream_both(roles):
    for ref in ("stable/A", "stable/B"):
        _mock_roles(ref, roles)


NOVA = 'nova_api_image: "u"\nnova_api_tag: "u"\nnova_tag: "u"\n'


@responses.activate
def test_flags_removed_service_image_and_tag(tmp_path):
    # upstream has nova only -> monasca_* and osismbuilt_* are orphaned.
    _mock_upstream_both({"nova": NOVA})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert sorted(d.image for d in drifts) == [
        "monasca_api_image",
        "monasca_api_tag",
        "monasca_tag",
        "osismbuilt_image",
        "osismbuilt_tag",
    ]
    assert all(not d.allowlisted for d in drifts)


@responses.activate
def test_present_service_not_flagged(tmp_path):
    # upstream also defines monasca -> only osismbuilt_* remain orphaned.
    _mock_upstream_both(
        {
            "nova": NOVA,
            "monasca": 'monasca_api_image: "u"\nmonasca_api_tag: "u"\nmonasca_tag: "u"\n',
        }
    )
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert sorted(d.image for d in drifts) == ["osismbuilt_image", "osismbuilt_tag"]


@responses.activate
def test_union_across_releases_not_intersection(tmp_path):
    # monasca defined ONLY at release B. The union covers it -> not orphaned.
    _mock_roles("stable/A", {"nova": NOVA})
    _mock_roles(
        "stable/B",
        {
            "nova": NOVA,
            "monasca": 'monasca_api_image: "u"\nmonasca_api_tag: "u"\nmonasca_tag: "u"\n',
        },
    )
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert sorted(d.image for d in drifts) == ["osismbuilt_image", "osismbuilt_tag"]


@responses.activate
def test_allowlist_marks_osism_built(tmp_path):
    _mock_upstream_both({"nova": NOVA})
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_image_orphan",
                image="osismbuilt_image",
                reason="OSISM-built image, no upstream role",
            ),
            AllowEntry(
                plugin="kolla_image_orphan",
                image="osismbuilt_tag",
                reason="OSISM-built image, no upstream role",
            ),
        )
    )
    drifts = plugin.run(_cfg(tmp_path), al)
    built = {d.image: d for d in drifts}
    assert built["osismbuilt_image"].allowlisted is True
    assert built["monasca_api_image"].allowlisted is False


@responses.activate
def test_ceph_catalogue_ignored(tmp_path):
    # ceph_image lives in 002-images-ceph.yml, outside the *images-kolla* glob:
    # never considered, so upstream having no ceph role does not flag it.
    _mock_upstream_both({"nova": NOVA})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert "ceph_image" not in {d.image for d in drifts}


@responses.activate
def test_empty_upstream_union_raises(tmp_path):
    # role dirs listed but none carries image/tag keys -> empty union -> raise.
    _mock_upstream_both({"nova": "nova_enable: true\n"})
    with pytest.raises(SourceError, match="empty upstream"):
        plugin.run(_cfg(tmp_path), Allowlist(()))


def test_empty_catalogue_match_raises(tmp_path):
    # a defaults/all with no *images-kolla* file -> no OSISM keys -> raise,
    # rather than silently reporting zero drift.
    cat = tmp_path / "defaults" / "all"
    cat.mkdir(parents=True)
    (cat / "099-kolla.yml").write_text('enable_nova: "yes"\n')
    cfg = Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        remote_fallback=True,
        release_version="latest",
        plugins={"kolla_image_orphan": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=("A", "B"),
    )
    with pytest.raises(SourceError, match="catalogue"):
        plugin.run(cfg, Allowlist(()))

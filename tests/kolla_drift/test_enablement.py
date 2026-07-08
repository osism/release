import pytest
import responses
from osism_drift.config import Config, Remote, SourceCfg
from osism_drift import enablement


def test_canon_normalizes_hyphen_to_underscore():
    assert enablement.canon("kolla-toolbox") == "kolla_toolbox"
    assert enablement.canon("redis") == "redis"


def test_parse_enable_flags_strips_prefix():
    body = b'enable_redis: "yes"\nenable_heat: "no"\nother_var: 1\n'
    assert enablement.parse_enable_flags(body) == {"redis": "yes", "heat": "no"}


def test_truthy_enables_literal_only_and_canon():
    flags = {
        "redis": "yes",
        "heat": "no",
        "grafana": True,
        "off2": False,
        "senlin": "{{ enable_x | bool }}",
        "multi_word": "yes",
    }
    assert enablement.truthy_enables(flags) == {"redis", "grafana", "multi_word"}


def test_parse_build_set_unions_both_blocks_canon():
    body = b"""
infrastructure_projects:
  redis:
  kolla-toolbox:
openstack_projects:
  cinder: stable-2025.2
"""
    assert enablement.parse_build_set(body) == {"redis", "kolla_toolbox", "cinder"}


def _cfg_with_release_dir(tmp_path, releases=()):
    # Isolated release dir per test — never the shared fixtures dir.
    rel = tmp_path / "release" / "latest"
    rel.mkdir(parents=True)
    (rel / "openstack-2024.1.yml").write_text("openstack_projects: {cinder: x}\n")
    (rel / "openstack-2025.2.yml").write_text("openstack_projects: {cinder: y}\n")
    (rel / "openstack.yml").write_text("# alias file; must be ignored by the filter\n")
    return Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={},
        sources={},
        releases=releases,
    )


def test_release_range_derives_from_file_set(tmp_path):
    assert enablement.release_range(_cfg_with_release_dir(tmp_path)) == [
        "2024.1",
        "2025.2",
    ]


def test_release_range_override_wins(tmp_path):
    cfg = _cfg_with_release_dir(tmp_path, releases=("2025.2",))
    assert enablement.release_range(cfg) == ["2025.2"]


def _ka_config():
    # kolla_ansible owner override; no local roots so every read is remote and
    # responses-mockable. release_to_ref/read_at_ref/list_dir_at_ref are all
    # always-remote regardless.
    return Config(
        remote=Remote(
            "https://raw.githubusercontent.com/",
            "https://api.github.com/repos/",
            "main",
            "osism",
        ),
        release_version="latest",
        plugins={},
        sources={"kolla_ansible": SourceCfg(owner="openstack")},
    )


@responses.activate
def test_upstream_enable_keys_monolithic():
    cfg = _ka_config()
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla-ansible/commits/stable/2025.1",
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.1/ansible/group_vars/all.yml",
        body=b'enable_redis: "no"\nenable_valkey: "no"\nother_var: 1\n',
        status=200,
    )
    assert enablement.upstream_enable_keys("2025.1", cfg) == {"redis", "valkey"}


@responses.activate
def test_upstream_enable_keys_split_dir():
    cfg = _ka_config()
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla-ansible/commits/stable/2025.2",
        status=200,
    )
    # monolithic absent -> 404 -> fall through to the split dir
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all.yml",
        status=404,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla-ansible/contents/ansible/group_vars/all",
        json=[
            {"name": "valkey.yml", "type": "file"},
            {"name": "redis.yml", "type": "file"},
            {"name": "README.md", "type": "file"},
        ],
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all/valkey.yml",
        body=b'enable_valkey: "no"\n',
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all/redis.yml",
        body=b'enable_redis: "no"\n',
        status=200,
    )
    assert enablement.upstream_enable_keys("2025.2", cfg) == {"valkey", "redis"}


@responses.activate
def test_upstream_groupvars_keys_monolithic():
    # ALL top-level keys (not just enable_*), raw (no prefix strip, no canon).
    cfg = _ka_config()
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla-ansible/commits/stable/2025.1",
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.1/ansible/group_vars/all.yml",
        body=b'enable_redis: "no"\nkeystone_listen_port: "5000"\nother_var: 1\n',
        status=200,
    )
    assert enablement.upstream_groupvars_keys("2025.1", cfg) == {
        "enable_redis",
        "keystone_listen_port",
        "other_var",
    }


@responses.activate
def test_upstream_groupvars_keys_split_dir():
    # monolithic absent -> split all/ dir; union all top-level keys across files.
    cfg = _ka_config()
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla-ansible/commits/stable/2025.2",
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all.yml",
        status=404,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla-ansible/contents/ansible/group_vars/all",
        json=[
            {"name": "keystone.yml", "type": "file"},
            {"name": "nova.yml", "type": "file"},
            {"name": "README.md", "type": "file"},
        ],
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all/keystone.yml",
        body=b'enable_keystone: "no"\nkeystone_listen_port: "5000"\n',
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all/nova.yml",
        body=b"nova_database_name: nova\n",
        status=200,
    )
    assert enablement.upstream_groupvars_keys("2025.2", cfg) == {
        "enable_keystone",
        "keystone_listen_port",
        "nova_database_name",
    }


def test_osism_groupvars_keys_merges_defaults_and_versions_template(tmp_path):
    # OSISM supplies group_vars from BOTH osism/defaults all/*.yml AND the rendered
    # container-image-kolla-ansible versions.yml. Both must count (non-.yml under
    # all/ skipped); the jinja versions template is parsed for top-level keys.
    dall = tmp_path / "defaults" / "all"
    dall.mkdir(parents=True)
    (dall / "001-kolla-defaults.yml").write_text(
        "keystone_public_port: 5000\nenable_foo: yes\n"
    )
    (dall / "keystone.yml").write_text('keystone_listen_port: "5000"\n')
    (dall / "notes.txt").write_text("ignored_key: 1\n")  # not .yml -> skipped
    vt = tmp_path / "container-image-kolla-ansible" / "files" / "src" / "templates"
    vt.mkdir(parents=True)
    (vt / "versions.yml.j2").write_text(
        'openstack_release: "{{ openstack_version }}"\n'
        'openstack_previous_release_name: "{{ openstack_previous_version }}"\n'
        "kolla_nova_version: \"{{ versions['nova']|default(openstack_version) }}\"\n"
    )
    cfg = Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={},
        sources={},
    )
    assert enablement.osism_groupvars_keys(cfg) == {
        "keystone_public_port",
        "enable_foo",
        "keystone_listen_port",
        "openstack_release",
        "openstack_previous_release_name",
        "kolla_nova_version",
    }


def test_osism_groupvars_keys_counts_overlays(tmp_path):
    # The third supply path: container-image-kolla-ansible
    # overlays/<release>/kolla-ansible.yml, baked into group_vars/all at deploy
    # time. Top-level keys of every per-release overlay are unioned in; a deeper
    # overlays/release/<ver>/ tree is NOT a group_vars overlay and is skipped.
    dall = tmp_path / "defaults" / "all"
    dall.mkdir(parents=True)
    (dall / "001-kolla-defaults.yml").write_text("keystone_public_port: 5000\n")
    vt = tmp_path / "container-image-kolla-ansible" / "files" / "src" / "templates"
    vt.mkdir(parents=True)
    (vt / "versions.yml.j2").write_text('openstack_release: "{{ v }}"\n')
    overlays = tmp_path / "container-image-kolla-ansible" / "overlays"
    (overlays / "2024.1").mkdir(parents=True)
    (overlays / "2024.1" / "kolla-ansible.yml").write_text(
        'ceph_cinder_keyring: "client.cinder.keyring"\nglance_backend_swift: "no"\n'
    )
    (overlays / "2024.2").mkdir(parents=True)
    (overlays / "2024.2" / "kolla-ansible.yml").write_text(
        'ceph_glance_keyring: "client.glance.keyring"\n'
    )
    (overlays / "release" / "6.0.1").mkdir(parents=True)
    (overlays / "release" / "6.0.1" / "kolla-ansible.yml").write_text(
        "not_a_groupvar: 1\n"
    )
    cfg = Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={},
        sources={},
    )
    assert enablement.osism_groupvars_keys(cfg) == {
        "keystone_public_port",
        "openstack_release",
        "ceph_cinder_keyring",
        "glance_backend_swift",
        "ceph_glance_keyring",
    }


def test_osism_enable_flags_merges_across_all_files(tmp_path):
    # enable_* flags are collected from EVERY defaults all/*.yml, so the OSISM
    # enable set is independent of which file a flag lives in (layout-agnostic).
    # A single-file reader would miss enable_keystone here.
    dall = tmp_path / "defaults" / "all"
    dall.mkdir(parents=True)
    (dall / "099-kolla.yml").write_text('enable_redis: "yes"\nenable_heat: "no"\n')
    (dall / "keystone.yml").write_text(
        'enable_keystone: "yes"\nkeystone_listen_port: "5000"\n'
    )
    (dall / "notes.txt").write_text("enable_ignored: yes\n")  # not .yml -> skipped
    cfg = Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={},
        sources={},
    )
    assert enablement.osism_enable_flags(cfg) == {
        "redis": "yes",
        "heat": "no",
        "keystone": "yes",
    }


def test_osism_enable_ids_truthy():
    flags = {"redis": "yes", "off": "no", "grafana": True, "jinja": "{{ x | bool }}"}
    assert enablement.osism_enable_ids(flags, "truthy") == {"redis", "grafana"}


def test_osism_enable_ids_explicit_includes_all_keys():
    flags = {"redis": "yes", "off": "no", "grafana": True}
    assert enablement.osism_enable_ids(flags, "explicit") == {"redis", "off", "grafana"}


def test_osism_enable_ids_unknown_scope_raises():
    with pytest.raises(ValueError):
        enablement.osism_enable_ids({}, "bogus")


API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


def _cfg_ka():
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(),  # no local checkout -> remote (mocked)
        remote_fallback=True,
        release_version="latest",
        plugins={},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=("A",),
    )


def _mock_roles(ref, roles):
    # roles: {role_name: "<yaml body of that role's defaults/main.yml>"}
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


def test_groupvars_home_in_newest_keys():
    path, note = enablement.groupvars_home("k", "2025.2", {"k"}, {})
    assert path == "all/001-kolla-defaults.yml"
    assert "2025.2" in note


def test_groupvars_home_dropped_only():
    path, note = enablement.groupvars_home("k", "2025.2", set(), {"k": "2024.1"})
    assert path == "all/010-2024.1.yml"
    assert "2024.1" in note


def test_groupvars_home_newest_wins_over_dropped():
    # key in both newest_keys and dropped_map: newest takes priority -> 001
    path, _ = enablement.groupvars_home("k", "2025.2", {"k"}, {"k": "2024.1"})
    assert path == "all/001-kolla-defaults.yml"


def test_groupvars_home_neither_returns_none():
    assert enablement.groupvars_home("k", "2025.2", set(), {}) is None


@responses.activate
def test_upstream_image_tag_keys_collects_both_suffixes():
    _mock_roles(
        "stable/A",
        {
            "nova": 'nova_tag: "x"\nnova_api_image: "y"\nnova_api_tag: "z"\n',
            "glance": 'glance_image: "g"\nglance_tag: "t"\nglance_image_full: "drop"\n',
        },
    )
    images, tags = enablement.upstream_image_tag_keys("A", _cfg_ka())
    assert images == {"nova_api_image", "glance_image"}  # *_image_full excluded
    assert tags == {"nova_tag", "nova_api_tag", "glance_tag"}

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


def test_osism_enable_ids_truthy():
    flags = {"redis": "yes", "off": "no", "grafana": True, "jinja": "{{ x | bool }}"}
    assert enablement.osism_enable_ids(flags, "truthy") == {"redis", "grafana"}


def test_osism_enable_ids_explicit_includes_all_keys():
    flags = {"redis": "yes", "off": "no", "grafana": True}
    assert enablement.osism_enable_ids(flags, "explicit") == {"redis", "off", "grafana"}


def test_osism_enable_ids_unknown_scope_raises():
    with pytest.raises(ValueError):
        enablement.osism_enable_ids({}, "bogus")

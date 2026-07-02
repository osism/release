import pytest
from osism_drift.config import load_config, ConfigError, load_allowlist, Allowlist
from osism_drift.model import DriftEntry


def test_load_minimal_config(tmp_path):
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/osism/
  github_api: https://api.github.com/repos/osism/
  branch: main
release_version: latest
plugins:
  release_vs_manager: {enabled: true}
""")
    cfg = load_config(cfg_path)
    assert cfg.release_version == "latest"
    assert cfg.plugins["release_vs_manager"].enabled is True
    assert cfg.remote.branch == "main"
    assert cfg.base_dirs == ()
    assert cfg.remote_fallback is False


def test_osism_root_and_paths_are_unknown_keys(tmp_path):
    # The hardcoded local-path config was removed; --base-dir replaces it.
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/osism/
  github_api: https://api.github.com/repos/osism/
  branch: main
osism_root: /tmp/repos
paths: {}
release_version: latest
plugins: {}
""")
    with pytest.raises(ConfigError, match="osism_root"):
        load_config(cfg_path)


def test_unknown_top_level_key_errors(tmp_path):
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/osism/
  github_api: https://api.github.com/repos/osism/
  branch: main
release_version: latest
plugins:
  release_vs_manager: {enabled: true}
typo_field: oops
""")
    with pytest.raises(ConfigError, match="typo_field"):
        load_config(cfg_path)


def _entry(
    plugin="release_vs_manager",
    image="redis",
    alias="manager_redis",
    found_src="testbed/environments/manager/images.yml",
):
    return DriftEntry(
        plugin=plugin,
        image=image,
        alias=alias,
        expected="x",
        found="y",
        expected_src="release/latest/base.yml",
        found_src=found_src,
    )


def test_allowlist_broad_match(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("""
allow:
  - plugin: release_vs_manager
    image: redis
    reason: "everywhere intentional"
""")
    a = load_allowlist(p)
    match = a.match(_entry())
    assert match is not None
    assert match.reason == "everywhere intentional"


def test_apply_marks_matching_drift_allowlisted(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("""
allow:
  - plugin: release_vs_manager
    image: redis
    reason: "everywhere intentional"
""")
    a = load_allowlist(p)
    result = a.apply(_entry())
    assert result.allowlisted is True
    assert result.reason == "everywhere intentional"


def test_apply_returns_unmatched_drift_unchanged(tmp_path):
    a = load_allowlist(tmp_path / "missing.yml")  # empty allowlist
    drift = _entry()
    result = a.apply(drift)
    assert result is drift
    assert result.allowlisted is False


def test_allowlist_narrow_by_alias(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("""
allow:
  - plugin: release_vs_manager
    image: redis
    alias: manager_redis
    reason: "only manager_redis is intentional"
""")
    a = load_allowlist(p)
    assert a.match(_entry(alias="manager_redis")) is not None
    assert a.match(_entry(alias="netbox_redis")) is None


def test_allowlist_very_narrow(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("""
allow:
  - plugin: role_shadows
    image: redis
    alias: manager_redis
    found_src: ansible-collection-services/roles/manager/defaults/main.yml
    reason: "operational pin"
""")
    a = load_allowlist(p)
    assert (
        a.match(
            _entry(
                plugin="role_shadows",
                alias="manager_redis",
                found_src="ansible-collection-services/roles/manager/defaults/main.yml",
            )
        )
        is not None
    )
    assert (
        a.match(
            _entry(
                plugin="role_shadows",
                alias="manager_redis",
                found_src="ansible-collection-services/roles/other/defaults/main.yml",
            )
        )
        is None
    )


def test_allowlist_empty_reason_errors(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("""
allow:
  - plugin: release_vs_manager
    image: redis
    reason: ""
""")
    with pytest.raises(ConfigError, match="reason"):
        load_allowlist(p)


def test_allowlist_no_file_returns_empty(tmp_path):
    a = load_allowlist(tmp_path / "missing.yml")
    assert a.match(_entry()) is None


from osism_drift.config import AllowEntry  # noqa: E402  (used by stale tests in PR2)


def test_sources_parsed(tmp_path):
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  default_owner: osism
  branch: main
sources:
  kolla: {owner: openstack, branch: stable/2025.2}
release_version: latest
plugins: {}
""")
    cfg = load_config(cfg_path)
    assert cfg.remote.default_owner == "osism"
    assert cfg.sources["kolla"].owner == "openstack"
    assert cfg.sources["kolla"].branch == "stable/2025.2"


def test_default_owner_defaults_when_absent(tmp_path):
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  branch: main
release_version: latest
plugins: {}
""")
    cfg = load_config(cfg_path)
    assert cfg.remote.default_owner == "osism"
    assert cfg.sources == {}


def test_unknown_source_key_errors(tmp_path):
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  branch: main
sources:
  kolla: {owner: openstack, ref: stable/2025.2}
release_version: latest
plugins: {}
""")
    with pytest.raises(ConfigError, match="ref"):
        load_config(cfg_path)


def test_stale_flags_unused_entry():
    al = Allowlist(
        (
            AllowEntry(plugin="p", image="used", reason="r"),
            AllowEntry(plugin="p", image="dead", reason="r"),
        )
    )
    d = DriftEntry(
        plugin="p",
        image="used",
        alias="used",
        expected="e",
        found="f",
        expected_src="s",
        found_src="t",
    )
    stale = al.stale([d], {"p"})
    assert [e.image for e in stale] == ["dead"]


def test_stale_scoped_to_ran_plugins():
    al = Allowlist((AllowEntry(plugin="q", image="z", reason="r"),))
    # Plugin q did not run, so its entry is not considered stale this run.
    assert al.stale([], {"p"}) == []


def _pdrift(image, plugin="kolla_inventory"):
    return DriftEntry(
        plugin=plugin,
        image=image,
        alias=image,
        expected="e",
        found="f",
        expected_src="s",
        found_src="t",
    )


def test_prefix_matches_service_and_subgroups():
    e = AllowEntry(plugin="kolla_inventory", image="cyborg", reason="r", match="prefix")
    assert e.matches(_pdrift("cyborg")) is True
    assert e.matches(_pdrift("cyborg:children")) is True
    assert e.matches(_pdrift("cyborg-agent:children")) is True


def test_prefix_does_not_match_adjacent_name():
    e = AllowEntry(plugin="kolla_inventory", image="cyborg", reason="r", match="prefix")
    assert e.matches(_pdrift("cyborgx:children")) is False
    assert e.matches(_pdrift("cyborg2")) is False


def test_exact_is_default_and_unchanged():
    e = AllowEntry(plugin="kolla_inventory", image="control", reason="r")
    assert e.match == "exact"
    assert e.matches(_pdrift("control")) is True
    assert e.matches(_pdrift("control-plane")) is False


def test_allowlist_unknown_match_value_errors(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("""
allow:
  - {plugin: kolla_inventory, image: cyborg, match: glob, reason: "x"}
""")
    with pytest.raises(ConfigError, match="match"):
        load_allowlist(p)


def test_allowlist_empty_image_errors(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("""
allow:
  - {plugin: kolla_inventory, image: "", match: prefix, reason: "x"}
""")
    with pytest.raises(ConfigError, match="image"):
        load_allowlist(p)


def test_prefix_entry_stale_when_no_match():
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_inventory", image="cyborg", reason="r", match="prefix"
            ),
            AllowEntry(
                plugin="kolla_inventory", image="ghost", reason="r", match="prefix"
            ),
        )
    )
    drifts = [_pdrift("cyborg-agent:children")]
    stale = al.stale(drifts, {"kolla_inventory"})
    assert [e.image for e in stale] == ["ghost"]


def _write_cfg(tmp_path, body):
    p = tmp_path / "c.yml"
    p.write_text(body)
    return p


def test_releases_and_release_refs_parse(tmp_path):
    cfg = load_config(
        _write_cfg(
            tmp_path,
            """
remote: {github_raw: "https://raw/", github_api: "https://api/", branch: main}
release_version: latest
plugins: {}
releases: ["2024.1", "2025.2"]
release_refs:
  kolla:
    "2024.2": "2024.2-eol"
""",
        )
    )
    assert cfg.releases == ("2024.1", "2025.2")
    assert cfg.release_refs == {"kolla": {"2024.2": "2024.2-eol"}}


def test_releases_default_empty(tmp_path):
    cfg = load_config(
        _write_cfg(
            tmp_path,
            """
remote: {github_raw: "https://raw/", github_api: "https://api/", branch: main}
release_version: latest
plugins: {}
""",
        )
    )
    assert cfg.releases == ()
    assert cfg.release_refs == {}

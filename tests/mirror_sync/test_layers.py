import pytest
from osism_drift.mirror_sync import layers


def test_mirror_layer_refuses_a_monolithic_upstream_layout(tmp_path, monkeypatch):
    # <=2025.1 yields [("all.yml", body)]; writing that makes all/001-all.yml.
    monkeypatch.setattr(
        layers.enablement,
        "upstream_groupvars_files",
        lambda rel, cfg: [("all.yml", b"k: v\n")],
    )
    with pytest.raises(layers.MonolithicLayout):
        layers.mirror_layer(None, "2025.1", tmp_path)


def test_mirror_layer_refuses_an_empty_upstream_listing(tmp_path, monkeypatch):
    # Without this guard the plan is "delete every 001-* file, write nothing":
    # a renamed upstream path or a bad ref arrives here as an empty list, and
    # once the writer exists that wipes the whole mirror layer.
    allsrc = tmp_path / "all"
    allsrc.mkdir()
    (allsrc / "001-nova.yml").write_bytes(b"x\n")
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_files", lambda rel, cfg: []
    )
    with pytest.raises(layers.EmptyUpstreamLayer, match="listed no group_vars"):
        layers.mirror_layer(None, "2025.2", tmp_path)


def test_every_arms_refusal_shares_one_base(tmp_path):
    # The CLI catches the base class; a stray sibling would escape as a traceback.
    for exc in (
        layers.MonolithicLayout,
        layers.EmptyUpstreamLayer,
        layers.UnownedCompatFile,
    ):
        assert issubclass(exc, layers.LayerError)


def test_mirror_layer_writes_prefixed_copies_and_deletes_orphans(tmp_path, monkeypatch):
    allsrc = tmp_path / "all"
    allsrc.mkdir()
    (allsrc / "001-nova.yml").write_bytes(b"old\n")
    (allsrc / "001-zun.yml").write_bytes(b"gone upstream\n")
    (allsrc / "099-kolla.yml").write_bytes(b"osism: yes\n")  # must be untouched
    monkeypatch.setattr(
        layers.enablement,
        "upstream_groupvars_files",
        lambda rel, cfg: [("nova.yml", b"new\n"), ("cinder.yml", b"c\n")],
    )
    writes, deletes, added, deleted = layers.mirror_layer(None, "2025.2", tmp_path)

    assert writes == {"all/001-nova.yml": b"new\n", "all/001-cinder.yml": b"c\n"}
    assert deletes == ("all/001-zun.yml",)
    assert added == ("all/001-cinder.yml",)
    assert deleted == ("all/001-zun.yml",)


def test_mirror_layer_rejects_an_unsafe_upstream_name(tmp_path, monkeypatch):
    monkeypatch.setattr(
        layers.enablement,
        "upstream_groupvars_files",
        lambda rel, cfg: [("sub/nova.yml", b"x\n")],
    )
    with pytest.raises(ValueError, match="not a bare basename"):
        layers.mirror_layer(None, "2025.2", tmp_path)


def test_mirror_homes_reads_the_tree_with_last_wins(tmp_path):
    # Pre-sync homes must come from disk: after an upstream backport on the same
    # target, the on-disk mirror is an older commit of that target, not prev.
    allsrc = tmp_path / "all"
    allsrc.mkdir()
    (allsrc / "001-common.yml").write_bytes(b"database_address: a\nshared: c\n")
    (allsrc / "001-database.yml").write_bytes(b"database_address: b\n")
    homes = layers.mirror_homes(tmp_path)
    # database.yml sorts after common.yml, so it wins -- as Ansible resolves it.
    assert homes["database_address"] == "database.yml"
    assert homes["shared"] == "common.yml"


def test_compat_layer_emits_values_from_the_release_that_still_has_them(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        layers.enablement,
        "dropped_key_release_map",
        lambda cfg: {"enable_zun": "2025.2", "distro_python_version": "2025.2"},
    )
    monkeypatch.setattr(
        layers.enablement,
        "upstream_groupvars_values",
        lambda rel, cfg: {"enable_zun": False, "distro_python_version": "3.12"},
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    shas = {"2025.1": "b" * 40, "2025.2": "a" * 40}
    writes, deletes, dests, _ = layers.compat_layer(
        None, ["2024.1", "2025.1", "2025.2", "2026.1"], shas, tmp_path
    )
    body = writes["all/010-2025.2.yml"]
    assert "generated-by: sync-mirror" in body
    # sorted keys, and the complete replay map recorded in the header
    assert body.index("distro_python_version") < body.index("enable_zun")
    assert "a" * 40 in body and "b" * 40 in body
    assert dests == {
        "enable_zun": "all/010-2025.2.yml",
        "distro_python_version": "all/010-2025.2.yml",
    }
    assert deletes == ()


def test_compat_layer_excludes_keys_another_osism_layer_supplies(tmp_path, monkeypatch):
    """A dropped key OSISM already supplies needs no 010 entry.

    It is redundant -- the enforced union is satisfied -- and shadowed, since
    010-* sorts before 099-*. Validated against the committed 010 files: without
    this rule the generator emitted 37 extra keys and all 37 were in
    osism_supply_excluding_mirror().
    """
    monkeypatch.setattr(
        layers.enablement,
        "dropped_key_release_map",
        lambda cfg: {"gone": "2025.1", "enable_redis": "2025.1"},
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: {"enable_redis"}
    )
    monkeypatch.setattr(
        layers.enablement,
        "upstream_groupvars_values",
        lambda rel, cfg: {"gone": 1, "enable_redis": 2},
    )
    _, _, dests, _ = layers.compat_layer(
        None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path
    )
    assert set(dests) == {"gone"}


def test_compat_layer_excludes_keys_the_target_still_defines(tmp_path, monkeypatch):
    """dropped_key_release_map() is not a map of dropped keys.

    It maps every key defined by a release below the newest to the newest such
    release; a key counts as dropped only when the target no longer defines it.
    Using the map directly emitted 880 keys for a 2025.2 target where 31 were
    dropped -- caught by the first end-to-end run, not by a mocked unit test.
    """
    monkeypatch.setattr(
        layers.enablement,
        "dropped_key_release_map",
        lambda cfg: {"gone": "2025.1", "still_here": "2025.1"},
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: {"still_here"}
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    monkeypatch.setattr(
        layers.enablement,
        "upstream_groupvars_values",
        lambda rel, cfg: {"gone": 1, "still_here": 2},
    )
    writes, _, dests, _ = layers.compat_layer(
        None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path
    )
    assert set(dests) == {"gone"}
    assert "still_here" not in writes["all/010-2025.1.yml"]


def test_compat_layer_raises_when_a_dropped_key_has_no_value(tmp_path, monkeypatch):
    monkeypatch.setattr(
        layers.enablement, "dropped_key_release_map", lambda cfg: {"ghost": "2025.2"}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_values", lambda rel, cfg: {}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    with pytest.raises(KeyError, match="ghost"):
        layers.compat_layer(
            None, ["2025.1", "2025.2", "2026.1"], {"2025.2": "a" * 40}, tmp_path
        )


def _managed(tmp_path, name, marked=True):
    d = tmp_path / "all"
    d.mkdir(exist_ok=True)
    head = "---\n# generated-by: sync-mirror\n" if marked else "---\n# hand written\n"
    (d / name).write_text(head + "k: 1\n")
    return tmp_path


def test_compat_layer_deletes_a_managed_file_with_no_desired_keys(
    tmp_path, monkeypatch
):
    _managed(tmp_path, "010-2025.1.yml")
    monkeypatch.setattr(layers.enablement, "dropped_key_release_map", lambda cfg: {})
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_values", lambda rel, cfg: {}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    writes, deletes, _, _ = layers.compat_layer(
        None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path
    )
    assert writes == {}
    assert deletes == ("all/010-2025.1.yml",)


def test_report_mode_reports_an_unmarked_file_instead_of_refusing(
    tmp_path, monkeypatch
):
    """The guard protects writes, so it must not abort a read-only report.

    Blocking there hides the very plan an operator needs in order to discover
    that a marker commit is required -- which is exactly what happened on the
    first run against a real defaults tree.
    """
    _managed(tmp_path, "010-2025.1.yml", marked=False)
    monkeypatch.setattr(
        layers.enablement, "dropped_key_release_map", lambda cfg: {"k": "2025.1"}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_values", lambda rel, cfg: {"k": 1}
    )
    writes, _, _, unowned = layers.compat_layer(
        None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path, strict=False
    )
    # The plan is still computed in full, and the blocker is named.
    assert "all/010-2025.1.yml" in writes
    assert unowned == ("all/010-2025.1.yml",)


def test_compat_layer_refuses_an_unmarked_managed_file(tmp_path, monkeypatch):
    _managed(tmp_path, "010-2025.1.yml", marked=False)
    monkeypatch.setattr(
        layers.enablement, "dropped_key_release_map", lambda cfg: {"k": "2025.1"}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_values", lambda rel, cfg: {"k": 1}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    with pytest.raises(layers.UnownedCompatFile, match="generated-by"):
        layers.compat_layer(None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path)


def test_compat_layer_refuses_to_delete_an_unmarked_file(tmp_path, monkeypatch):
    _managed(tmp_path, "010-2025.1.yml", marked=False)
    monkeypatch.setattr(layers.enablement, "dropped_key_release_map", lambda cfg: {})
    monkeypatch.setattr(layers.enablement, "upstream_groupvars_values", lambda r, c: {})
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    with pytest.raises(layers.UnownedCompatFile):
        layers.compat_layer(None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path)


def test_require_owned_rejects_a_marker_used_inside_a_value(tmp_path, monkeypatch):
    d = tmp_path / "all"
    d.mkdir()
    (d / "010-2025.1.yml").write_text(
        '---\n# hand written\nnote: "generated-by: sync-mirror"\n'
    )
    monkeypatch.setattr(layers.enablement, "dropped_key_release_map", lambda cfg: {})
    monkeypatch.setattr(layers.enablement, "upstream_groupvars_values", lambda r, c: {})
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    with pytest.raises(layers.UnownedCompatFile):
        layers.compat_layer(None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path)


def test_require_owned_rejects_a_marker_below_the_header(tmp_path, monkeypatch):
    d = tmp_path / "all"
    d.mkdir()
    (d / "010-2025.1.yml").write_text(
        "---\n" + "# filler\n" * 8 + "# generated-by: sync-mirror\nk: 1\n"
    )
    monkeypatch.setattr(layers.enablement, "dropped_key_release_map", lambda cfg: {})
    monkeypatch.setattr(layers.enablement, "upstream_groupvars_values", lambda r, c: {})
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    with pytest.raises(layers.UnownedCompatFile):
        layers.compat_layer(None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path)


def test_compat_layer_never_inspects_a_file_outside_the_range(tmp_path, monkeypatch):
    # An unmarked 010 for a release after --target must not even be looked at.
    _managed(tmp_path, "010-2025.2.yml", marked=False)
    monkeypatch.setattr(layers.enablement, "dropped_key_release_map", lambda cfg: {})
    monkeypatch.setattr(layers.enablement, "upstream_groupvars_values", lambda r, c: {})
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    writes, deletes, _, _ = layers.compat_layer(
        None, ["2025.1", "2025.2"], {"2025.1": "a" * 40}, tmp_path
    )
    assert writes == {} and deletes == ()


def test_compat_layer_ignores_releases_after_the_target(tmp_path, monkeypatch):
    monkeypatch.setattr(
        layers.enablement, "dropped_key_release_map", lambda cfg: {"k": "2025.1"}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_values", lambda rel, cfg: {"k": 1}
    )
    monkeypatch.setattr(
        layers.enablement, "upstream_groupvars_keys", lambda rel, cfg: set()
    )
    monkeypatch.setattr(
        layers.enablement, "osism_supply_excluding", lambda cfg, skip: set()
    )
    writes, deletes, _, _ = layers.compat_layer(
        None, ["2024.1", "2025.1", "2025.2"], {"2025.1": "b" * 40}, tmp_path
    )
    assert set(writes) == {"all/010-2025.1.yml"}
    assert "all/010-2025.2.yml" not in writes
    assert deletes == ()


def test_changed_and_created_compares_bytes_against_disk(tmp_path):
    allsrc = tmp_path / "all"
    allsrc.mkdir()
    (allsrc / "001-nova.yml").write_bytes(b"same\n")
    writes = {"all/001-nova.yml": b"same\n", "all/001-new.yml": b"fresh\n"}
    changed, created = layers.changed_and_created(writes, tmp_path)
    assert changed == ("all/001-new.yml",)
    assert created == ("all/001-new.yml",)


def test_changed_and_created_sees_a_content_change(tmp_path):
    allsrc = tmp_path / "all"
    allsrc.mkdir()
    (allsrc / "001-nova.yml").write_bytes(b"old\n")
    changed, created = layers.changed_and_created(
        {"all/001-nova.yml": b"new\n"}, tmp_path
    )
    assert changed == ("all/001-nova.yml",)
    assert created == ()


def test_generated_sequences_are_indented_for_yamllint():
    """osism/defaults runs yamllint with `extends: default`, so a block sequence
    at the parent's indentation is an error -- a re-sync would fail CI on a file
    this generator wrote. yaml.safe_dump produces exactly that form."""
    out = layers._render_compat(
        "2025.1",
        ["run_default_subdirectories"],
        {"run_default_subdirectories": ["/run/netns", "/run/nova"]},
        {"2025.1": "a" * 40},
    )
    assert 'run_default_subdirectories:\n  - "/run/netns"\n  - "/run/nova"\n' in out
    assert '\n- "/run/netns"' not in out


def test_generated_values_match_the_upstream_quoting_style():
    """The 001 mirror carries 677 double-quoted values and no single-quoted ones,
    and the hand-written 010 layers matched it. PyYAML's default inverts that and
    escapes an embedded quote as '' where upstream writes '."""
    out = layers._render_compat(
        "2025.2",
        ["jinja", "falsey", "count", "empty"],
        {
            "jinja": "{{ x == 'influxdb' }}",
            "falsey": "no",
            "count": 42463,
            "empty": None,
        },
        {"2025.2": "a" * 40},
    )
    assert "jinja: \"{{ x == 'influxdb' }}\"" in out
    assert "''" not in out
    assert 'falsey: "no"' in out
    # non-strings keep their type and stay unquoted
    assert "count: 42463" in out
    assert "empty: null" in out


def test_mapping_keys_are_not_quoted():
    """Upstream writes a bare key with a quoted value; quoting both would be a
    style the directory does not use anywhere."""
    out = layers._render_compat(
        "2025.2", ["some_key"], {"some_key": "v"}, {"2025.2": "a" * 40}
    )
    assert 'some_key: "v"' in out
    assert '"some_key"' not in out


def test_nested_keys_stay_bare_and_keep_upstream_order():
    """Upstream writes nested keys bare too, and sorting every level reordered a
    container's ulimits to hard-before-soft for no gain."""
    out = layers._render_compat(
        "2025.2",
        ["z_last", "a_first", "dims"],
        {
            "z_last": "v",
            "a_first": "v",
            "dims": {"ulimits": {"nofile": {"soft": 1048576, "hard": 1048576}}},
        },
        {"2025.2": "a" * 40},
    )
    assert "  ulimits:\n    nofile:\n      soft: 1048576\n      hard: 1048576" in out
    assert '"ulimits"' not in out
    # the top level is still sorted, which is what determinism needs
    assert out.index("a_first:") < out.index("dims:") < out.index("z_last:")

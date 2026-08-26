from osism_drift.mirror_sync import classify


def _supply(**keys):
    return keys


# --- representation: the explicit truth table -------------------------------


def test_quoted_no_vs_false_is_representation_not_semantic():
    # bool("no") is True in Python, so a naive implementation calls this a truth
    # change. Upstream's 2026.1 edit was quoted -> bare, so all 112 rows land here.
    cls, note = classify.classify("k", "no", False, _supply())
    assert cls == "representation"
    assert "only the YAML type or spelling differs" in note


def test_yes_vs_true_is_representation():
    assert classify.classify("k", "yes", True, _supply())[0] == "representation"


def test_on_off_tokens_are_covered():
    assert classify.classify("k", "on", True, _supply())[0] == "representation"
    assert classify.classify("k", "off", False, _supply())[0] == "representation"


def test_token_match_is_case_insensitive():
    assert classify.classify("k", "No", False, _supply())[0] == "representation"


def test_opposite_truth_values_are_semantic():
    assert classify.classify("k", "yes", False, _supply())[0] == "semantic"


def test_a_non_boolish_string_is_not_representation():
    assert classify.classify("k", "maybe", False, _supply())[0] == "semantic"


# --- notation: narrow, enumerated normalization ------------------------------


def test_jinja_delimiter_whitespace_only_is_notation():
    old = "{{ a }}://{% if not loop.last %},{% endif %}"
    new = "{{a}}://{%if not loop.last %},{% endif %}"
    assert classify.classify("k", old, new, _supply())[0] == "notation"


def test_whitespace_inside_a_jinja_expression_stays_semantic():
    # Collapsing inside an expression can alter a string literal; refuse to.
    assert classify.classify("k", "{{ 'a b' }}", "{{ 'ab' }}", _supply())[0] == (
        "semantic"
    )


def test_a_changed_filter_argument_stays_semantic():
    old = "{{ x | join(':') }}"
    new = "{{ x | join(' ') }}"
    assert classify.classify("k", old, new, _supply())[0] == "semantic"


# --- overridden: narrowed, by lexical position ------------------------------


def test_overridden_requires_an_unconditional_later_layer():
    info = {"k": {"layers": ["all/099-kolla.yml"], "value": "monitoring"}}
    assert classify.classify("k", "", "openstack-monitoring", info)[0] == "overridden"


def test_a_100_layer_counts_as_unconditional():
    # all/100-ansible.yml is real, global, and sorts after the 001-* mirror. An
    # earlier draft gated on an "all/0" prefix and silently excluded it.
    info = {"k": {"layers": ["all/100-ansible.yml"], "value": "plain"}}
    assert classify.classify("k", 1, 2, info)[0] == "overridden"


def test_a_010_layer_does_not_count():
    info = {"k": {"layers": ["all/010-2025.1.yml"], "value": "plain"}}
    assert classify.classify("k", 1, 2, info)[0] == "semantic"


def test_a_layer_sorting_before_the_mirror_does_not_count():
    info = {"k": {"layers": ["all/000-early.yml"], "value": "plain"}}
    assert classify.classify("k", 1, 2, info)[0] == "semantic"


def test_release_conditional_override_is_not_overridden():
    # A gate applies per release, so it does not prove the change cannot land.
    info = {
        "k": {
            "layers": ["all/099-kolla.yml"],
            "value": "{{ 'a' if openstack_version in ['2024.1'] else 'b' }}",
        }
    }
    assert classify.classify("k", 1, 2, info)[0] == "semantic"


def test_a_release_gate_nested_in_a_mapping_is_not_overridden():
    # The gate need not be the whole value: a later layer may supply a dict whose
    # nested string carries it, and calling that unconditional would wave a real
    # per-release change through as non-gating.
    info = {
        "k": {
            "layers": ["all/099-kolla.yml"],
            "value": {
                "nested": "{{ 'old' if openstack_version in ['2025.2'] else 'new' }}"
            },
        }
    }
    assert classify.classify("k", 1, 2, info)[0] == "semantic"


def test_a_release_gate_nested_in_a_list_is_not_overridden():
    info = {
        "k": {
            "layers": ["all/099-kolla.yml"],
            "value": ["plain", "{{ 'a' if openstack_release in ['2025.1'] else 'b' }}"],
        }
    }
    assert classify.classify("k", 1, 2, info)[0] == "semantic"


def test_a_release_gate_nested_two_levels_deep_is_not_overridden():
    info = {
        "k": {
            "layers": ["all/100-ansible.yml"],
            "value": {"outer": [{"inner": "{{ 'a' if openstack_version in [] }}"}]},
        }
    }
    assert classify.classify("k", 1, 2, info)[0] == "semantic"


def test_a_plain_mapping_override_is_still_overridden():
    # Recursion must not make every structured value look conditional.
    info = {"k": {"layers": ["all/099-kolla.yml"], "value": {"a": 1, "b": [2, 3]}}}
    assert classify.classify("k", 1, 2, info)[0] == "overridden"


def test_overlay_only_supply_is_not_overridden():
    info = {"k": {"layers": ["overlay:2025.2"], "value": "x"}}
    assert classify.classify("k", 1, 2, info)[0] == "semantic"


def test_versions_yml_is_the_named_exception():
    # openstack_release: "{{ openstack_version }}" is Jinja but unconditional and
    # global -- the file is generated per deployment and sorts after every 001-*.
    info = {"k": {"layers": ["versions.yml"], "value": "{{ openstack_version }}"}}
    assert classify.classify("k", "2025.2", "2026.1", info)[0] == "overridden"


# --- supply_info: the layer map must include versions.yml for real ----------


def test_supply_info_reads_the_defaults_layers_with_last_wins(tmp_path, monkeypatch):
    allsrc = tmp_path / "all"
    allsrc.mkdir()
    (allsrc / "001-nova.yml").write_bytes(b"mirrored: 1\n")  # mirror is excluded
    (allsrc / "099-a.yml").write_bytes(b"shared: first\n")
    (allsrc / "099-b.yml").write_bytes(b"shared: second\n")
    monkeypatch.setattr(classify.source, "read", lambda repo, path, cfg: b"")
    info = classify.supply_info(None, tmp_path)
    assert "mirrored" not in info
    assert info["shared"]["layers"] == ["all/099-a.yml", "all/099-b.yml"]
    assert info["shared"]["value"] == "second"  # lexically last wins


def test_supply_info_includes_the_rendered_versions_layer(tmp_path, monkeypatch):
    # Regression: an earlier design globbed defaults/all/*.yml only, so the
    # versions.yml exception could never fire in a real run and openstack_release
    # -- the one key it exists for -- came out semantic.
    (tmp_path / "all").mkdir()
    monkeypatch.setattr(
        classify.source,
        "read",
        lambda repo, path, cfg: b'openstack_release: "{{ openstack_version }}"\n',
    )
    info = classify.supply_info(None, tmp_path)
    assert "versions.yml" in info["openstack_release"]["layers"]
    # and it must actually classify as overridden through that layer
    assert classify.classify("openstack_release", "2025.2", "2026.1", info)[0] == (
        "overridden"
    )

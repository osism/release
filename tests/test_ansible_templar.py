import pytest

from ansible_templar import Templar, TemplateFailure, ansible_concat


def templar(*layers):
    """A Templar over (rank, label, {name: raw}) layers, lowest first."""
    return Templar(
        [
            (rank, {name: (raw, label) for name, raw in values.items()})
            for rank, label, values in layers
        ]
    )


def resolve(t, name):
    value, _ = t.resolve(name)
    return value


def test_literal_strings_keep_their_text():
    t = templar((0, "l", {"a": "2026.10", "b": "1.00", "c": "{{ a }}", "d": "{{ b }}"}))
    assert [resolve(t, n) for n in "abcd"] == ["2026.10", "1.00", "2026.10", "1.00"]


def test_booleans_survive_as_conditions():
    t = templar((0, "l", {"off": "{{ false }}", "x": "{{ 'a' if off else 'b' }}"}))
    assert resolve(t, "off") is False
    assert resolve(t, "x") == "b"


def test_concat_matches_ansible_core_2_18():
    assert ansible_concat(iter([])) == ""
    assert ansible_concat(iter(["2026.10"])) == "2026.10"
    assert ansible_concat(iter(["a", 1])) == "a1"
    assert ansible_concat(iter(["True"])) is True
    assert ansible_concat(iter(["[1, ", "2]"])) == [1, 2]
    assert ansible_concat(iter(['{"a": true}'])) == {"a": True}
    assert ansible_concat(iter(["{not python"])) == "{not python"


def test_inactive_branch_with_a_cycle_does_not_fail():
    t = templar(
        (0, "l", {"good": "g", "x": "{{ good if true else cyc }}", "cyc": "{{ cyc }}"})
    )
    assert resolve(t, "x") == "g"


def test_inactive_branch_with_an_undefined_name_does_not_fail():
    t = templar((0, "l", {"x": "{{ 'g' if true else nowhere }}"}))
    assert resolve(t, "x") == "g"


def test_active_cycle_fails():
    t = templar((0, "l", {"a": "{{ b }}", "b": "{{ a }}"}))
    with pytest.raises(TemplateFailure, match="recursive loop: a -> b -> a"):
        t.resolve("a")


def test_default_does_not_hide_a_broken_variable():
    t = templar((0, "l", {"x": "{{ cyc|default('d') }}", "cyc": "{{ cyc }}"}))
    with pytest.raises(TemplateFailure, match="recursive loop"):
        t.resolve("x")


def test_default_replaces_an_undefined_name():
    t = templar((0, "l", {"x": "{{ nowhere|default('d') }}"}))
    assert resolve(t, "x") == "d"


def test_chained_defaults():
    t = templar(
        (
            0,
            "role defaults: opensearch",
            {
                "opensearch_dashboards_tag": "{{ kolla_opensearch_dashboards_version"
                "|default(kolla_opensearch_version)|default(kolla_image_version) }}"
            },
        ),
        (1, "versions.yml", {"kolla_opensearch_version": "3.9.0.20261008"}),
    )
    assert resolve(t, "opensearch_dashboards_tag") == "3.9.0.20261008"


def test_undefined_without_default_fails():
    t = templar((0, "l", {"x": "{{ nowhere }}"}))
    with pytest.raises(TemplateFailure, match="nowhere"):
        t.resolve("x")


def test_unknown_name_is_not_defined():
    with pytest.raises(TemplateFailure, match="x is not defined"):
        templar((0, "l", {})).resolve("x")


def test_ansible_only_filter_fails_naming_it():
    t = templar((0, "l", {"x": "{{ 'yes' | bool }}"}))
    with pytest.raises(TemplateFailure, match="bool"):
        t.resolve("x")


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_memo_does_not_keep_values_of_unfinished_ancestors(order):
    t = templar((0, "l", {"a": "{{ 'ok' if true else b }}", "b": "{{ a }}"}))
    assert [resolve(t, n) for n in order] == ["ok", "ok"]


def test_higher_rank_wins():
    t = templar(
        (0, "role defaults: r", {"x": "low"}),
        (1, "defaults all/099-kolla.yml", {"x": "high"}),
    )
    assert resolve(t, "x") == "high"


def test_equal_rank_layers_agreeing_once_resolved():
    t = templar(
        (
            1,
            "versions.yml",
            {
                "openstack_version": "2026.1",
                "openstack_release": "{{ openstack_version }}",
            },
        ),
        (1, "defaults all/001-common.yml", {"openstack_release": "2026.1"}),
    )
    assert resolve(t, "openstack_release") == "2026.1"


def test_equal_rank_layers_disagreeing_fail():
    t = templar((1, "overlay", {"x": "a"}), (1, "versions.yml", {"x": "b"}))
    with pytest.raises(TemplateFailure, match="differs between layers"):
        t.resolve("x")


def test_equal_rank_disagreement_only_matters_where_used():
    t = templar(
        (1, "overlay", {"unused": "a", "x": "ok"}),
        (1, "versions.yml", {"unused": "b"}),
    )
    assert resolve(t, "x") == "ok"


def test_hops_name_value_and_layer():
    t = templar(
        (0, "role defaults: prometheus", {"e_tag": "{{ p_tag }}"}),
        (
            1,
            "defaults all/002-images-kolla.yml",
            {"p_tag": "{{ kolla_p_version|default(kolla_image_version) }}"},
        ),
        (1, "versions.yml", {"kolla_p_version": "3.5.5"}),
    )
    value, hops = t.resolve("e_tag")
    assert value == "3.5.5"
    assert [(h.name, h.raw, h.layer) for h in hops] == [
        ("e_tag", "{{ p_tag }}", "role defaults: prometheus"),
        (
            "p_tag",
            "{{ kolla_p_version|default(kolla_image_version) }}",
            "defaults all/002-images-kolla.yml",
        ),
        ("kolla_p_version", "3.5.5", "versions.yml"),
    ]


def test_containers_are_templated_recursively():
    t = templar((0, "l", {"v": "1", "x": ["{{ v }}", {"k": "{{ v }}"}]}))
    assert resolve(t, "x") == ["1", {"k": "1"}]

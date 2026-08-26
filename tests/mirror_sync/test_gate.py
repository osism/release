import pytest
from osism_drift.mirror_sync import gate
from osism_drift.mirror_sync.model import Plan, Row, SyncOpts

# The real idiom, from all/099-kolla.yml:11.
CANON = "{{ 'yes' if openstack_version in ['2024.1', '2024.2', '2025.1'] else 'no' }}"
# Real gates the parser must decline, from 099-kolla.yml:103 and :233.
COMPOUND = (
    "{{ '8080' if (enable_haproxy | bool and openstack_version not in ['2024.1']) "
    "else horizon_tls_port }}"
)
NESTED = (
    "{{ (A if ansible_facts.os_family == 'RedHat' else '{}') "
    "if openstack_version in ['2024.1'] else B }}"
)


def _supply(value):
    return {"k": {"layers": ["all/099-kolla.yml"], "value": value}}


def _plan_with(key, old, new):
    return Plan(
        target_release="2025.2",
        release_shas={},
        defaults_base_sha="d" * 40,
        prev_release=None,
        range_base_sha=None,
        mirror_writes={},
        mirror_deletes=(),
        compat_writes={},
        compat_deletes=(),
        changed_writes=(),
        created_paths=(),
        rows=(Row(key, old, new, "semantic", (), ""),),
        added_keys=(),
        dropped_keys={},
        added_files=(),
        deleted_files=(),
        unowned_compat=(),
    )


def _opts(**kw):
    return SyncOpts(target="2025.2", **{k: frozenset(v) for k, v in kw.items()})


# --- the parser -------------------------------------------------------------


def test_parses_the_canonical_shape():
    g = gate.parse_gate(CANON)
    assert g == {"old": "yes", "new": "no", "releases": ["2024.1", "2024.2", "2025.1"]}


def test_declines_a_compound_condition():
    assert gate.parse_gate(COMPOUND) is None


def test_declines_a_nested_ternary():
    assert gate.parse_gate(NESTED) is None


def test_declines_a_mixed_release_list():
    """A malformed list must be declined, not crash the run.

    YAML parses ['2024.1', 2024.2] as a str and a float; a later sorted() over the
    mixture raises TypeError, which escapes as a traceback and exit 1 instead of a
    controlled refusal pointing at --retain-unverified.
    """
    assert (
        gate.parse_gate("{{ 'a' if openstack_version in ['2024.1', 2024.2] else 'b' }}")
        is None
    )


def test_declines_an_unquoted_release():
    assert (
        gate.parse_gate("{{ 'a' if openstack_version in [2024.1] else 'b' }}") is None
    )


def test_declines_an_empty_release_list():
    # A gate that retains for no release is meaningless.
    assert gate.parse_gate("{{ 'a' if openstack_version in [] else 'b' }}") is None


def test_accepts_a_trailing_comma():
    g = gate.parse_gate("{{ 'a' if openstack_version in ['2024.1',] else 'b' }}")
    assert g["releases"] == ["2024.1"]


def test_declines_a_variable_branch():
    assert (
        gate.parse_gate("{{ 'a' if openstack_version in ['2024.1'] else b }}") is None
    )


def test_accepts_openstack_release_as_an_alias():
    g = gate.parse_gate("{{ 'a' if openstack_release in ['2024.1'] else 'b' }}")
    assert g["releases"] == ["2024.1"]


# --- check_retain -----------------------------------------------------------


def test_check_retain_accepts_a_correct_gate():
    note = gate.check_retain(
        "k", "yes", "no", _supply(CANON), ["2024.1", "2024.2", "2025.1"]
    )
    assert "verified" in note


def test_check_retain_rejects_a_reversed_gate():
    """The whole reason association matters.

    A gate returning the NEW value for the older releases and the OLD value for the
    target names every right release and carries both values, so any set of
    unordered checks passes it.
    """
    reversed_gate = "{{ 'no' if openstack_version in ['2024.1'] else 'yes' }}"
    with pytest.raises(gate.GateMismatch, match="in-branch"):
        gate.check_retain("k", "yes", "no", _supply(reversed_gate), ["2024.1"])


def test_check_retain_rejects_a_wrong_release_list():
    with pytest.raises(gate.GateMismatch, match="releases"):
        gate.check_retain("k", "yes", "no", _supply(CANON), ["2024.1"])


def test_check_retain_rejects_a_non_canonical_gate():
    with pytest.raises(gate.NotCanonical, match="retain-unverified"):
        gate.check_retain("k", "8080", "x", _supply(COMPOUND), ["2024.1"])


def test_check_retain_rejects_a_key_no_later_layer_supplies():
    with pytest.raises(gate.GateMismatch, match="no later layer"):
        gate.check_retain("k", "yes", "no", {}, ["2024.1"])


def test_check_retain_rejects_a_gate_in_the_010_layer():
    # 010-* sorts BEFORE 099-*, so it cannot carry retention for a changed value.
    supply = {"k": {"layers": ["all/010-2025.1.yml"], "value": CANON}}
    with pytest.raises(gate.GateMismatch, match="no later layer"):
        gate.check_retain("k", "yes", "no", supply, ["2024.1", "2024.2", "2025.1"])


# --- validate_dispositions / missing_dispositions ---------------------------


def test_retain_unverified_is_rejected_for_a_canonical_gate():
    """The strong path must not be bypassable out of habit."""
    plan = _plan_with("k", "yes", "no")
    with pytest.raises(gate.WrongDisposition, match="use --retain"):
        gate.validate_dispositions(
            plan,
            _opts(retain_unverified={"k"}),
            ["2024.1", "2024.2", "2025.1"],
            _supply(CANON),
        )


def test_retain_unverified_is_rejected_when_no_gate_exists():
    """Acknowledging nothing is not a disposition."""
    plan = _plan_with("k", "yes", "no")
    with pytest.raises(gate.GateMismatch, match="no gate to acknowledge"):
        gate.validate_dispositions(plan, _opts(retain_unverified={"k"}), ["2024.1"], {})


def test_retain_unverified_accepts_a_present_non_canonical_gate():
    plan = _plan_with("k", "8080", "x")
    notes = gate.validate_dispositions(
        plan, _opts(retain_unverified={"k"}), ["2024.1"], _supply(COMPOUND)
    )
    assert "acknowledged" in notes["k"]


def test_an_unknown_disposition_key_is_an_error():
    # A stale flag from a previous re-sync would otherwise read as a decision.
    plan = _plan_with("k", "yes", "no")
    with pytest.raises(gate.UnknownDisposition, match="typo_key"):
        gate.validate_dispositions(
            plan, _opts(accept_upstream={"typo_key"}), ["2024.1"], _supply(CANON)
        )


def test_a_failed_retention_check_propagates_rather_than_becoming_missing():
    plan = _plan_with("k", "yes", "no")
    reversed_gate = "{{ 'no' if openstack_version in ['2024.1'] else 'yes' }}"
    with pytest.raises(gate.GateMismatch):
        gate.validate_dispositions(
            plan, _opts(retain={"k"}), ["2024.1"], _supply(reversed_gate)
        )


def test_missing_dispositions_only_lists_rows_with_no_flag():
    plan = _plan_with("k", "yes", "no")
    assert gate.missing_dispositions(plan, _opts()) == ["k"]
    assert gate.missing_dispositions(plan, _opts(retain={"k"})) == []


def test_accept_upstream_is_noted():
    plan = _plan_with("k", "yes", "no")
    notes = gate.validate_dispositions(
        plan, _opts(accept_upstream={"k"}), ["2024.1"], _supply(CANON)
    )
    assert "upstream" in notes["k"]

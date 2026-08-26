from osism_drift.mirror_sync.model import Plan, Row


def _plan(**kw):
    base = dict(
        target_release="2025.2",
        release_shas={"2025.2": "a" * 40},
        defaults_base_sha="d" * 40,
        prev_release="2025.1",
        range_base_sha="b" * 40,
        mirror_writes={"all/001-nova.yml": b"x\n"},
        mirror_deletes=(),
        compat_writes={},
        compat_deletes=(),
        unowned_compat=(),
        changed_writes=(),
        created_paths=(),
        rows=(),
        added_keys=(),
        dropped_keys={},
        added_files=(),
        deleted_files=(),
    )
    base.update(kw)
    return Plan(**base)


def test_has_changes_is_false_when_every_write_is_unchanged():
    # mirror_writes is the DESIRED layer and always non-empty; only
    # changed_writes may drive has_changes, or exit 0 is unreachable.
    assert _plan().has_changes is False


def test_has_changes_true_on_a_changed_write():
    assert _plan(changed_writes=("all/001-nova.yml",)).has_changes is True


def test_has_changes_true_on_a_mirror_delete_alone():
    assert _plan(mirror_deletes=("all/001-zun.yml",)).has_changes is True


def test_has_changes_true_on_a_compat_delete_alone():
    assert _plan(compat_deletes=("all/010-2025.1.yml",)).has_changes is True


def test_semantic_keys_selects_only_semantic_rows_in_row_order():
    rows = (
        Row("b", "no", False, "representation", (), ""),
        Row("a", 1, 2, "semantic", (), ""),
        Row("c", 1, 1, "notation", (), ""),
        Row("z", 3, 4, "semantic", (), ""),
    )
    assert _plan(rows=rows).semantic_keys == ("a", "z")


def test_plan_is_frozen():
    import dataclasses

    import pytest

    p = _plan()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.target_release = "2026.1"

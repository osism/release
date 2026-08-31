import pytest
from osism_drift.mirror_sync import write
from osism_drift.mirror_sync.model import Plan


def _plan(tree, **kw):
    base = dict(
        target_release="2025.2",
        release_shas={"2025.2": "a" * 40},
        defaults_base_sha="d" * 40,
        prev_release=None,
        range_base_sha=None,
        mirror_writes={"all/001-nova.yml": b"new\n", "all/001-same.yml": b"same\n"},
        mirror_deletes=("all/001-zun.yml",),
        compat_writes={"all/010-2025.1.yml": "---\nk: 1\n"},
        compat_deletes=(),
        changed_writes=("all/001-nova.yml", "all/010-2025.1.yml"),
        created_paths=("all/010-2025.1.yml",),
        rows=(),
        added_keys=(),
        dropped_keys={},
        added_files=(),
        deleted_files=(),
        unowned_compat=(),
    )
    base.update(kw)
    return Plan(**base)


def _tree(tmp_path):
    d = tmp_path / "all"
    d.mkdir()
    (d / "001-nova.yml").write_bytes(b"old\n")
    (d / "001-same.yml").write_bytes(b"same\n")
    (d / "001-zun.yml").write_bytes(b"gone\n")
    return tmp_path


def test_writes_only_changed_paths(tmp_path):
    t = _tree(tmp_path)
    before = (t / "all" / "001-same.yml").stat().st_mtime_ns
    result = write.apply_plan(_plan(t), t)
    assert (t / "all" / "001-nova.yml").read_bytes() == b"new\n"
    assert (t / "all" / "010-2025.1.yml").read_text() == "---\nk: 1\n"
    # An unchanged file is not rewritten: it would dirty git diff and mtimes for
    # 55 of 56 files in a typical re-sync.
    assert (t / "all" / "001-same.yml").stat().st_mtime_ns == before
    assert result["written"] == ["all/001-nova.yml", "all/010-2025.1.yml"]


def test_deletes_after_writing(tmp_path):
    t = _tree(tmp_path)
    result = write.apply_plan(_plan(t), t)
    assert not (t / "all" / "001-zun.yml").exists()
    assert result["deleted"] == ["all/001-zun.yml"]


def test_a_missing_delete_target_is_not_an_error(tmp_path):
    t = _tree(tmp_path)
    (t / "all" / "001-zun.yml").unlink()
    assert write.apply_plan(_plan(t), t)["deleted"] == []


def test_refuses_a_path_outside_the_all_directory(tmp_path):
    t = _tree(tmp_path)
    p = _plan(
        t, mirror_writes={"../escape.yml": b"x\n"}, changed_writes=("../escape.yml",)
    )
    with pytest.raises(write.WriteFailed, match="not a plain relative path"):
        write.apply_plan(p, t)
    assert not (tmp_path.parent / "escape.yml").exists()


def test_refuses_when_the_all_directory_is_a_symlink(tmp_path):
    """A confinement root derived from the path being checked is not a check.

    With `all` a symlink to an external directory, resolving both sides put them
    in that directory and the comparison passed -- measured before the fix, the
    accepted path was outside the worktree entirely.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / "all").symlink_to(outside)
    with pytest.raises(write.WriteFailed, match="symlink"):
        write._resolve(tree, "all/001-nova.yml")


def test_refuses_a_symlinked_destination(tmp_path):
    # Writing through a link mutates its target rather than the planned path.
    t = _tree(tmp_path)
    target = tmp_path / "elsewhere.yml"
    target.write_bytes(b"target\n")
    (t / "all" / "001-link.yml").symlink_to(target)
    with pytest.raises(write.WriteFailed, match="symlink"):
        write._resolve(t, "all/001-link.yml")
    assert target.read_bytes() == b"target\n"


def test_refuses_a_symlinked_delete_target(tmp_path):
    t = _tree(tmp_path)
    target = tmp_path / "keep-me.yml"
    target.write_bytes(b"keep\n")
    (t / "all" / "001-link.yml").symlink_to(target)
    p = _plan(t, mirror_deletes=("all/001-link.yml",))
    with pytest.raises(write.WriteFailed, match="symlink"):
        write.apply_plan(p, t)
    assert target.exists()


def test_refuses_a_traversal_that_stays_lexically_under_all(tmp_path):
    # "all/../../escape.yml" starts with "all/" lexically, so a prefix test alone
    # accepts it.
    t = _tree(tmp_path)
    with pytest.raises(write.WriteFailed, match="not a plain relative path"):
        write._resolve(t, "all/../../escape.yml")


def test_refuses_an_absolute_path(tmp_path):
    t = _tree(tmp_path)
    with pytest.raises(write.WriteFailed, match="not a plain relative path"):
        write._resolve(t, "/etc/passwd")


def test_refuses_when_the_plan_is_blocked(tmp_path):
    t = _tree(tmp_path)
    p = _plan(t, unowned_compat=("all/010-2025.1.yml",))
    with pytest.raises(write.WriteFailed, match="blocked"):
        write.apply_plan(p, t)
    # Nothing was written, so the caller may remove the worktree.
    assert (t / "all" / "001-nova.yml").read_bytes() == b"old\n"


def test_signals_before_the_first_mutation(tmp_path):
    """The callback must fire before anything is touched, and only then.

    The caller keys the worktree's survival on it: a failure before the first
    write means nothing was modified, while a failure after it leaves partial
    state that must be preserved for inspection.
    """
    t = _tree(tmp_path)
    seen = []

    def note():
        # Nothing may have changed on disk yet when this fires.
        seen.append((t / "all" / "001-nova.yml").read_bytes())

    write.apply_plan(_plan(t), t, on_first_write=note)
    assert seen == [b"old\n"]


def test_a_preflight_failure_does_not_signal(tmp_path):
    t = _tree(tmp_path)
    p = _plan(t, changed_writes=("all/001-has-no-content.yml",))
    calls = []
    with pytest.raises(write.WriteFailed, match="no desired content"):
        write.apply_plan(p, t, on_first_write=lambda: calls.append(1))
    assert calls == [], "nothing was written, so the tree may be removed"


def test_a_blocked_plan_does_not_signal(tmp_path):
    t = _tree(tmp_path)
    p = _plan(t, unowned_compat=("all/010-2025.1.yml",))
    calls = []
    with pytest.raises(write.WriteFailed):
        write.apply_plan(p, t, on_first_write=lambda: calls.append(1))
    assert calls == []

import importlib.util
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "sync-mirror.py"


def _load():
    spec = importlib.util.spec_from_file_location("sync_mirror_cli", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_mode_also_requires_defaults_dir(capsys):
    # Report reads a worktree of this repo, so there is no mode where it is
    # optional -- and passing None through would reach Path(None) instead.
    rc = _load().main(["--target", "2025.2"])
    assert rc == 3
    assert "--defaults-dir is required" in capsys.readouterr().err


def _ready_plan():
    """A Plan that yields exit 1 -- changes present, nothing to dispose."""
    from osism_drift.mirror_sync.model import Plan

    return Plan(
        target_release="2025.2",
        release_shas={"2025.2": "a" * 40},
        defaults_base_sha="d" * 40,
        prev_release=None,
        range_base_sha=None,
        mirror_writes={"all/001-nova.yml": b"x\n"},
        mirror_deletes=(),
        compat_writes={},
        compat_deletes=(),
        changed_writes=("all/001-nova.yml",),
        created_paths=(),
        rows=(),
        added_keys=(),
        dropped_keys={},
        added_files=(),
        deleted_files=(),
        unowned_compat=(),
    )


class _Session:
    def __init__(self, tree):
        self._tree = tree

    def __enter__(self):
        return self._tree, "d" * 40

    def __exit__(self, *a):
        return False


def _apply_harness(mod, monkeypatch, tree, cwd=None):
    """Stub every outward call the apply path makes.

    `cwd` moves the default report file out of the repo: --report-file defaults to
    ./sync-mirror-<target>.txt, so without it the suite writes into the checkout.

    effective_range() and release_range() both glob latest/openstack-*.yml through
    `source`, which with no --base-dir means a live GitHub request. Without these
    two the tests pass on a networked machine and fail in offline CI -- verified by
    running them behind a dead proxy.
    """
    monkeypatch.setattr(mod.worktree, "session", lambda *a, **k: _Session(tree))
    monkeypatch.setattr(mod.mirror_sync, "build_plan", lambda *a, **k: _ready_plan())
    monkeypatch.setattr(mod.classify, "supply_info", lambda *a, **k: {})
    monkeypatch.setattr(mod.gate, "validate_dispositions", lambda *a, **k: {})
    monkeypatch.setattr(
        mod.effective, "effective_range", lambda cfg, target: ["2025.1", "2025.2"]
    )
    monkeypatch.setattr(
        mod.enablement, "release_range", lambda cfg: ["2025.1", "2025.2"]
    )
    if cwd is not None:
        monkeypatch.chdir(cwd)


def test_the_same_key_under_two_dispositions_is_an_error(tmp_path, capsys):
    rc = _load().main(
        [
            "--target",
            "2025.2",
            "--defaults-dir",
            str(tmp_path),
            "--retain",
            "k",
            "--accept-upstream",
            "k",
        ]
    )
    assert rc == 3
    assert "one disposition" in capsys.readouterr().err


def test_keep_is_decided_at_exit_not_entry(tmp_path, monkeypatch):
    """A refused apply must not leave the worktree behind.

    keep=args.apply would retain it for a run blocked on a missing disposition,
    occupying the default path so the NEXT run refuses on it.
    """
    mod = _load()
    seen = {}

    def fake_session(defaults_dir, path, base_ref, keep=False):
        seen["keep"] = keep
        raise mod.worktree.CreateFailed("stop")

    monkeypatch.setattr(mod.worktree, "session", fake_session)
    mod.main(["--target", "2025.2", "--defaults-dir", str(tmp_path), "--apply"])
    assert callable(seen["keep"]), "keep must be evaluated at exit"
    assert seen["keep"]() is False, "nothing written yet, so do not keep"


def test_a_midwrite_failure_names_the_worktree_and_how_to_remove_it(
    tmp_path, monkeypatch, capsys
):
    """The tree is kept on purpose, so the operator must be told where it is."""
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)

    def boom(plan, tree, on_first_write=None):
        on_first_write()  # mutation began
        raise OSError("No space left on device")

    monkeypatch.setattr(mod.write, "apply_plan", boom)
    rc = mod.main(["--target", "2025.2", "--defaults-dir", str(tmp_path), "--apply"])
    err = capsys.readouterr().err
    assert rc == 3
    assert "No space left on device" in err
    assert "partial write" in err
    assert "worktree remove" in err and str(wt) in err


def test_a_failure_before_any_write_does_not_claim_a_partial_write(
    tmp_path, monkeypatch, capsys
):
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    monkeypatch.setattr(
        mod.write,
        "apply_plan",
        lambda plan, tree, on_first_write=None: (_ for _ in ()).throw(
            mod.write.WriteFailed("bad path")
        ),
    )
    rc = mod.main(["--target", "2025.2", "--defaults-dir", str(tmp_path), "--apply"])
    err = capsys.readouterr().err
    assert rc == 3
    assert "bad path" in err
    assert "partial write" not in err


def test_a_successful_apply_returns_zero_and_emits_one_json_document(
    tmp_path, monkeypatch, capsys
):
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    monkeypatch.setattr(
        mod.write,
        "apply_plan",
        lambda plan, tree, on_first_write=None: (
            on_first_write(),
            {"written": ["all/001-nova.yml"], "deleted": []},
        )[1],
    )
    rc = mod.main(
        [
            "--target",
            "2025.2",
            "--defaults-dir",
            str(tmp_path),
            "--apply",
            "--format",
            "json",
        ]
    )
    out = capsys.readouterr().out
    # 0, not the pre-apply 1: the changes are no longer "ready to apply".
    assert rc == 0
    payload = json.loads(out)  # one document, not JSON followed by prose
    assert payload["applied"] is True
    assert payload["written"] == ["all/001-nova.yml"]
    assert "acceptance_command" in payload


def test_a_refused_apply_reports_applied_false_not_null(tmp_path, monkeypatch, capsys):
    """Three states, three values.

    null means "report run"; False means "an apply was attempted and did not
    happen". Collapsing them leaves a consumer unable to tell the difference.
    """
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    monkeypatch.setattr(mod.render, "exit_code", lambda *a, **k: 2)
    rc = mod.main(
        [
            "--target",
            "2025.2",
            "--defaults-dir",
            str(tmp_path),
            "--apply",
            "--format",
            "json",
        ]
    )
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["applied"] is False


def test_a_report_run_reports_applied_null(tmp_path, monkeypatch, capsys):
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    mod.main(
        ["--target", "2025.2", "--defaults-dir", str(tmp_path), "--format", "json"]
    )
    assert json.loads(capsys.readouterr().out)["applied"] is None


def test_worktree_failures_exit_3(tmp_path, monkeypatch, capsys):
    # Unit-testing worktree.CleanupFailed is not enough: the CLI must map the whole
    # category to the documented status instead of letting a traceback exit 1.
    mod = _load()
    for exc, msg in (
        (mod.worktree.CleanupFailed, "stuck"),
        (mod.worktree.CreateFailed, "no HEAD"),
        (mod.worktree.WorktreeExists, "occupied"),
    ):
        monkeypatch.setattr(
            mod.worktree,
            "session",
            lambda *a, exc=exc, msg=msg, **k: (_ for _ in ()).throw(exc(msg)),
        )
        rc = mod.main(["--target", "2025.2", "--defaults-dir", str(tmp_path)])
        assert rc == 3
        assert msg in capsys.readouterr().err


def test_arms_failures_exit_3(tmp_path, monkeypatch, capsys):
    mod = _load()
    for exc, msg in (
        (mod.layers.MonolithicLayout, "monolithic"),
        (mod.layers.EmptyUpstreamLayer, "listed no"),
        (mod.layers.UnownedCompatFile, "no marker"),
    ):
        monkeypatch.setattr(
            mod.worktree,
            "session",
            lambda *a, exc=exc, msg=msg, **k: (_ for _ in ()).throw(exc(msg)),
        )
        rc = mod.main(["--target", "2025.2", "--defaults-dir", str(tmp_path)])
        assert rc == 3
        assert msg in capsys.readouterr().err


def test_pins_from_conflicting_with_base_ref_is_an_error(tmp_path, capsys):
    report = tmp_path / "r.json"
    report.write_text('{"schema": 1}')
    rc = _load().main(
        [
            "--target",
            "2025.2",
            "--defaults-dir",
            str(tmp_path),
            "--pins-from",
            str(report),
            "--base-ref",
            "some-branch",
        ]
    )
    assert rc == 3
    assert "conflicts" in capsys.readouterr().err


def test_the_report_file_is_written_by_default_and_named_in_the_output(
    tmp_path, monkeypatch, capsys
):
    """The terminal elides values, so the file it points at has to exist."""
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    rc = mod.main(["--target", "2025.2", "--defaults-dir", str(tmp_path)])
    report = tmp_path / "sync-mirror-2025.2.txt"
    assert rc == 1
    assert report.exists()
    assert "sync-mirror detail report" in report.read_text(encoding="utf-8")
    assert str(report) in capsys.readouterr().out


def test_report_file_honours_an_explicit_path(tmp_path, monkeypatch, capsys):
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    chosen = tmp_path / "elsewhere.txt"
    mod.main(
        [
            "--target",
            "2025.2",
            "--defaults-dir",
            str(tmp_path),
            "--report-file",
            str(chosen),
        ]
    )
    assert chosen.exists()
    assert not (tmp_path / "sync-mirror-2025.2.txt").exists()


def test_a_refused_apply_still_leaves_the_report_behind(tmp_path, monkeypatch, capsys):
    """The values behind a refusal are exactly what is needed to resolve it."""
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    monkeypatch.setattr(mod.render, "exit_code", lambda *a, **k: 2)
    rc = mod.main(["--target", "2025.2", "--defaults-dir", str(tmp_path), "--apply"])
    assert rc == 2
    assert (tmp_path / "sync-mirror-2025.2.txt").exists()


def test_json_names_the_report_file_so_a_consumer_can_find_it(
    tmp_path, monkeypatch, capsys
):
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    mod.main(
        ["--target", "2025.2", "--defaults-dir", str(tmp_path), "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_file"].endswith("sync-mirror-2025.2.txt")


def test_commit_without_apply_is_refused(tmp_path, capsys):
    """--commit alone would silently do nothing: there is no plan on disk yet."""
    rc = _load().main(
        ["--target", "2025.2", "--defaults-dir", str(tmp_path), "--commit"]
    )
    assert rc == 3
    assert "--commit needs --apply" in capsys.readouterr().err


def test_commit_branch_without_apply_is_refused_too(tmp_path, capsys):
    rc = _load().main(
        [
            "--target",
            "2025.2",
            "--defaults-dir",
            str(tmp_path),
            "--commit-branch",
            "whatever",
        ]
    )
    assert rc == 3
    assert "--commit needs --apply" in capsys.readouterr().err


def test_apply_reports_the_branch_and_commit_it_made(tmp_path, monkeypatch, capsys):
    mod = _load()
    wt = tmp_path / "wt"
    wt.mkdir()
    _apply_harness(mod, monkeypatch, wt, cwd=tmp_path)
    monkeypatch.setattr(mod.commit_mod, "commit", lambda *a, **k: "c" * 40)
    rc = mod.main(
        ["--target", "2025.2", "--defaults-dir", str(tmp_path), "--apply", "--commit"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed cccccccccccc on sync-mirror/2025.2 in the worktree" in out
    assert "the reasoning is not" in out

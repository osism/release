import importlib.util
from pathlib import Path

import pytest

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


def test_apply_is_not_available_yet(capsys):
    rc = _load().main(["--target", "2025.2", "--defaults-dir", "/tmp", "--apply"])
    assert rc == 3
    assert "not implemented" in capsys.readouterr().err


def test_retain_flags_are_not_accepted_in_this_build():
    # They arrive with --apply; argparse must reject them here rather than
    # letting a run report "all dispositioned" without the gate parser.
    with pytest.raises(SystemExit):
        _load().main(["--target", "2025.2", "--defaults-dir", "/tmp", "--retain", "k"])


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

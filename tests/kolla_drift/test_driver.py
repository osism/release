import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_drift",
    Path(__file__).resolve().parents[2] / "src" / "check-drift.py",
)
driver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(driver)


def test_base_dir_not_found_exits_2(capsys, tmp_path):
    # --base-dir given but the OSISM repos aren't under it, no --remote-fallback.
    rc = driver.main(
        [
            "--group",
            "kolla",
            "--base-dir",
            str(tmp_path),
            "--plugin",
            "kolla_enablement_orphan",
        ]
    )
    assert rc == 2
    assert "not found under any --base-dir" in capsys.readouterr().err

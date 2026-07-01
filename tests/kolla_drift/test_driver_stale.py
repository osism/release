import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXT = Path(__file__).parent / "fixtures"


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "drift_driver", ROOT / "src" / "check-drift.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_cfg(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  default_owner: osism
  branch: main
release_version: latest
plugins:
  kolla_version_chain_inner: {enabled: true}
""")
    return cfg


def test_stale_entry_forces_exit_1_and_is_reported(tmp_path, capsys):
    driver = _load_driver()
    cfg = _write_cfg(tmp_path)
    al = tmp_path / "a.yml"
    al.write_text("""
allow:
  - plugin: kolla_version_chain_inner
    image: inert_x
    reason: "allowlist the real drift"
  - {plugin: kolla_version_chain_inner, image: ghost,   reason: "dead entry"}
""")
    rc = driver.main(
        [
            "--config",
            str(cfg),
            "--allowlist",
            str(al),
            "--group",
            "kolla",
            "--base-dir",
            str(FIXT),
            "--plugin",
            "kolla_version_chain_inner",
        ]
    )
    out = capsys.readouterr().out
    # inert_x (the only real drift) is allowlisted → no actionable drift,
    # but the `ghost` entry matched nothing → stale → exit 1.
    assert rc == 1
    assert "STALE ALLOWLIST" in out
    assert "ghost" in out


def test_no_allowlist_ignores_invalid_file(tmp_path, capsys):
    driver = _load_driver()
    cfg = _write_cfg(tmp_path)
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "allow:\n  - {plugin: x}\n"
    )  # missing image/reason → ConfigError if parsed
    rc = driver.main(
        [
            "--config",
            str(cfg),
            "--allowlist",
            str(bad),
            "--group",
            "kolla",
            "--no-allowlist",
            "--base-dir",
            str(FIXT),
            "--plugin",
            "kolla_version_chain_inner",
        ]
    )
    out = capsys.readouterr().out
    # --no-allowlist must NOT read the file: exit 1 from the real inert_x drift,
    # not exit 2 from a config error. (Old behavior parsed it and returned 2.)
    assert rc == 1
    assert "inert_x" in out

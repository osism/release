import importlib.util
from pathlib import Path

from osism_drift.config import Config, Remote
from osism_drift.driver import _network_blocked

_SPEC = importlib.util.spec_from_file_location(
    "check_drift",
    Path(__file__).resolve().parents[2] / "src" / "check-drift.py",
)
driver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(driver)


class _Repos:
    """A plugin that only reads OSISM repos."""

    NAME = "reads_repos_only"


class _External(_Repos):
    """A plugin that reads a host no checkout can stand in for."""

    NAME = "probes_upstream"
    EXTERNAL_HOSTS = ("tarballs.opendev.org",)


def _cfg(**kw):
    return Config(
        remote=Remote(
            "https://raw.githubusercontent.com/", "https://api.github.com/", "main"
        ),
        release_version="latest",
        plugins={},
        **kw,
    )


def test_local_only_run_refuses_a_plugin_that_must_reach_the_network():
    """--base-dir without --remote-fallback is a promise of no remote reads."""
    msg = _network_blocked([_External], _cfg(base_dirs=("/somewhere",)))
    assert "probes_upstream" in msg
    assert "tarballs.opendev.org" in msg
    assert "--remote-fallback" in msg


def test_remote_fallback_permits_the_network_read():
    cfg = _cfg(base_dirs=("/somewhere",), remote_fallback=True)
    assert _network_blocked([_External], cfg) is None


def test_a_fully_remote_run_is_never_blocked():
    """No --base-dir means every read was going to be remote anyway."""
    assert _network_blocked([_External], _cfg()) is None


def test_repo_only_plugins_are_not_blocked():
    assert _network_blocked([_Repos], _cfg(base_dirs=("/somewhere",))) is None


def test_every_offending_plugin_is_listed_at_once():
    """Same courtesy as describe_resolution: one run, the whole list."""
    other = type("Other", (), {"NAME": "probes_elsewhere", "EXTERNAL_HOSTS": ("x.io",)})
    msg = _network_blocked([_External, _Repos, other], _cfg(base_dirs=("/d",)))
    assert "probes_upstream" in msg
    assert "probes_elsewhere" in msg
    assert "reads_repos_only" not in msg


def test_network_plugin_in_a_local_only_run_exits_2(capsys, tmp_path):
    """End to end: refused before any probe, not mid-sweep."""
    rc = driver.main(
        [
            "--group",
            "kolla",
            "--base-dir",
            str(tmp_path),
            "--plugin",
            "kolla_source_ref_phase",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "tarballs.opendev.org" in err
    assert "no --base-dir can serve" in err


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

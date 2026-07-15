import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import rolling_pin

_BASE_YML = """\
---
manager_version: latest
docker_images:
  substation: 'latest'
  tempest: 'latest'
  sonic_vs: 'latest'
  edgy: main
  growing: develop
  shouty: LATEST
  ara_server: '1.7.5'
  weird_but_pinned: '6.1-23.10_beta'
"""


@pytest.fixture
def cfg(tmp_path):
    base = tmp_path / "release" / "latest"
    base.mkdir(parents=True)
    (base / "base.yml").write_text(_BASE_YML)
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={"rolling_pin": PluginCfg(enabled=True)},
        sources={},
    )


def _by_image(drifts):
    return {d.image: d for d in drifts}


def test_rolling_pins_flagged(cfg):
    """Every docker_images value in the rolling set is flagged, case-insensitively."""
    drifts = _by_image(rolling_pin.run(cfg, Allowlist(())))
    assert set(drifts) == {
        "substation",
        "tempest",
        "sonic_vs",
        "edgy",
        "growing",
        "shouty",
    }


def test_concrete_pin_not_flagged(cfg):
    """A concrete semver pin is never a candidate."""
    assert "ara_server" not in _by_image(rolling_pin.run(cfg, Allowlist(())))


def test_odd_but_pinned_not_flagged(cfg):
    """A denylist (not a not-semver heuristic) leaves odd-but-immutable tags alone."""
    assert "weird_but_pinned" not in _by_image(rolling_pin.run(cfg, Allowlist(())))


def test_top_level_key_ignored(cfg):
    """Only docker_images is scanned; the top-level manager_version is out of scope."""
    assert "manager_version" not in _by_image(rolling_pin.run(cfg, Allowlist(())))


def test_entry_fields(cfg):
    """The finding names the release key and shows the rolling value; actionable."""
    d = _by_image(rolling_pin.run(cfg, Allowlist(())))["substation"]
    assert d.image == "substation"
    assert d.alias == "substation"
    assert d.found == "latest"
    assert d.expected == "a concrete, immutable tag"
    assert d.expected_src == "release/latest/base.yml"
    assert d.severity == "actionable"
    assert not d.allowlisted


def test_allowlist_marks_entry(cfg):
    """A rolling-by-design pin is marked allowlisted, not dropped."""
    al = Allowlist(
        (
            AllowEntry(
                plugin="rolling_pin",
                image="tempest",
                alias=None,
                found_src=None,
                reason="rolling by design",
            ),
        )
    )
    drifts = _by_image(rolling_pin.run(cfg, al))
    assert drifts["tempest"].allowlisted
    assert not drifts["substation"].allowlisted

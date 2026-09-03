"""Guard against an incomplete reconstruction, which is this plugin's failure mode.

Skipped unless CHECK_DRIFT_INTEGRATION=1 and the checkouts are present. Below the
first real differential every catalog entry must resolve; an earlier draft of the
reconstruction, missing two sources, reported six false positives at every release.
"""

import dataclasses
import os
from pathlib import Path

import pytest

from osism_drift.config import Allowlist, load_config
from osism_drift.drift import catalog_role_missing as plugin

CLEAN_RELEASES = ("2024.1", "2024.2", "2025.1")

# Same two roots the end-to-end CLI invocation in docs/check-drift-catalog.md
# uses: OSISM repos (release, python-osism, the container-image-* repos,
# osism-kubernetes, ansible-playbooks) and, for kolla-ansible, openstack.
_OSISM_BASE = Path("~/data/rcs/osism").expanduser()
_OPENSTACK_BASE = Path("~/data/rcs/openstack").expanduser()

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "src" / "drift-config.yml"

_SKIP = os.environ.get("CHECK_DRIFT_INTEGRATION") != "1" or not (
    _OSISM_BASE.is_dir() and _OPENSTACK_BASE.is_dir()
)
_SKIP_REASON = "needs CHECK_DRIFT_INTEGRATION=1 and local checkouts under ~/data/rcs"


@pytest.fixture
def real_cfg():
    """The shipped drift-config.yml, wired the same way the CLI wires
    `--base-dir ~/data/rcs/osism --base-dir ~/data/rcs/openstack
    --remote-fallback`: local checkouts first, remote fallback for anything a
    base-dir does not resolve."""
    config = load_config(_CONFIG_PATH)
    return dataclasses.replace(
        config,
        base_dirs=(str(_OSISM_BASE), str(_OPENSTACK_BASE)),
        remote_fallback=True,
    )


def _cfg_for(config, *, releases):
    return dataclasses.replace(config, releases=releases)


@pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)
def test_no_findings_below_the_differential(real_cfg):
    for release in CLEAN_RELEASES:
        cfg = _cfg_for(real_cfg, releases=(release,))
        assert plugin.run(cfg, Allowlist(())) == [], f"false positives at {release}"


@pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)
def test_redis_is_found_from_2025_2(real_cfg):
    cfg = _cfg_for(real_cfg, releases=("2025.2",))
    found = {(d.image, d.alias) for d in plugin.run(cfg, Allowlist(()))}
    assert ("redis", "nutshell") in found
    assert ("redis", "collection-infrastructure") in found
    assert ("redis", "cloudpod-infrastructure") in found

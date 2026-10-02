"""Shared test config: makes src/ importable as `osism_drift`, and builds the
ansible-playbooks checkout that playbooks.osism_interface() reads."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# The tag fixtures/release/latest/base.yml pins as playbooks_version.
PLAYBOOKS_REF = "v0.fixture"

# playbooks/<env>/<name>.yml of the fixture ansible-playbooks repo. With the
# fixture generate-playbook-symlinks.py (SKIP: manager-netbox.yml) and
# render-playbooks.py (PREFIXES: infrastructure, manager) the interface is
# adminer -> infrastructure, netbox -> infrastructure, manager -> manager,
# traefik -> manager (the later prefix wins).
_PLAYBOOKS = (
    "playbooks/infrastructure/adminer.yml",
    "playbooks/infrastructure/netbox.yml",
    "playbooks/infrastructure/traefik.yml",
    "playbooks/manager/manager.yml",
    "playbooks/manager/netbox.yml",
    "playbooks/manager/traefik.yml",
)


def _git(repo, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def playbooks_base(tmp_path_factory):
    """A --base-dir holding a git ansible-playbooks repo tagged PLAYBOOKS_REF.

    ansible-playbooks is read at a ref (playbooks_version), which the source
    layer serves locally only from git objects of a pinned repo, so the
    checkout cannot live in the static fixtures tree. Pass this directory as a
    second base dir and pin ansible_playbooks (SourceCfg(branch="main")).
    """
    base = tmp_path_factory.mktemp("playbooks-base")
    repo = base / "ansible-playbooks"
    for rel in _PLAYBOOKS:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    _git(repo, "tag", PLAYBOOKS_REF)
    return base

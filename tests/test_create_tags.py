import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "create-tags.sh"
VERSION = "v0.20261009.0"
PROJECTS = [
    "kolla",
    "osism-kubernetes",
    "kolla-ansible",
    "ceph-ansible",
    "osism-ansible",
    "inventory-reconciler",
]
# Stub of scripts/check-versions.sh: passes if latest/openstack.yml of the
# checked directory points to 2026.1, and says which directory it checked
CHECK_VERSIONS_STUB = """#!/bin/bash
while [ $# -gt 0 ]; do
    case "$1" in
        --latest) latest="$2"; shift 2 ;;
        --tagging) tagging="$2"; shift 2 ;;
        *) shift ;;
    esac
done
echo "check-versions: $latest"
echo "tagging: $tagging"
[ "$(readlink "$latest/openstack.yml")" = "openstack-2026.1.yml" ]
"""


def files(series="2026.1"):
    return {
        "latest/openstack-2025.1.yml": "openstack_version: '2025.1'\n",
        "latest/openstack-2026.1.yml": "openstack_version: '2026.1'\n",
        "latest/openstack.yml": ("symlink", f"openstack-{series}.yml"),
        "etc/changelog-repositories.yml": "components: {}\n",
        "scripts/create-tags.sh": ("executable", SCRIPT.read_text()),
        "scripts/check-versions.sh": ("executable", CHECK_VERSIONS_STUB),
    }


@pytest.fixture
def repo(release_repo):
    release_repo.commit(files())
    release_repo.push()
    return release_repo


def create_tags(repo, *options):
    return subprocess.run(
        ["bash", str(repo.checkout / "scripts" / "create-tags.sh"), *options, VERSION],
        cwd=repo.checkout,
        env=repo.env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def test_tags_the_pushed_state_of_main(repo):
    result = create_tags(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    head = repo.git("rev-parse", "HEAD")
    assert repo.upstream_tags() == {f"{p}-{VERSION}": head for p in PROJECTS}


def test_refuses_another_branch(repo):
    repo.git("switch", "-q", "-c", "feature")
    result = create_tags(repo)
    assert result.returncode == 1
    assert "the current branch is feature" in result.stderr
    assert repo.upstream_tags() == {}


def test_refuses_detached_head(repo):
    repo.git("switch", "-q", "--detach")
    result = create_tags(repo)
    assert result.returncode == 1
    assert "the current branch is HEAD" in result.stderr
    assert repo.upstream_tags() == {}


def test_refuses_an_unpushed_commit(repo):
    repo.commit({"latest/other.yml": "other: 1\n"})
    result = create_tags(repo)
    assert result.returncode == 1
    assert "is not origin/main" in result.stderr
    assert repo.upstream_tags() == {}


def test_refuses_a_checkout_behind_origin(repo):
    repo.commit({"latest/other.yml": "other: 1\n"})
    repo.push()
    repo.git("reset", "-q", "--hard", "HEAD~1")
    result = create_tags(repo)
    assert result.returncode == 1
    assert "is not origin/main" in result.stderr
    assert repo.upstream_tags() == {}


def test_refuses_unreachable_origin(repo, tmp_path):
    repo.set_origin(tmp_path / "gone.git")
    result = create_tags(repo)
    assert result.returncode == 1
    assert "Could not fetch origin" in result.stderr


def test_failing_check_creates_no_tags(release_repo):
    release_repo.commit(files(series="2025.1"))
    release_repo.push()
    result = create_tags(release_repo)
    assert result.returncode == 1
    assert "check-versions.sh failed" in result.stderr
    assert release_repo.upstream_tags() == {}


def test_skip_check_versions(release_repo):
    release_repo.commit(files(series="2025.1"))
    release_repo.push()
    result = create_tags(release_repo, "--skip-check-versions")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipping scripts/check-versions.sh" in result.stdout
    assert len(release_repo.upstream_tags()) == len(PROJECTS)


def test_check_reads_the_commit_not_the_working_tree(release_repo):
    release_repo.commit(files(series="2025.1"))
    release_repo.push()
    link = release_repo.checkout / "latest" / "openstack.yml"
    link.unlink()
    link.symlink_to("openstack-2026.1.yml")
    result = create_tags(release_repo)
    assert result.returncode == 1
    assert "check-versions.sh failed" in result.stderr
    assert release_repo.upstream_tags() == {}


def test_check_runs_on_an_extracted_copy(repo):
    result = create_tags(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    checked = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("check-versions: ")
    ]
    assert len(checked) == 1
    assert str(repo.checkout) not in checked[0]
    assert "tagging: v0.20261009.0" in result.stdout


def test_unknown_option(repo):
    result = create_tags(repo, "--force")
    assert result.returncode == 1
    assert "Unknown option --force" in result.stderr
    assert repo.upstream_tags() == {}

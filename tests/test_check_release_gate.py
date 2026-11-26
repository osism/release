import os
import pathlib
import subprocess

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check-release-gate.sh"
)
# Stands in for uv and python3: prints the drift check's arguments
FAKE_RUNNER = '#!/bin/bash\necho "args: $*"\n'


@pytest.fixture
def repo(release_repo):
    release_repo.commit(
        {"scripts/check-release-gate.sh": ("executable", SCRIPT.read_text())}
    )
    release_repo.push()
    return release_repo


def gate(repo, tmp_path, *args):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("uv", "python3"):
        runner = bin_dir / name
        runner.write_text(FAKE_RUNNER)
        runner.chmod(0o755)
    env = dict(repo.env, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return subprocess.run(
        ["bash", str(repo.checkout / "scripts" / "check-release-gate.sh"), *args],
        cwd=repo.checkout,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def push_from_elsewhere(repo, tmp_path):
    """Advance upstream main from a second clone; return the new commit."""
    other = tmp_path / "other"

    def run(*args):
        return subprocess.run(
            ["git", *args], env=repo.env, check=True, capture_output=True, text=True
        )

    run("clone", "-q", str(repo.upstream), str(other))
    run("-C", str(other), "commit", "-q", "--allow-empty", "-m", "elsewhere")
    run("-C", str(other), "push", "-q", "origin", "main")
    return run("-C", str(other), "rev-parse", "HEAD").stdout.strip()


def test_defaults_to_freshly_fetched_origin_main(repo, tmp_path):
    upstream_head = push_from_elsewhere(repo, tmp_path)
    result = gate(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"--group catalog --release-gate {upstream_head}" in result.stdout
    assert "Release drift gate for origin/main" in result.stderr


def test_resolves_a_given_ref_to_its_sha(repo, tmp_path):
    head = repo.git("rev-parse", "HEAD")
    result = gate(repo, tmp_path, "main")
    assert result.returncode == 0, result.stderr
    assert f"--release-gate {head}" in result.stdout


def test_unknown_ref_exits_2(repo, tmp_path):
    result = gate(repo, tmp_path, "no-such-ref")
    assert result.returncode == 2
    assert "no-such-ref is not a commit" in result.stderr
    assert "args:" not in result.stdout


def test_two_arguments_exit_2(repo, tmp_path):
    result = gate(repo, tmp_path, "main", "main")
    assert result.returncode == 2
    assert "Usage:" in result.stderr

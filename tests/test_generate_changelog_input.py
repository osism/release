import os
import pathlib
import subprocess

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "generate-changelog-input.sh"
)
REPO = "osism/demo"

CHANGELOG = """# Changelog

All notable changes to this project will be documented in this file.

## [v0.20261001.0] - 2026-10-01

### Added

- Initial release (osism/demo#0)
"""

# Stub of the claude CLI. Like the real one, it prints its error message to
# stdout and exits 1 when a call fails. It fails on prompts that contain
# $FAKE_CLAUDE_FAIL_ON, answers without any references if
# $FAKE_CLAUDE_NO_REFS is set, and otherwise writes one entry per #N
# reference of a commit subject in the prompt.
CLAUDE_STUB = """#!/bin/bash
while [ $# -gt 0 ]; do
    case "$1" in
        -p) prompt="$2"; shift 2 ;;
        *) shift ;;
    esac
done
if [ -n "$FAKE_CLAUDE_FAIL_ON" ] && [[ "$prompt" == *"$FAKE_CLAUDE_FAIL_ON"* ]]; then
    echo "Failed to authenticate. API Error: 401 OAuth access token is invalid."
    exit 1
fi
echo "### Changed"
echo ""
if [ -n "$FAKE_CLAUDE_NO_REFS" ]; then
    echo "- Something changed"
    exit 0
fi
for num in $(echo "$prompt" | grep -oE '\\(#[0-9]+\\)' | tr -d '(#)' | sort -un); do
    [ "$num" = 0 ] || echo "- Change $num (osism/demo#$num)"
done
"""

# Stub of the GitHub CLI: not authenticated, so the script never calls the
# GitHub API
GH_STUB = """#!/bin/bash
exit 1
"""


@pytest.fixture
def repo(release_repo, tmp_path):
    release_repo.set_origin(f"https://github.com/{REPO}.git")
    release_repo.commit({"CHANGELOG.md": CHANGELOG}, "Initial release (#0)")
    release_repo.tag("v0.20261001.0", push=False)
    release_repo.commit({"a": "a"}, "Add a (#1)")
    release_repo.commit({"b": "b"}, "Fix b (#2)")
    release_repo.tag("v0.20261002.0", push=False)
    release_repo.commit({"c": "c"}, "Add c (#3)")
    release_repo.tag("v0.20261003.0", push=False)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, content in (("claude", CLAUDE_STUB), ("gh", GH_STUB)):
        (bin_dir / name).write_text(content)
        (bin_dir / name).chmod(0o755)
    release_repo.env = {
        **release_repo.env,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "INTERNAL_EMAIL_DOMAINS": "example.com",
    }
    return release_repo


def generate(repo, *options, **env):
    return subprocess.run(
        ["bash", str(SCRIPT), "--auto", "--tags-only", *options],
        cwd=repo.checkout,
        env={**repo.env, **env},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def changelog(repo):
    return (repo.checkout / "CHANGELOG.md").read_text()


def test_writes_entries_with_references(repo):
    result = generate(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    text = changelog(repo)
    assert "## [v0.20261003.0]" in text
    assert "## [v0.20261002.0]" in text
    new_entries = text[: text.index("## [v0.20261001.0]")]
    assert sorted(
        line for line in new_entries.splitlines() if line.startswith("- ")
    ) == [f"- Change {num} ({REPO}#{num})" for num in (1, 2, 3)]


def test_claude_failure_stops_the_run(repo):
    result = generate(repo, FAKE_CLAUDE_FAIL_ON="v0.20261002.0")
    assert result.returncode == 1
    assert "claude exited with status 1" in result.stderr
    assert "Failed to authenticate" in result.stderr
    assert changelog(repo) == CHANGELOG


def test_failure_on_a_later_tag_restores_changelog(repo):
    # The first tag succeeds and is inserted into CHANGELOG.md, then the
    # login fails on the second one
    result = generate(repo, FAKE_CLAUDE_FAIL_ON="v0.20261003.0")
    assert result.returncode == 1
    assert "Inserted into CHANGELOG.md" in result.stdout
    assert "CHANGELOG.md restored" in result.stderr
    assert changelog(repo) == CHANGELOG


def test_claude_failure_on_commit_message_commits_nothing(repo):
    head = repo.git("rev-parse", "HEAD")
    result = generate(repo, "--commit", FAKE_CLAUDE_FAIL_ON="Write the body")
    assert result.returncode == 1
    assert repo.git("rev-parse", "HEAD") == head
    assert repo.git("branch", "--show-current") == "main"
    assert changelog(repo) == CHANGELOG


def test_entry_without_references_is_not_repaired(repo):
    # Without the check, the direct repair would build the entry from the
    # raw commit subjects
    result = generate(repo, FAKE_CLAUDE_NO_REFS="1")
    assert result.returncode == 1
    assert "references are missing from the generated entry" in result.stderr
    assert changelog(repo) == CHANGELOG


def test_no_claude_run_needs_no_claude(repo):
    result = generate(repo, "--no-run", FAKE_CLAUDE_FAIL_ON="v0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert changelog(repo) == CHANGELOG

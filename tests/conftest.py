import os
import subprocess

import pytest

GIT_DATE = "2026-10-08T12:00:00+00:00"


def git_env(date=GIT_DATE):
    """A git environment independent of the user's configuration."""
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }


def run_git(repo, *args, date=GIT_DATE):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=git_env(date),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class ReleaseRepo:
    """A bare upstream repository and a clone of it with origin set to it."""

    def __init__(self, base):
        self.upstream = base / "upstream.git"
        self.checkout = base / "release"
        self.env = git_env()
        run_git(base, "init", "-q", "--bare", "-b", "main", str(self.upstream))
        run_git(base, "clone", "-q", str(self.upstream), str(self.checkout))
        run_git(self.checkout, "symbolic-ref", "HEAD", "refs/heads/main")

    def commit(self, files, message="release", date=GIT_DATE):
        """Write the files and commit them, returns the commit.

        A value is the content of the file, ("symlink", target) or
        ("executable", content).
        """
        for path, content in files.items():
            target = self.checkout / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            if isinstance(content, tuple) and content[0] == "symlink":
                target.symlink_to(content[1])
            elif isinstance(content, tuple) and content[0] == "executable":
                target.write_text(content[1])
                target.chmod(0o755)
            else:
                target.write_text(content)
        run_git(self.checkout, "add", "-A")
        run_git(self.checkout, "commit", "-q", "-m", message, date=date)
        return run_git(self.checkout, "rev-parse", "HEAD")

    def git(self, *args):
        """Run git in the checkout, returns its output."""
        return run_git(self.checkout, *args)

    def push(self):
        run_git(self.checkout, "push", "-q", "-u", "origin", "main")

    def tag(self, name, ref="HEAD", annotated=False, push=True):
        """Create or move a tag, lightweight unless annotated."""
        args = ["-a", "-m", name] if annotated else []
        run_git(self.checkout, "tag", "-f", *args, name, ref)
        if push:
            run_git(self.checkout, "push", "-q", "-f", "origin", f"refs/tags/{name}")

    def move_upstream_tag(self, name, commit):
        """Move a tag on origin only, as another checkout would."""
        run_git(
            self.checkout, "push", "-q", "-f", "origin", f"{commit}:refs/tags/{name}"
        )

    def delete_local_tag(self, name):
        run_git(self.checkout, "tag", "-d", name)

    def set_origin(self, url):
        run_git(self.checkout, "remote", "set-url", "origin", str(url))

    def upstream_tags(self):
        """Tag name -> commit on origin."""
        output = run_git(
            self.upstream,
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            "refs/tags",
        )
        return dict(line.split() for line in output.splitlines())


@pytest.fixture
def release_repo(tmp_path):
    """A release repository: a bare upstream and a clone with origin set to it.

    Tests of code that uses src/release_tags.py point its REPO_ROOT to the
    checkout themselves, so that this fixture also serves tests of scripts
    that do not use it.
    """
    return ReleaseRepo(tmp_path)

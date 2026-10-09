# Shared lookup of the release tags of this repository for the image checks
# (src/check-kolla-images.py, src/check-osism-images.py).
#
# A release tag <project>-v<version> names the commit of this repository a
# component image is built from. The checks need the tag in the local
# checkout, because they read latest/ at the tag. A plain "git fetch --tags"
# does not update a tag that was moved on origin, so every lookup compares
# the local tag with origin and stops if they differ.

import os
import pathlib
import posixpath
import re
import subprocess
from datetime import datetime

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SYMLINK_MODE = "120000"


class ReleaseError(Exception):
    pass


def git(*args):
    """The output of a git command in this repository, None if it fails."""
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def parse_time(value):
    """A timestamp of git or Harbor, to the second."""
    return datetime.fromisoformat(re.sub(r"\.\d+", "", value).replace("Z", "+00:00"))


def remote_commit(release_tag):
    """The commit the tag points to on origin.

    The tags of scripts/create-tags.sh are lightweight: origin only has the
    ref itself. An annotated tag also has the peeled ref ^{} that names the
    commit, which is preferred.
    """
    ref = f"refs/tags/{release_tag}"
    # never prompt for credentials: an origin that needs them fails instead
    result = subprocess.run(
        ["git", "ls-remote", "origin", ref, f"{ref}^{{}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise ReleaseError(
            f"the tag {release_tag} could not be looked up on origin: "
            f"{result.stderr.strip()}"
        )
    refs = {}
    for line in result.stdout.splitlines():
        commit, name = line.split("\t")
        refs[name] = commit
    commit = refs.get(f"{ref}^{{}}") or refs.get(ref)
    if commit is None:
        raise ReleaseError(f"the tag {release_tag} does not exist on origin")
    return commit


def release_commit(release_tag):
    """The commit of the tag and when it was made.

    The local tag has to point to the same commit as on origin.
    """
    output = git("log", "-1", "--format=%H %cI", f"refs/tags/{release_tag}^{{commit}}")
    if output is None:
        raise ReleaseError(
            f"the tag {release_tag} does not exist in this repository "
            "(git fetch --tags)"
        )
    commit, committed = output.split()
    upstream = remote_commit(release_tag)
    if upstream != commit:
        raise ReleaseError(
            f"the local tag {release_tag} points to {commit[:7]}, origin to "
            f"{upstream[:7]} (git fetch --tags --force)"
        )
    return commit, parse_time(committed)


def read_latest(ref, name):
    """A YAML file in latest/ at a ref, None if it does not exist.

    A symlink is followed: git stores its target as its content.
    """
    path = f"latest/{name}"
    entry = git("ls-tree", ref, path)
    if not entry:
        return None
    if entry.split()[0] == SYMLINK_MODE:
        target = git("show", f"{ref}:{path}")
        path = posixpath.normpath(posixpath.join("latest", target.strip()))
    content = git("show", f"{ref}:{path}")
    return yaml.safe_load(content) if content is not None else None


def release_series(release_tag, files, key):
    """The series the files in latest/ name at the tag of this repository."""
    for name in files:
        data = read_latest(release_tag, name)
        if data is None:
            continue
        if isinstance(data, dict) and data.get(key):
            return str(data[key])
        break
    raise ReleaseError(f"latest/ names no {key} at the tag {release_tag}")

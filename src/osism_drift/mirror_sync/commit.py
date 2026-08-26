"""Commit an applied plan inside the worktree, on request.

Only reached through --commit, and only after --apply has written. Every
judgement the re-sync needs -- the disposition of each semantic value, any 099
gate -- has been made and validated by then, so what is left is mechanical:
branch the detached worktree, stage exactly what the plan touched, write a
message whose provenance the tool already computed.

The branch is not optional. The worktree is detached, so a commit made here
would be unreferenced the moment the worktree is removed, and the operator would
be left digging in the reflog for work they were told had been committed.
"""

import subprocess
from pathlib import Path


class CommitError(Exception):
    """Any failure while committing; the CLI maps it to exit 3."""


class BranchExists(CommitError):
    """The target branch is already present; refuse rather than move it."""


def _git(tree, *args, stdin=None):
    return subprocess.run(
        ["git", "-C", str(tree), *args],
        capture_output=True,
        check=False,
        input=stdin.encode() if stdin else None,
    )


def default_branch(target) -> str:
    return f"sync-mirror/{target}"


def signoff(tree) -> str:
    """`Signed-off-by:` from the repo's git config.

    Without it the eventual PR fails Zuul's DCO gate, so a commit this tool made
    would be born broken. Passing --commit is the operator asking for the commit,
    which is the same assertion `git commit -s` makes.
    """
    name = _git(tree, "config", "user.name").stdout.decode().strip()
    email = _git(tree, "config", "user.email").stdout.decode().strip()
    if not name or not email:
        raise CommitError(
            "git config user.name and user.email must be set to sign off a commit"
        )
    return f"Signed-off-by: {name} <{email}>"


def message(target, provenance, tree) -> str:
    """Subject, the provenance block, and the sign-off.

    Deliberately not the whole message: why each semantic value was accepted or
    retained is the reviewer's contribution and no generator can supply it. The
    caller tells the operator to amend.
    """
    subject = f"kolla: re-sync the mirror layer for {target}"
    return f"{subject}\n\n{provenance.rstrip()}\n\n{signoff(tree)}\n"


def commit(tree, branch, target, provenance, paths) -> str:
    """Branch, stage `paths`, commit. Returns the new commit's sha."""
    tree = Path(tree)
    if (
        _git(
            tree, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"
        ).returncode
        == 0
    ):
        raise BranchExists(
            f"branch {branch} already exists in {tree}; pass --commit-branch"
        )
    r = _git(tree, "switch", "-c", branch)
    if r.returncode != 0:
        raise CommitError(f"git switch -c {branch} failed: {_err(r)}")

    # --all so a path the plan deleted is staged as a deletion. Pathspecs, not a
    # bare `git add -A`: the tree should hold nothing but the plan's writes, and
    # if it does that is a bug worth surfacing rather than sweeping into the
    # commit.
    r = _git(tree, "add", "--all", "--", *sorted(paths))
    if r.returncode != 0:
        raise CommitError(f"git add failed: {_err(r)}")

    r = _git(tree, "commit", "-F", "-", stdin=message(target, provenance, tree))
    if r.returncode != 0:
        raise CommitError(f"git commit failed: {_err(r)}")

    head = _git(tree, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise CommitError("commit succeeded but HEAD did not resolve")
    return head.stdout.decode().strip()


def _err(r) -> str:
    return r.stderr.decode().strip() or r.stdout.decode().strip() or str(r.returncode)

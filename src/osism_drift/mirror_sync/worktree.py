"""Worktree lifecycle for sync-mirror.

Both report and apply read a worktree created from --base-ref. Report removes it
on exit; apply keeps it as the artifact to review. Sharing the tree is what makes
report and apply the same plan, and it means the operator's uncommitted work is
never consulted -- which the report states, so nobody expects it to be.

Writing into a throwaway tree also removes a whole category of hazard the earlier
in-place design needed guards for: no dirty-tree check, no --allow-dirty escape,
no recovery command that has to distinguish tracked from created files.
"""

import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class WorktreeError(Exception):
    """Base for every worktree failure, so the CLI can map them all to exit 3."""


class WorktreeExists(WorktreeError):
    """The target path is already present; refuse rather than guess."""


class CreateFailed(WorktreeError):
    """The worktree could not be created, or its HEAD did not verify."""


class CleanupFailed(WorktreeError):
    """The worktree could not be removed; the next run would refuse on its path."""


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )


def default_path(defaults_dir, target) -> Path:
    """<defaults-repo>.worktrees/sync-<target>/defaults

    The leaf must be named `defaults`: source._local_repo_dir() resolves a repo as
    <base-dir>/<repo-name>, so check-drift can only address the synced tree if the
    directory is called that. The nesting keeps the workspace's .worktrees layout.
    """
    d = Path(defaults_dir).resolve()
    return d.parent / f"{d.name}.worktrees" / f"sync-{target}" / "defaults"


def create(defaults_dir, path, base_ref) -> str:
    """Add a detached worktree at `path` from `base_ref`; return its commit."""
    path = Path(path)
    if path.exists():
        raise WorktreeExists(
            f"{path} already exists; remove it "
            f"(git -C {defaults_dir} worktree remove {path} --force) "
            "or pass --worktree-path"
        )
    r = _git(defaults_dir, "worktree", "add", str(path), "--detach", base_ref)
    if r.returncode != 0:
        raise CreateFailed(
            f"git worktree add failed: {r.stderr.decode().strip() or r.returncode}"
        )
    head = _git(path, "rev-parse", "HEAD")
    sha = head.stdout.decode().strip()
    if head.returncode != 0 or not _SHA40.match(sha):
        # A worktree we cannot identify is useless and must not be left behind:
        # the next run would refuse on its path.
        err = CreateFailed(
            f"worktree at {path} created but HEAD did not resolve to a commit"
        )
        if not remove(defaults_dir, path):
            raise CleanupFailed(
                f"{err}; and it could not be removed -- run "
                f"'git -C {defaults_dir} worktree remove {path} --force'"
            ) from err
        raise err
    return sha


def remove(defaults_dir, path) -> bool:
    """Remove the worktree. True on success; the caller decides how loud to be."""
    return (
        _git(defaults_dir, "worktree", "remove", str(path), "--force").returncode == 0
    )


@contextmanager
def session(defaults_dir, path, base_ref, keep=False):
    """Yield (path, base_sha); remove the worktree on exit unless `keep`.

    Removal happens on the exception path too, so a refused run leaves nothing
    behind. Report mode always removes; apply keeps it once a write has started.

    A failed cleanup is not swallowed: it leaves the default path occupied, so the
    next run refuses and the operator sees a confusing "already exists" instead of
    the real cause. On the happy path that raises CleanupFailed; on an exception
    path the original error is chained onto it, so the first diagnostic survives.
    """
    path = Path(path)
    # Evaluated at EXIT, not entry: an apply refused for a missing disposition or
    # a failed gate must not keep the tree, and only the caller knows -- at the
    # end -- whether a write actually began.
    keep_now = keep if callable(keep) else (lambda: keep)
    sha = create(defaults_dir, path, base_ref)
    try:
        yield path, sha
    except BaseException as exc:
        if not keep_now() and not remove(defaults_dir, path):
            raise CleanupFailed(
                f"{path} could not be removed -- run "
                f"'git -C {defaults_dir} worktree remove {path} --force'"
            ) from exc
        raise
    if not keep_now() and not remove(defaults_dir, path):
        raise CleanupFailed(
            f"report finished but {path} could not be removed -- run "
            f"'git -C {defaults_dir} worktree remove {path} --force'"
        )

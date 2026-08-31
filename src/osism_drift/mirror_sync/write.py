"""Apply a Plan to the worktree.

Deliberately dull: every decision was made while computing the Plan. The writer
refuses to act on a plan that says it is blocked, confines paths, signals when it
starts mutating, and writes in a defined order.
"""

from pathlib import Path

_ALL = "all"


class WriteFailed(Exception):
    """Refused before writing, or failed part-way through."""


def _resolve(tree, rel):
    """Confine `rel` beneath <tree>/all before it becomes a filesystem path.

    Checked lexically, then component by component -- never by resolving the
    candidate and comparing it to a resolved root. Deriving the root from the same
    path being checked is not a confinement check: with `all` a symlink to an
    external directory, both sides resolve into that directory and every write
    lands outside the worktree while the comparison passes. Measured before the
    fix: `_resolve(tree, "all/001-nova.yml")` returned a path under /tmp/outside
    and was accepted.

    So: reject an absolute path or any `..` component up front, require the first
    component to be `all`, and refuse if any component along the way is a symlink.
    The last one also stops the writer mutating a link's target instead of the
    planned path.
    """
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise WriteFailed(f"{rel} is not a plain relative path beneath {_ALL}/")
    if rel_path.parts[:1] != (_ALL,):
        raise WriteFailed(f"{rel} is not beneath {_ALL}/")

    root = Path(tree)
    cur = root
    for part in rel_path.parts:
        cur = cur / part
        if cur.is_symlink():
            raise WriteFailed(
                f"{rel}: {cur} is a symlink; refusing to write or delete through it"
            )
    return cur


def apply_plan(plan, tree, on_first_write=None) -> dict:
    """Write the changed paths and apply the deletes. Returns what it did.

    `on_first_write` is called once, after preflight and immediately before the
    first mutation. The caller uses it to decide the worktree's fate: a failure
    before it means nothing was touched and the tree can be removed, while a
    failure after it leaves partial state that must be preserved for inspection.
    Signalling only on a successful return would delete the evidence of a
    part-way failure -- exactly the state worth keeping.
    """
    if plan.blocked:
        raise WriteFailed("plan is blocked: " + "; ".join(plan.blocked))

    desired = dict(plan.mirror_writes)
    desired.update(plan.compat_writes)

    # Preflight: resolve every path and confirm every content before touching any
    # of them, so a bad path cannot leave a half-applied tree.
    todo = []
    for rel in plan.changed_writes:
        if rel not in desired:
            raise WriteFailed(f"{rel} is in changed_writes but has no desired content")
        todo.append((rel, _resolve(tree, rel), desired[rel]))
    dels = [
        (rel, _resolve(tree, rel))
        for rel in tuple(plan.mirror_deletes) + tuple(plan.compat_deletes)
    ]

    if on_first_write is not None:
        on_first_write()

    written = []
    for rel, path, content in todo:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        written.append(rel)

    # Deletes last: an interrupted run then leaves a tree with too much rather
    # than too little, which is the easier state to reason about.
    deleted = []
    for rel, path in dels:
        if path.exists():
            path.unlink()
            deleted.append(rel)
    return {"written": written, "deleted": deleted}

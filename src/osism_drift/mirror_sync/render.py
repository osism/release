"""Text and JSON renderers, and the exit status.

Three independent facts sit behind the status -- whether anything would change,
whether any semantic row lacks a disposition, and whether a write happened -- so
each is its own JSON field. A single axis cannot express "work to do but nothing
to review", which is the shape of an ordinary clean round.
"""

from .model import SyncOpts

_CLASSES = ("semantic", "notation", "representation", "overridden")


def _opts(plan, opts):
    return opts or SyncOpts(target=plan.target_release)


def _missing(plan, opts):
    """Semantic keys with no disposition, in row order.

    Only --accept-upstream counts. `retain` / `retain_unverified` exist on SyncOpts
    for --apply, but the canonical-gate parser that gives them meaning is not
    in this build: honouring them here would let a run report "all dispositioned"
    without performing the checks those flags promise.
    """
    done = set(opts.accept_upstream)
    return [k for k in plan.semantic_keys if k not in done]


def json_payload(plan, opts=None, applied=None) -> dict:
    """The machine-readable report. Stable, sorted, and free of wall-clock.

    Carries target_release, release_shas and defaults_base_sha because
    --pins-from reads exactly those three back to replay a run.
    """
    opts = _opts(plan, opts)
    return {
        "schema": 1,
        "target_release": plan.target_release,
        "release_shas": dict(sorted(plan.release_shas.items())),
        "defaults_base_sha": plan.defaults_base_sha,
        "prev_release": plan.prev_release,
        "range_base_sha": plan.range_base_sha,
        "has_changes": plan.has_changes,
        "missing_dispositions": _missing(plan, opts),
        "blocked": list(plan.blocked),
        "applied": applied,
        "counts": {
            c: sum(1 for r in plan.rows if r.cls == c)
            for c in _CLASSES
            if any(r.cls == c for r in plan.rows)
        },
        "rows": [
            {
                "key": r.key,
                "old": repr(r.old),
                "new": repr(r.new),
                "class": r.cls,
                "commits": list(r.commits),
                "note": r.note,
            }
            for r in sorted(plan.rows, key=lambda r: (r.cls, r.key))
        ],
        "changed_writes": sorted(plan.changed_writes),
        "created_paths": sorted(plan.created_paths),
        "deletes": sorted(plan.mirror_deletes) + sorted(plan.compat_deletes),
        "added_files": sorted(plan.added_files),
        "deleted_files": sorted(plan.deleted_files),
        "added_keys": sorted(plan.added_keys),
        "dropped_keys": dict(sorted(plan.dropped_keys.items())),
    }


def text(plan, opts=None) -> str:
    """The human report."""
    opts = _opts(plan, opts)
    out = [
        f"target {plan.target_release} @ "
        f"{plan.release_shas.get(plan.target_release, '?')}",
        f"defaults base {plan.defaults_base_sha}",
        "",
    ]
    if not plan.rows:
        # An empty section would read as "nothing was computed". Say which it is.
        out.append("no value differences between the current and desired mirror")
        out.append("")
    for cls in _CLASSES:
        rows = [r for r in plan.rows if r.cls == cls]
        if not rows:
            continue
        out.append(f"{cls} ({len(rows)})")
        for r in sorted(rows, key=lambda r: r.key):
            out.append(f"  {r.key}: {r.old!r} -> {r.new!r}")
            if r.note:
                out.append(f"      {r.note}")
            for line in r.commits:
                out.append(f"      upstream: {line}")
        out.append("")
    if plan.added_files:
        out.append(f"files added:   {', '.join(sorted(plan.added_files))}")
    if plan.deleted_files:
        out.append(f"files deleted: {', '.join(sorted(plan.deleted_files))}")
    if plan.added_keys:
        out.append(
            f"keys added ({len(plan.added_keys)}): "
            f"{', '.join(sorted(plan.added_keys))}"
        )
    if plan.dropped_keys:
        out.append(f"keys dropped ({len(plan.dropped_keys)}):")
        for key, dest in sorted(plan.dropped_keys.items()):
            out.append(f"  {key} -> {dest}")
    if plan.blocked:
        out.append("")
        out.append("--apply is blocked until these are resolved:")
        out.extend(f"  {b}" for b in plan.blocked)
        out.append(
            "  (add the '# generated-by: sync-mirror' header line, or let sync-mirror "
            "create the file)"
        )
    missing = _missing(plan, opts)
    if missing:
        out.append("")
        # Only --accept-upstream exists in this build; naming the retention flags
        # would point the operator at options argparse rejects.
        out.append("needs a disposition (--accept-upstream):")
        out.extend(f"  {k}" for k in missing)
        out.append("  (retention dispositions arrive with --apply)")
    return "\n".join(out) + "\n"


def exit_code(plan, opts) -> int:
    """0 clean | 1 changes, ready to apply | 2 operator action needed.

    2 covers both an undispositioned semantic row and a blocker such as an
    unowned 010 file: in either case --apply cannot proceed, so reporting 1
    ("would apply cleanly") would be false.
    """
    if _missing(plan, opts) or plan.blocked:
        return 2
    return 1 if plan.has_changes else 0

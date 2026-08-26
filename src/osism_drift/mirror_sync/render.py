"""Text and JSON renderers, and the exit status.

Three independent facts sit behind the status -- whether anything would change,
whether any semantic row lacks a disposition, and whether a write happened -- so
each is its own JSON field. A single axis cannot express "work to do but nothing
to review", which is the shape of an ordinary clean re-sync.
"""

import shlex
import textwrap
from pathlib import Path

from .model import SyncOpts

_CLASSES = ("semantic", "notation", "representation", "overridden")

# One line per class, from what classify() actually tests. A bare "semantic (16)"
# does not tell a first-time reader whether those 16 are a problem.
_SUMMARY = {
    "semantic": "meaning changed and no rule explains it; blocks --apply",
    "notation": "identical Jinja once whitespace beside {{ }} / {% %} is collapsed",
    "representation": "bool spelling changed, truth value did not ('no' -> False)",
    "overridden": "an unconditional OSISM layer already supplies the key",
}

_EXIT_MEANING = {
    0: "nothing to do; the mirror already matches",
    1: "changes ready to apply; re-run with --apply",
    2: "operator action needed; the plan was not applied",
    3: "refused; the plan was not applied",
}

# Longer than this, or containing a newline, and the terminal shows shape only.
_INLINE_MAX = 60

# Names shown per group before the list is truncated to a count.
_NAME_CAP = 12


def _inlinable(value) -> bool:
    if isinstance(value, str) and "\n" in value:
        return False
    return len(repr(value)) <= _INLINE_MAX


def _shape(value) -> str:
    """A value too big to print, described well enough to recognise in the report."""
    if isinstance(value, str):
        lead = "multi-line " if "\n" in value else ""
        return f"{lead}str, {len(value)} chars"
    if isinstance(value, (list, tuple)):
        return f"{type(value).__name__}, {_plural(len(value), 'item')}"
    if isinstance(value, dict):
        return f"dict, {_plural(len(value), 'key')}"
    return type(value).__name__


def _plural(n, noun) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _wrap_names(names, indent="    ", cap=_NAME_CAP) -> list:
    """Wrapped name list, truncated past `cap`.

    101 key names is 30 lines of terminal for one repeated finding. The names are
    all in the detail file, so the terminal owes the reader a sample and a count,
    not the set.
    """
    names = sorted(names)
    shown, rest = names[:cap], len(names) - cap
    text = ", ".join(shown)
    if rest > 0:
        text += f", ... and {rest} more (see report)"
    return textwrap.wrap(
        text, width=76, initial_indent=indent, subsequent_indent=indent
    )


def _opts(plan, opts):
    return opts or SyncOpts(target=plan.target_release)


def _missing(plan, opts):
    """Semantic keys carrying no disposition flag at all.

    Validation is not done here: gate.validate_dispositions() raises on a bad
    disposition, so by the time a report renders, every flag present is either
    sound or the run has already exited 3. That keeps exit_code's signature
    unchanged from the report-only build.
    """
    from . import gate

    return gate.missing_dispositions(plan, opts)


def acceptance_command(worktree_path, base_dirs, target=None, newest=None) -> str:
    """The check-drift invocation that measures the synced tree.

    Prepends the worktree's parent to the run's own base_dirs. Three things go
    wrong otherwise:

    - order: source._local_repo_dir() takes the first --base-dir containing a
      directory named after the repo, so with the operator's checkout ahead of the
      worktree the detector reads the unsynced tree and reports a pass on nothing;
    - completeness: the run's other roots are needed too. Dropping the OpenStack
      root loses kolla-ansible and the command cannot run at all;
    - resolution policy: passing ANY --base-dir puts check-drift in local mode
      (source._resolve returns remote only when base_dirs is empty), so a repo
      absent from every root becomes a hard SourceError rather than a remote read.
      The worktree parent holds only `defaults`, and a run with no --base-dir was
      reading everything remotely, which this command would otherwise silently
      convert to local-only. Hence --remote-fallback, unconditionally; it governs
      repo resolution alone, so it costs nothing when every repo is local.

    Every path is shell-quoted: this is copy-pasted, and an unquoted path with a
    space in it silently means something else.
    """
    parent = str(Path(worktree_path).parent)
    dirs = [parent] + [d for d in base_dirs if d != parent]
    lines = "".join(f"    --base-dir {shlex.quote(d)} \\\n" for d in dirs)
    cmd = (
        "PYTHONPATH=src python3 src/check-drift.py --group all \\\n"
        + lines
        + "    --remote-fallback"
    )
    if target and newest and target != newest:
        # check-drift derives its own range by globbing latest/openstack-*.yml and
        # anchors the mirror on the newest of those, with no CLI flag to narrow it.
        # So with a newer release declared, this command measures THAT re-sync and
        # its count says nothing about the sync just applied. Say so rather than
        # letting a large number read as a failure of this run.
        cmd += (
            f"\n\n# NOTE: the newest supported release is {newest}, not the "
            f"{target} just synced.\n"
            "# check-drift derives its range from latest/openstack-*.yml and has no\n"
            "# flag to narrow it, so the command above measures the "
            f"{newest} re-sync and\n"
            f"# its count is not a verdict on this sync. To gate on {target}, set\n"
            f'#   releases: [..., "{target}"]\n'
            "# in src/drift-config.yml (or a copy passed with --config) first."
        )
    return cmd


def commit_summary(plan, opts=None, notes=None) -> str:
    """Provenance for the commit that carries an apply, ready to paste.

    The generated 010 headers already record every pin, so the tree is not
    missing that. What nothing records is which disposition each semantic key
    got: accepting upstream leaves no trace at all, because the value simply
    ends up matching upstream. That belongs in the commit message, and retyping
    fifteen key names by hand is where it stops happening.

    Wrapped at 72 columns, which is what a commit body wants.
    """
    opts = _opts(plan, opts)
    out = [
        f"Generated by sync-mirror for {plan.target_release}.",
        "",
        "Upstream pins (openstack/kolla-ansible ansible/group_vars/all):",
    ]
    out += [f"  {r}: {plan.release_shas[r]}" for r in sorted(plan.release_shas)]
    out.append(f"osism/defaults base: {plan.defaults_base_sha}")
    groups = [
        ("accepted upstream", sorted(opts.accept_upstream)),
        ("retained, gate verified", sorted(opts.retain)),
        ("retained, gate NOT verified", sorted(opts.retain_unverified)),
    ]
    present = [(label, keys) for label, keys in groups if keys]
    if present:
        out += ["", "Dispositions:"]
        for label, keys in present:
            out.append(f"  {label} ({len(keys)}):")
            out += textwrap.wrap(
                ", ".join(keys),
                width=72,
                initial_indent="    ",
                subsequent_indent="    ",
            )
    return "\n".join(out) + "\n"


def json_payload(plan, opts=None, applied=None, notes=None) -> dict:
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
        "dispositions": dict(sorted((notes or {}).items())),
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


def text(plan, opts=None, notes=None, code=None, report_path=None, applied=None) -> str:
    """The human report: what to decide, not what changed.

    Every full value lives in the detail file instead. A re-wrapped Jinja
    expression repr()s to a single 1400-char line, and printing a hundred of them
    ahead of the actionable tail buries the only part an operator acts on.
    """
    opts = _opts(plan, opts)
    out = [
        f"target        {plan.target_release} @ "
        f"{plan.release_shas.get(plan.target_release, '?')}",
        f"defaults base {opts.base_ref} @ {plan.defaults_base_sha}",
        "",
    ]
    if not plan.rows:
        # An empty section would read as "nothing was computed". Say which it is.
        out.append("no value differences between the current and desired mirror")
        out.append("")
    else:
        out.extend(_summary(plan))
        out.extend(_semantic_section(plan))
        out.extend(_grouped_sections(plan))
    out.extend(_file_and_key_moves(plan))
    if plan.blocked:
        out.append("")
        out.append("--apply is blocked until these are resolved:")
        out.extend(f"  {b}" for b in plan.blocked)
        out.append(
            "  (add the '# generated-by: sync-mirror' header line, or let sync-mirror "
            "create the file)"
        )
    if notes:
        out.append("")
        out.append("dispositioned:")
        for key in sorted(notes):
            out.append(f"  {key}: {notes[key]}")
    missing = _missing(plan, opts)
    if missing:
        out.append("")
        out.append(
            "needs a disposition (--accept-upstream / --retain / "
            "--retain-unverified):"
        )
        out.extend(f"  {k}" for k in missing)
    if report_path:
        out.append("")
        out.append(f"full values for every key: {report_path}")
    if code is not None:
        out.append("")
        # Exit 0 means two different things: a run that had nothing to do, and a
        # run that applied everything. Reporting "the mirror already matches"
        # after writing 47 files is worse than saying nothing.
        meaning = (
            "the plan was applied to the worktree"
            if applied
            else _EXIT_MEANING.get(code, "unknown")
        )
        out.append(f"exit {code}: {meaning}")
    return "\n".join(out) + "\n"


def _summary(plan) -> list:
    """The counts table. Each class says what it means, so a bare number does not
    send the reader to the source to find out whether it needs them."""
    counts = {c: sum(1 for r in plan.rows if r.cls == c) for c in _CLASSES}
    present = [c for c in _CLASSES if counts[c]]
    sem = counts["semantic"]
    lead = f"{len(plan.rows)} mirror values changed. "
    lead += (
        f"{sem} need a decision from you; the rest are carried through."
        if sem
        else "None need a decision."
    )
    width = max(len(c) for c in present)
    rows = [f"  {c:<{width}} {counts[c]:>4}  {_SUMMARY[c]}" for c in present]
    return [lead, ""] + rows + [""]


def _semantic_section(plan) -> list:
    """Semantic rows in full-ish: the only class the operator has to read.

    A value is inlined only when it is short and single-line. Anything else is
    described by shape and left to the detail file -- the terminal cannot render
    a 500-char re-wrap in a form anyone would read.
    """
    rows = [r for r in plan.rows if r.cls == "semantic"]
    if not rows:
        return []
    out = [
        f"semantic ({len(rows)}) - each needs --accept-upstream / --retain / "
        "--retain-unverified"
    ]
    for r in sorted(rows, key=lambda r: r.key):
        if _inlinable(r.old) and _inlinable(r.new):
            out.append(f"  {r.key}: {r.old!r} -> {r.new!r}")
        else:
            out.append(f"  {r.key}: {_shape(r.old)} -> {_shape(r.new)} (see report)")
        for line in r.commits:
            out.append(f"      upstream: {line}")
    return out + [""]


def _grouped_sections(plan) -> list:
    """The three non-gating classes, grouped by note.

    classify() emits a fixed note per class (overridden varies only by supplying
    layer), so a section is a handful of groups however many keys it holds. The
    old shape repeated one constant sentence 101 times.
    """
    out = []
    for cls in _CLASSES:
        if cls == "semantic":
            continue
        rows = [r for r in plan.rows if r.cls == cls]
        if not rows:
            continue
        out.append(f"{cls} ({len(rows)}) - carried through, no decision needed")
        groups = {}
        for r in rows:
            groups.setdefault(r.note, []).append(r.key)
        for note, keys in sorted(groups.items()):
            out.extend(
                textwrap.wrap(
                    f"{note or 'no note'} ({len(keys)}):",
                    width=78,
                    initial_indent="  ",
                    subsequent_indent="  ",
                )
            )
            out.extend(_wrap_names(keys))
        out.append("")
    return out


def _file_and_key_moves(plan) -> list:
    """File-level effects and the added/dropped key counts.

    Dropped keys are summarised per destination: the names are in the detail
    file, and 75 of them ahead of the blocked list is what pushed the actionable
    tail off the screen.
    """
    out = []
    if plan.added_files:
        out.append(f"files added ({len(plan.added_files)}):")
        out.extend(_wrap_names(plan.added_files))
    if plan.deleted_files:
        out.append(f"files deleted ({len(plan.deleted_files)}):")
        out.extend(_wrap_names(plan.deleted_files))
    if plan.added_keys:
        out.append(f"keys added ({len(plan.added_keys)}) - listed in the report")
    if plan.dropped_keys:
        dests = {}
        for key, dest in plan.dropped_keys.items():
            dests.setdefault(dest, []).append(key)
        out.append(
            f"keys routed into back-compat layers ({len(plan.dropped_keys)} in the "
            "desired tree, not all of them new):"
        )
        for dest, keys in sorted(dests.items()):
            out.append(f"  {dest}: {_plural(len(keys), 'key')} ({_status(plan, dest)})")
    return out


def _status(plan, path) -> str:
    """What this run actually does to one back-compat layer.

    "rewritten" is true but reads as data loss, and a 010 file is rewritten on
    every run regardless: its header carries the pinned commit of each release in
    range, so the bytes move even when no key does. Report the key-level effect
    and name the header explicitly when that is the only change.
    """
    if path in plan.created_paths:
        return "new file"
    eff = plan.compat_effects.get(path)
    if eff is None:
        return "unchanged" if path not in plan.changed_writes else "updated"
    parts = []
    if eff["added"]:
        parts.append(f"+{_plural(len(eff['added']), 'key')}")
    if eff["removed"]:
        parts.append(f"-{_plural(len(eff['removed']), 'key')}")
    if eff["updated"]:
        parts.append(f"{_plural(len(eff['updated']), 'value')} updated")
    if parts:
        return ", ".join(parts)
    if path in plan.changed_writes:
        # Same keys, same values: only the pinned-commit header moved.
        return "same keys and values; only the pin header is refreshed"
    return "unchanged"


def exit_code(plan, opts) -> int:
    """0 clean | 1 changes, ready to apply | 2 operator action needed.

    2 covers both an undispositioned semantic row and a blocker such as an
    unowned 010 file: in either case --apply cannot proceed, so reporting 1
    ("would apply cleanly") would be false.
    """
    if _missing(plan, opts) or plan.blocked:
        return 2
    return 1 if plan.has_changes else 0

"""The full per-key report, written to a file rather than the terminal.

The terminal report answers "what do I have to decide"; this answers "what
exactly changed". They are split because the values that most need reading are
the ones a terminal renders worst: a re-wrapped Jinja expression is one 1449-char
line with literal \\n escapes when repr()'d, and no operator reads that.

Multi-line values are therefore emitted with REAL newlines and indented, so a
re-wrap reads as a re-wrap. Values are the parsed YAML, not the source bytes:
that is what classification compared, so it is what a disposition is about.
"""

from .model import SyncOpts

_CLASSES = ("semantic", "notation", "representation", "overridden")

_MEANING = {
    "semantic": "the meaning changed and no rule explains it; each key needs a "
    "disposition and blocks --apply until it has one",
    "notation": "identical Jinja once whitespace adjacent to {{ }} / {% %} is "
    "collapsed; carried through unchanged",
    "representation": "a bool-ish token whose truth value did not change "
    "('no' -> False); the 001 layer is a byte-for-byte mirror, so upstream's "
    "spelling is carried as-is. See docs/sync-mirror.md",
    "overridden": "an OSISM layer that applies unconditionally already supplies "
    "this key, so the upstream change cannot reach a deployment",
}


def _block(value, indent="     ") -> list:
    """One value, readable: real newlines for multi-line strings, repr otherwise.

    repr() is kept for everything else because quoting matters here -- 'no' and
    False are the whole point of the representation class, and str() would render
    them identically.
    """
    if isinstance(value, str) and "\n" in value:
        return [f"{indent}{line}" if line else "" for line in value.split("\n")]
    return [f"{indent}{value!r}"]


def text(plan, opts=None) -> str:
    """The detail report for one plan."""
    opts = opts or SyncOpts(target=plan.target_release)
    out = [
        "sync-mirror detail report",
        "",
        f"target        {plan.target_release} @ "
        f"{plan.release_shas.get(plan.target_release, '?')}",
        f"defaults base {opts.base_ref} @ {plan.defaults_base_sha}",
        "",
        "Every changed value, in full. The terminal report inlines only short",
        "single-line values; the rest are here. Values are the parsed YAML.",
    ]

    for cls in _CLASSES:
        rows = [r for r in plan.rows if r.cls == cls]
        if not rows:
            continue
        out += ["", "=" * 72, f"{cls} ({len(rows)})", _MEANING[cls], "=" * 72]
        for r in sorted(rows, key=lambda r: r.key):
            out += ["", f"-- {r.key}"]
            for line in r.commits:
                out.append(f"   upstream: {line}")
            if r.note:
                out.append(f"   {r.note}")
            out += ["", "   mirror (current):"]
            out += _block(r.old)
            out += ["", f"   upstream {plan.target_release}:"]
            out += _block(r.new)

    if plan.added_files or plan.deleted_files:
        out += ["", "=" * 72, "files", "=" * 72]
        out += [f"  + {f}" for f in sorted(plan.added_files)]
        out += [f"  - {f}" for f in sorted(plan.deleted_files)]
    if plan.added_keys:
        out += ["", "=" * 72, f"keys added ({len(plan.added_keys)})", "=" * 72]
        out += [f"  {k}" for k in sorted(plan.added_keys)]
    if plan.dropped_keys:
        out += ["", "=" * 72, f"keys dropped ({len(plan.dropped_keys)})", "=" * 72]
        out += [
            "the target no longer defines these and no other OSISM layer supplies",
            "them, so they move into a release-scoped back-compat layer:",
            "",
        ]
        out += [f"  {k} -> {d}" for k, d in sorted(plan.dropped_keys.items())]

    return "\n".join(out) + "\n"

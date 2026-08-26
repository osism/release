"""Disposition validation for semantic rows.

--accept-upstream needs nothing checked. --retain claims a 099 release gate keeps
the old value for the older releases, and that claim is checkable for one shape:

    {{ <old-literal> if openstack_version in [<release>, ...] else <new-literal> }}

Association matters. Requiring only that the old value, the new value and the
release names each APPEAR somewhere is satisfied by a reversed gate -- new value
for the older releases, old value for the target -- so the parser binds the
in-branch literal to the old value and the else-branch to the new one.

There is no jinja2 dependency, and adding one to parse arbitrary gates would be
the wrong trade: real gates include compound conditions, `not in`, nested
ternaries and variable branches (099-kolla.yml lines 103 and 233). Those are
legitimate and simply not parseable, so they take --retain-unverified and the
report says sync-mirror checked nothing.
"""

import re

import yaml

from . import classify

_LIT = r"(?:'[^']*'|\"[^\"]*\")"
# Every list item must be a quoted literal. Accepting arbitrary contents lets a
# mixed list such as ['2024.1', 2024.2] through, where YAML yields a str and a
# float and a later sorted() raises TypeError -- a traceback and exit 1 instead of
# a controlled refusal.
_LIST = rf"{_LIT}(?:\s*,\s*{_LIT})*\s*,?"
CANONICAL = re.compile(
    r"^\{\{\s*"
    rf"(?P<old>{_LIT})"
    r"\s+if\s+(?:openstack_version|openstack_release)\s+in\s*\[\s*"
    rf"(?P<releases>{_LIST})"
    r"\s*\]\s+else\s+"
    rf"(?P<new>{_LIT})"
    r"\s*\}\}$"
)


class GateError(Exception):
    """Base: one except clause covers every disposition refusal."""


class NotCanonical(GateError):
    """The gate exists but this parser cannot read it."""


class GateMismatch(GateError):
    """The gate is canonical and says the wrong thing, or there is no gate."""


class UnknownDisposition(GateError):
    """A disposition names a key that is not a semantic row in this plan."""


class WrongDisposition(GateError):
    """A canonical gate was passed through the acknowledgement flag."""


def _lit(text):
    return yaml.safe_load(text)


def parse_gate(value):
    """{"old", "new", "releases"} for a canonical gate, else None."""
    if not isinstance(value, str):
        return None
    m = CANONICAL.match(" ".join(value.split()))
    if not m:
        return None
    rels = [_lit(r.strip()) for r in m.group("releases").split(",") if r.strip()]
    # Belt and braces: the regex already admits only quoted literals, but a
    # non-string here would reach sorted() alongside strings and raise TypeError
    # rather than producing a refusal the operator can act on.
    if not all(isinstance(r, str) for r in rels):
        return None
    return {
        "old": _lit(m.group("old")),
        "new": _lit(m.group("new")),
        "releases": rels,
    }


def _gate_layer(supply, key):
    """(record, layer) for the later, non-010 layer supplying `key`.

    010-* sorts before 099-*, so a gate there cannot retain a changed value -- the
    mirror's new value would win for every release.
    """
    rec = supply.get(key) or {}
    layers = [
        L
        for L in (rec.get("layers") or [])
        if classify.is_unconditional_layer(L) or L.startswith("all/099-")
    ]
    return (rec, layers[-1]) if layers else (rec, None)


def check_retain(key, old, new, supply, older):
    """Return a note, or raise. `older` is the releases that must keep `old`."""
    rec, layer = _gate_layer(supply, key)
    if layer is None:
        raise GateMismatch(
            f"{key}: no later layer supplies it, so nothing retains the old value "
            "for the older releases"
        )
    g = parse_gate(rec.get("value"))
    if g is None:
        raise NotCanonical(
            f"{key}: the gate in {layer} is not in the canonical "
            "'<old> if openstack_version in [...] else <new>' shape; sync-mirror "
            "cannot verify it -- pass --retain-unverified to record an "
            "acknowledgement instead"
        )
    if g["old"] != old or g["new"] != new:
        raise GateMismatch(
            f"{key}: the gate in {layer} binds the wrong values -- its in-branch "
            f"yields {g['old']!r} and its else yields {g['new']!r}, but the mirror "
            f"changes {old!r} -> {new!r}"
        )
    if sorted(g["releases"]) != sorted(older):
        raise GateMismatch(
            f"{key}: the gate in {layer} lists releases {sorted(g['releases'])}, "
            f"but the releases needing the old value are {sorted(older)}"
        )
    return f"verified against the canonical gate in {layer}"


def missing_dispositions(plan, opts) -> list:
    """Semantic keys carrying no disposition flag at all, in row order.

    Deliberately does no validation: a bad disposition is an operational failure
    with its own diagnostic, not a row that merely still needs one. Flattening the
    two produces a generic "needs a disposition" for a gate that was supplied and
    wrong, which tells the operator nothing about what to fix.
    """
    given = set(opts.accept_upstream) | set(opts.retain) | set(opts.retain_unverified)
    return [r.key for r in plan.rows if r.cls == "semantic" and r.key not in given]


def validate_dispositions(plan, opts, older, supply) -> dict:
    """{key: note} for dispositions that pass. Raises on any that do not.

    Three failures are operational, not "still needs review":

    - a disposition naming a key that is not a semantic row here. A stale flag
      from a previous re-sync would otherwise pass silently and read as though a
      decision had been recorded for something.
    - --retain whose gate does not bind the right values to the right branches, or
      names the wrong releases. The diagnostic is the whole value of the check.
    - --retain-unverified for a gate this parser CAN bind, or for a key with no
      gate at all. The first records "unchecked" for something checkable; the
      second acknowledges nothing while --apply would change the value for every
      release.
    """
    semantic = {r.key: r for r in plan.rows if r.cls == "semantic"}
    for flag, keys in (
        ("--accept-upstream", opts.accept_upstream),
        ("--retain", opts.retain),
        ("--retain-unverified", opts.retain_unverified),
    ):
        for key in sorted(set(keys) - set(semantic)):
            raise UnknownDisposition(
                f"{flag} {key}: not a semantic row in this plan -- a stale flag "
                "from an earlier re-sync, or a typo"
            )

    notes = {}
    for key in sorted(opts.retain):
        row = semantic[key]
        notes[key] = check_retain(key, row.old, row.new, supply, older)
    for key in sorted(opts.retain_unverified):
        rec, layer = _gate_layer(supply, key)
        if layer is None:
            raise GateMismatch(
                f"--retain-unverified {key}: no later layer supplies it, so there "
                "is no gate to acknowledge -- nothing retains the old value for "
                "the older releases"
            )
        if parse_gate(rec.get("value")) is not None:
            raise WrongDisposition(
                f"--retain-unverified {key}: the gate in {layer} is canonical and "
                "can be verified -- use --retain"
            )
        notes[key] = f"acknowledged; gate in {layer} is not in the canonical shape"
    for key in sorted(opts.accept_upstream):
        notes[key] = "upstream value accepted for every supported release"
    return notes

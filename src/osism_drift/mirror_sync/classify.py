"""Classify a changed mirror value: overridden / notation / representation / semantic.

Only `semantic` blocks a write. The other three each exist for a stated reason,
two of them are narrower than they first appear.
"""

import re
from pathlib import Path

from osism_drift import enablement, secrets_map, source

# Explicit, because Python disagrees: bool("no") is True. Never call bool() on a
# raw value in this module.
TRUTH = {
    True: True,
    "yes": True,
    "true": True,
    "on": True,
    False: False,
    "no": False,
    "false": False,
    "off": False,
}

_VERSIONS_LAYER = "versions.yml"
_ALL = "all/"
_RELEASE_VARS = ("openstack_version", "openstack_release")
_COND = re.compile(r"\bif\b|\bin\b\s*\[")


def _truth(v):
    """The truth value of a bool-ish token, or None if it is not one."""
    if isinstance(v, bool):
        return TRUTH[v]
    if isinstance(v, str):
        return TRUTH.get(v.lower())
    return None


def _norm_jinja(s: str) -> str:
    """Collapse whitespace ADJACENT to Jinja delimiters, and nothing else.

    Reaches `{% if` -> `{%if` and `{{ a }}` -> `{{a}}`, which is the measured
    difference in the transport_url keys. Deliberately cannot reach inside an
    expression: collapsing there can alter a string literal, a filter argument or
    an operator.
    """
    s = re.sub(r"\{\{\s+", "{{", s)
    s = re.sub(r"\s+\}\}", "}}", s)
    s = re.sub(r"\{%\s+", "{%", s)
    s = re.sub(r"\s+%\}", "%}", s)
    return s


def _is_release_conditional(value) -> bool:
    """True if the value branches on the release anywhere inside it.

    Recurses through mappings and sequences rather than testing only strings. A
    later layer may supply a dict or list whose *nested* value carries the gate,
    and treating that as unconditional would classify a real per-release change as
    non-gating -- the exact outcome narrowing `overridden` exists to prevent. No
    such value exists in the defaults layers today, but 90 non-string values do, so
    adding a gate inside one is an ordinary edit.
    """
    if isinstance(value, str):
        return any(v in value for v in _RELEASE_VARS) and bool(_COND.search(value))
    if isinstance(value, dict):
        return any(
            _is_release_conditional(k) or _is_release_conditional(v)
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_is_release_conditional(v) for v in value)
    return False


def is_unconditional_layer(label: str) -> bool:
    """True for layers that apply to every deployment unconditionally.

    Judged by lexical position against the mirror prefix, not by a hard-coded
    prefix: an earlier design tested for "all/0", which silently excluded
    all/100-ansible.yml -- a real global layer that sorts after the mirror.

    010-* is excluded because it is release-scoped by content, and anything not an
    all/*.yml file (a per-release overlay, say) is excluded because overlay-only
    supply proves nothing about the other releases.
    """
    if label == _VERSIONS_LAYER:
        return True
    if not label.startswith(_ALL) or not label.endswith(".yml"):
        return False
    name = label[len(_ALL) :]
    if name.startswith("010-"):
        return False
    return name > enablement.MIRROR_PREFIX


def supply_info(config, tree) -> dict:
    """{key: {"layers": [...], "value": ...}} for keys supplied outside the mirror.

    osism_supply_excluding_mirror() returns a key SET only, which is why
    membership alone cannot decide `overridden`; the per-layer values are read
    here.

    The rendered versions.yml layer has to be included or the exception in
    is_unconditional_layer is unreachable in a real run: an earlier design
    globbed defaults/all/*.yml only, so openstack_release -- the single key that
    exception exists for -- came out `semantic`.
    """
    info = {}
    for path in sorted((Path(tree) / "all").glob("*.yml")):
        if path.name.startswith(enablement.MIRROR_PREFIX):
            continue
        for key, value in enablement.groupvars_values([path.read_bytes()]).items():
            rec = info.setdefault(key, {"layers": [], "value": None})
            rec["layers"].append(f"{_ALL}{path.name}")
            rec["value"] = value  # last lexical layer wins, as Ansible does

    # Same source osism_supply_excluding_mirror() uses. Keys only, which is all the
    # exception needs: it is keyed on the LAYER, not the value. The file is
    # generated per deployment with openstack_version already bound and sorts after
    # every 001-* file, so it is unconditional and global by construction.
    repo, path = enablement._VERSIONS_TEMPLATE
    for key in secrets_map.parse_secret_keys(source.read(repo, path, config)):
        rec = info.setdefault(key, {"layers": [], "value": None})
        rec["layers"].append(_VERSIONS_LAYER)
    return info


def classify(key, old, new, supply):
    """(class, note) for one key whose parsed value changed."""
    rec = supply.get(key)
    if rec:
        unconditional = [
            L for L in (rec.get("layers") or []) if is_unconditional_layer(L)
        ]
        if unconditional and (
            _VERSIONS_LAYER in unconditional
            or not _is_release_conditional(rec.get("value"))
        ):
            return "overridden", f"supplied unconditionally by {unconditional[-1]}"

    if isinstance(old, str) and isinstance(new, str):
        if _norm_jinja(old) == _norm_jinja(new):
            return "notation", "whitespace adjacent to Jinja delimiters only"

    t_old, t_new = _truth(old), _truth(new)
    if t_old is not None and t_new is not None and t_old == t_new:
        return (
            "representation",
            "truth value unchanged; only the YAML type or spelling differs "
            "(see docs/sync-mirror.md)",
        )

    return "semantic", ""

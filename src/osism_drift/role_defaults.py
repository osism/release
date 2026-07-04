"""Parser for ansible-collection-services/roles/X/defaults/main.yml."""

import yaml


def parse_role_defaults(body: bytes) -> dict[str, str]:
    """Return {alias: tag} for every <alias>_tag with a concrete string value.

    Skips any <alias>_tag whose value contains a Jinja2 expression — those
    are intentionally undeclared (the "drop the hard-coded pin; require an
    override" pattern, e.g. {{ lookup('vars', X, default=Undefined) }}) and
    not drift.

    Exception: a lone single-hop reference to the alias's own <alias>_version
    (i.e. the value is exactly "{{ <alias>_version }}", ignoring whitespace) is
    resolved one hop by reading <alias>_version from the same file, when that
    value is itself concrete. This surfaces pins that live behind the
    <alias>_tag -> <alias>_version indirection. Any other Jinja2 value — a
    composed tag, a reference to a different var, or a non-concrete _version —
    stays skipped.
    """
    data = yaml.safe_load(body) or {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if not isinstance(k, str) or not k.endswith("_tag"):
            continue
        alias = k[: -len("_tag")]
        s = str(v)
        if "{{" in s:
            resolved = _resolve_version_hop(s, alias, data)
            if resolved is None:
                continue
            s = resolved
        out[alias] = s
    return out


def _resolve_version_hop(value: str, alias: str, data: dict) -> str | None:
    """Resolve a lone "{{ <alias>_version }}" reference to a concrete pin.

    Returns the concrete <alias>_version value, or None if `value` is anything
    other than an exact single-hop reference to <alias>_version whose target is
    present and concrete.
    """
    stripped = value.strip()
    if not (stripped.startswith("{{") and stripped.endswith("}}")):
        return None
    if stripped[2:-2].strip() != f"{alias}_version":
        return None
    version_key = f"{alias}_version"
    if version_key not in data:
        return None
    resolved = str(data[version_key])
    if "{{" in resolved:
        return None
    return resolved

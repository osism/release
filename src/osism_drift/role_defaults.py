"""Parser for ansible-collection-services/roles/X/defaults/main.yml."""

import yaml


def parse_role_defaults(body: bytes) -> dict[str, str]:
    """Return {alias: tag} for every <alias>_tag with a concrete string value.

    Skips any <alias>_tag whose value contains a Jinja2 expression — those
    are intentionally undeclared (the "drop the hard-coded pin; require an
    override" pattern, e.g. {{ lookup('vars', X, default=Undefined) }}) and
    not drift.
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
            continue
        out[alias] = s
    return out

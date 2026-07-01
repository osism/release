"""Parser for the rendered testbed/environments/manager/images.yml."""

import re
from dataclasses import dataclass

import yaml

KIND_PLAIN = "plain"
KIND_LATEST_OVERRIDE = "latest_override"
KIND_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ManagerEntry:
    value: str
    kind: str


_OVERRIDE_RE = re.compile(
    r"^\{\{\s*\w+_version\|default\(['\"]([^'\"]+)['\"]\)\s*\}\}$"
)


def parse_manager(body: bytes) -> dict:
    """Return {alias: ManagerEntry} for every <alias>_tag entry in the rendered file."""
    data = yaml.safe_load(body) or {}
    out = {}
    for k, v in data.items():
        if not isinstance(k, str) or not k.endswith("_tag"):
            continue
        alias = k[: -len("_tag")]
        s = str(v)
        m = _OVERRIDE_RE.match(s)
        if m is not None:
            out[alias] = ManagerEntry(value=m.group(1), kind=KIND_LATEST_OVERRIDE)
        elif "{{" in s:
            out[alias] = ManagerEntry(value=s, kind=KIND_UNRESOLVED)
        else:
            out[alias] = ManagerEntry(value=s, kind=KIND_PLAIN)
    return out

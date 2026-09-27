"""Parsers for the runner images' versions.yml.j2 templates.

container-image-kolla-ansible's template references release keys as
versions['K'] inside kolla_*_version defaults; parse_versions_keys reads those.
container-image-osism-ansible's template carries release pins for the services
osism-ansible deploys as whole lines, one per role variable; parse_versions_map
reads those. Both images deliver the rendered file as
group_vars/all/100-versions-<runner>.yml through the inventory reconciler.
"""

import re

from osism_drift.http import SourceError

_KEY_RE = re.compile(r"versions\['([a-z0-9_]+)'\]")

# <variable>: "{{ versions['<key>'] }}" -- a whole-line pin, no filter.
_PIN_RE = re.compile(
    r"""^(?P<variable>[a-z0-9_]+):\s*"\{\{\s*versions\['(?P<key>[a-z0-9_]+)'\]\s*\}\}"\s*$"""
)
# {% if '<key>' in versions -%} -- the guard that omits a pin the release lacks.
_GUARD_RE = re.compile(
    r"""^\{%-?\s*if\s+'(?P<key>[a-z0-9_]+)'\s+in\s+versions\s*-?%\}\s*$"""
)


def parse_versions_keys(body: bytes) -> set[str]:
    """Return the distinct keys K referenced as versions['K'] in the template."""
    return set(_KEY_RE.findall(body.decode("utf-8")))


def parse_versions_map(body: bytes) -> dict[str, str]:
    """Return {variable: release_key} for every whole-line versions['K'] pin.

    Matches lines of the form ``<variable>: "{{ versions['<key>'] }}"``. Such a
    line may be guarded by ``{% if '<key>' in versions -%}`` on the line before
    it, so that a release without the key renders nothing for it. A guard that
    names a different key than the line it guards would drop the pin silently
    for every release, so that raises SourceError.
    """
    out = {}
    guard = None
    for lineno, line in enumerate(body.decode("utf-8").splitlines(), start=1):
        m = _GUARD_RE.match(line)
        if m:
            guard = (lineno, m.group("key"))
            continue
        m = _PIN_RE.match(line)
        if m:
            if guard is not None and guard[1] != m.group("key"):
                raise SourceError(
                    f"versions.yml.j2:{guard[0]}: guard tests {guard[1]!r} but "
                    f"line {lineno} pins {m.group('variable')} from "
                    f"versions[{m.group('key')!r}]"
                )
            out[m.group("variable")] = m.group("key")
        guard = None
    return out

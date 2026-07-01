"""Parser for container-image-kolla-ansible's versions.yml.j2 template."""

import re

_KEY_RE = re.compile(r"versions\['([a-z0-9_]+)'\]")


def parse_versions_keys(body: bytes) -> set[str]:
    """Return the distinct keys K referenced as versions['K'] in the template."""
    return set(_KEY_RE.findall(body.decode("utf-8")))

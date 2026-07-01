"""Extract alias set from the container-image-osism-ansible override template.

The template (files/src/templates/images.yml.j2) is rendered by
container-image-osism-ansible into group_vars/all/images.yml, which carries
Ansible higher precedence than role defaults.  Every alias whose _tag key
appears in that template is thus overridden at deploy time by the release pin
— making the corresponding role default dormant.
"""

import re

_ALIAS_RE = re.compile(r"^(\w+)_tag:", re.MULTILINE)


def parse_override_aliases(template_bytes: bytes) -> set[str]:
    """Return the set of alias names covered by the image-override template.

    Matches every line of the form ``<alias>_tag:`` at the start of a line
    (MULTILINE).  The right-hand side is ignored — only the alias key matters.
    Lines matching ``_image:``, comments, and blank lines are skipped naturally
    because the pattern requires the literal suffix ``_tag:``.
    """
    text = template_bytes.decode("utf-8")
    return {m.group(1) for m in _ALIAS_RE.finditer(text)}

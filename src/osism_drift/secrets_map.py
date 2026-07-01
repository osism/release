"""Parser for the top-level key set of an OSISM secrets or kolla passwords file.

Both osism cfg-cookiecutter secrets templates and kolla-ansible's
etc/kolla/passwords.yml are flat YAML mappings of var names to (often
jinja/templated) values. Only the var names matter for the orphan comparison,
and the values may not be valid YAML (cookiecutter/jinja placeholders), so the
keys are extracted by line regex rather than a YAML load. Top-level keys only:
an indented (nested) line is not a secret var name.
"""

import re

_KEY = re.compile(rb"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*:", re.MULTILINE)


def parse_secret_keys(body: bytes) -> set[str]:
    """Return the set of top-level YAML keys (secret/password var names)."""
    return {m.decode() for m in _KEY.findall(body)}

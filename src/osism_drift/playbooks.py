"""Reconstruct the playbook interface each OSISM runtime image advertises.

python-osism builds MAP_ROLE2ENVIRONMENT at runtime from /interface/playbooks/*.yml,
which each runtime image writes at build time. No repository holds that map, so a
static check has to rebuild it from the same inputs the image builds consume.

Transform constants are read out of the build scripts themselves rather than
copied here: a copy is a second thing to keep in sync, which is the defect class
this module exists to detect.
"""

import ast

from osism_drift.source import SourceError


def load_const(body: bytes, name: str):
    """Return the value of a module-level literal assignment in a build script.

    Raises SourceError when the name is gone or is no longer a literal.
    A build script that changed shape must stop the run, never yield a
    quietly smaller interface.
    """
    last_value = None
    found = False

    for node in ast.parse(body).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            found = True
            try:
                last_value = ast.literal_eval(node.value)
            except (ValueError, TypeError) as exc:
                raise SourceError(
                    f"{name} is no longer a literal in build script"
                ) from exc

    if not found:
        raise SourceError(f"{name} not found in build script; it changed shape")
    return last_value

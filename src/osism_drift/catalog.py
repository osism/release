"""Extract python-osism's static role catalogs from enums.py, by AST.

python-osism/osism/data/enums.py carries the two catalogs the drift check
compares runtime images against: MAP_ROLE2ROLE (collection name -> nested
Role(...) trees) and VALIDATE_PLAYBOOKS (validator key -> runtime/playbook/
environment). The module is never imported: it is read as a source blob at
whatever ref the run reads python-osism at, and importing it would bind the
checker to whatever version happens to be installed instead of that ref.
"""

import ast

from osism_drift import source
from osism_drift.source import SourceError

_ENUMS = "osism/data/enums.py"


def _assignment(body: bytes, name: str):
    """The RHS expression node of the top-level `name = ...` assignment.

    Raises SourceError (rather than returning None) if enums.py no longer
    assigns `name` at module level -- a vanished assignment must fail loud,
    not be read back as an empty catalog.
    """
    for node in ast.parse(body).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return node.value
    raise SourceError(f"{name} not found in {_ENUMS}")


def _role_names(node, collection: str) -> list:
    """Every Role("name", ...) reachable under `node`, depth-first, source order.

    Plain ast.walk() is breadth-first: it would visit a role's siblings before
    its own dependencies, scrambling the natural (parent, then each dependency
    in turn) order the nested Role(...) literals are written in. A depth-first
    pre-order walk instead visits a Call node before recursing into its
    "dependencies=[...]" keyword, matching how the nesting reads on the page.

    A Role(...) call whose name is missing or not a literal is not silently
    dropped: that role would vanish from `collection` and the drift check
    would report no finding for it at all -- the exact silent-under-report
    the fail-loud contract exists to rule out, just scoped to one role rather
    than the whole catalog. SourceError names both `collection` and the
    source line so the failure points straight at the offending Role(...).
    """
    names = []
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Role":
        if not node.args or not isinstance(node.args[0], ast.Constant):
            raise SourceError(
                f"{collection!r}: Role(...) with a non-literal or missing name "
                f"at {_ENUMS}:{node.lineno}"
            )
        names.append(node.args[0].value)
    for child in ast.iter_child_nodes(node):
        names.extend(_role_names(child, collection))
    return names


def collections(config) -> dict:
    """collection name -> every Role(...) name reachable in it, at any depth.

    Reads MAP_ROLE2ROLE as an AST rather than a literal: it holds Role(...)
    constructor calls, which ast.literal_eval cannot evaluate.
    """
    value = _assignment(source.read("python_osism", _ENUMS, config), "MAP_ROLE2ROLE")
    if not isinstance(value, ast.Dict):
        raise SourceError("MAP_ROLE2ROLE is no longer a dict literal")
    out = {}
    for key, entry in zip(value.keys, value.values):
        if not isinstance(key, ast.Constant):
            raise SourceError("MAP_ROLE2ROLE has a non-literal collection name")
        out[key.value] = _role_names(entry, key.value)
    return out


def _validator_entry(key: str, entry: dict) -> dict:
    """{runtime, environment, playbook} for one VALIDATE_PLAYBOOKS entry.

    "runtime" is required by every current entry; an entry that omits it
    must still fail through SourceError (the module's own error type, caught
    the same way everywhere else a catalog is malformed) rather than
    surfacing as a bare KeyError traceback the caller has no path to handle.
    """
    if not isinstance(entry, dict):
        # A string entry would make "runtime" not in entry a substring test,
        # not a key-membership one -- checked before the membership test so a
        # malformed entry shape fails through SourceError, never a wrong-
        # reason False negative.
        raise SourceError(f"VALIDATE_PLAYBOOKS[{key!r}] is not a dict entry: {entry!r}")
    if "runtime" not in entry:
        raise SourceError(f'VALIDATE_PLAYBOOKS[{key!r}] has no "runtime"')
    return {
        "runtime": entry["runtime"],
        "environment": entry.get("environment"),
        "playbook": entry.get("playbook", f"validate-{key}"),
    }


def validators(config) -> dict:
    """validator key -> {runtime, environment, playbook}, playbook already defaulted.

    osism/commands/validate.py:89-92 falls back to f"validate-{key}" when an
    entry omits "playbook" (13 of the current 28 entries take that branch),
    and "environment" is present only on the osism-ansible entries. Callers
    must not re-derive either: this returns the effective playbook name for
    every entry and `None` for a genuinely absent environment, rather than
    omitting the key.
    """
    value = _assignment(
        source.read("python_osism", _ENUMS, config), "VALIDATE_PLAYBOOKS"
    )
    try:
        raw = ast.literal_eval(value)
    except ValueError as exc:
        raise SourceError(f"VALIDATE_PLAYBOOKS is not a literal: {exc}") from exc
    if not isinstance(raw, dict):
        raise SourceError("VALIDATE_PLAYBOOKS is no longer a dict literal")
    return {key: _validator_entry(key, entry) for key, entry in raw.items()}

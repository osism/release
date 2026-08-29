"""role_registry_orphan plugin: docker_registry_<alias> defaults nothing uses."""

import re

from osism_drift import source
from osism_drift.model import DriftEntry

NAME = "role_registry_orphan"
DESCRIPTION = (
    "Flag docker_registry_<alias> variables defined in a role's defaults that "
    "no role, manager playbook or manager render template references "
    "(orphaned registry overrides)."
)
INPUT_FILES = [
    ("ansible_collection_services", "roles/*/defaults/main.yml"),
    ("ansible_collection_services", "roles/ (docker_registry_<alias> consumers)"),
    ("ansible_playbooks_manager", "playbooks/ (docker_registry_<alias> consumers)"),
    ("generics", "environments/manager/ (docker_registry_<alias> consumers)"),
]
SUMMARY = (
    "{n} docker_registry_<alias> role defaults that nothing references "
    "(orphaned registry overrides):"
)
REMEDIATION = (
    "remove the orphaned docker_registry_<alias> line — an operator setting it "
    "is overriding a registry for an image the collection no longer resolves. "
    "Allowlist it only if it is consumed in a form this scan misses; the scan "
    "counts any occurrence of the name outside its own definition line."
)

_ROLES_REPO = "ansible_collection_services"

# Where a docker_registry_<alias> reference can legitimately live. Roles and
# manager playbooks deploy the containers; the generics manager template renders
# the var into every deployed images.yml, so a var used only there is consumed.
_CONSUMER_SOURCES = [
    ("ansible_collection_services", "roles"),
    ("ansible_playbooks_manager", "playbooks"),
    ("generics", "environments/manager"),
]
_CONSUMER_EXTS = (".yml", ".yaml", ".j2")

# Bare \b match, not {{ ... }}: docker_registry_mirrors is consumed as a loop
# source ({% for m in docker_registry_mirrors %}) and would false-flag under a
# {{ var }}-only pattern. "_" is a word character, so \b also keeps
# docker_registry_osism from matching inside docker_registry_osism_frontend.
_REF_RE = re.compile(r"\bdocker_registry_[a-z0-9_]+\b")
_DEF_RE = re.compile(r"^(docker_registry_[a-z0-9_]+)\s*:")


def _definitions(text: str) -> list[str]:
    """Return the docker_registry_<alias> keys defined at the top level."""
    return [m.group(1) for line in text.splitlines() if (m := _DEF_RE.match(line))]


def _references(text: str) -> set[str]:
    """Return every docker_registry_<alias> named outside its own definition.

    A definition line still consumes the vars in its *value*: the real
    `docker_registry_osism_netbox: "{{ docker_registry_ansible }}"` defines one
    var and references another.
    """
    out = set()
    for line in text.splitlines():
        names = _REF_RE.findall(line)
        defined = _DEF_RE.match(line)
        if defined and names and names[0] == defined.group(1):
            names = names[1:]
        out.update(names)
    return out


def run(config, allowlist, verbose: bool = False) -> list:
    defined = []
    for role in sorted(source.list_dir(_ROLES_REPO, "roles", config, dirs_only=True)):
        rel = f"roles/{role}/defaults/main.yml"
        body = source.read_optional(_ROLES_REPO, rel, config)
        if body is None:
            continue
        text = body.decode("utf-8", errors="ignore")
        for var in _definitions(text):
            defined.append((var, f"ansible-collection-services/{rel}"))

    consumed = set()
    for repo, root in _CONSUMER_SOURCES:
        for path in source.list_tree(repo, root, config):
            if not path.endswith(_CONSUMER_EXTS):
                continue
            body = source.read_optional(repo, path, config)
            if body is None:
                continue
            consumed |= _references(body.decode("utf-8", errors="ignore"))

    drifts = []
    for var, found_src in sorted(set(defined)):
        if var in consumed:
            continue
        d = DriftEntry(
            plugin=NAME,
            image=var,
            alias=var[len("docker_registry_") :],
            expected=(
                f"a reference to {var} in a role, a manager playbook "
                "or the manager render template"
            ),
            found=f"defined in {found_src}; nothing references it",
            expected_src=(
                "ansible-collection-services/roles/, "
                "ansible-playbooks-manager/playbooks/, "
                "generics/environments/manager/"
            ),
            found_src=found_src,
            summary=SUMMARY,
            remediation=REMEDIATION,
        )
        drifts.append(allowlist.apply(d))
    return drifts

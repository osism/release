"""Shared per-role defaults scan for the role-pin drift checks.

role_shadows and role_unpinned both walk ansible-collection-services roles,
read each role's defaults/main.yml, and resolve every <alias>_tag pin to its
release key (skipping stream-resolved aliases). This module owns that scaffold
so the two checks scan an identical role set; they differ only in what they do
with each yielded pin.
"""

from dataclasses import dataclass

from osism_drift import manager_template, role_defaults, source

_ROLES_REPO = "ansible_collection_services"
_IMAGES = "environments/manager/images.yml"


@dataclass(frozen=True)
class RolePin:
    """One <alias>_tag pin found in a role's defaults, alias resolved to a key."""

    role: str
    found_src: str
    alias: str
    found: str
    release_key: str


def iter_role_pins(config):
    """Yield a RolePin for every non-stream-resolved <alias>_tag pin in each
    role's defaults/main.yml, with the alias resolved to its release key."""
    template_bytes = source.read("generics", _IMAGES, config)
    alias_map = manager_template.extract_alias_map(template_bytes)
    stream_resolved = manager_template.extract_stream_resolved(template_bytes)
    role_names = source.list_dir(_ROLES_REPO, "roles", config, dirs_only=True)

    for role in sorted(role_names):
        rel = f"roles/{role}/defaults/main.yml"
        body = source.read_optional(_ROLES_REPO, rel, config)
        if body is None:
            continue
        found_src = f"ansible-collection-services/{rel}"
        for alias, found in role_defaults.parse_role_defaults(body).items():
            if alias in stream_resolved:
                continue
            release_key = alias_map.get(alias, alias)
            yield RolePin(role, found_src, alias, found, release_key)

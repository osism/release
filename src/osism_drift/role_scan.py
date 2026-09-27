"""Shared per-role defaults scan for the role-pin drift checks.

role_shadows and role_unpinned both walk the roles of the collections the
osism-ansible image ships (osism.services and osism.validations), read each
role's defaults/main.yml, and resolve every <alias>_tag pin to its release key
(skipping stream-resolved aliases). This module owns that scaffold so the two
checks scan an identical role set; they differ only in what they do with each
yielded pin.

An alias resolves to a release key through the two templates that carry
release pins: generics' manager template (<alias>_tag lines) and
container-image-osism-ansible's versions.yml.j2 (<alias>_tag or
<alias>_version lines). An alias neither names resolves to itself.
"""

from dataclasses import dataclass

from osism_drift import manager_template, role_defaults, source, versions_template
from osism_drift.source import SourceError

_ROLE_REPOS = ("ansible_collection_services", "ansible_collection_validations")
_IMAGES = "environments/manager/images.yml"
_OSISM_ANSIBLE = "container_image_osism_ansible"
_VERSIONS = "files/src/templates/versions.yml.j2"
_SUFFIXES = ("_tag", "_version")


@dataclass(frozen=True)
class RolePin:
    """One <alias>_tag pin found in a role's defaults, alias resolved to a key."""

    role: str
    found_src: str
    alias: str
    found: str
    release_key: str


def runner_pins(config) -> dict[str, str]:
    """{variable: release_key} that osism-ansible's versions.yml.j2 carries."""
    body = source.read(_OSISM_ANSIBLE, _VERSIONS, config)
    return versions_template.parse_versions_map(body)


def runner_alias_map(pins: dict[str, str]) -> dict[str, str]:
    """{alias: release_key} for the <alias>_tag / <alias>_version pins."""
    out = {}
    for variable, key in pins.items():
        for suffix in _SUFFIXES:
            if variable.endswith(suffix):
                out[variable[: -len(suffix)]] = key
    return out


def merge_alias_maps(manager: dict[str, str], runner: dict[str, str]) -> dict[str, str]:
    """Union of both maps; an alias the two map to different keys is an error."""
    for alias in sorted(manager.keys() & runner.keys()):
        if manager[alias] != runner[alias]:
            raise SourceError(
                f"{alias}: generics' manager template pins release key "
                f"{manager[alias]!r}, osism-ansible's versions.yml.j2 pins "
                f"{runner[alias]!r}"
            )
    return {**manager, **runner}


def iter_role_pins(config):
    """Yield a RolePin for every non-stream-resolved <alias>_tag pin in each
    role's defaults/main.yml, with the alias resolved to its release key."""
    template_bytes = source.read("generics", _IMAGES, config)
    alias_map = merge_alias_maps(
        manager_template.extract_alias_map(template_bytes),
        runner_alias_map(runner_pins(config)),
    )
    stream_resolved = manager_template.extract_stream_resolved(template_bytes)

    for repo in _ROLE_REPOS:
        for role in sorted(source.list_dir(repo, "roles", config, dirs_only=True)):
            rel = f"roles/{role}/defaults/main.yml"
            body = source.read_optional(repo, rel, config)
            if body is None:
                continue
            found_src = f"{repo.replace('_', '-')}/{rel}"
            for alias, found in role_defaults.parse_role_defaults(body).items():
                if alias in stream_resolved:
                    continue
                release_key = alias_map.get(alias, alias)
                yield RolePin(role, found_src, alias, found, release_key)

"""Shared parse helpers for the enablement-drift checks (no I/O policy).

Pure functions over file bytes: parse OSISM enable flags and the OSISM build
set, plus the supported-release range. `canon` normalises the hyphen/underscore
split between key spaces (enable_* uses underscores; docker/ dirs and release
build keys use hyphens) so every cross-space comparison is on one form.
"""

import yaml

from osism_drift import source


def canon(name: str) -> str:
    """Canonical id for cross-key-space compares: hyphens -> underscores."""
    return name.replace("-", "_")


def parse_enable_flags(body: bytes) -> dict:
    """{service_id: raw_value} for every enable_<id> in an OSISM vars file."""
    data = yaml.safe_load(body) or {}
    return {
        k[len("enable_") :]: v
        for k, v in data.items()
        if isinstance(k, str) and k.startswith("enable_")
    }


def truthy_enables(flags: dict) -> set:
    """canon ids whose value is a literal yes/true (skip no/false/jinja)."""
    out = set()
    for sid, val in flags.items():
        if val is True:
            out.add(canon(sid))
        elif isinstance(val, str) and val.strip().lower() in ("yes", "true"):
            out.add(canon(sid))
    return out


def parse_build_set(body: bytes) -> set:
    """canon keys OSISM builds at a release: infra ∪ openstack project keys."""
    data = yaml.safe_load(body) or {}
    keys = set()
    for block in ("infrastructure_projects", "openstack_projects"):
        keys |= {canon(k) for k in (data.get(block) or {})}
    return keys


def release_range(config) -> list:
    """Supported releases: config.releases override, else derived from the
    osism/release latest/openstack-*.yml file set (sorted)."""
    if config.releases:
        return list(config.releases)
    names = source.list_dir("release", "latest", config)
    rels = [
        n[len("openstack-") : -len(".yml")]
        for n in names
        if n.startswith("openstack-") and n.endswith(".yml")
    ]
    return sorted(rels)


def upstream_enable_keys(release, config) -> set:
    """canon ids of every top-level enable_<X> default in upstream kolla-ansible
    at `release`'s resolved ref.

    The enable-defaults layer moves between releases: a monolithic
    ansible/group_vars/all.yml (2024.1/2024.2/2025.1) or a split
    ansible/group_vars/all/*.yml dir (2025.2+). Probe the monolithic file first;
    on a 404 fall through to the split dir. Top-level defaults only — never
    role-defaults/tasks/tests/releasenotes — so a reference-only mention of an
    enable var is not counted as a definition. Always remote.
    """
    ref = source.release_to_ref("kolla_ansible", release, config)
    mono = source.read_at_ref(
        "kolla_ansible", "ansible/group_vars/all.yml", ref, config, optional=True
    )
    if mono is not None:
        return {canon(k) for k in parse_enable_flags(mono)}
    keys = set()
    for name in source.list_dir_at_ref(
        "kolla_ansible", "ansible/group_vars/all", ref, config
    ):
        if name.endswith(".yml"):
            body = source.read_at_ref(
                "kolla_ansible", f"ansible/group_vars/all/{name}", ref, config
            )
            keys |= {canon(k) for k in parse_enable_flags(body)}
    return keys


def osism_enable_ids(flags, scope) -> set:
    """OSISM enable ids selected by scope. 'truthy' -> literal yes/true only;
    'explicit' -> every enable_* key (canon-normalized) regardless of value."""
    if scope == "truthy":
        return truthy_enables(flags)
    if scope == "explicit":
        return {canon(k) for k in flags}
    raise ValueError(f"unknown scope {scope!r}")

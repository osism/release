"""Shared parse helpers for the enablement-drift checks (no I/O policy).

Pure functions over file bytes: parse OSISM enable flags and the OSISM build
set, plus the supported-release range. `canon` normalises the hyphen/underscore
split between key spaces (enable_* uses underscores; docker/ dirs and release
build keys use hyphens) so every cross-space comparison is on one form.
"""

import yaml

from osism_drift import secrets_map, source


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


def top_level_keys(body: bytes) -> set:
    """Every top-level mapping key of a group_vars/defaults YAML file, verbatim.

    Both upstream kolla-ansible group_vars/all and osism/defaults all/*.yml are
    valid YAML (jinja lives only in values, which load as strings), so a
    safe_load of the top-level mapping is the accurate key set — no prefix strip
    and no hyphen/underscore canon: an Ansible var name is a Python identifier,
    so these keys are compared exactly. A non-mapping document yields no keys.
    """
    data = yaml.safe_load(body) or {}
    if not isinstance(data, dict):
        return set()
    return {k for k in data if isinstance(k, str)}


_OSISM_DEFAULTS_DIR = "all"


def _osism_defaults_bodies(config):
    """Yield the bytes of every osism/defaults all/*.yml file (sorted, .yml only).

    Reading the union of the directory — not a single hardcoded file — keeps the
    OSISM view independent of how the defaults are split across files, so a
    reorganization (e.g. a per-service split mirroring upstream) cannot silently
    drop a key from the comparison.
    """
    for fn in sorted(source.list_dir("defaults", _OSISM_DEFAULTS_DIR, config)):
        if fn.endswith(".yml"):
            yield source.read("defaults", f"{_OSISM_DEFAULTS_DIR}/{fn}", config)


def osism_enable_flags(config) -> dict:
    """{service_id: raw_value} for every enable_<id> across all OSISM defaults
    files (`osism/defaults` all/*.yml), merged."""
    flags = {}
    for body in _osism_defaults_bodies(config):
        flags.update(parse_enable_flags(body))
    return flags


_VERSIONS_TEMPLATE = (
    "container_image_kolla_ansible",
    "files/src/templates/versions.yml.j2",
)


def osism_groupvars_keys(config) -> set:
    """Union of every top-level group_vars key OSISM supplies to the kolla-ansible
    container's group_vars/all — the OSISM side of the group_vars diff.

    OSISM delivers these from two paths, and BOTH must count or a var supplied by
    the second path false-positives as missing:
      1. osism/defaults all/*.yml (the generics gilt overlay), and
      2. container-image-kolla-ansible files/src/templates/versions.yml.j2, rendered
         into group_vars/all/versions.yml in the image (openstack_release,
         openstack_previous_release_name, the kolla_*_version pins, ...).
    The versions template is jinja/cookiecutter, not valid YAML, so its top-level
    keys are read with the line-key parser, not a YAML load.
    """
    keys = set()
    for body in _osism_defaults_bodies(config):
        keys |= top_level_keys(body)
    repo, path = _VERSIONS_TEMPLATE
    keys |= secrets_map.parse_secret_keys(source.read(repo, path, config))
    return keys


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


def _upstream_groupvars_bodies(release, config) -> list:
    """Bytes of upstream kolla-ansible group_vars/all at `release`'s resolved ref.

    The group_vars layer moves between releases: a monolithic
    ansible/group_vars/all.yml (2024.1/2024.2/2025.1) or a split
    ansible/group_vars/all/*.yml dir (2025.2+). Probe the monolithic file first;
    on a 404 fall through to the split dir. Top-level group_vars only — never
    role-defaults/tasks/tests/releasenotes — so a reference-only mention is not
    counted as a definition. Always remote.
    """
    ref = source.release_to_ref("kolla_ansible", release, config)
    mono = source.read_at_ref(
        "kolla_ansible", "ansible/group_vars/all.yml", ref, config, optional=True
    )
    if mono is not None:
        return [mono]
    bodies = []
    for name in source.list_dir_at_ref(
        "kolla_ansible", "ansible/group_vars/all", ref, config
    ):
        if name.endswith(".yml"):
            bodies.append(
                source.read_at_ref(
                    "kolla_ansible", f"ansible/group_vars/all/{name}", ref, config
                )
            )
    return bodies


def upstream_enable_keys(release, config) -> set:
    """canon ids of every top-level enable_<X> default in upstream kolla-ansible
    at `release`'s resolved ref (the enable_-prefixed subset of the group_vars,
    stripped of the prefix and hyphen/underscore-normalised)."""
    keys = set()
    for body in _upstream_groupvars_bodies(release, config):
        keys |= {canon(k) for k in parse_enable_flags(body)}
    return keys


def upstream_groupvars_keys(release, config) -> set:
    """Every top-level group_vars/all key in upstream kolla-ansible at `release`'s
    resolved ref, verbatim. The upstream side of the group_vars diff."""
    keys = set()
    for body in _upstream_groupvars_bodies(release, config):
        keys |= top_level_keys(body)
    return keys


def osism_enable_ids(flags, scope) -> set:
    """OSISM enable ids selected by scope. 'truthy' -> literal yes/true only;
    'explicit' -> every enable_* key (canon-normalized) regardless of value."""
    if scope == "truthy":
        return truthy_enables(flags)
    if scope == "explicit":
        return {canon(k) for k in flags}
    raise ValueError(f"unknown scope {scope!r}")

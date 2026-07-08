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


_CONTAINER_IMAGE_REPO = "container_image_kolla_ansible"
_VERSIONS_TEMPLATE = (
    _CONTAINER_IMAGE_REPO,
    "files/src/templates/versions.yml.j2",
)
_OVERLAYS_DIR = "overlays"
_OVERLAY_FILE = "kolla-ansible.yml"


def _osism_overlay_bodies(config):
    """Yield the bytes of every container-image-kolla-ansible per-release overlay
    group_vars file (overlays/<release>/kolla-ansible.yml).

    The Dockerfile bakes the deployed release's overlay into the image's
    group_vars/all (mv /overlays/$OPENSTACK_VERSION/kolla-ansible.yml /overlays,
    staged by the entrypoint), so its top-level keys are part of OSISM's effective
    group_vars supply — the third path alongside osism/defaults and the rendered
    versions.yml. Enumerated from the overlays/ tree (not a hardcoded release
    list) so the set tracks whatever releases carry an overlay and a repo with no
    overlays/ dir contributes nothing. Only the per-release overlay files count:
    overlays/<release>/kolla-ansible.yml has exactly one path segment between the
    dir and the file, which skips the overlays/release/<ver>/ image-release tree
    (not a group_vars overlay). Union across all release overlays, consistent with
    the union-over-supported-releases approximation the group_vars diff uses.
    """
    prefix = f"{_OVERLAYS_DIR}/"
    suffix = f"/{_OVERLAY_FILE}"
    for path in sorted(
        source.list_tree(_CONTAINER_IMAGE_REPO, _OVERLAYS_DIR, config, missing_ok=True)
    ):
        if not (path.startswith(prefix) and path.endswith(suffix)):
            continue
        # Exactly one segment between overlays/ and the file (a release id): keep
        # overlays/<release>/kolla-ansible.yml, skip any deeper nesting.
        if path[len(prefix) : -len(suffix)].count("/") == 0:
            yield source.read(_CONTAINER_IMAGE_REPO, path, config)


def osism_groupvars_keys(config) -> set:
    """Union of every top-level group_vars key OSISM supplies to the kolla-ansible
    container's group_vars/all — the OSISM side of the group_vars diff.

    OSISM delivers these from three paths, and ALL must count or a var supplied by
    one of them false-positives as missing:
      1. osism/defaults all/*.yml (the generics gilt overlay),
      2. container-image-kolla-ansible files/src/templates/versions.yml.j2, rendered
         into group_vars/all/versions.yml in the image (openstack_release,
         openstack_previous_release_name, the kolla_*_version pins, ...), and
      3. container-image-kolla-ansible overlays/<release>/kolla-ansible.yml, the
         per-release group_vars the Dockerfile bakes into the image at deploy time
         (the release-specific analogue of a defaults/all backward-compat entry:
         vars upstream removed from group_vars/all but an older supported release's
         role still references, e.g. the external-Ceph keyrings and the 2024.x
         swift group_vars).
    The versions template is jinja/cookiecutter, not valid YAML, so its top-level
    keys are read with the line-key parser, not a YAML load; the overlays are valid
    YAML (jinja only in values), so top_level_keys reads them like the defaults.
    """
    keys = set()
    for body in _osism_defaults_bodies(config):
        keys |= top_level_keys(body)
    repo, path = _VERSIONS_TEMPLATE
    keys |= secrets_map.parse_secret_keys(source.read(repo, path, config))
    for body in _osism_overlay_bodies(config):
        keys |= top_level_keys(body)
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


_UPSTREAM_ROLES_DIR = "ansible/roles"


def upstream_image_tag_keys(release, config) -> tuple[set, set]:
    """(image_vars, tag_vars) defined across upstream kolla-ansible role defaults
    at `release`'s resolved ref.

    The canonical source of kolla image/tag parameters is
    ansible/roles/<role>/defaults/main.yml, where each carries `<svc>_image` and
    `<svc>_tag` top-level defaults. Read every role's defaults once and split the
    top-level keys by suffix: `*_image` (excluding the derived `*_image_full`)
    and `*_tag`. A role without a defaults file is skipped. Compared by exact
    name (an Ansible var name is a Python identifier), matching top_level_keys.
    """
    ref = source.release_to_ref("kolla_ansible", release, config)
    images, tags = set(), set()
    for role in source.list_dir_at_ref(
        "kolla_ansible", _UPSTREAM_ROLES_DIR, ref, config, dirs_only=True
    ):
        body = source.read_at_ref(
            "kolla_ansible",
            f"{_UPSTREAM_ROLES_DIR}/{role}/defaults/main.yml",
            ref,
            config,
            optional=True,
        )
        if body is None:
            continue
        for k in top_level_keys(body):
            if k.endswith("_image") and not k.endswith("_image_full"):
                images.add(k)
            elif k.endswith("_tag"):
                tags.add(k)
    return images, tags


def osism_enable_ids(flags, scope) -> set:
    """OSISM enable ids selected by scope. 'truthy' -> literal yes/true only;
    'explicit' -> every enable_* key (canon-normalized) regardless of value."""
    if scope == "truthy":
        return truthy_enables(flags)
    if scope == "explicit":
        return {canon(k) for k in flags}
    raise ValueError(f"unknown scope {scope!r}")


def groupvars_values(bodies) -> dict:
    """Merge yaml.safe_load_all of each body into one {str key: parsed value}
    map (later docs/files win). Non-mapping documents contribute nothing.

    Value-aware counterpart to top_level_keys: kolla_mirror_verbatim compares
    001's values against upstream, so keys alone are insufficient. safe_load_all
    handles a multi-document file harmlessly (001 is single-document today)."""
    out = {}
    for body in bodies:
        for doc in yaml.safe_load_all(body):
            if isinstance(doc, dict):
                out.update({k: v for k, v in doc.items() if isinstance(k, str)})
    return out


def upstream_groupvars_values(release, config) -> dict:
    """{key: parsed value} of upstream kolla-ansible group_vars/all at `release`'s
    resolved ref. Reuses _upstream_groupvars_bodies, so the monolithic all.yml
    (<=2025.1) vs split all/ dir (2025.2+) layout is handled transparently."""
    return groupvars_values(_upstream_groupvars_bodies(release, config))


_MIRROR_FILE = "all/001-kolla-defaults.yml"


def osism_mirror_values(config) -> dict:
    """{key: parsed value} of osism/defaults all/001-kolla-defaults.yml ONLY.

    Scoped to the single mirror file (not the all/*.yml union) because
    kolla_mirror_verbatim enforces 001 purity specifically: a key OSISM supplies
    from 099-* must not count as "in the mirror"."""
    return groupvars_values([source.read("defaults", _MIRROR_FILE, config)])


def osism_supply_excluding_mirror(config) -> set:
    """Top-level keys OSISM supplies from every layer EXCEPT 001 — the other
    all/*.yml files + the rendered versions.yml.j2 + the per-release overlays.

    Same three-path logic as osism_groupvars_keys, minus the 001 file: lets
    kolla_mirror_verbatim tell "a 001-only key that another layer already
    supplies" (delete from 001) from "a 001-only key nothing else supplies"."""
    keys = set()
    for fn in sorted(source.list_dir("defaults", _OSISM_DEFAULTS_DIR, config)):
        if fn.endswith(".yml") and f"{_OSISM_DEFAULTS_DIR}/{fn}" != _MIRROR_FILE:
            keys |= top_level_keys(
                source.read("defaults", f"{_OSISM_DEFAULTS_DIR}/{fn}", config)
            )
    repo, path = _VERSIONS_TEMPLATE
    keys |= secrets_map.parse_secret_keys(source.read(repo, path, config))
    for body in _osism_overlay_bodies(config):
        keys |= top_level_keys(body)
    return keys


def groupvars_home(key, newest, newest_keys, dropped_map):
    """Return (path, note) for where a group_var key belongs, or None.

    key in newest_keys  -> ("all/001-kolla-defaults.yml", note with newest)
    else key in dropped -> (f"all/010-{L}.yml", note with L)    if L
    else                -> None  (caller falls back to static text)

    Pure: takes precomputed sets/maps, no I/O."""
    if key in newest_keys:
        return ("all/001-kolla-defaults.yml", f"upstream defines it at {newest}")
    L = dropped_map.get(key)
    if L:
        return (f"all/010-{L}.yml", f"upstream dropped by {newest}; last in {L}")
    return None


def dropped_key_release_map(config) -> dict:
    """{key: L} over every upstream group_vars/all key defined by some supported
    release BELOW the newest, where L is the NEWEST such release still defining the
    key. kolla_mirror_verbatim routes a dropped 001 key to all/010-<L>.yml (parent
    spec D8): L is exactly the release whose EOL retires that file. A key's presence
    in this map also classifies it as backward-compat rather than OSISM-invented.

    Sort before slicing: release_range returns an explicit config.releases override
    in caller order, so sorted(...)[:-1] drops the true newest, consistent with the
    plugin's sorted(...)[-1]; a bare [:-1] on an out-of-order override would slice
    off the wrong release and misclassify every dropped key as OSISM-invented.
    Iterating the older releases ascending, last-writer-wins yields the newest
    release still carrying each key. Empty when only one release is supported."""
    m = {}
    for r in sorted(release_range(config))[:-1]:  # ascending -> newest overwrites
        for k in upstream_groupvars_keys(r, config):
            m[k] = r
    return m

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


def parse_source_refs(body: bytes) -> dict:
    """openstack_projects as {project: ref}, project names verbatim.

    Unlike parse_build_set the keys are not canonicalised: they index the
    upstream source tarball names, where the real project name (e.g.
    neutron-dynamic-routing) is what resolves. A key with no value maps to None
    so the caller can skip it.
    """
    data = yaml.safe_load(body) or {}
    return dict(data.get("openstack_projects") or {})


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


def _upstream_groupvars_named_bodies(release, config) -> list:
    """[(filename, bytes)] of upstream kolla-ansible group_vars/all at `release`.

    Sorted by filename, which is Ansible's own merge order for a group_vars
    directory, so a caller building a {key: file} map records the file that
    actually wins. The monolithic layout (<=2025.1) yields the single pair
    ("all.yml", body). Top-level group_vars only. Always remote.

    Memoized on config.groupvars_cache, keyed by the resolved ref. The split
    layout costs one listing plus ~56 file reads, and keys, values and homes are
    all derived from it, by two plugins, in one run: without the memo a single
    run repeats those reads several times over. Keyed by ref rather than release
    because the ref is what determines the content.
    """
    ref = source.release_to_ref("kolla_ansible", release, config)
    cached = config.groupvars_cache.get(ref)
    if cached is not None:
        return cached
    mono = source.read_at_ref(
        "kolla_ansible", "ansible/group_vars/all.yml", ref, config, optional=True
    )
    if mono is not None:
        config.groupvars_cache[ref] = [("all.yml", mono)]
        return config.groupvars_cache[ref]
    out = []
    for name in sorted(
        source.list_dir_at_ref("kolla_ansible", "ansible/group_vars/all", ref, config)
    ):
        if name.endswith(".yml"):
            out.append(
                (
                    name,
                    source.read_at_ref(
                        "kolla_ansible", f"ansible/group_vars/all/{name}", ref, config
                    ),
                )
            )
    config.groupvars_cache[ref] = out
    return out


def _upstream_groupvars_bodies(release, config) -> list:
    """Bytes of upstream kolla-ansible group_vars/all at `release`'s resolved ref.

    The group_vars layer moves between releases: a monolithic
    ansible/group_vars/all.yml (2024.1/2024.2/2025.1) or a split
    ansible/group_vars/all/*.yml dir (2025.2+); both are handled transparently.
    Names are dropped here — use _upstream_groupvars_named_bodies when the file a
    key came from matters. Top-level group_vars only. Always remote.
    """
    return [body for _, body in _upstream_groupvars_named_bodies(release, config)]


def upstream_groupvars_homes(release, config) -> dict:
    """{key: upstream filename} at `release`, lexically-last file winning.

    Ansible merges group_vars/all in lexical filename order, so the file whose
    value takes effect is the LAST one defining the key. Upstream relies on that:
    the seven database_* keys sit in both common.yml and database.yml, and
    database.yml wins. Iterating sorted() (see _upstream_groupvars_named_bodies)
    is what makes the recorded home the winning one rather than whichever the
    GitHub listing happened to yield last.

    Empty for the monolithic layout, where there is no per-service home to name.
    """
    named = _upstream_groupvars_named_bodies(release, config)
    if len(named) == 1 and named[0][0] == "all.yml":
        return {}
    homes = {}
    for name, body in named:  # already sorted -> last wins
        for key in top_level_keys(body):
            homes[key] = name
    return homes


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


_MIRROR_PREFIX = "001-"

# User-facing label for the mirror layer. One definition, so the plugin that
# routes keys into the layer and the plugin that reports on it cannot disagree
# about what to call it.
MIRROR_LAYER = f"{_OSISM_DEFAULTS_DIR}/{_MIRROR_PREFIX}*.yml"


def _mirror_filenames(config) -> list[str]:
    """Sorted all/ filenames forming the 001 mirror layer.

    Sorted because the caller merges them: all/ is loaded in lexical filename
    order with later files winning, and the GitHub contents API makes no
    ordering promise. Upstream itself relies on this (it defines the same
    database_* keys in both common.yml and database.yml, and database.yml wins),
    so an unsorted listing would silently pick the wrong value.
    """
    return sorted(
        fn
        for fn in source.list_dir("defaults", _OSISM_DEFAULTS_DIR, config)
        if fn.startswith(_MIRROR_PREFIX) and fn.endswith(".yml")
    )


def osism_mirror_values(config) -> dict:
    """{key: parsed value} of the osism/defaults all/001-* mirror layer ONLY.

    Scoped to the mirror layer (not the all/*.yml union) because
    kolla_mirror_verbatim enforces 001 purity specifically: a key OSISM supplies
    from 099-* must not count as "in the mirror". A layer rather than one file so
    the mirror can be split per upstream service without touching this code."""
    return groupvars_values(
        source.read("defaults", f"{_OSISM_DEFAULTS_DIR}/{fn}", config)
        for fn in _mirror_filenames(config)
    )


def osism_supply_excluding_mirror(config) -> set:
    """Top-level keys OSISM supplies from every layer EXCEPT the 001-* mirror
    layer — the other all/*.yml files + the rendered versions.yml.j2 + the
    per-release overlays.

    Same three-path logic as osism_groupvars_keys, minus the 001 file: lets
    kolla_mirror_verbatim tell "a 001-only key that another layer already
    supplies" (delete from 001) from "a 001-only key nothing else supplies"."""
    keys = set()
    for fn in sorted(source.list_dir("defaults", _OSISM_DEFAULTS_DIR, config)):
        if fn.endswith(".yml") and not fn.startswith(_MIRROR_PREFIX):
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

    key in newest_keys  -> (MIRROR_LAYER, note with newest)
    else key in dropped -> (f"all/010-{L}.yml", note with L)    if L
    else                -> None  (caller falls back to static text)

    Pure: takes precomputed sets/maps, no I/O."""
    if key in newest_keys:
        return (MIRROR_LAYER, f"upstream defines it at {newest}")
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

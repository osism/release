"""Reconstruct the playbook interface each OSISM runtime image advertises.

python-osism builds MAP_ROLE2ENVIRONMENT at runtime from /interface/playbooks/*.yml,
which each runtime image writes at build time. No repository holds that map, so a
static check has to rebuild it from the same inputs the image builds consume.

Transform constants are read out of the build scripts themselves rather than
copied here: a copy is a second thing to keep in sync, which is the defect class
this module exists to detect.
"""

import ast
import re
from types import MappingProxyType

import yaml

from osism_drift import source
from osism_drift.source import SourceError

_CONTAINER_IMAGE_KOLLA_ANSIBLE = "container_image_kolla_ansible"
_SPLIT_SCRIPT = "files/scripts/split-kolla-ansible-site.py"

_CONTAINER_IMAGE_OSISM_ANSIBLE = "container_image_osism_ansible"
_CONTAINER_IMAGE_CEPH_ANSIBLE = "container_image_ceph_ansible"
_OSISM_KUBERNETES = "osism_kubernetes"
_ANSIBLE_PLAYBOOKS = "ansible_playbooks"
_CEPH_ANSIBLE = "ceph_ansible"

_SYMLINK_SCRIPT = "files/src/generate-playbook-symlinks.py"
_RENDER_SCRIPT = "files/src/render-playbooks.py"

# Containerfile:25-26/161-162 (container-image-ceph-ansible): rm -f wipes every
# ceph-purge-*.yml this build has produced so far -- OSISM's own flavour-dir
# copies (Containerfile:21) and any upstream ceph-ansible purge-*.yml alike
# (Containerfile:151) -- then only these two explicitly staged files are moved
# back. Anything else matching ceph-purge-*.yml never survives, regardless of
# where it came from.
_CEPH_PURGE_SURVIVORS = frozenset(
    {"ceph-purge-storage-node.yml", "ceph-purge-cluster.yml"}
)


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


def _apply_role_names(site_body: bytes, unsupported) -> set:
    """Play names 'Apply role <X>' in kolla's site.yml, as the split script cuts them.

    Mirrors split-kolla-ansible-site.py: strip all whitespace from the tail of the
    play name, drop UNSUPPORTED_ROLES, rename rabbitmq(outward).
    """
    names = set()
    for play in yaml.safe_load(site_body) or []:
        name = (play or {}).get("name", "")
        if not name.startswith("Apply role"):
            continue
        role = re.sub(r"\s+", "", name[len("Apply role") :])
        if role in unsupported:
            continue
        names.add("rabbitmq-outward" if role == "rabbitmq(outward)" else role)
    return names


def kolla_files(release, config) -> frozenset:
    """/ansible/kolla-*.yml basenames the kolla-ansible image ships at `release`.

    Accumulates in the Containerfile's own build order (shared OSISM playbooks,
    then the split site.yml roles, then upstream's top-level playbooks, then
    the alias/collision fixups, then per-release OSISM playbooks) rather than
    as an order-free union: the fixups at Containerfile:174-176 read and
    mutate whatever the build has produced so far, so getting there in the
    wrong order can silently keep or drop the wrong file.
    """
    ref = source.release_to_ref("kolla_ansible", release, config)
    # Containerfile:23 -- OSISM's own playbooks shared across every release
    files = {
        n
        for n in source.list_dir(
            _CONTAINER_IMAGE_KOLLA_ANSIBLE, "files/playbooks", config
        )
        if n.startswith("kolla-") and n.endswith(".yml")
    }
    # Containerfile:164 (split-kolla-ansible-site.py) -- one kolla-<role>.yml
    # per surviving "Apply role" play in upstream's site.yml
    unsupported = load_const(
        source.read(_CONTAINER_IMAGE_KOLLA_ANSIBLE, _SPLIT_SCRIPT, config),
        "UNSUPPORTED_ROLES",
    )
    names = _apply_role_names(
        source.read_at_ref("kolla_ansible", "ansible/site.yml", ref, config),
        unsupported,
    )
    files |= {f"kolla-{n}.yml" for n in names}
    # Containerfile:171-172 -- every top-level upstream ansible/*.yml is copied in
    top_level = {
        n[: -len(".yml")]
        for n in source.list_dir_at_ref("kolla_ansible", "ansible", ref, config)
        if n.endswith(".yml")
    }
    files |= {f"kolla-{n}.yml" for n in top_level}
    # Containerfile:174-175 -- backward-compat hyphenated aliases, kept
    # alongside whichever underscore-named mariadb maintenance playbooks the
    # build has produced by this point.
    if "kolla-mariadb_backup.yml" in files:
        files.add("kolla-mariadb-backup.yml")
    if "kolla-mariadb_recovery.yml" in files:
        files.add("kolla-mariadb-recovery.yml")
    # Containerfile:176 -- these two collide with names kolla-ansible itself
    # already uses as a "kolla-" prefix, so the build removes the copies.
    files -= {"kolla-kolla-host.yml", "kolla-post-deploy.yml"}
    # Containerfile:224 -- OSISM's per-release playbooks, copied in last via
    # `COPY --link files/playbooks/$OPENSTACK_VERSION/kolla-*.yml /ansible/`,
    # the same wildcard-COPY construct osism_files() (above) already argues
    # fails the image build outright if the source directory is gone. This
    # directory's absence is therefore not legitimate here either: not
    # missing_ok, so a missing directory raises SourceError rather than
    # silently shrinking the interface.
    files |= {
        n
        for n in source.list_dir(
            _CONTAINER_IMAGE_KOLLA_ANSIBLE,
            f"files/playbooks/{release}",
            config,
        )
        if n.startswith("kolla-") and n.endswith(".yml")
    }
    return frozenset(files)


def kolla_interface(release, config) -> dict:
    """role -> 'kolla', as the image's render-playbooks.py would write it."""
    body = source.read(_CONTAINER_IMAGE_KOLLA_ANSIBLE, _RENDER_SCRIPT, config)
    hide = load_const(body, "HIDE")
    keep = load_const(body, "KEEP_PREFIX")
    prefix = load_const(body, "PREFIX")
    if f"{prefix}-" != "kolla-":
        # kolla_files() hardcodes the "kolla-" filter independently of this
        # script's own PREFIX; a rename here must raise, not silently shrink
        # the interface to empty.
        raise SourceError(
            f"render-playbooks.py PREFIX changed to {prefix!r} but "
            "kolla_files() still filters on 'kolla-'"
        )
    out = {}
    for fname in kolla_files(release, config):
        name = fname[len("kolla-") : -len(".yml")]
        if name in hide:
            continue
        out[f"kolla-{name}" if name in keep else name] = "kolla"
    return out


def _pin(release_file: str, key: str, config) -> str:
    """A *_version pin read from release/latest/<release_file>, or SourceError.

    These pins (playbooks_version, ceph_ansible_version, ...) are what each
    Containerfile's `git checkout "$(yq ... /release/latest/<file>)"` resolves
    at build time, so reading them is how a release/flavour ref is discovered
    rather than assumed.
    """
    data = (
        yaml.safe_load(source.read("release", f"latest/{release_file}", config)) or {}
    )
    if key not in data:
        raise SourceError(f"{key} missing from latest/{release_file}")
    return str(data[key])


def osism_files(config) -> frozenset:
    """/ansible/<env>-<name>.yml basenames the osism-ansible image ships.

    Containerfile:151 (`cp -r /playbooks/playbooks/* /ansible`) lands
    ansible-playbooks' playbooks/<env>/*.yml under /ansible/<env>/;
    Containerfile:163 (generate-playbook-symlinks.py) then symlinks each as
    /ansible/<env>-<name>.yml for every ENVIRONMENTS entry not in SKIP. That
    script's leftover "not name.startswith('_')" guard tests the *env-prefixed*
    name, which can never start with "_" -- it filters nothing today, so an
    underscore-prefixed source file (e.g. generic/_gather-facts-limit.yml) is
    symlinked and appears like any other. Mirrored here rather than the
    filename convention the guard was presumably meant to enforce, since
    fidelity to the actual (buggy) build is the point.

    Containerfile:19 (`COPY files/playbooks/* /ansible/`) also lands this
    image's own top-level playbooks straight into /ansible, unprefixed; that
    directory is empty today (a placeholder since 2019) but a real COPY step,
    so a future file there is still picked up -- and would need a name already
    matching one of render-playbooks.py's PREFIXES to reach the interface,
    exactly like a same-shaped ansible-playbooks file would. Both this
    directory and the per-release override directory in kolla_files() are
    wildcard COPY --link sources whose absence fails the image build, so
    neither listing is missing_ok. This directory holds only a placeholder
    today, but a future file there would still be picked up.
    """
    body = source.read(_CONTAINER_IMAGE_OSISM_ANSIBLE, _SYMLINK_SCRIPT, config)
    skip = load_const(body, "SKIP")
    envs = load_const(body, "ENVIRONMENTS")
    ref = _pin("base.yml", "playbooks_version", config)
    # GitHub's contents API 404s identically for a missing directory and for a
    # bad ref (source.py's list_dir_at_ref). missing_ok=True below is needed
    # for the former (some ENVIRONMENTS entries have no upstream directory),
    # but that same 404 would silently swallow the latter across all nine
    # environments too, returning an empty interface with no error. Checking
    # the ref once up front closes that hole without a per-environment request.
    if not source.ref_exists(_ANSIBLE_PLAYBOOKS, ref, config):
        raise SourceError(
            f"playbooks_version={ref!r} (latest/base.yml) does not exist in "
            f"{_ANSIBLE_PLAYBOOKS}"
        )
    files = {
        n
        for n in source.list_dir(
            _CONTAINER_IMAGE_OSISM_ANSIBLE, "files/playbooks", config
        )
        if n.endswith(".yml")
    }
    for env in envs:
        # A listed ENVIRONMENTS entry with no matching directory upstream
        # (e.g. "kubernetes" today) is a legitimate absence: the Containerfile's
        # glob over a nonexistent /ansible/<env>/ simply yields nothing.
        for name in source.list_dir_at_ref(
            _ANSIBLE_PLAYBOOKS, f"playbooks/{env}", ref, config, missing_ok=True
        ):
            if not name.endswith(".yml"):
                continue
            fname = f"{env}-{name}"
            if fname in skip or fname.startswith("_"):
                continue
            files.add(fname)
    return frozenset(files)


def osism_interface(config) -> dict:
    """role -> environment, as the image's render-playbooks.py would write it.

    Iterates PREFIXES in order, exactly as render-playbooks.py's own
    `for prefix in PREFIXES: for path in Path("/ansible").glob(f"{prefix}-*.yml")`
    does, rather than over the frozenset osism_files() returns. Two different
    env prefixes can strip to the same role name -- e.g. today
    infrastructure-traefik.yml and manager-traefik.yml both strip to
    "traefik", and generic-configuration.yml and manager-configuration.yml
    both strip to "configuration" -- and the build's own dict assignment
    means the LAST prefix in PREFIXES order wins. Iterating a frozenset
    instead would make the winner depend on Python's set hash order, which
    varies with PYTHONHASHSEED: a non-deterministic map that can also be
    silently wrong, not just unstable.

    HIDE/KEEP_PREFIX are indexed directly (`hide[prefix]`, not
    `hide.get(prefix, [])`): the real script does the same, so a PREFIXES
    entry missing from either dict crashes the real build, and this must
    fail the same way rather than silently treat it as an empty list.
    """
    body = source.read(_CONTAINER_IMAGE_OSISM_ANSIBLE, _RENDER_SCRIPT, config)
    prefixes = load_const(body, "PREFIXES")
    hide = load_const(body, "HIDE")
    keep = load_const(body, "KEEP_PREFIX")
    files = osism_files(config)
    out = {}
    for prefix in prefixes:
        for fname in files:
            if not fname.startswith(f"{prefix}-"):
                continue
            name = fname[len(prefix) + 1 : -len(".yml")]
            if name in hide[prefix]:
                continue
            out[f"{prefix}-{name}" if name in keep[prefix] else name] = prefix
    return out


def _ceph_flavours(config) -> list:
    """Ceph flavour names from release/latest/ceph-<flavour>.yml basenames.

    latest/ceph.yml is a symlink alias (e.g. to ceph-reef.yml), not a flavour
    of its own; it is excluded for free because "ceph.yml" itself never
    matches the "ceph-*.yml" prefix match below.
    """
    names = source.list_dir("release", "latest", config)
    return sorted(
        n[len("ceph-") : -len(".yml")]
        for n in names
        if n.startswith("ceph-") and n.endswith(".yml")
    )


def ceph_files(config) -> frozenset:
    """/ansible/ceph-*.yml basenames the ceph-ansible image ships, unioned
    across every ceph flavour built today (quincy, reef, squid).

    Containerfile:22 lands OSISM's flavour-independent playbooks;
    Containerfile:21 lands OSISM's own per-flavour playbooks (already named
    ceph-*.yml in the source tree, purge pair included). Containerfile:151
    adds every top-level ceph/ceph-ansible infrastructure-playbook,
    ceph-prefixed, at that flavour's own ceph_ansible_version pin
    (release/latest/ceph-<flavour>.yml -- a different upstream ref per
    flavour). Containerfile:158 unconditionally symlinks
    ceph-rolling_update.yml to ceph-upgrade.yml regardless of whether the
    source exists, so that name always appears. Finally the purge survivors
    fixup below mirrors Containerfile:161-162 (see _CEPH_PURGE_SURVIVORS).
    """
    files = {
        n
        for n in source.list_dir(
            _CONTAINER_IMAGE_CEPH_ANSIBLE, "files/playbooks", config
        )
        if n.startswith("ceph-") and n.endswith(".yml")
    }
    for flavour in _ceph_flavours(config):
        files |= {
            n
            for n in source.list_dir(
                _CONTAINER_IMAGE_CEPH_ANSIBLE, f"files/playbooks/{flavour}", config
            )
            if n.startswith("ceph-") and n.endswith(".yml")
        }
        ref = _pin(f"ceph-{flavour}.yml", "ceph_ansible_version", config)
        files |= {
            f"ceph-{n[: -len('.yml')]}.yml"
            for n in source.list_dir_at_ref(
                _CEPH_ANSIBLE, "infrastructure-playbooks", ref, config
            )
            if n.endswith(".yml")
        }
    files.add("ceph-upgrade.yml")
    files = {f for f in files if not f.startswith("ceph-purge-")}
    files |= _CEPH_PURGE_SURVIVORS
    return frozenset(files)


def ceph_interface(config) -> dict:
    """role -> ceph-ansible's PREFIX, full stem retained.

    Unlike kolla/osism-ansible, ceph-ansible's render-playbooks.py has no
    HIDE/KEEP_PREFIX transform: every ceph-*.yml basename becomes a role keyed
    by its full stem (prefix kept), valued by PREFIX itself.
    """
    prefix = load_const(
        source.read(_CONTAINER_IMAGE_CEPH_ANSIBLE, _RENDER_SCRIPT, config), "PREFIX"
    )
    if f"{prefix}-" != "ceph-":
        # ceph_files() hardcodes the "ceph-" filter independently of this
        # script's own PREFIX; a rename here must raise, not silently shrink
        # the interface to empty.
        raise SourceError(
            f"render-playbooks.py PREFIX changed to {prefix!r} but "
            "ceph_files() still filters on 'ceph-'"
        )
    return {fname[: -len(".yml")]: prefix for fname in ceph_files(config)}


def kubernetes_files(config) -> frozenset:
    """/ansible/kubernetes-*.yml basenames the osism-kubernetes image ships.

    Containerfile:16 (`COPY playbooks/* /ansible/`) copies this repo's own
    playbooks/ directory straight into /ansible -- unlike osism-ansible and
    ceph-ansible, there is no separate ansible-playbooks clone/checkout, so
    there is no release/flavour ref to resolve here.
    """
    return frozenset(
        n
        for n in source.list_dir(_OSISM_KUBERNETES, "playbooks", config)
        if n.startswith("kubernetes-") and n.endswith(".yml")
    )


def kubernetes_interface(config) -> dict:
    """role -> osism-kubernetes's PREFIX, prefix stripped."""
    prefix = load_const(
        source.read(_OSISM_KUBERNETES, _RENDER_SCRIPT, config), "PREFIX"
    )
    if f"{prefix}-" != "kubernetes-":
        # kubernetes_files() hardcodes the "kubernetes-" filter independently
        # of this script's own PREFIX; a rename here must raise, not silently
        # shrink the interface to empty.
        raise SourceError(
            f"render-playbooks.py PREFIX changed to {prefix!r} but "
            "kubernetes_files() still filters on 'kubernetes-'"
        )
    return {
        fname[len(prefix) + 1 : -len(".yml")]: prefix
        for fname in kubernetes_files(config)
    }


_RUNTIME_FILES = {
    "kolla-ansible": lambda release, config: kolla_files(release, config),
    "osism-ansible": lambda release, config: osism_files(config),
    "ceph-ansible": lambda release, config: ceph_files(config),
    "osism-kubernetes": lambda release, config: kubernetes_files(config),
}


def playbook_files(runtime: str, release: str, config) -> frozenset:
    """Dispatch to `runtime`'s own *_files(); raises SourceError for an
    unknown runtime name so a typo fails loud instead of silently matching
    nothing.

    `release` is accepted uniformly across all four runtimes but only
    kolla-ansible's build actually keys its playbook set by OSISM release;
    the other three ignore it (ceph-ansible keys by ceph flavour instead,
    osism-ansible and osism-kubernetes don't key their playbook set at all).
    """
    try:
        fn = _RUNTIME_FILES[runtime]
    except KeyError:
        raise SourceError(f"unknown runtime {runtime!r}") from None
    return fn(release, config)


def runtime_interface(release, config) -> dict:
    """role -> frozenset of environments advertising it, merged across runtimes.

    python-osism builds its single MAP_ROLE2ENVIRONMENT from an unsorted
    Path.glob over /interface/playbooks/*.yml (osism/data/playbooks.py:29),
    one file per runtime image, with plain dict-union so a name two runtimes
    both advertise resolves to whichever file the glob happened to visit
    last -- filesystem order, not something this static check can know or
    should pretend to. Keeping every environment a role appears under (not
    just a winner) is deliberately a *superset* of what any single running
    python-osism process would report: every check this module supports
    (`resolves`, `validate_resolves`) only needs membership, never a winner,
    so collapsing to one here would encode an order that does not exist and
    could not be verified anyway.

    Memoized per release on `config.playbooks_cache`, mirroring the other
    per-run caches on Config (ref_cache, groupvars_cache, snapshot_cache):
    this walks all four runtimes' sources and callers ask repeatedly per run.
    Returned as a `MappingProxyType` view over the cached dict -- every other
    producer in this module hands back an immutable frozenset/dict-of-frozensets
    shape, and a caller mutating the live cached dict would corrupt it for
    every later call this run, not just its own.
    """
    cached = config.playbooks_cache.get(release)
    if cached is not None:
        return cached
    merged = {}
    for part in (
        kolla_interface(release, config),
        osism_interface(config),
        ceph_interface(config),
        kubernetes_interface(config),
    ):
        for role, env in part.items():
            merged[role] = merged.get(role, frozenset()) | {env}
    view = MappingProxyType(merged)
    config.playbooks_cache[release] = view
    return view


def resolves(role: str, interface: dict) -> bool:
    """Whether `osism apply <role>` finds a playbook to run for `role`.

    Mirrors apply.py's role resolution (apply.py:300-380), which is a
    lookup into MAP_ROLE2ENVIRONMENT (== the map `runtime_interface` merges)
    under two cases, checked here as plain membership since only presence,
    never the winning environment, is meaningful to this check:

    1. role == "ceph": apply.py hardcodes environment="ceph" and never
       strips a "ceph-" prefix off the literal string "ceph" (it does not
       have one), so it always runs the ceph runtime's own "ceph-ceph.yml" --
       present in the interface iff the ceph runtime ships "ceph-ceph".
    2. role is exactly a key of the interface: the plain MAP_ROLE2ENVIRONMENT
       lookup apply.py performs whenever no --environment override is given.

    Deliberately no "kolla-"/"ceph-" prefix handling: apply.py's prefix strip
    (apply.py:338, :318) runs strictly AFTER this dict lookup, once the
    environment is already known, on whatever string the lookup already
    matched -- it reconstructs the on-disk filename for the runtime call, it
    does not widen what counts as a hit. A "kolla-"-prefixed role only ever
    reaches that strip by first succeeding as an exact key (e.g. "kolla-facts"
    when KEEP_PREFIX keeps that name), or via an explicit --environment
    override that bypasses the dict lookup entirely -- a manual escape hatch
    this catalog check cannot know about and must not credit as resolving.
    Stripping the prefix before checking membership, as an earlier version of
    this function did, only ever turns real drift into a false pass: e.g. if
    kolla-ansible stopped shipping "kolla-facts" (KEEP_PREFIX-kept) while
    osism-ansible still advertised bare "facts" under "generic", a
    prefix-stripped check would answer True from the wrong runtime's entry
    while `osism apply kolla-facts` (no override) actually falls through to
    environment "custom" and fails.
    """
    if role == "ceph":
        return "ceph-ceph" in interface  # apply.py's explicit special case
    return role in interface


def validate_resolves(runtime, environment, playbook, release, config) -> bool:
    """Whether `osism validate <key>` finds a playbook file to run.

    Mirrors validate.py:87-105: it never consults MAP_ROLE2ENVIRONMENT (the
    map `runtime_interface` merges) at all -- it reads the entry's own
    `runtime` and dispatches straight to ceph.run / kolla.run / ansible.run,
    each of which resolves a playbook FILE inside that one runtime's own
    image, named by that runtime's own convention (kolla-/ceph-/<environment>-
    prefix). So this is a per-runtime file-existence question, answered
    against `playbook_files`, never against the merged interface -- building
    one shared resolver for `resolves` and `validate_resolves` would be wrong
    in both directions (accepting a validate entry some other runtime's
    prefix rules happen to also produce; rejecting one whose own runtime
    ships it under a name the merged map's collision-handling obscures).
    """
    files = playbook_files(runtime, release, config)
    if runtime == "kolla-ansible":
        return f"kolla-{playbook}.yml" in files
    if runtime == "ceph-ansible":
        return f"ceph-{playbook}.yml" in files
    if runtime == "osism-ansible":
        # validate.py:102 reads VALIDATE_PLAYBOOKS[validator]["environment"]
        # unconditionally and raises KeyError if it is absent -- a missing
        # environment is a malformed catalog entry, never a legitimate "no
        # environment" case, so this must fail loud the same way rather than
        # silently probe a "None-<playbook>.yml" file that can never exist.
        if environment is None:
            raise SourceError(f"osism-ansible entry {playbook!r} has no environment")
        return f"{environment}-{playbook}.yml" in files
    raise SourceError(f"unknown runtime {runtime!r}")

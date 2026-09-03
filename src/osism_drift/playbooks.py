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

import yaml

from osism_drift import source
from osism_drift.source import SourceError

_CONTAINER_IMAGE_KOLLA_ANSIBLE = "container_image_kolla_ansible"
_SPLIT_SCRIPT = "files/scripts/split-kolla-ansible-site.py"
_KOLLA_RENDER = "files/src/render-playbooks.py"


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
    # Containerfile:224 -- OSISM's per-release playbooks, copied in last. Not
    # every release has an override directory; that is a legitimate absence,
    # not a corrupt input -- but any other failure (outage, rate limit) must
    # still raise rather than be mistaken for one.
    files |= {
        n
        for n in source.list_dir(
            _CONTAINER_IMAGE_KOLLA_ANSIBLE,
            f"files/playbooks/{release}",
            config,
            missing_ok=True,
        )
        if n.startswith("kolla-") and n.endswith(".yml")
    }
    return frozenset(files)


def kolla_interface(release, config) -> dict:
    """role -> 'kolla', as the image's render-playbooks.py would write it."""
    body = source.read(_CONTAINER_IMAGE_KOLLA_ANSIBLE, _KOLLA_RENDER, config)
    hide = load_const(body, "HIDE")
    keep = load_const(body, "KEEP_PREFIX")
    out = {}
    for fname in kolla_files(release, config):
        name = fname[len("kolla-") : -len(".yml")]
        if name in hide:
            continue
        out[f"kolla-{name}" if name in keep else name] = "kolla"
    return out

"""kolla_orphan_config: dead companion/image config for orphaned services.

kolla_enablement_orphan flags the enable_X flag of a service upstream dropped,
but osism/defaults usually also carries that service's companion vars and image
definitions (X_*, e.g. senlin_api_port, senlin_api_image/_tag). Those are
equally dead and must be removed with the flag, yet nothing enumerated them, so
a cleanup that only deleted the enable flag left them dangling.

This sweep closes that gap. For each genuinely dead service (an orphan per
kolla_enablement_orphan, excluding the OSISM-invented ones kept via that check's
allowlist) it reports every top-level var defined across osism/defaults
all/*.yml whose name is the service id or begins with "<service id>_". The
enable_X flag itself is left to kolla_enablement_orphan (it never matches the
"<service>_" prefix). Keys are read with the shared top-level-key parser, not a
YAML load, and matched on the canon (hyphen/underscore) form.
"""

from osism_drift import enablement, secrets_map, source
from osism_drift.drift import kolla_enablement_orphan
from osism_drift.model import DriftEntry

NAME = "kolla_orphan_config"
DESCRIPTION = (
    "Flag dead companion/image config vars (<service>_*) in osism/defaults for "
    "services kolla_enablement_orphan reports as orphaned."
)
INPUT_FILES = [
    ("defaults", "all/*.yml"),
    ("defaults", "all/099-kolla.yml (enable flags, via kolla_enablement_orphan)"),
]
SUMMARY = (
    "{n} dead companion/image config vars for services upstream removed (their "
    "enable_<name> flag is reported separately by kolla_enablement_orphan); "
    "these must be removed too:"
)
REMEDIATION = (
    "remove these vars from the listed osism/defaults file, or allowlist any "
    "that are intentionally kept (an OSISM invention with no upstream service)."
)

_DEFAULTS_DIR = "all"


def _owning_service(var: str, dead: set) -> str | None:
    """The dead service that owns `var` (var == sid or var starts sid+'_'),
    longest match wins; None if no dead service owns it."""
    v = enablement.canon(var)
    owners = [
        sid for sid in dead if v == sid or v.startswith(f"{enablement.canon(sid)}_")
    ]
    return max(owners, key=len) if owners else None


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return dead-config drifts: <service>_* vars of orphaned services."""
    # Genuinely dead services: orphaned AND not kept via the orphan allowlist
    # (so OSISM inventions like common/kolla_operations are excluded).
    dead = set()
    for sid in kolla_enablement_orphan.orphan_ids(config):
        probe = DriftEntry(
            plugin=kolla_enablement_orphan.NAME,
            image=sid,
            alias=sid,
            expected="",
            found="",
            expected_src="",
            found_src="",
        )
        if allowlist.match(probe) is None:
            dead.add(sid)
    if not dead:
        return []

    # Every top-level var across defaults all/*.yml, remembering its file.
    var_file = {}
    for fn in sorted(source.list_dir("defaults", _DEFAULTS_DIR, config)):
        if not fn.endswith(".yml"):
            continue
        body = source.read("defaults", f"{_DEFAULTS_DIR}/{fn}", config)
        for key in secrets_map.parse_secret_keys(body):
            var_file.setdefault(key, f"{_DEFAULTS_DIR}/{fn}")

    drifts = []
    for var in sorted(var_file):
        if var.startswith("enable_"):
            continue  # enable flags belong to kolla_enablement_orphan
        owner = _owning_service(var, dead)
        if owner is None:
            continue
        d = DriftEntry(
            plugin=NAME,
            image=var,
            alias=var,
            expected=f"removed with the orphaned service '{owner}'",
            found=f"dead companion/image config in {var_file[var]}",
            expected_src="openstack/kolla-ansible (service removed upstream)",
            found_src=f"osism/defaults {var_file[var]}",
        )
        drifts.append(allowlist.apply(d))
    return drifts

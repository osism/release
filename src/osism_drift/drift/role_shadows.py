"""role_shadows plugin: <alias>_tag pins in role defaults vs release."""

from osism_drift import manager_template, playbooks, release, role_scan, source
from osism_drift.model import DriftEntry

NAME = "role_shadows"
SHOW_VALUES = True
DESCRIPTION = (
    "Detect tag values pinned in role defaults that disagree with the release, "
    "classified as live / dormant by whether a release transport overrides them."
)
INPUT_FILES = [
    ("release", "<release_version>/base.yml"),
    ("generics", "environments/manager/images.yml"),
    ("ansible_collection_services", "roles/*/defaults/main.yml"),
    ("ansible_collection_validations", "roles/*/defaults/main.yml"),
    # The playbook map (playbooks.osism_interface): the environment each role's
    # `osism apply` runs in, which decides whether generics' manager render
    # reaches it.
    ("ansible_playbooks", "playbooks/<env>/*.yml (at playbooks_version)"),
    (
        "container_image_osism_ansible",
        "files/src/templates/versions.yml.j2 + files/playbooks/*.yml + "
        "files/src/{generate-playbook-symlinks,render-playbooks}.py",
    ),
]
SUMMARY = "{n} <alias>_tag pins in role defaults disagree with the release base.yml:"
REMEDIATION = (
    "update or remove the <alias>_tag value in the role's defaults/main.yml, "
    "or allowlist it if the pin is intentional."
)

_LIVE_SUMMARY = (
    "{n} LIVE — no release transport overrides it; the role default is what "
    "actually deploys:"
)
_LIVE_REMEDIATION = (
    "carry `<alias>_tag` (or the role's `<alias>_version`) in "
    "container-image-osism-ansible's versions.yml.j2 so the latest/base.yml pin "
    "governs the deployed version."
)
_DORMANT_SUMMARY = (
    "{n} DORMANT — a release transport overrides it at deploy; the role default "
    "only affects standalone use of the collection:"
)
_DORMANT_REMEDIATION = "lower priority; sync when convenient."


def _is_dormant(pin, carried: set, manager_aliases: set, environments: dict) -> bool:
    """True when a release value reaches the role's deploy and beats its default.

    Two transports do that: osism-ansible's versions.yml (for any environment,
    via the inventory reconciler), and generics' manager render (only for a
    role whose `osism apply` runs in the manager environment, which loads it
    as an extra var).
    """
    if f"{pin.alias}_tag" in carried or f"{pin.alias}_version" in carried:
        return True
    return pin.alias in manager_aliases and environments.get(pin.role) == "manager"


def run(config, allowlist, verbose: bool = False) -> list:
    release_bytes = source.read("release", f"{config.release_version}/base.yml", config)
    docker_images = release.parse_release(release_bytes)
    carried = set(role_scan.runner_pins(config))
    manager_aliases = set(
        manager_template.extract_alias_map(
            source.read("generics", "environments/manager/images.yml", config)
        )
    )
    environments = playbooks.osism_interface(config)

    expected_src = f"release/{config.release_version}/base.yml"
    drifts = []
    for pin in role_scan.iter_role_pins(config):
        expected = docker_images.get(pin.release_key)
        if expected is None or expected == pin.found:
            continue
        if _is_dormant(pin, carried, manager_aliases, environments):
            summary, remediation, severity = (
                _DORMANT_SUMMARY,
                _DORMANT_REMEDIATION,
                "advisory",
            )
        else:
            summary, remediation, severity = (
                _LIVE_SUMMARY,
                _LIVE_REMEDIATION,
                "actionable",
            )
        d = DriftEntry(
            plugin=NAME,
            image=pin.release_key,
            alias=pin.alias,
            expected=expected,
            found=pin.found,
            expected_src=expected_src,
            found_src=pin.found_src,
            summary=summary,
            remediation=remediation,
            severity=severity,
        )
        drifts.append(allowlist.apply(d))
    return drifts

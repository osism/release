"""role_shadows plugin: <alias>_tag pins in role defaults vs release."""

from osism_drift import override_template, release, role_scan, source
from osism_drift.model import DriftEntry

NAME = "role_shadows"
SHOW_VALUES = True
DESCRIPTION = (
    "Detect tag values pinned in role defaults that disagree with the release, "
    "classified as live / dormant by override precedence."
)
INPUT_FILES = [
    ("release", "<release_version>/base.yml"),
    ("generics", "environments/manager/images.yml"),
    ("ansible_collection_services", "roles/*/defaults/main.yml"),
    ("container_image_osism_ansible", "files/src/templates/images.yml.j2"),
]
SUMMARY = "{n} <alias>_tag pins in role defaults disagree with the release base.yml:"
REMEDIATION = (
    "update or remove the <alias>_tag value in the role's defaults/main.yml, "
    "or allowlist it if the pin is intentional."
)

_LIVE_SUMMARY = (
    "{n} LIVE — no images.yml override; the role default is what actually deploys:"
)
_LIVE_REMEDIATION = (
    "add `<alias>_tag`/`<alias>_image` to the manager render template "
    "(images.yml.j2) so the latest/base.yml pin governs the deployed version."
)
_DORMANT_SUMMARY = "{n} DORMANT — overridden by the rendered images.yml; the release pin wins at deploy:"
_DORMANT_REMEDIATION = "lower priority; sync when convenient."


def _advice(alias: str, override_aliases: set) -> tuple[str, str, str]:
    """Return (summary, remediation, severity) for the drift's advice class.

    Dormant (overridden by images.yml) is advisory — the release pin wins at
    deploy, so it is sync-when-convenient, not act-now. Live is actionable.
    """
    if alias in override_aliases:
        return _DORMANT_SUMMARY, _DORMANT_REMEDIATION, "advisory"
    return _LIVE_SUMMARY, _LIVE_REMEDIATION, "actionable"


def run(config, allowlist, verbose: bool = False) -> list:
    release_bytes = source.read("release", f"{config.release_version}/base.yml", config)
    override_bytes = source.read(
        "container_image_osism_ansible", "files/src/templates/images.yml.j2", config
    )
    docker_images = release.parse_release(release_bytes)
    override_aliases = override_template.parse_override_aliases(override_bytes)

    expected_src = f"release/{config.release_version}/base.yml"
    drifts = []
    for pin in role_scan.iter_role_pins(config):
        expected = docker_images.get(pin.release_key)
        if expected is None or expected == pin.found:
            continue
        summary, remediation, severity = _advice(pin.alias, override_aliases)
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

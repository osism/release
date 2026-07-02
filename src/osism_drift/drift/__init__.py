"""Plugin registry. Plugins are appended here as they are added."""

from osism_drift import report
from osism_drift.drift import (
    image_orphan,
    kolla_enablement_build,
    kolla_enablement_orphan,
    kolla_groupvars_missing,
    kolla_inventory,
    kolla_orphan_config,
    kolla_secrets_orphan,
    kolla_version_chain_inner,
    kolla_version_chain_upstream,
    release_vs_manager,
    role_shadows,
    role_unpinned,
)

KOLLA_PLUGINS = [
    kolla_enablement_orphan,
    kolla_groupvars_missing,
    kolla_orphan_config,
    kolla_secrets_orphan,
    kolla_enablement_build,
    kolla_version_chain_upstream,
    kolla_version_chain_inner,
    kolla_inventory,
]

IMAGE_PLUGINS = [release_vs_manager, role_shadows, role_unpinned, image_orphan]

PLUGIN_GROUPS = {"image": IMAGE_PLUGINS, "kolla": KOLLA_PLUGINS}

REPORT_HEADERS = {
    "image": (
        "Checks follow an image's version pin: release base.yml → rendered "
        "manager images.yml → role defaults."
    ),
    "kolla": report.HEADER,
}

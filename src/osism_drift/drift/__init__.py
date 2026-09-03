"""Plugin registry. Plugins are appended here as they are added."""

from osism_drift import report
from osism_drift.drift import (
    catalog_role_missing,
    image_orphan,
    kolla_enablement_build,
    kolla_enablement_orphan,
    kolla_groupvars_missing,
    kolla_image_orphan,
    kolla_inventory,
    kolla_mirror_verbatim,
    kolla_orphan_config,
    kolla_secrets_orphan,
    kolla_source_ref_phase,
    kolla_version_chain_inner,
    kolla_version_chain_upstream,
    release_vs_manager,
    role_shadows,
    role_unpinned,
    rolling_pin,
)

KOLLA_PLUGINS = [
    kolla_source_ref_phase,
    kolla_enablement_orphan,
    kolla_groupvars_missing,
    kolla_mirror_verbatim,
    kolla_orphan_config,
    kolla_image_orphan,
    kolla_secrets_orphan,
    kolla_enablement_build,
    kolla_version_chain_upstream,
    kolla_version_chain_inner,
    kolla_inventory,
]

IMAGE_PLUGINS = [
    release_vs_manager,
    role_shadows,
    role_unpinned,
    rolling_pin,
    image_orphan,
]

CATALOG_PLUGINS = [catalog_role_missing]

PLUGIN_GROUPS = {
    "image": IMAGE_PLUGINS,
    "kolla": KOLLA_PLUGINS,
    "catalog": CATALOG_PLUGINS,
}

REPORT_HEADERS = {
    "image": (
        "Checks follow an image's version pin: release base.yml → rendered "
        "manager images.yml → role defaults."
    ),
    "kolla": report.HEADER,
    "catalog": (
        "Checks follow a role name from python-osism's static catalogs to the "
        "playbook interface each runtime image advertises, per supported "
        "release."
    ),
}

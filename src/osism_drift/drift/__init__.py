"""Plugin registry. Plugins are appended here as they are added."""

from osism_drift.drift import (
    image_orphan,
    release_vs_manager,
    role_shadows,
    role_unpinned,
)

KOLLA_PLUGINS = []

IMAGE_PLUGINS = [release_vs_manager, role_shadows, role_unpinned, image_orphan]

PLUGIN_GROUPS = {"image": IMAGE_PLUGINS}

REPORT_HEADERS = {
    "image": (
        "Checks follow an image's version pin: release base.yml → rendered "
        "manager images.yml → role defaults."
    ),
}

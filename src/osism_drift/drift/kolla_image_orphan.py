"""kolla_image_orphan: dead <svc>_image / <svc>_tag catalogue entries.

The image-catalogue-driven complement to the enable-flag-driven
kolla_enablement_orphan -> kolla_orphan_config path. That path only flags a
removed service's companion vars when OSISM still defines its enable_<svc> flag.
A service upstream removed for which OSISM never defined an enable flag leaves
its <svc>_image / <svc>_tag definitions in the kolla image catalogue undetected.

This check keys off the image catalogue instead: for each OSISM kolla image/tag
var, flag it when its name is absent from upstream kolla-ansible's role defaults
(ansible/roles/*/defaults/main.yml *_image / *_tag) at every supported release.
It is a pure variable-NAME set-diff over the union of supported refs -- no
image-name resolution, alias chains, or service-id inference (the false-positive
sources a role-name-mapping approach would hit). Both suffixes use the same test
independently: upstream role defaults define both the role tag (nova_tag) and
per-image tags (nova_api_tag), matching OSISM's naming on both sides.

OSISM-built images with no upstream role (e.g. mariabackup, tempest) are kept via
the allowlist. Both an empty catalogue match and an empty upstream union fail
loud, since either would turn the set-diff into a silent all-clear or a mass
false positive.
"""

import fnmatch

from osism_drift import enablement, source
from osism_drift.model import DriftEntry

NAME = "kolla_image_orphan"
DESCRIPTION = (
    "Flag OSISM kolla image-catalogue *_image / *_tag vars whose name is absent "
    "from upstream kolla-ansible role defaults across all supported releases."
)
INPUT_FILES = [
    ("defaults", "all/*images-kolla*.yml"),
    ("kolla_ansible", "ansible/roles/*/defaults/main.yml (per resolved release ref)"),
]
SUMMARY = (
    "{n} OSISM kolla image/tag vars for services upstream kolla-ansible no "
    "longer defines at any supported release (orphaned image-catalogue entries):"
)
REMEDIATION = (
    "remove these <svc>_image / <svc>_tag vars from the listed osism/defaults "
    "catalogue file, or allowlist any that are intentionally kept (an OSISM-built "
    "image with no upstream kolla-ansible role)."
)

_DEFAULTS_DIR = "all"
_CATALOGUE_GLOB = "*images-kolla*.yml"


def _catalogue_keys(config):
    """(img_file, tag_file): {var: 'all/<file>'} maps for every *_image (excl.
    *_image_full) and *_tag top-level key across the OSISM kolla image catalogue
    files (defaults all/ entries matching _CATALOGUE_GLOB). First file wins on a
    duplicate key. Restricting to the kolla catalogue glob (not all/*.yml) keeps
    non-kolla images -- ceph, cilium, k3s -- out of a kolla-ansible comparison."""
    img_file, tag_file = {}, {}
    for fn in sorted(source.list_dir("defaults", _DEFAULTS_DIR, config)):
        if not fnmatch.fnmatch(fn, _CATALOGUE_GLOB):
            continue
        body = source.read("defaults", f"{_DEFAULTS_DIR}/{fn}", config)
        rel = f"{_DEFAULTS_DIR}/{fn}"
        for k in enablement.top_level_keys(body):
            if k.endswith("_image") and not k.endswith("_image_full"):
                img_file.setdefault(k, rel)
            elif k.endswith("_tag"):
                tag_file.setdefault(k, rel)
    return img_file, tag_file


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return orphan drifts: OSISM kolla image/tag vars absent upstream."""
    img_file, tag_file = _catalogue_keys(config)
    if not img_file and not tag_file:
        # A rename outside _CATALOGUE_GLOB (or a moved catalogue) would leave the
        # OSISM side empty, making orphan = empty - union = empty read as "no
        # drift" and silently disable the check. Fail loud instead.
        raise source.SourceError(
            f"no OSISM kolla image-catalogue keys found (glob {_CATALOGUE_GLOB!r} "
            f"under defaults {_DEFAULTS_DIR}/); the catalogue may have moved"
        )

    releases = enablement.release_range(config)
    if not releases:
        raise source.SourceError(
            "empty supported release range; cannot compute the upstream "
            "image/tag union"
        )
    up_img, up_tag = set(), set()
    for r in releases:
        images, tags = enablement.upstream_image_tag_keys(r, config)
        up_img |= images
        up_tag |= tags
    if not up_img and not up_tag:
        # Nothing read from role defaults (e.g. upstream restructured the roles
        # layout): every OSISM var would false-orphan. Fail loud.
        raise source.SourceError(
            "empty upstream image/tag union across supported releases; the "
            "kolla-ansible role-defaults layout may have changed"
        )

    drifts = []
    orphans = sorted((set(img_file) - up_img) | (set(tag_file) - up_tag))
    for var in orphans:
        rel = img_file.get(var) or tag_file.get(var)
        d = DriftEntry(
            plugin=NAME,
            image=var,
            alias=var,
            expected=(
                "defined in upstream kolla-ansible role defaults at some "
                "supported release"
            ),
            found=(
                "absent from upstream role-defaults *_image/*_tag across all "
                "supported releases (orphan)"
            ),
            expected_src="openstack/kolla-ansible roles/*/defaults @ supported refs",
            found_src=f"osism/defaults {rel}",
        )
        drifts.append(allowlist.apply(d))
    return drifts

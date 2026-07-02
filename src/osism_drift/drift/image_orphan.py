"""image_orphan plugin: image vars emitted by manager that no consumer uses."""

import re

from osism_drift import source
from osism_drift.model import DriftEntry

NAME = "image_orphan"
DESCRIPTION = (
    "Flag image vars emitted by the manager images.yml that no role or "
    "manager playbook consumes (orphaned image definitions)."
)
INPUT_FILES = [
    ("generics", "environments/manager/images.yml"),
    ("ansible_collection_services", "roles/ ({{ <alias>_image }} consumers)"),
    ("ansible_playbooks_manager", "playbooks/ ({{ <alias>_image }} consumers)"),
]
SUMMARY = (
    "{n} image vars emitted by the manager images.yml that no role or "
    "playbook consumes:"
)
REMEDIATION = (
    "remove the orphaned <alias>_tag/<alias>_image (and its "
    "etc/images.yml + role-default remnants), or allowlist it if the image is "
    "genuinely consumed in a form this scan misses — it flags an alias only "
    "when no literal {{ <alias>_image }} reference is found in the scanned "
    "roles/playbooks."
)

# Repos (and the root within each) scanned for {{ <alias>_image }} consumers. A
# manager-plane image is deployed from an ansible-collection-services role or
# driven by a manager orchestration playbook; an alias referenced in neither is
# an orphan. (ansible-collection-commons is host/OS setup with no container
# images, so it is intentionally not scanned.)
_CONSUMER_SOURCES = [
    ("ansible_collection_services", "roles"),
    ("ansible_playbooks_manager", "playbooks"),
]

# A {{ <alias>_image }} reference only ever lives in ansible YAML or a jinja
# template; restricting the scan to these extensions avoids one remote read per
# non-text file (LICENSE, .md, binaries) in the recursively-listed role/playbook
# trees, which in remote mode is one HTTP GET each.
_CONSUMER_EXTS = (".yml", ".yaml", ".j2")

_TAG_RE = re.compile(r"^(\w+)_tag:", re.M)
_IMAGE_REF_RE = re.compile(r"\{\{\s*(\w+)_image\s*\}\}")


def run(config, allowlist, verbose: bool = False) -> list:
    template_bytes = source.read("generics", "environments/manager/images.yml", config)
    text = template_bytes.decode("utf-8", errors="ignore")
    emitted = {m.group(1) for m in _TAG_RE.finditer(text)}

    consumed = set()
    for repo, root in _CONSUMER_SOURCES:
        for path in source.list_tree(repo, root, config):
            if not path.endswith(_CONSUMER_EXTS):
                continue
            body = source.read_optional(repo, path, config)
            if body is None:
                continue
            for m in _IMAGE_REF_RE.finditer(body.decode("utf-8", errors="ignore")):
                consumed.add(m.group(1))

    drifts = []
    for alias in sorted(emitted - consumed):
        d = DriftEntry(
            plugin=NAME,
            image=alias,
            alias=alias,
            expected=(
                f"a {{{{ {alias}_image }}}} consumer under "
                "ansible-collection-services/roles/ or ansible-playbooks-manager/"
            ),
            found=(
                f"emitted by the manager images.yml; "
                f"no role or playbook references {{{{ {alias}_image }}}}"
            ),
            # Constant across entries so the report groups all orphans into one
            # block (the per-alias detail lives in `found`/`expected`, JSON only).
            expected_src=(
                "ansible-collection-services/roles/, "
                "ansible-playbooks-manager/playbooks/"
            ),
            found_src="generics/environments/manager/images.yml",
            summary=SUMMARY,
            remediation=REMEDIATION,
        )
        drifts.append(allowlist.apply(d))
    return drifts

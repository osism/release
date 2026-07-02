"""release_vs_manager plugin: rendered manager images.yml vs release pins."""

import sys

from osism_drift import manager, manager_template, release, source
from osism_drift.model import DriftEntry

NAME = "release_vs_manager"
DESCRIPTION = "Compare release/<version>/base.yml against rendered manager images.yml."
INPUT_FILES = [
    ("release", "<release_version>/base.yml"),
    ("generics", "environments/manager/images.yml"),
    ("testbed", "environments/manager/images.yml"),
]
SUMMARY = "{n} image tags in the rendered manager images.yml disagree with the release base.yml pins:"
REMEDIATION = "re-render environments/manager/images.yml from the current release, or allowlist the entry if the divergence is intentional (e.g. a rolling-release image pinned to 'latest')."


def run(config, allowlist, verbose: bool = False) -> list:
    release_bytes = source.read("release", f"{config.release_version}/base.yml", config)
    template_bytes = source.read("generics", "environments/manager/images.yml", config)
    rendered_bytes = source.read("testbed", "environments/manager/images.yml", config)

    docker_images = release.parse_release(release_bytes)
    alias_map = manager_template.extract_alias_map(template_bytes)
    rendered = manager.parse_manager(rendered_bytes)

    expected_src = f"release/{config.release_version}/base.yml"
    found_src = "testbed/environments/manager/images.yml"
    drifts = []

    for alias, entry in rendered.items():
        if entry.kind == manager.KIND_UNRESOLVED:
            if verbose:
                print(
                    f"warning: unresolved Jinja in rendered manager file: "
                    f"{alias}_tag = {entry.value}",
                    file=sys.stderr,
                )
            continue
        release_key = alias_map.get(alias, alias)
        expected = docker_images.get(release_key)
        if expected is None:
            continue
        if entry.value == expected:
            continue
        d = DriftEntry(
            plugin=NAME,
            image=release_key,
            alias=alias,
            expected=expected,
            found=entry.value,
            expected_src=expected_src,
            found_src=found_src,
        )
        d = allowlist.apply(d)
        drifts.append(d)
    return drifts

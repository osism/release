"""kolla_build_conf_unknown_section: a kolla-build.conf source section kolla
does not read.

osism/container-images-kolla renders kolla-build.conf from
templates/<release>/kolla-build.conf.j2 and the openstack_projects of release
latest/openstack-<release>.yml. Each project gets a source section that points
kolla at the tarball the release file pins. kolla only reads a source section
whose name is a key of SOURCES in kolla/common/sources.py. It ignores any other
section without a warning and builds that image from its default upstream URL.
The pin then has no effect, and nothing fails.

kolla_source_ref_phase checks that the pinned ref is the right one. That says
nothing about whether the build reads the pin, so a correct release file can
still hide an unused pin. This plugin checks the step after it: every source
section the build config contains must be a key kolla reads at that release.

The real template is rendered, not a copy of its naming rules, so a future
template change or an upstream key rename is caught as well. Only the
generator's project selection (src/generate-kolla-build-config.py) is mirrored
here, because the generator is a script and cannot be imported. Section names
depend only on project names, so the generator's repository mapping is not
needed.

A section counts as a source section when it sets `type`, which every kolla
source section must. Other sections in the file (DEFAULT, or option groups such
as profiles) are not source sections and are left alone.
"""

import ast
import configparser

import jinja2
import yaml

from osism_drift import enablement, source
from osism_drift.http import SourceError
from osism_drift.model import DriftEntry

NAME = "kolla_build_conf_unknown_section"
DESCRIPTION = (
    "Flag source sections in the rendered kolla-build.conf that are not keys of "
    "kolla's SOURCES at that release, so kolla ignores the pin they carry."
)
INPUT_FILES = [
    ("release", "latest/openstack-<release>.yml"),
    ("container_images_kolla", "templates/<release>/kolla-build.conf.j2"),
    ("kolla", "kolla/common/sources.py (per resolved release ref)"),
]
SUMMARY = (
    "{n} source sections in the rendered kolla-build.conf that kolla does not "
    "read, so these images build from kolla's default upstream URL and ignore "
    "the ref pinned in the release file:"
)
REMEDIATION = (
    "rename the section in container-images-kolla "
    "templates/<release>/kolla-build.conf.j2 to the key kolla uses for that "
    "source in kolla/common/sources.py. kolla ignores an unknown section "
    "without a warning, so the build keeps succeeding with unpinned sources."
)

_TEMPLATE = "templates/{r}/kolla-build.conf.j2"
_SOURCES_PY = "kolla/common/sources.py"

# Projects src/generate-kolla-build-config.py leaves out of `projects`, so the
# template renders no per-project section for them. Keep in step with the
# generator.
_GENERATOR_SKIPS = frozenset({"gnocchi", "novajoin", "monasca"})


def generator_projects(versions: dict) -> list:
    """The `projects` list the generator passes to the template.

    Mirrors src/generate-kolla-build-config.py: skipped projects and projects
    without a version are dropped. `repository` is the project name except for
    the two agents the generator maps; the section name never depends on it,
    but the template reads it, so it must be set.
    """
    repos = {
        "neutron-lbaas-agent": "neutron-lbaas",
        "neutron-vpnaas-agent": "neutron-vpnaas",
    }
    projects = []
    for name, version in (versions.get("openstack_projects") or {}).items():
        if name in _GENERATOR_SKIPS or not version:
            continue
        projects.append(
            {"name": name, "version": version, "repository": repos.get(name, name)}
        )
    return projects


def render(template: str, versions: dict) -> str:
    """Render kolla-build.conf.j2 with the data the generator passes.

    The DEFAULT values (base, namespace, ...) do not affect section names, so
    placeholders stand in for the generator's environment-derived values.
    """
    data = {
        "base": "ubuntu",
        "base_tag": "placeholder",
        "gnocchi_version": (versions.get("openstack_projects") or {}).get(
            "gnocchi", ""
        ),
        "install_type": "source",
        "is_release": "False",
        "namespace": "osism",
        "openstack_release": versions.get("openstack_version", ""),
        "projects": generator_projects(versions),
        "versions": versions,
    }
    if "novajoin" in (versions.get("openstack_projects") or {}):
        data["novajoin_version"] = versions["openstack_projects"]["novajoin"]
    return jinja2.Environment().from_string(template).render(data)


def source_sections(conf: str) -> list:
    """Names of the sections in `conf` that set `type`, in file order."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read_string(conf)
    return [s for s in parser.sections() if parser.has_option(s, "type")]


def kolla_source_keys(sources_py: str) -> set:
    """The keys of the SOURCES dict literal in kolla/common/sources.py."""
    for node in ast.parse(sources_py).body:
        targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
        if any(getattr(t, "id", None) == "SOURCES" for t in targets):
            return set(ast.literal_eval(node.value))
    raise SourceError(f"no SOURCES assignment found in {_SOURCES_PY}")


def unknown_sections(template: str, versions: dict, keys: set) -> list:
    """Source sections of the rendered template that are not in `keys`."""
    return sorted(
        {s for s in source_sections(render(template, versions)) if s not in keys}
    )


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return one drift per (release, source section kolla does not read)."""
    drifts = []
    for release in enablement.release_range(config):
        versions = yaml.safe_load(
            source.read("release", f"latest/openstack-{release}.yml", config)
        )
        template_path = _TEMPLATE.format(r=release)
        template = source.read("container_images_kolla", template_path, config)
        ref = source.release_to_ref("kolla", release, config)
        keys = kolla_source_keys(
            source.read_at_ref("kolla", _SOURCES_PY, ref, config).decode()
        )
        try:
            unknown = unknown_sections(template.decode(), versions or {}, keys)
        except (jinja2.TemplateError, configparser.Error) as e:
            raise SourceError(
                f"rendering container-images-kolla {template_path}: {e}"
            ) from e
        for section in unknown:
            d = DriftEntry(
                plugin=NAME,
                image=section,
                alias=section,
                expected="(a key of kolla SOURCES)",
                found=section,
                expected_src=f"openstack/kolla {_SOURCES_PY}@{ref}",
                found_src=f"osism/container-images-kolla {template_path}",
            )
            drifts.append(allowlist.apply(d))
    return drifts

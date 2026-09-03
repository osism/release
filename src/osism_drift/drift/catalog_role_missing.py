"""catalog_role_missing: a catalog names a role no runtime advertises at some release.

python-osism's MAP_ROLE2ROLE collections and VALIDATE_PLAYBOOKS entries are static
and carry no notion of an OpenStack release, while the playbook set the runtime
images advertise is built per release. A name that resolves at some supported
releases and not others makes the collection non-portable across the range; a name
that resolves at none is dead outright.

The two catalogs resolve differently and must not share a resolver: `osism apply`
looks a role up in the merged interface map (playbooks.resolves), while `osism
validate` dispatches on the entry's own `runtime` and never consults that map
(playbooks.validate_resolves, mirroring validate.py:87-105). They also mean
something different by "expected"/"found" provenance and by "how to fix it", so
each branch below carries its own expected_src/found_src/remediation rather than
one set of strings stretched to cover both.
"""

from osism_drift import catalog, enablement, playbooks, source
from osism_drift.model import DriftEntry

NAME = "catalog_role_missing"
DESCRIPTION = (
    "Flag roles in python-osism's static catalogs that no runtime image "
    "advertises at some supported OpenStack release."
)
INPUT_FILES = [
    ("python_osism", "osism/data/enums.py"),
    ("container_image_kolla_ansible", "files/{src,scripts,playbooks}/..."),
    ("kolla_ansible", "ansible/site.yml + ansible/*.yml (per resolved release ref)"),
    ("ansible_playbooks", "playbooks/<env>/*.yml (at playbooks_version)"),
    ("container_image_ceph_ansible", "files/playbooks/..."),
    ("osism_kubernetes", "playbooks/kubernetes-*.yml"),
    # release_range's own listing, plus the *_version pins playbooks.py's
    # _pin()/_ceph_flavours() read to resolve ansible-playbooks/ceph-ansible refs.
    (
        "release",
        "latest/ (openstack-*.yml, ceph-*.yml) + latest/{base,ceph-<flavour>}.yml",
    ),
    # osism-ansible's own top-level playbooks and the two build scripts
    # osism_files()/osism_interface() read (playbooks.py:209,256).
    (
        "container_image_osism_ansible",
        "files/playbooks/*.yml + files/src/{generate-playbook-symlinks,render-playbooks}.py",
    ),
    # ceph-ansible's own infrastructure-playbooks, at each flavour's pinned ref
    # (playbooks.py:322, inside ceph_files()).
    (
        "ceph_ansible",
        "infrastructure-playbooks/*.yml (per flavour, at ceph_ansible_version pin)",
    ),
]
SUMMARY = (
    "{n} catalog entries naming a role the runtime images do not advertise at "
    "every supported release:"
)
REMEDIATION = (
    "drop the role from the collection, replace it with the name upstream now "
    "uses, or gate the collection on the release. A role missing at every "
    "release is dead and should be removed."
)

# expected_src/found_src for a MAP_ROLE2ROLE finding: the merged runtime
# interface apply.py's own lookup would consult, and the collection entry in
# enums.py that named the role.
_INTERFACE_SRC = "osism runtime images /interface/playbooks (reconstructed)"
_ENUMS_SRC = "osism/python-osism osism/data/enums.py"


def _interface_src(releases, config) -> str:
    """expected_src for a MAP_ROLE2ROLE finding, naming the refs it was
    reconstructed at (spec's entry-shape table: `(reconstructed) @ <refs>`).

    Of the four runtime sources runtime_interface() merges, kolla-ansible is
    the only one whose ref varies by OpenStack release -- playbooks.kolla_files()
    resolves it per release via source.release_to_ref(), the same call this
    reuses (memoized on config.ref_cache, so it costs nothing extra here).
    Naming that ref for every release checked is what lets a reader reproduce
    the exact comparison a finding's `found`/`ok` release lists ran against.
    """
    refs = ", ".join(
        f"{r}={source.release_to_ref('kolla_ansible', r, config)}" for r in releases
    )
    return f"{_INTERFACE_SRC} @ kolla-ansible {refs}"


# A VALIDATE_PLAYBOOKS finding is a malformed/stale validator entry, not a
# collection membership problem -- validate.py never consults the merged
# interface at all (see module docstring), so "drop it from the collection"
# would be actively wrong advice here.
_VALIDATOR_REMEDIATION = (
    "correct or drop the VALIDATE_PLAYBOOKS entry: point its runtime/"
    "environment/playbook fields at a file the runtime actually ships, or "
    "remove the entry if the validator no longer applies."
)


def _summary(ok: list, bad: list) -> str:
    """Per-entry summary naming the actual failing (and resolving) releases.

    report.py's default (non-SHOW_VALUES) text renderer shows only d.image per
    entry -- the release lists this plugin puts in expected/found survive only
    in --format json. Naming them here instead, in the one field report.py
    always renders and groups by, is how a text-report reader learns which
    releases are actually broken; that differential is this check's entire
    reason to exist. `{{n}}` is left literal -- report.py fills it in via
    `summary.format(n=len(entries))`.
    """
    bad_str = ", ".join(bad)
    if ok:
        return (
            f"{{n}} catalog entries whose role is unresolvable at {bad_str} "
            f"(it resolves at {', '.join(ok)}), so the collection is not "
            "portable across the supported range:"
        )
    return (
        f"{{n}} catalog entries whose role no runtime has advertised at any "
        f"supported release ({bad_str}):"
    )


def _checks(config, releases):
    """Yield one dict per entry in both python-osism catalogs, checked over the
    full release range: {subject, alias, ok, bad, expected_src, found_src,
    remediation}.

    A MAP_ROLE2ROLE role is checked with playbooks.resolves() against the
    per-release merged runtime_interface (mirrors `osism apply`); a
    VALIDATE_PLAYBOOKS entry is checked with playbooks.validate_resolves()
    against its own declared runtime, which never consults that merged map
    (mirrors `osism validate`). `subject` is the effective playbook/role name
    the report shows as `image`; `alias` is the collection name or validator
    key the catalog entry came from. `dict.fromkeys` de-dupes role names
    within one collection while preserving source order -- catalog._role_names
    appends at any depth with no de-dupe of its own, so a role reachable twice
    under one collection (none exist in the real catalog today) must not
    surface as two identical findings.
    """
    collections = catalog.collections(config)
    # Computed once per run, not per (collection, role): the same releases
    # are checked for every MAP_ROLE2ROLE entry, so the ref list is identical
    # across all of them.
    interface_src = _interface_src(releases, config) if collections else _INTERFACE_SRC
    for collection, roles in collections.items():
        for role in dict.fromkeys(roles):
            ok, bad = [], []
            for release in releases:
                interface = playbooks.runtime_interface(release, config)
                (ok if playbooks.resolves(role, interface) else bad).append(release)
            yield {
                "subject": role,
                "alias": collection,
                "ok": ok,
                "bad": bad,
                "expected_src": interface_src,
                "found_src": f"{_ENUMS_SRC} MAP_ROLE2ROLE[{collection}]",
                "remediation": None,  # falls back to the module-level REMEDIATION
            }

    for key, entry in catalog.validators(config).items():
        ok, bad = [], []
        for release in releases:
            resolved = playbooks.validate_resolves(
                entry["runtime"],
                entry["environment"],
                entry["playbook"],
                release,
                config,
            )
            (ok if resolved else bad).append(release)
        yield {
            "subject": entry["playbook"],
            "alias": key,
            "ok": ok,
            "bad": bad,
            "expected_src": f"{entry['runtime']} runtime image playbook set (reconstructed)",
            "found_src": f"{_ENUMS_SRC} VALIDATE_PLAYBOOKS[{key}]",
            "remediation": _VALIDATOR_REMEDIATION,
        }


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return one entry per (catalog entry, role) that fails to resolve at
    at least one supported release.

    Sorted by (alias, image) before returning: a set-order or dict-order
    walk here would make the report's finding order depend on the hash
    seed, which is unreviewable across runs.
    """
    del verbose
    releases = enablement.release_range(config)
    if not releases:
        # A hard stop, not zero findings: an empty range would make every
        # catalog role look unresolvable-everywhere, a mass false positive
        # rather than a genuine "nothing supported" result.
        raise source.SourceError(
            "empty supported release range; cannot compute the runtime interface"
        )

    drifts = []
    for check in _checks(config, releases):
        if not check["bad"]:
            continue
        drifts.append(
            allowlist.apply(
                DriftEntry(
                    plugin=NAME,
                    image=check["subject"],
                    alias=check["alias"],
                    expected=(
                        "advertised by a runtime image at every supported release"
                    ),
                    found=(
                        f"unresolvable at {', '.join(check['bad'])}"
                        + (
                            f" (resolves at {', '.join(check['ok'])})"
                            if check["ok"]
                            else ""
                        )
                    ),
                    expected_src=check["expected_src"],
                    found_src=check["found_src"],
                    summary=_summary(check["ok"], check["bad"]),
                    remediation=check["remediation"],
                )
            )
        )
    drifts.sort(key=lambda d: (d.alias, d.image))
    return drifts

"""The two generated layers: the 001 byte-copy mirror and the 010 back-compat emit.

Both compute desired state only. Nothing here touches the filesystem except to
read it for comparison; the writer consumes the resulting Plan.

The report is deliberately NOT here: classification lives in classify.py,
upstream attribution in attribute.py, and rendering in render.py/detail.py.
"""

from pathlib import Path

import yaml

from osism_drift import enablement

_ALL = "all"
_PREFIX = enablement.MIRROR_PREFIX  # "001-"
_MARKER = "generated-by: sync-mirror"
_MARKER_LINE = f"# {_MARKER}"
_MARKER_SCAN_LINES = 5  # header only; a marker further down is not ownership


class LayerError(Exception):
    """Base for every refusal in this module, so one except clause covers them."""


class MonolithicLayout(LayerError):
    """Upstream ships one group_vars/all.yml (<=2025.1); the 001 mirror layer needs the split."""


class EmptyUpstreamLayer(LayerError):
    """Upstream listed no group_vars files; deleting the whole mirror is not a plan."""


class UnownedCompatFile(LayerError):
    """A managed 010-* file lacks the generator marker; refuse to touch it."""


def _safe_name(name: str) -> str:
    """Confine an upstream basename before it becomes a write path.

    Upstream names come from a non-recursive git tree listing, so separators cannot
    appear today. This guards our own code: if that listing is ever made recursive,
    a 'subdir/foo.yml' would write outside the intended set silently.
    """
    if name != Path(name).name or not name.endswith(".yml"):
        raise ValueError(f"upstream group_vars name {name!r} is not a bare basename")
    return name


def mirror_layer(config, target, tree):
    """(writes, deletes, added_files, deleted_files) for the 001 layer.

    The 001- prefix is a constant, never a parameter. all/ merges in lexical
    filename order with later files winning, and 64 keys in the mirror are
    deliberately overridden by later OSISM files; a copy named nova.yml would sort
    after every digit-prefixed file and silently reverse all of them.
    """
    files = enablement.upstream_groupvars_files(target, config)
    names = [_safe_name(n) for n, _ in files]
    if not names:
        # Every existing 001-* file would have no upstream counterpart, so the plan
        # would be "delete the entire mirror, write nothing". A vanished listing is
        # far likelier than upstream genuinely shipping no group_vars -- a renamed
        # path or a bad ref reaches here as an empty list.
        raise EmptyUpstreamLayer(
            f"upstream {target} listed no group_vars/all files; refusing to plan "
            "deletion of the whole mirror layer"
        )
    if names == ["all.yml"]:
        raise MonolithicLayout(
            f"upstream {target} ships a monolithic group_vars/all.yml; "
            "sync-mirror supports the per-service layout only"
        )

    writes = {
        f"{_ALL}/{_PREFIX}{n}": body for n, body in zip(names, [b for _, b in files])
    }
    upstream_names = set(names)

    existing = sorted(
        p.name for p in (Path(tree) / _ALL).glob(f"{_PREFIX}*.yml") if p.is_file()
    )
    deletes = tuple(
        f"{_ALL}/{fn}" for fn in existing if fn[len(_PREFIX) :] not in upstream_names
    )
    added = tuple(sorted(p for p in writes if Path(p).name not in existing))
    return writes, deletes, added, deletes


def mirror_homes(tree) -> dict:
    """{key: upstream basename} for the mirror layer as it exists on disk now.

    Lexically last file wins, matching how Ansible loads a group_vars directory,
    so the file named is the one whose value actually takes effect. The 001-
    prefix is stripped so these line up with upstream_groupvars_homes().

    Read from disk rather than inferred from the previous release: re-run sync-mirror
    after an upstream backport on the same target and the on-disk mirror is an
    earlier commit of that target, not of prev.
    """
    homes = {}
    for path in sorted((Path(tree) / _ALL).glob(f"{_PREFIX}*.yml")):
        for key in enablement.groupvars_values([path.read_bytes()]):
            homes[key] = path.name[len(_PREFIX) :]
    return homes


def compat_layer(config, rng, shas, tree, strict=True):
    """(writes, deletes, {key: destination}) for the 010 back-compat layer.

    Keys come from dropped_key_release_map, at key granularity: of the 31 keys
    dropped between 2025.2 and 2026.1, three came from files that still exist, so
    a whole-file carry would miss exactly those. Values come from the newest
    release that still defines the key.

    Only releases in rng[:-1] are managed. Without that scoping a replay with an
    older --target would find no keys for a later re-sync's 010 file and delete
    it; files outside the set are never written, deleted, or even inspected.
    """
    target = rng[-1]
    managed = set(rng[:-1])
    # dropped_key_release_map() is NOT a map of dropped keys: it maps EVERY key
    # defined by a release below the newest to the newest such release. A key is
    # dropped only if the target no longer defines it -- which is how
    # groupvars_home() reads it, checking `key in newest_keys` first and consulting
    # the map only on the way past. Using the map directly emitted 880 keys for a
    # 2025.2 target where 31 were dropped.
    still_defined = enablement.upstream_groupvars_keys(target, config)
    # A dropped key that another OSISM layer already supplies needs no 010 entry:
    # the enforced union is satisfied, and 010-* sorts before 099-* so the entry
    # would be shadowed anyway. Validated against ground truth -- without this the
    # generator emitted 37 keys the committed files omit, and all 37 were supplied
    # by a 099 layer.
    #
    # osism_supply_excluding_mirror() cannot serve here: it excludes the 001 mirror
    # but INCLUDES the 010 layer, so every key already present in a committed 010
    # file reads as "supplied" and the generator empties its own output.
    supplied = enablement.osism_supply_excluding(config, (_PREFIX, "010-"))
    by_release = {}
    for key, rel in enablement.dropped_key_release_map(config).items():
        if rel in managed and key not in still_defined and key not in supplied:
            by_release.setdefault(rel, []).append(key)

    writes, dests, unowned = {}, {}, []
    for rel, keys in by_release.items():
        values = enablement.upstream_groupvars_values(rel, config)
        missing = [k for k in keys if k not in values]
        if missing:
            raise KeyError(
                f"dropped keys {sorted(missing)} have no value at release {rel}; "
                "dropped_key_release_map and upstream_groupvars_values disagree"
            )
        path = f"{_ALL}/010-{rel}.yml"
        if not _owned(tree, path):
            unowned.append(path)
            if strict:
                _refuse(path)
        writes[path] = _render_compat(rel, keys, values, shas)
        for k in keys:
            dests[k] = path

    # An existing managed file with no desired keys is stale and must go, or the
    # layer keeps supplying keys upstream has since restored.
    deletes = []
    for rel in sorted(managed):
        path = f"{_ALL}/010-{rel}.yml"
        if path in writes:
            continue
        if (Path(tree) / path).exists():
            if not _owned(tree, path):
                unowned.append(path)
                if strict:
                    _refuse(path)
            deletes.append(path)
    return writes, tuple(deletes), dests, tuple(sorted(set(unowned)))


def _owned(tree, path) -> bool:
    """True if we may write or delete this 010 file.

    An absent file is ours to create. An existing one must carry an exact marker
    line in its header -- not a substring anywhere, because a hand-written file
    could legitimately mention the marker in a value or a comment.
    """
    f = Path(tree) / path
    if not f.exists():
        return True
    head = f.read_text(encoding="utf-8").splitlines()[:_MARKER_SCAN_LINES]
    return any(line.strip() == _MARKER_LINE for line in head)


def _refuse(path):
    raise UnownedCompatFile(
        f"{path} has no '{_MARKER_LINE}' header line; refusing to overwrite or "
        "delete a file this tool did not generate"
    )


class _IndentedDumper(yaml.SafeDumper):
    """SafeDumper that indents block sequences under their key.

    yaml.safe_dump emits a block sequence at the PARENT's indentation:

        run_default_subdirectories:
        - /run/netns

    osism/defaults runs yamllint with `extends: default`, whose
    indentation rule sets indent-sequences: true, so that form is an error --
    a re-sync would fail CI on a file this generator wrote. The hand-written
    layers were indented; the generated ones have to be too.
    """

    def increase_indent(self, flow=False, indentless=False):
        # indentless=True is what drops the sequence indentation. Never honour it.
        return super().increase_indent(flow, False)


def _double_quoted_str(dumper, value):
    """Emit every string double-quoted, the way upstream and the hand-written
    layers do.

    PyYAML's default prefers single quotes, which inverted the house style in
    this directory: the 001 mirror carries 677 double-quoted values and no
    single-quoted ones, and the hand-maintained 010 layers matched it. Worse, a
    Jinja value containing a single quote came out with YAML's doubling escape --
    "== ''influxdb''" -- where upstream writes "== 'influxdb'".

    No value in the layer contains a double quote, so forcing this style needs no
    escaping; multi-line strings would fold awkwardly, and none occur here
    because the 010 layers carry values from releases that predate upstream's
    re-wrap.
    """
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


class _BareKey(str):
    """A mapping key, emitted unquoted.

    The double-quoted style above applies to every str, keys included, and
    upstream writes bare keys with quoted values -- `enable_nova: "{{ ... }}"`.
    Marking the keys is less invasive than reimplementing represent_mapping to
    tell one from the other.
    """


def _bare_key(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style=None)


def _mark_keys(value):
    """Mark every mapping key, at any depth, as bare.

    Nested keys are as bare as top-level ones upstream -- a container's
    `ulimits: {nofile: {soft: ...}}` has no quotes at any level -- so marking
    only the outer dict left the inner ones quoted.
    """
    if isinstance(value, dict):
        return {
            (_BareKey(k) if isinstance(k, str) else k): _mark_keys(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_mark_keys(v) for v in value]
    return value


_IndentedDumper.add_representer(str, _double_quoted_str)
_IndentedDumper.add_representer(_BareKey, _bare_key)


def _render_compat(rel, keys, values, shas) -> str:
    """Generator-owned 010 file: fixed header, sorted keys, one document.

    Values ARE re-serialized here, unlike the 001 layer. That is safe and
    deliberate: kolla_mirror_verbatim enforces verbatim on the 001 layer only, and
    010-* is OSISM-authored back-compat rather than a mirror. The header says so,
    so nobody "fixes" the notation later.
    """
    head = [
        "---",
        _MARKER_LINE,
        f"# Self-retiring backward-compat layer for release {rel}.",
        "#",
        "# Upstream group_vars/all keys that a newer supported release dropped but",
        f"# that release {rel} still defines. Loads between the 001-* mirror and",
        "# 099 (OSISM opinions). Values are re-serialized, not byte-copied:",
        "# verbatim is enforced on the 001 layer only. No OSISM edits belong here.",
        "#",
        f"# Remove this file when {rel} leaves the supported release range.",
        "# Source: openstack/kolla-ansible ansible/group_vars/all, at the pinned",
        "# commit for each supported release in this run:",
    ] + [f"#   {r}: {shas[r]}" for r in sorted(shas)]
    # sort_keys=False, with the top level sorted explicitly above: sorting every
    # level would reorder a nested mapping away from the order upstream wrote it
    # in -- a container's ulimits came out hard-before-soft -- for no gain, since
    # determinism only needs the top level fixed.
    body = yaml.dump(
        _mark_keys({k: values[k] for k in sorted(keys)}),
        Dumper=_IndentedDumper,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    return "\n".join(head) + "\n" + body


def compat_key_effects(writes, tree):
    """{path: {"added": [...], "removed": [...], "updated": [...]}} per 010 layer.

    A byte comparison can only say "differs", and a 010 file differs on every run
    because its header carries the pinned commit of each release in range. Saying
    "rewritten" for a file whose keys and values are untouched reads as data loss.
    So compare the parsed bodies and report what actually moves -- usually
    nothing.
    """
    effects = {}
    for path, desired in sorted(writes.items()):
        on_disk = Path(tree) / path
        if not on_disk.exists():
            continue
        old = yaml.safe_load(on_disk.read_text(encoding="utf-8")) or {}
        new = yaml.safe_load(desired) or {}
        effects[path] = {
            "added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)),
            "updated": sorted(k for k in set(old) & set(new) if old[k] != new[k]),
        }
    return effects


def changed_and_created(writes, tree):
    """(changed_writes, created_paths) by byte comparison against `tree`.

    has_changes must come from this, not from writes being non-empty: writes is
    the full desired layer and always holds every unchanged file too.
    """
    changed, created = [], []
    for path, desired in sorted(writes.items()):
        on_disk = Path(tree) / path
        want = desired if isinstance(desired, bytes) else desired.encode()
        if not on_disk.exists():
            changed.append(path)
            created.append(path)
        elif on_disk.read_bytes() != want:
            changed.append(path)
    return tuple(changed), tuple(created)

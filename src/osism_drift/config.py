"""Config + allowlist loaders with schema validation."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


class ConfigError(Exception):
    """Raised on invalid config or allowlist content."""


_ALLOWED_TOP_KEYS = {
    "remote",
    "release_version",
    "plugins",
    "sources",
    "releases",
    "release_refs",
}
_ALLOWED_REMOTE_KEYS = {"github_raw", "github_api", "branch", "default_owner"}
_ALLOWED_SOURCE_KEYS = {"owner", "branch"}


@dataclass(frozen=True)
class Remote:
    """GitHub raw/API base URLs and the default owner for remote reads."""

    github_raw: str
    github_api: str
    branch: str
    default_owner: str = "osism"


@dataclass(frozen=True)
class SourceCfg:
    """Per-repo owner/ref override. A set `branch` pins the repo (read remotely)."""

    owner: Optional[str] = None
    branch: Optional[str] = None


@dataclass(frozen=True)
class PluginCfg:
    """Per-plugin configuration (currently only the enabled flag)."""

    enabled: bool


@dataclass(frozen=True)
class Config:  # pylint: disable=too-many-instance-attributes  # data record
    """Resolved drift-detector configuration (file plus CLI overrides)."""

    remote: Remote
    release_version: str
    plugins: dict  # name -> PluginCfg
    sources: dict = field(default_factory=dict)  # repo -> SourceCfg
    releases: tuple = ()  # explicit supported-release override; () -> derive
    release_refs: dict = field(default_factory=dict)  # {repo: {release: ref}} override
    base_dirs: tuple = ()  # local checkout roots (CLI --base-dir); () -> all remote
    remote_fallback: bool = False  # CLI --remote-fallback: not-found-local -> remote
    ref_cache: dict = field(
        default_factory=dict
    )  # per-run release_to_ref memo (runtime state)
    archive: bool = False  # driver sets True (archive reads); --use-raw-get keeps False
    snapshot_cache: dict = field(
        default_factory=dict
    )  # per-run (github_api, owner, slug, ref) -> extracted Path


def load_config(path) -> Config:
    """Parse and schema-validate a config YAML file into a Config."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    extra = set(raw) - _ALLOWED_TOP_KEYS
    if extra:
        raise ConfigError(f"unknown top-level keys: {sorted(extra)}")

    remote_raw = raw["remote"]
    extra_r = set(remote_raw) - _ALLOWED_REMOTE_KEYS
    if extra_r:
        raise ConfigError(f"unknown remote keys: {sorted(extra_r)}")
    remote = Remote(**remote_raw)

    sources = {}
    for name, spec in (raw.get("sources") or {}).items():
        spec = spec or {}
        extra_s = set(spec) - _ALLOWED_SOURCE_KEYS
        if extra_s:
            raise ConfigError(f"source {name!r}: unknown keys {sorted(extra_s)}")
        sources[name] = SourceCfg(owner=spec.get("owner"), branch=spec.get("branch"))

    plugin_raw = raw.get("plugins", {})
    plugins = {
        name: PluginCfg(enabled=bool(spec.get("enabled", True)))
        for name, spec in plugin_raw.items()
    }

    releases = tuple(str(r) for r in (raw.get("releases") or []))
    release_refs = {
        repo: {str(rel): str(ref) for rel, ref in (mapping or {}).items()}
        for repo, mapping in (raw.get("release_refs") or {}).items()
    }

    return Config(
        remote=remote,
        release_version=raw["release_version"],
        plugins=plugins,
        sources=sources,
        releases=releases,
        release_refs=release_refs,
    )


@dataclass(frozen=True)
class AllowEntry:
    """One allowlist rule that marks matching drift entries as allowlisted."""

    plugin: str
    image: str
    reason: str
    alias: Optional[str] = None
    found_src: Optional[str] = None
    match: str = "exact"

    def _image_matches(self, image: str) -> bool:
        # Boundary-aware prefix: a prefix covers a service and its sub-groups
        # (separators - and :) without swallowing an adjacent name (cyborgx).
        # "_" is deliberately NOT a boundary. canon() maps "-" to "_", so a
        # "senlin" prefix would silently swallow senlin_api_port and friends; the
        # underscore-namespaced plugins (kolla_orphan_config, kolla_enablement_orphan,
        # kolla_secrets_orphan) are allowlisted by exact match instead. For family
        # suppression, spell the boundary out (image: senlin_). Silently masking a
        # future finding is the worst failure mode for a drift detector.
        if self.match == "prefix":
            return (
                image == self.image
                or image.startswith(self.image + "-")
                or image.startswith(self.image + ":")
            )
        return image == self.image

    def matches(self, drift) -> bool:
        """True if this entry covers `drift` (plugin, image, alias, source)."""
        if drift.plugin != self.plugin or not self._image_matches(drift.image):
            return False
        if self.alias is not None and drift.alias != self.alias:
            return False
        if self.found_src is not None and drift.found_src != self.found_src:
            return False
        return True


@dataclass(frozen=True)
class Allowlist:
    """An ordered collection of AllowEntry allowlist rules."""

    entries: tuple

    def match(self, drift):
        """Return the first entry matching `drift`, or None."""
        for e in self.entries:
            if e.matches(drift):
                return e
        return None

    def apply(self, drift):
        """Return `drift`, marked allowlisted if an entry matches it.

        Folds the match-then-mark idiom every plugin repeats per finding into
        one call: unmatched drifts are returned unchanged, matched ones as an
        allowlisted copy carrying the match reason.
        """
        match = self.match(drift)
        if match is not None:
            return drift.as_allowlisted(match.reason)
        return drift

    def stale(self, drifts, plugins):
        """Entries whose plugin ran but which matched none of the drifts.

        A stale entry matches no real drift and is a hard error. Scoped to
        `plugins` (the names of the plugins that actually ran) so a filtered
        run does not flag entries belonging to plugins that did not run.
        """
        return [
            e
            for e in self.entries
            if e.plugin in plugins and not any(e.matches(d) for d in drifts)
        ]


_ALLOWED_ALLOW_KEYS = {"plugin", "image", "reason", "alias", "found_src", "match"}


def load_allowlist(path) -> Allowlist:
    """Parse and validate an allowlist YAML file into an Allowlist.

    A missing file yields an empty allowlist.
    """
    p = Path(path)
    if not p.exists():
        return Allowlist(entries=())
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for i, item in enumerate(raw.get("allow", [])):
        extra = set(item) - _ALLOWED_ALLOW_KEYS
        if extra:
            raise ConfigError(f"allowlist entry {i}: unknown keys {sorted(extra)}")
        for required in ("plugin", "image", "reason"):
            if required not in item:
                raise ConfigError(
                    f"allowlist entry {i}: missing required field {required!r}"
                )
        if not item["image"]:
            raise ConfigError(f"allowlist entry {i}: image must be non-empty")
        if not item["reason"]:
            raise ConfigError(f"allowlist entry {i}: reason must be non-empty")
        match = item.get("match", "exact")
        if match not in ("exact", "prefix"):
            raise ConfigError(
                f"allowlist entry {i}: match must be 'exact' or 'prefix', got {match!r}"
            )
        out.append(
            AllowEntry(
                plugin=item["plugin"],
                image=item["image"],
                reason=item["reason"],
                alias=item.get("alias"),
                found_src=item.get("found_src"),
                match=match,
            )
        )
    return Allowlist(entries=tuple(out))

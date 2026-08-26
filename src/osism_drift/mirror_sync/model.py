"""Pure data for sync-mirror: no I/O, no git, no network."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SyncOpts:
    """Resolved CLI intent for one run."""

    target: str
    base_ref: str = "origin/main"
    pin: str = None
    pins: dict = field(default_factory=dict)  # release -> sha, from --pins-from
    accept_upstream: frozenset = frozenset()
    retain: frozenset = frozenset()
    retain_unverified: frozenset = frozenset()
    worktree_path: str = None
    apply: bool = False


@dataclass(frozen=True)
class Row:
    """One key whose parsed value differs between the pre- and post-sync mirror."""

    key: str
    old: object
    new: object
    cls: str  # overridden | notation | representation | semantic
    commits: tuple = ()
    note: str = ""


@dataclass(frozen=True)
class Plan:  # pylint: disable=too-many-instance-attributes  # data record
    """Everything a run would do, computed before anything is written."""

    target_release: str
    release_shas: dict
    defaults_base_sha: str
    prev_release: str
    range_base_sha: str
    mirror_writes: dict  # path -> desired bytes (the FULL layer)
    mirror_deletes: tuple
    compat_writes: dict  # path -> desired text
    compat_deletes: tuple
    unowned_compat: tuple  # existing 010 files lacking the generator marker
    changed_writes: tuple  # paths whose bytes differ from what is on disk
    created_paths: tuple  # changed_writes that do not exist yet
    rows: tuple
    added_keys: tuple
    dropped_keys: dict  # key -> 010 destination path
    added_files: tuple
    deleted_files: tuple
    # {010 path: {added/removed/updated: [keys]}}; absent for a file being created
    compat_effects: dict = field(default_factory=dict)

    @property
    def blocked(self) -> tuple:
        """Reasons --apply cannot proceed, independent of dispositions."""
        return tuple(f"{p} lacks the generator marker" for p in self.unowned_compat)

    @property
    def has_changes(self) -> bool:
        """True only if something on disk would actually change.

        mirror_writes always holds the whole desired layer, unchanged files
        included, so it can never be the signal here: deriving from it would make
        "nothing to do" unreachable and report work pending on a correct tree.
        """
        return bool(self.changed_writes or self.mirror_deletes or self.compat_deletes)

    @property
    def semantic_keys(self) -> tuple:
        """Keys needing a disposition, in row order."""
        return tuple(r.key for r in self.rows if r.cls == "semantic")
